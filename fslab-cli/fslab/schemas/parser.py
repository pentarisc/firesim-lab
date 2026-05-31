"""
fslab/schemas/parser.py
=======================
Orchestration layer for the **two-pass configuration system**.

Public API
----------
    load_and_validate(project_yaml_path) -> (FSLabConfig, MasterRegistry)

Two-Pass Architecture
---------------------
Pass 1 — Registry Loading:
    a. Read ``advanced.default_registry`` from the project YAML.
    b. Read each path in ``advanced.custom_registries`` (in order).
    c. Parse each file into a ``RegistryFile``.
    d. Merge all ``RegistryFile`` objects into a single ``MasterRegistry``
       using last-definition-wins semantics (REG-07).

Pass 2 — Project Validation:
    Parse ``fslab.yaml`` into an ``FSLabConfig``, injecting the
    ``MasterRegistry`` as Pydantic validation context so that all
    cross-registry checks (PROJ-11, PROJ-12, PROJ-13) can run.

    Between reading the raw YAML and Pydantic validation, the
    ``_merge_target_defaults`` step folds the platform-registry's
    per-host-model, per-publisher, and per-artifact-source defaults into
    the user-supplied blocks (e.g. ``host_models.ec2_launch.instance_type``
    → user's ``target.build.host.instance_type`` when the user did not
    override). Covers both ``target.build`` and ``target.run`` — the
    registry-default merge step referenced in the build-pipeline and
    run-pipeline handoff documents.
"""

from __future__ import annotations

import os
import threading
import importlib.util
from pathlib import Path
from typing import Tuple, Annotated, Dict, Optional, Union, List
import functools
import operator
import yaml
from pydantic import ValidationError, create_model, Field, model_validator

from .registry import MasterRegistry, RegistryFile
from .project import AdvancedConfig, FSLabConfig
from .resolvers import BRIDGE_CFG_REGISTRY
from fslab.utils.versioning import (
    VERSION_FIELD,
    check_project_version,
    check_registry_version,
)

# Default registry path:
_DEFAULT_REGISTRY = Path("/opt/firesim-lab/lib/registry.yaml")

_CONFIG_LOCK = threading.Lock()
_LOADED_PATH: Optional[Path] = None
_CACHED_DATA: Optional[Tuple[FSLabConfig, MasterRegistry]] = None

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _read_yaml(path: Path) -> dict:
    """Read a YAML file and return its contents as a plain dict."""
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data or {}


