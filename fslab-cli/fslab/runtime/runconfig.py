"""Resolved run configuration.

`RunConfig` is the value-object the run orchestration layer operates on.
Constructed once via `RunConfig.from_validated()` from already-validated
pydantic config classes (project + registry) and treated as immutable.

Mirrors `fslab.bitstream.buildconfig.BuildConfig` in spirit — pydantic
checks have already run on the source classes (`HostModelConfig`,
`TargetRunConfig`, `RunnerEntry`, `PlatformEntry`); this layer does
filesystem-existence checks, cross-object derivation, and placeholder
substitution.

Driver-binary convention
------------------------
The local driver binary is expected at:

    <project_dir>/build/fpga/cl_{quintuplet}/driver/{driver_basename}

where `driver_basename` is derived per-project as
`<host.driver_name>-<platform_id>`, matching the build pipeline's CMake
output (see `DRIVER_TARGET` in CMakeLists.txt.j2). `from_validated`
raises `InvalidRunConfig` if the binary is missing — the user is
expected to have built the driver before invoking `fslab sim fpga` (no
implicit auto-compile, per Phase 3 decision).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fslab.schemas.artifact_source import ArtifactSourceConfig
from fslab.schemas.host_model import HostModelConfig
from fslab.schemas.runner_args import resolve_args_schema


class InvalidRunConfig(Exception):
    """Raised when validated config + registry cannot be resolved into a
    workable RunConfig (e.g. driver binary missing on local filesystem,
    or platform has no runner configured)."""


@dataclass(frozen=True)
class RunConfig:
    """All resolved settings for one foreground FPGA run.

    Constructed via `from_validated`. Treat as immutable.
    """

    # --- identifiers ------------------------------------------------------
    project_name: str
    platform_id: str
    quintuplet: str  # f"{project_name}-{platform_id}"
    runner_id: str
    runner_python_class: str

    # --- local paths (absolute, fully resolved) --------------------------
    project_dir: Path
    local_driver_path: Path
    """Absolute path to the driver binary
    (build/fpga/cl_{quintuplet}/driver/{driver_basename})."""

    # --- remote paths (strings; posix-style) ------------------------------
    remote_platform_path: str
    remote_slot_parent_subdir: str
    driver_basename: str
    """Filename of the driver binary, derived as
    `<host.driver_name>-<platform_id>` to match the build's CMake output."""

    # --- validated config blocks -----------------------------------------
    host: HostModelConfig
    """target.run.host — discriminated union. Providers narrow with
    isinstance checks."""

    artifact_source: ArtifactSourceConfig
    """target.run.artifact_source — discriminated by `type`."""

    runner_args: Any
    """Validated `RunnerArgsBase` subclass instance (F2RunnerArgs for
    today's only registered runner). The CLI calls `from_validated`,
    which re-parses the dict through the resolved schema so the
    orchestration layer gets typed access (rather than a `dict[str, Any]`)."""

    # --- derived ----------------------------------------------------------

    @property
    def remote_slot_dir(self) -> str:
        """Per-slot run dir on the remote host. Driver + artifacts live here."""
        if self.remote_slot_parent_subdir:
            return f"{self.remote_platform_path}/{self.remote_slot_parent_subdir}"
        return self.remote_platform_path

    @property
    def remote_driver_path(self) -> str:
        return f"{self.remote_slot_dir}/{self.driver_basename}"

    # ----------------------------------------------------------------------
    # Construction
    # ----------------------------------------------------------------------

    @classmethod
    def from_validated(
        cls,
        project: object,
        registry: object,
    ) -> "RunConfig":
        """Resolve a validated project + registry into a RunConfig.

        `project` and `registry` are already-validated pydantic objects.
        This method performs only what pydantic structurally cannot:
          * filesystem existence checks (driver binary)
          * cross-object lookup (platform → runner entry)
          * re-parse `runner_args` dict through the resolved args schema
            (cross-validation step ARTSRC-01 / RUNA-01 has already
            confirmed it validates; we re-parse to get a typed instance)
        """
        # --- target.run is required for `fslab sim fpga` -----------------
        run = getattr(project.target, "run", None)
        if run is None:
            raise InvalidRunConfig(
                "project.target.run is missing; required for `fslab sim fpga`. "
                "Add a `run:` block to target in fslab.yaml."
            )

        project_name: str = project.project.name
        project_dir = Path(str(project.project.project_dir)).expanduser().resolve()
        platform_id: str = project.target.platform
        quintuplet = f"{project_name}-{platform_id}"

        # --- registry lookup ---------------------------------------------
        platform_entry = registry.platforms.get(platform_id)
        if platform_entry is None:
            known = sorted(registry.platforms.keys())
            raise InvalidRunConfig(
                f"platform {platform_id!r} not found in registry. Known: {known}"
            )

        # --- runner lookup -----------------------------------------------
        # Pydantic cross-validation ([RUN-20]) already enforces this, but
        # defend at the factory level too.
        if platform_entry.runner is None:
            raise InvalidRunConfig(
                f"platform '{platform_id}' has no runner configured for "
                f"fpga simulation. `fslab sim fpga` requires the platform's "
                f"`runner:` field to reference an entry in the runners catalog."
            )
        runner_entry = registry.runners.get(platform_entry.runner)
        if runner_entry is None:
            # Defensive: [RUN-10] cross-validation should already have caught this.
            raise InvalidRunConfig(
                f"platform '{platform_id}' references runner "
                f"'{platform_entry.runner}' which is not present in the "
                f"merged registry (known: {sorted(registry.runners)})."
            )

        # --- remote_platform_path: required ------------------------------
        remote_platform_path = run.host.remote_platform_path
        if remote_platform_path is None:
            raise InvalidRunConfig(
                f"run.host.remote_platform_path is unset for host.type="
                f"'{run.host.type}'. Either supply it in fslab.yaml or "
                f"ensure the registry-default merge populates it."
            )

        # --- driver basename: derived from project, not registry ---------
        # Mirrors CMakeLists.txt.j2's DRIVER_TARGET = "${DRIVER_NAME}-${PLATFORM}".
        # Single source of truth for the binary name is host.driver_name in
        # fslab.yaml; the build emits "<driver_name>-<platform_id>".
        driver_basename = f"{project.host.driver_name}-{platform_id}"

        # --- local driver path -------------------------------------------
        # Convention: built by `fslab build fpga` into
        # build/fpga/cl_{quintuplet}/driver/{driver_basename}.
        local_driver_path = (
            project_dir
            / "build" / "fpga" / f"cl_{quintuplet}"
            / "driver" / driver_basename
        ).resolve()
        if not local_driver_path.is_file():
            raise InvalidRunConfig(
                f"Driver binary not found: {local_driver_path}\n"
                f"  -> Run `fslab build fpga` first to produce the driver."
            )

        # --- re-parse runner_args through the resolved schema ------------
        # The cross-validation in FSLabConfig.cross_validate_with_registry
        # already validated this dict; re-parsing here gives the
        # orchestrator a typed instance instead of an opaque dict.
        args_cls = resolve_args_schema(runner_entry.args_schema)
        runner_args = args_cls.model_validate(run.runner_args or {})

        return cls(
            project_name=project_name,
            platform_id=platform_id,
            quintuplet=quintuplet,
            runner_id=platform_entry.runner,
            runner_python_class=runner_entry.python_class,
            project_dir=project_dir,
            local_driver_path=local_driver_path,
            remote_platform_path=remote_platform_path,
            remote_slot_parent_subdir=runner_entry.remote_slot_parent_subdir,
            driver_basename=driver_basename,
            host=run.host,
            artifact_source=run.artifact_source,
            runner_args=runner_args,
        )
