"""Resolved build configuration.

`BuildConfig` is the single value-object the build orchestration layer
operates on. It is constructed once via `BuildConfig.from_validated()` from
already-validated pydantic config classes (project + registry) and is
treated as immutable thereafter.

Why a frozen dataclass and not pydantic?
  `BuildConfig` is not parsed from a YAML — it is the *output* of
  orchestration logic, holding resolved `Path` objects and derived
  string properties. The pydantic checks that mattered have already
  run on the source classes (`BuildHostConfig`, `TargetBuildConfig`,
  `RemoteBuildConfig`); what's left here is filesystem state and
  cross-object derivation, neither of which is pydantic's strength.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

from fslab.schemas.project import BuildHostConfig, BuildStrategy
from fslab.utils.placeholders import substitute


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InvalidBuildConfig(Exception):
    """Raised when validated config + registry cannot be resolved into a
    workable BuildConfig (e.g. paths missing on local filesystem)."""


# ---------------------------------------------------------------------------
# BuildConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildConfig:
    """All resolved settings for a bitstream build.

    Constructed via `from_validated`. Treat as immutable.
    """

    # --- identifiers -------------------------------------------------------
    project_name: str
    platform_id: str
    quintuplet: str  # f"{project_name}-{platform_id}"

    # --- local paths (absolute, fully resolved) ---------------------------
    project_dir: Path
    platforms_root: Path
    local_platform_path: Path
    local_build_script: Path
    local_project_staging_dir: Path  # build/fpga/cl_{quintuplet}/
    local_results_base: Path  # build/fpga/results-build/

    # --- remote paths (strings — remote OS may differ) ---------------------
    remote_platform_path: str
    remote_cl_parent_subdir: str
    template_cl_name: str
    remote_build_script_name: str

    # --- build params ------------------------------------------------------
    fpga_frequency: float
    build_strategy: BuildStrategy

    # --- build host (the pydantic class lives in schemas/build.py) --------
    build_host: BuildHostConfig

    # --- derived helpers (string concat is correct: remote = posix) -------

    @property
    def remote_cl_parent(self) -> str:
        return f"{self.remote_platform_path}/{self.remote_cl_parent_subdir}"

    @property
    def remote_template_cl(self) -> str:
        return f"{self.remote_cl_parent}/{self.template_cl_name}"

    @property
    def remote_cl_dir(self) -> str:
        return f"{self.remote_cl_parent}/cl_{self.quintuplet}"

    # ----------------------------------------------------------------------
    # Construction
    # ----------------------------------------------------------------------

    @classmethod
    def from_validated(
        cls,
        project: object,
        registry: object,
        *,
        require_staging_dir: bool = True,
    ) -> "BuildConfig":
        """Resolve a validated project + registry into a BuildConfig.

        `project` and `registry` are already-validated pydantic objects. This
        method performs only what pydantic structurally cannot:
          * filesystem existence checks
          * placeholder substitution
          * cross-object lookup (platform → registry entry)

        Set `require_staging_dir=False` if the caller wants to construct a
        BuildConfig before the cmake staging step has produced its output
        (e.g. for unit tests or dry-run inspection).
        """
        # --- platforms root (with default + ${PLATFORMS_ROOT} resolution) -
        platforms_root = _platforms_root(project)

        # --- project basics ------------------------------------------------
        project_name: str = project.project.name
        project_dir = Path(str(project.project.project_dir)).expanduser().resolve()
        platform_id: str = project.target.platform
        quintuplet = f"{project_name}-{platform_id}"

        # --- registry lookup ----------------------------------------------
        platform_entry = _find_platform(registry, platform_id)
        rb = platform_entry.remote_build  # already validated by pydantic

        # --- substitute placeholders --------------------------------------
        subs = {"PLATFORMS_ROOT": str(platforms_root)}

        local_platform_path = Path(substitute(rb.local_platform_path, subs)).resolve()
        local_build_script = Path(substitute(rb.local_build_script, subs)).resolve()
        local_project_staging_dir = (
            project_dir / rb.local_project_staging_subdir.format(quintuplet=quintuplet)
        ).resolve()
        local_results_base = (project_dir / rb.local_results_subdir).resolve()

        # --- filesystem checks --------------------------------------------
        if not local_platform_path.is_dir():
            raise InvalidBuildConfig(
                f"local_platform_path does not exist or is not a directory: "
                f"{local_platform_path}"
            )
        if not local_build_script.is_file():
            raise InvalidBuildConfig(
                f"local_build_script does not exist or is not a file: "
                f"{local_build_script}"
            )
        if require_staging_dir and not local_project_staging_dir.is_dir():
            raise InvalidBuildConfig(
                f"Local project staging dir not found: {local_project_staging_dir}\n"
                f"  -> Run the fpga staging step first."
            )

        # --- target.build is required for fpga builds ---------------------
        build = getattr(project.target, "build", None)
        if build is None:
            raise InvalidBuildConfig(
                "project.target.build is missing; required for `fslab build fpga`."
            )

        return cls(
            project_name=project_name,
            platform_id=platform_id,
            quintuplet=quintuplet,
            project_dir=project_dir,
            platforms_root=platforms_root,
            local_platform_path=local_platform_path,
            local_build_script=local_build_script,
            local_project_staging_dir=local_project_staging_dir,
            local_results_base=local_results_base,
            remote_platform_path=rb.platform_path,
            remote_cl_parent_subdir=rb.remote_cl_parent_subdir,
            template_cl_name=rb.template_cl_name,
            remote_build_script_name=rb.build_script,
            fpga_frequency=float(build.fpga_frequency),
            build_strategy=BuildStrategy(build.build_strategy),
            build_host=build.build_host,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_PLATFORMS_ROOT_DEFAULT = "/opt/firesim/platforms"


def _platforms_root(project: object) -> Path:
    raw = (
        getattr(getattr(project, "advanced", None), "platforms_root", None)
        or _PLATFORMS_ROOT_DEFAULT
    )
    return Path(str(raw)).expanduser().resolve()


def _find_platform(registry: object, platform_id: str) -> Any:
    platform = registry.platforms.get(platform_id)
    if platform is not None:
        return platform
    known = sorted(registry.platforms.keys())
    raise InvalidBuildConfig(
        f"platform {platform_id!r} not found in registry. Known: {known}"
    )