def _load_registry_file(path: Path) -> RegistryFile:
    """
    Parse a single ``registry.yaml`` file into a validated ``RegistryFile``.

    Raises ``FileNotFoundError``  if *path* does not exist.
    Raises ``pydantic.ValidationError`` if the YAML content is invalid.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Registry file not found: {path}"
        )
    raw = _read_yaml(path)
    # Refuse a registry whose MAJOR.MINOR does not match this CLI (and refuse
    # legacy files with no version field) before structural validation, so the
    # user gets a clear migration message instead of a schema error.
    check_registry_version(raw.get(VERSION_FIELD), source=str(path))
    return RegistryFile.model_validate(raw)


def _load_user_plugin(plugin_path: Path):
    """Dynamically loads a python script into memory."""
    if os.environ.get("ENABLE_CUSTOM_PLUGINS") != "1":
        raise PermissionError(
            f"The YAML configuration is attempting to run a custom Python plugin ({plugin_path.name}).\n"
            "For security reasons, this is disabled by default.\n"
            "If you trust this project set ENABLE_CUSTOM_PLUGINS=1 in /target/.firesim-lab.env file\n"
            "and restart the container using firesim-lab or source the file."
        )

    if not plugin_path.exists():
        raise FileNotFoundError(f"Custom plugin not found at: {plugin_path}")

    spec = importlib.util.spec_from_file_location(plugin_path.stem, plugin_path)
    module = importlib.util.module_from_spec(spec)
    # Executing this module registers their Pydantic classes into your system
    spec.loader.exec_module(module)

def _sync_bridge_refs(self):
    """
    This runs AFTER the entire LiveConfig (and all resources)
    have been validated and converted to Python types.
    """
    # 1. Access the validated sibling field, e.g. design.parameters
    if self.design.parameters:
        design_params = self.design.parameters

        # 2. Distribute to children
        for bridge in self.bridges:
            # Check if the child has the specific method to handle this
            if hasattr(bridge, "resolve_refs"):
                bridge.resolve_refs(design_params)

    print("sync bridge ref complete.")
    return self

def _get_live_config_model():
    """
    Creates a 'Specialized' version of FSLabConfig
    that knows about all currently registered plugins.
    """
    # 1. Build the dynamic Union from the current state of BRIDGE_CFG_REGISTRY
    DynamicUnion = functools.reduce(operator.or_, BRIDGE_CFG_REGISTRY)

    DiscriminatedBridgeConfig = Annotated[
        DynamicUnion,
        Field(discriminator='type')
    ]

    # 2. Create a NEW class that inherits from FSLabConfig
    # but OVERRIDES the 'resources' field with the real types.
    # This keeps all your other fields, methods, and validators intact!
    LiveConfig = create_model(
        "LiveFSLabConfig",
        __base__=FSLabConfig,
        bridges=(List[DiscriminatedBridgeConfig], Field(...)),
        __validators__={
            "sync_logic": model_validator(mode='after')(_sync_bridge_refs)
        }
    )

    return LiveConfig


def _merge_target_defaults(
    raw_project: dict, master_registry: MasterRegistry
) -> None:
    """Fold platform-registry defaults into the user's target.* blocks
    before pydantic validation.

    Layered-defaults policy (user wins on every key):
        registry.platforms[<id>].host_models[<host.type>]
            ⨯  user target.build.host dict
            ⨯  user target.run.host   dict
        registry.platforms[<id>].publish[<publish.type>]
            ⨯  user target.build.publish dict
        registry.platforms[<id>].run_artifact_sources[<artifact_source.type>]
            ⨯  user target.run.artifact_source dict

    The merge is shallow — sufficient for the current flat schemas. If a
    future block grows a nested dict the merge can be deepened here
    without touching the schemas themselves.

    The function mutates ``raw_project`` in place; safe because the dict
    has already been read from disk and is otherwise local to the parser.
    Silently noops when the platform has no entry (e.g. user mis-spelled
    the platform id — pydantic will catch that downstream with PROJ-11)
    or when target.run is absent.
    """
    target = raw_project.get("target") or {}
    platform_id = target.get("platform")
    if not platform_id:
        return  # pydantic will surface "missing platform" with a clearer error

    platform_entry = master_registry.platforms.get(platform_id)
    if platform_entry is None:
        return  # PROJ-11 catches the unknown platform downstream

    host_models = getattr(platform_entry, "host_models", None) or {}

    # ---- build axes -----------------------------------------------------
    build = target.get("build") or {}

    user_host = build.get("host")
    if isinstance(user_host, dict):
        host_type = user_host.get("type")
        defaults = host_models.get(host_type) if host_type else None
        if isinstance(defaults, dict):
            build["host"] = {**defaults, **user_host}

    user_publish = build.get("publish")
    if isinstance(user_publish, dict):
        publish_type = user_publish.get("type")
        publish_defaults_by_type = getattr(platform_entry, "publish", None) or {}
        defaults = (
            publish_defaults_by_type.get(publish_type) if publish_type else None
        )
        if isinstance(defaults, dict):
            build["publish"] = {**defaults, **user_publish}

    if "build" not in target:
        target["build"] = build

    # ---- run axes -------------------------------------------------------
    run = target.get("run")
    if isinstance(run, dict):
        user_run_host = run.get("host")
        if isinstance(user_run_host, dict):
            host_type = user_run_host.get("type")
            defaults = host_models.get(host_type) if host_type else None
            if isinstance(defaults, dict):
                run["host"] = {**defaults, **user_run_host}

        user_artifact = run.get("artifact_source")
        if isinstance(user_artifact, dict):
            art_type = user_artifact.get("type")
            art_defaults_by_type = (
                getattr(platform_entry, "run_artifact_sources", None) or {}
            )
            defaults = (
                art_defaults_by_type.get(art_type) if art_type else None
            )
            if isinstance(defaults, dict):
                run["artifact_source"] = {**defaults, **user_artifact}

        target["run"] = run

    # Make sure the mutations propagate back through the optional .get()s.
    if "target" not in raw_project:
        raw_project["target"] = target


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------
def load_and_validate(
    project_yaml_path: str = "fslab.yaml"
) -> Tuple[FSLabConfig, MasterRegistry]:
    global _LOADED_PATH, _CACHED_DATA

    canonical_path = Path(project_yaml_path).resolve()

    # 2. Use the lock to ensure only one thread validates at a time
    with _CONFIG_LOCK:
        # Check again inside the lock (Double-checked locking pattern)
        if _LOADED_PATH is not None:
            if canonical_path != _LOADED_PATH:
                raise RuntimeError(
                    f"Project mismatch! Locked to: {_LOADED_PATH}, "
                    f"requested: {canonical_path}."
                )
            return _CACHED_DATA

        # Perform the expensive I/O and validation
        config, registry = _internal_load_and_validate(canonical_path)

        # "Lock in" the result
        _LOADED_PATH = canonical_path
        _CACHED_DATA = (config, registry)

        return _CACHED_DATA

def _internal_load_and_validate(
    project_yaml_path: Path
) -> Tuple[FSLabConfig, MasterRegistry]:
    """
    Load and validate a complete fslab project in two passes.

    Parameters
    ----------
    project_yaml_path:
        Filesystem path to the user's ``fslab.yaml`` project file.

    Returns
    -------
    (FSLabConfig, MasterRegistry)
        The validated project configuration and the merged registry that was
        used to validate it.

    Raises
    ------
    FileNotFoundError
        If ``project_yaml_path`` or any referenced registry file does not exist.
    pydantic.ValidationError
        If any YAML file fails structural or semantic validation.

    Notes
    -----
    **Pass 1 – Registry**

    Registry files are loaded in this priority order (lowest → highest):

    1. ``advanced.default_registry``   (typically from the firesim-lab repo)
    2. Each entry in ``advanced.custom_registries`` (in list order)

    Later files overwrite earlier entries for the same ``id`` (REG-07).

    **Pass 2 – Project**

    The project YAML is validated with the ``MasterRegistry`` injected as
    Pydantic context so all cross-reference checks execute (PROJ-11–PROJ-13).
    A registry-default merge step folds per-platform ``host_models`` and
    ``publish`` defaults into the user's ``target.build`` block before
    pydantic validation runs.
    """
    project_path = project_yaml_path.resolve()

    if not project_path.exists():
        raise FileNotFoundError(
            f"Project file not found: {project_path}"
        )

    # ------------------------------------------------------------------
    # Pre-read: extract registry paths from the raw project YAML so that
    # we can build the registry *before* fully validating the project.
    # ------------------------------------------------------------------
    raw_project: dict = _read_yaml(project_path)

    # Refuse a project whose MAJOR.MINOR does not match this CLI (and refuse
    # legacy files with no version field) before any further work, so the user
    # gets a clear migration message instead of a downstream schema error.
    check_project_version(raw_project.get(VERSION_FIELD), source=str(project_path))

    advanced_raw: dict = raw_project.get("advanced", {})
    advanced = AdvancedConfig.model_validate(advanced_raw)

    # ------------------------------------------------------------------
    # PASS 1 — Build the MasterRegistry
    # ------------------------------------------------------------------
    registry_files: list[RegistryFile] = []

    # 1a. Default registry (lowest priority)
    if advanced.default_registry:
        default_path = Path(advanced.default_registry)
    else:
        default_path = _DEFAULT_REGISTRY

    registry_files.append(
        _load_registry_file(default_path)
    )

    # 1b. Custom registries (higher priority; loaded in list order — REG-07)
    for custom_entry in advanced.custom_registries:
        # custom_entry is now a RegistryEntry object!

        # ---> Load the Python plugin if the user provided one
        if custom_entry.plugin:
            plugin_path = Path(custom_entry.plugin)
            _load_user_plugin(plugin_path)

        # ---> Load the YAML registry file
        custom_path = Path(custom_entry.path)
        registry_files.append(
            _load_registry_file(custom_path)
        )

    # 1c. Merge — last-definition-wins (REG-07)
    master_registry = MasterRegistry.from_registry_files(registry_files)

    # ------------------------------------------------------------------
    # Registry-default merge into the raw project dict (build-pipeline
    # migration task 4b). Must run before pydantic validation so that
    # registry-supplied required fields satisfy the schema.
    # ------------------------------------------------------------------
    _merge_target_defaults(raw_project, master_registry)

    # ------------------------------------------------------------------
    # PASS 2 — Validate the project with MasterRegistry as context
    # ------------------------------------------------------------------
    LiveConfig = _get_live_config_model() # Generate dynamic bridge config classes.

    config = LiveConfig.model_validate(
        raw_project,
        context={"registry": master_registry},
    )

    return config, master_registry
