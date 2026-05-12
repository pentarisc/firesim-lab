"""Resolved build configuration.

`BuildConfig` is the single value-object the build orchestration layer
operates on. It is constructed once via `BuildConfig.from_validated()` from
already-validated pydantic config classes (project + registry) and is
treated as immutable thereafter.

Why a frozen dataclass and not pydantic?
  `BuildConfig` is not parsed from a YAML — it is the *output* of
  orchestration logic, holding resolved `Path` objects and derived
  string properties. The pydantic checks that mattered have already
  run on the source classes (`HostModelConfig`, `TargetBuildConfig`,
  `BitbuilderEntry`, `PlatformEntry`); what's left here is filesystem
  state and cross-object derivation, neither of which is pydantic's
  strength.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fslab.schemas.host_model import HostModelConfig
from fslab.schemas.project import BuildStrategy
from fslab.schemas.publish import PublishConfig
from fslab.utils.placeholders import substitute


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InvalidBuildConfig(Exception):
    """Raised when validated config + registry cannot be resolved into a
    workable BuildConfig (e.g. paths missing on local filesystem, or
    platform has no bitbuilder configured)."""


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
    bitbuilder_id: str
    """Id of the bitbuilder catalog entry. Used by make_bitbuilder to look
    up the python_class in registry.bitbuilders."""

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

    # --- host-acquisition config (discriminated union) --------------------
    host: HostModelConfig
    """The validated host_model block from target.build.host. Concrete
    subclass depends on host.type. Provider factories narrow this with
    isinstance checks."""

    # --- publish config (discriminated union) -----------------------------
    publish: PublishConfig
    """The validated publish block from target.build.publish. Concrete
    subclass depends on publish.type. The publisher factory narrows this
    with isinstance checks."""

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
          * cross-object lookup (platform → registry entry → bitbuilder entry)

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

        # --- target.build is required for fpga builds ---------------------
        build = getattr(project.target, "build", None)
        if build is None:
            raise InvalidBuildConfig(
                "project.target.build is missing; required for `fslab build fpga`."
            )

        # --- bitbuilder lookup --------------------------------------------
        # The schema layer ([BB-05]) guarantees that whenever bitbuilder is
        # set, all four local_* paths are also set. But platforms without a
        # bitbuilder configured (e.g. xilinx_alveo_u250 today) reach this
        # code path only when the user explicitly runs `fslab build fpga` —
        # raise a clear, factory-level error rather than letting an attribute
        # access on None propagate.
        if platform_entry.bitbuilder is None:
            raise InvalidBuildConfig(
                f"platform '{platform_id}' has no bitbuilder configured for "
                f"fpga build. Driver compilation and metasim still work, but "
                f"`fslab build fpga` requires the platform's `bitbuilder:` "
                f"field to reference an entry in the bitbuilders catalog."
            )

        bb_entry = registry.bitbuilders.get(platform_entry.bitbuilder)
        if bb_entry is None:
            # Defensive: [BB-10] cross-validation should already have caught this.
            raise InvalidBuildConfig(
                f"platform '{platform_id}' references bitbuilder "
                f"'{platform_entry.bitbuilder}' which is not present in the "
                f"merged registry (known: {sorted(registry.bitbuilders)})."
            )

        # --- substitute placeholders --------------------------------------
        subs = {"PLATFORMS_ROOT": str(platforms_root)}

        local_platform_path = Path(
            substitute(platform_entry.local_platform_path, subs)
        ).resolve()
        local_build_script = Path(
            substitute(platform_entry.local_build_script, subs)
        ).resolve()
        local_project_staging_dir = (
            project_dir
            / platform_entry.local_project_staging_subdir.format(quintuplet=quintuplet)
        ).resolve()
        local_results_base = (
            project_dir / platform_entry.local_results_subdir
        ).resolve()

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

        # --- remote_platform_path: present on every host_model variant ----
        # ExternalHostConfig requires it (pydantic enforces). Ec2LaunchHostConfig
        # keeps it Optional pending the registry-default merge step (parser
        # task 4b in the migration handoff); the explicit check below covers
        # the gap until that lands.
        remote_platform_path = build.host.remote_platform_path
        if remote_platform_path is None:
            raise InvalidBuildConfig(
                f"build.host.remote_platform_path is unset for host.type="
                f"'{build.host.type}'. Either supply it in fslab.yaml or wait "
                f"for the registry-default merge step (parser task 4b)."
            )

        return cls(
            project_name=project_name,
            platform_id=platform_id,
            quintuplet=quintuplet,
            bitbuilder_id=platform_entry.bitbuilder,
            project_dir=project_dir,
            platforms_root=platforms_root,
            local_platform_path=local_platform_path,
            local_build_script=local_build_script,
            local_project_staging_dir=local_project_staging_dir,
            local_results_base=local_results_base,
            remote_platform_path=remote_platform_path,
            remote_cl_parent_subdir=bb_entry.remote_cl_parent_subdir,
            template_cl_name=bb_entry.template_cl_name,
            remote_build_script_name=bb_entry.build_script_basename,
            fpga_frequency=float(build.fpga_frequency),
            build_strategy=BuildStrategy(build.build_strategy),
            host=build.host,
            publish=build.publish,
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
