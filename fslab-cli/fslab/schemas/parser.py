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
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Tuple

import yaml
from pydantic import ValidationError

from .registry import MasterRegistry, RegistryFile
from .project import AdvancedConfig, FSLabConfig

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
    return RegistryFile.model_validate(raw)


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
    for custom_str in advanced.custom_registries:
        custom_path = Path(custom_str)
        registry_files.append(
            _load_registry_file(custom_path)
        )

    # 1c. Merge — last-definition-wins (REG-07)
    master_registry = MasterRegistry.from_registry_files(registry_files)

    # ------------------------------------------------------------------
    # PASS 2 — Validate the project with MasterRegistry as context
    # ------------------------------------------------------------------
    config = FSLabConfig.model_validate(
        raw_project,
        context={"registry": master_registry},
    )

    return config, master_registry