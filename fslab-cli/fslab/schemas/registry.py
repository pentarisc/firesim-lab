"""
fslab/schemas/registry.py
=========================
Pydantic V2 models for parsing and validating one or more `registry.yaml`
files into a merged `MasterRegistry`.

Validation requirements
-----------------------
  REG-01  ID format regex
  REG-02  All required bridge fields present
  REG-03  Optional bridge fields (runtime_plusargs, scala_templates.top_imports)
  REG-04  Platform required fields
  REG-05  Feature required fields
  REG-06  ID uniqueness within a single RegistryFile
  REG-07  Merge / last-definition-wins across multiple RegistryFiles
  REG-08  Port name uniqueness and Verilog port-name pattern

  Platform cmake build fields
  REG-09  required_env_vars must be valid POSIX env var names (cmake $ENV{} style)
  REG-10  extra_libs must not carry -l prefix
  REG-11  extra_include_dirs / extra_link_dirs must be absolute or cmake-style refs
  REG-12  extra_cxx_flags / extra_link_options must start with '-'
  REG-13  cmake_fragment must not contain Jinja2 markers

  Metasim / fpgasim fields
  REG-09m required_env_vars same POSIX name rule (reused for MetaSimEntry/FpgaSimEntry)
  REG-10m extra_libs same no-prefix rule
  REG-11m extra_include_dirs / extra_link_dirs: Makefile-style paths
  REG-12m extra_cxx_flags / extra_link_options / tool_cxxopts must start with '-'
  REG-13m cmake_fragment must not contain Jinja2 markers (same as REG-13)
  REG-14  makefile_fragment must not contain Jinja2 markers
  REG-15  cmake_targets must be non-empty; each name must match ID regex
  REG-09x Cross-field: $(VAR) refs in Makefile-style paths must be in required_env_vars

  Build-pipeline fields  (NEW — replaces the previous RemoteBuildConfig block)
  BB-01   BitbuilderEntry.id format
  BB-02   python_class / args_schema / params_schema must be CamelCase identifiers
  BB-03   build_script_basename non-empty when build_script_flags are present
  BB-04   build_script_flags entries must start with '--'
  BB-05   PlatformEntry: when `bitbuilder` is set, all four `local_*` paths
          must also be set (paths required for the build pipeline)
  BB-06   PlatformEntry.local_project_staging_subdir must contain '{quintuplet}'
  BB-07   PlatformEntry.local_platform_path / local_build_script must be CMake-
          style path references (absolute, ${VAR}, or $ENV{VAR})
  BB-08   PlatformEntry.host_models keys must be registered in KNOWN_HOST_MODELS
  BB-09   PlatformEntry.publish keys must be registered in KNOWN_PUBLISH_TYPES
  BB-10   MasterRegistry cross-check: each platform.bitbuilder must reference
          an existing bitbuilder entry
  BB-11   MasterRegistry cross-check: bitbuilder.args_schema and params_schema
          must resolve via BITBUILDER_ARGS_REGISTRY / BITBUILDER_PARAMS_REGISTRY
  BB-12   MasterRegistry cross-check: platform.bitbuilder_params must validate
          against the resolved params_schema class

  Run-pipeline fields
  RUN-01  RunnerEntry.id format
  RUN-02  RunnerEntry.python_class / args_schema / params_schema must be
          CamelCase identifiers
  RUN-04  PlatformEntry.run_artifact_sources keys must be registered in
          KNOWN_ARTIFACT_SOURCE_TYPES
  RUN-10  MasterRegistry cross-check: each platform.runner (when set) must
          reference an existing runner entry
  RUN-11  MasterRegistry cross-check: runner.args_schema and params_schema
          must resolve via RUNNER_ARGS_REGISTRY / RUNNER_PARAMS_REGISTRY
"""

from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import fslab.utils.regexes as rx
from fslab.utils.display import regex_msg

from fslab.schemas.artifact_source import KNOWN_ARTIFACT_SOURCE_TYPES
from fslab.schemas.bitbuilder_args import (
    BITBUILDER_ARGS_REGISTRY,
    BITBUILDER_PARAMS_REGISTRY,
    resolve_args_schema,
    resolve_params_schema,
)
from fslab.schemas.host_model import KNOWN_HOST_MODELS
from fslab.schemas.publish import KNOWN_PUBLISH_TYPES
from fslab.schemas.runner_args import (
    RUNNER_ARGS_REGISTRY,
    RUNNER_PARAMS_REGISTRY,
)

_ORIGINS = {"firesim", "fslab", "custom"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_alpha_num(value: str, key: str, entity: str) -> str:
    """[REG-01] Validate that a value matches the allowed character set."""
    if not rx.ID_RE.match(value):
        raise ValueError(
            f"[REG-01] {entity} {key} '{value}' is invalid. " +
            regex_msg(rx.ID_RE)
        )
    return value


def _validate_no_jinja2(value: str, field_name: str, code: str) -> str:
    """[REG-13/REG-14] Reject Jinja2 markers in verbatim-emit fields."""
    if value and rx.JINJA2_EXPR_RE.search(value):
        raise ValueError(
            f"[{code}] {field_name} contains an unresolved Jinja2 marker "
            "('{{', '}}', '{%', or '{#'). "
            "The field is emitted verbatim — Jinja2 syntax will produce invalid "
            "CMake or Makefile output. Resolve any context variables in "
            "_build_template_context() before storing them here."
        )
    return value


def _validate_starts_with_dash(values: list[str], field_name: str, code: str) -> list[str]:
    """[REG-12 / REG-12m] Every flag/option must start with '-'."""
    for v in values:
        if not v:
            raise ValueError(f"[{code}] {field_name} contains an empty string entry.")
        if not v.startswith("-"):
            raise ValueError(
                f"[{code}] {field_name} entry '{v}' does not start with '-'. "
                f"Did you mean '-{v}'?"
            )
    return values


def _validate_lib_names(values: list[str], field_name: str, code: str) -> list[str]:
    """[REG-10 / REG-10m] Library names must be bare (no -l prefix)."""
    for lib in values:
        if not lib:
            raise ValueError(f"[{code}] {field_name} contains an empty string entry.")
        if lib.startswith("-l"):
            raise ValueError(
                f"[{code}] {field_name} entry '{lib}' must not start with '-l'. "
                f"Use '{lib[2:]}' instead."
            )
        if not rx.LIB_NAME_RE.match(lib):
            raise ValueError(
                f"[{code}] {field_name} entry '{lib}' contains invalid characters. "
                r"Allowed: alphanumerics, hyphens, underscores, dots, '+'."
            )
    return values


def _validate_paths(values: list[str], field_name: str, code: str,
                    path_re: re.Pattern, syntax_description: str) -> list[str]:
    """[REG-11 / REG-11m] Validate path reference format."""
    for path in values:
        if not path:
            raise ValueError(f"[{code}] {field_name} contains an empty string entry.")
        if not path_re.match(path):
            raise ValueError(
                f"[{code}] {field_name} entry '{path}' is not a recognised path "
                f"reference. {syntax_description} "
                "Relative paths are not allowed."
            )
    return values


# ---------------------------------------------------------------------------
# Bridge sub-models
# ---------------------------------------------------------------------------

class ScalaTemplates(BaseModel):
    """[REG-03] top_imports is optional; all other paths are required."""

    dut_imports: Optional[str] = None
    top_imports: Optional[str] = None
    ports: str
    wiring: str


class RuntimePlusarg(BaseModel):
    flag: str
    description: str
    required_params: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Simulator sub-model (shared by MetaSimEntry and FpgaSimEntry)
# ---------------------------------------------------------------------------

class SimTarget(BaseModel):
    """
    Declares a single cmake custom target exposed for a simulator tool.

    [REG-15] name must be non-empty and match the ID character set.
             make_target defaults to name when omitted.
    """

    name: str
    """
    Suffix for the cmake target name:
      metasim tools  -> cmake target "metasim-<name>"
      fpgasim tools  -> cmake target "fpgasim-<name>"
    Also used as the make target inside Makefile.<id>.sim unless make_target overrides.
    """

    make_target: str = ""
    """
    Make target name inside the generated Makefile.<id>.sim.
    Defaults to .name when left empty.
    """

    comment: str = ""
    """Progress message displayed by cmake --build."""

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """[REG-15] name must be non-empty and match the allowed character set."""
        if not v:
            raise ValueError("[REG-15] SimTarget.name must not be empty.")
        return _validate_alpha_num(v, "name", "SimTarget")

    @model_validator(mode="after")
    def default_make_target(self) -> "SimTarget":
        if not self.make_target:
            self.make_target = self.name
        return self


# ---------------------------------------------------------------------------
# Top-level registry entries — Bridge, Platform
# ---------------------------------------------------------------------------

class BridgeEntry(BaseModel):
    """
    [REG-02] All fields required except those in [REG-03].
    [REG-03] runtime_plusargs, top_imports are optional.
    """

    id: str
    label: str
    description: str
    origin: str
    input_ports: list[str]
    output_ports: list[str]
    cpp_type: str
    cpp_headers: list[str]
    cpp_sources: list[str]
    cpp_template: str
    scala_templates: ScalaTemplates

    runtime_plusargs: Optional[list[RuntimePlusarg]] = None
    required_params: Optional[list[str]] = None

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v: str) -> str:
        return _validate_alpha_num(v, "id", "Bridge")

    @field_validator("origin", mode="before")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """brige.origin must be one of 'firesim', 'fslab' or 'custom'."""
        if v not in _ORIGINS:
            raise ValueError(
                f"bridge.origin '{v}' is invalid. "
                f"Must be one of: {sorted(_ORIGINS)}"
            )
        return v

    @field_validator("cpp_type", mode="before")
    @classmethod
    def validate_cpp_type(cls, v: str) -> str:
        return _validate_alpha_num(v, "cpp_type", "Bridge")

    @model_validator(mode="after")
    def validate_ports(self) -> "BridgeEntry":
        """[REG-08] Port names must be valid Verilog identifiers and unique."""
        all_ports = list(self.input_ports) + list(self.output_ports)
        seen: set[str] = set()
        for port in all_ports:
            if not rx.VERILOG_PORT_RE.match(port):
                raise ValueError(
                    f"Bridge '{self.id}': port '{port}' is not a valid Verilog identifier."
                )
            if port in seen:
                raise ValueError(
                    f"Bridge '{self.id}': port '{port}' duplicated across input/output_ports."
                )
            seen.add(port)
        return self


# ---------------------------------------------------------------------------
# BitbuilderEntry
# ---------------------------------------------------------------------------

class BitbuilderEntry(BaseModel):
    """Catalog entry for a bitbuilder (silicon-specific build recipe).

    Multiple platforms may reference the same bitbuilder; per-platform
    differences are carried in `platforms[].bitbuilder_params` validated
    against `params_schema`.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str

    python_class: str
    """Class name in fslab.bitstream.bitbuilder. The factory resolves this
    string to a concrete class at build time."""

    args_schema: str
    """Pydantic class name (under fslab.schemas.bitbuilder_args) validating
    target.build.bitbuilder_args. Resolved via BITBUILDER_ARGS_REGISTRY."""

    params_schema: str
    """Pydantic class name validating platforms[].bitbuilder_params.
    Resolved via BITBUILDER_PARAMS_REGISTRY."""

    build_script_basename: str = Field(
        ..., min_length=1,
        description="Build script the bitbuilder uploads + runs on the build host.",
    )
    build_script_flags: list[str] = Field(
        default_factory=list,
        description=(
            "Long-form flags passed to build_script_basename (must start with '--'). "
            "Validated for set-equality, not order."
        ),
    )
    template_cl_name: str = Field(
        "",
        description="Name of the in-tree HDK template directory copied per build.",
    )
    remote_cl_parent_subdir: str = Field(
        "",
        description=(
            "Subpath under the platform's remote_platform_path where "
            "cl_<quintuplet> lives."
        ),
    )
    artifact_glob: str = Field(
        "",
        description=(
            "Glob (relative to local_results_subdir/cl_<q>/) matching the "
            "file(s) the publisher consumes."
        ),
    )

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v: str) -> str:
        """[BB-01]"""
        return _validate_alpha_num(v, "id", "Bitbuilder")

    @field_validator("python_class", "args_schema", "params_schema", mode="before")
    @classmethod
    def validate_class_name(cls, v: str, info) -> str:
        """[BB-02] Class names must be CamelCase identifiers."""
        if not rx.PY_CLASS_NAME_RE.match(v):
            raise ValueError(
                f"[BB-02] {info.field_name} '{v}' is invalid. "
                + regex_msg(rx.PY_CLASS_NAME_RE)
            )
        return v

    @field_validator("build_script_flags", mode="before")
    @classmethod
    def validate_flags(cls, v: list[str]) -> list[str]:
        """[BB-04]"""
        for f in v:
            if not f or not f.startswith("--"):
                raise ValueError(
                    f"[BB-04] build_script_flags entry '{f}' must start with '--'."
                )
        return v

    @field_validator("remote_cl_parent_subdir", mode="after")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        """[BB-07] Path concatenation downstream assumes no trailing slash."""
        return v.rstrip("/") if v else v


# ---------------------------------------------------------------------------
# RunnerEntry
# ---------------------------------------------------------------------------

class RunnerEntry(BaseModel):
    """Catalog entry for a runner (silicon-specific FPGA-run recipe).

    Parallel to `BitbuilderEntry`. Multiple platforms may reference the
    same runner; per-recipe parameter dicts are validated via the
    `params_schema` field (currently always empty for F2).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str

    python_class: str
    """Class name in fslab.runtime.runner (to be added in Phase 3). The
    factory resolves this string to a concrete class at run time."""

    args_schema: str
    """Pydantic class name (under fslab.schemas.runner_args) validating
    target.run.runner_args. Resolved via RUNNER_ARGS_REGISTRY."""

    params_schema: str
    """Pydantic class name validating per-runner static params. Resolved
    via RUNNER_PARAMS_REGISTRY. Reserved for future runner variants;
    today's F2RunnerParams is empty."""

    remote_slot_parent_subdir: str = Field(
        "",
        description=(
            "Subpath under the platform's remote_platform_path where the "
            "per-slot run dir lives. Empty means 'at the root of "
            "remote_platform_path'."
        ),
    )

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v: str) -> str:
        """[RUN-01]"""
        return _validate_alpha_num(v, "id", "Runner")

    @field_validator("python_class", "args_schema", "params_schema", mode="before")
    @classmethod
    def validate_class_name(cls, v: str, info) -> str:
        """[RUN-02] Class names must be CamelCase identifiers."""
        if not rx.PY_CLASS_NAME_RE.match(v):
            raise ValueError(
                f"[RUN-02] {info.field_name} '{v}' is invalid. "
                + regex_msg(rx.PY_CLASS_NAME_RE)
            )
        return v

    @field_validator("remote_slot_parent_subdir", mode="after")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        """Path concatenation downstream assumes no trailing slash."""
        return v.rstrip("/") if v else v


# ---------------------------------------------------------------------------
# PlatformEntry  (MODIFIED — replaces the old `remote_build` block with the
# new build-pipeline shape; cmake fields preserved verbatim)
# ---------------------------------------------------------------------------

class PlatformEntry(BaseModel):
    """
    Describes a target FPGA platform.

    Driver-side cmake fields configure how the host driver is compiled
    against the platform's SDK (unchanged from the previous schema).

    Build-pipeline fields select a bitbuilder + per-platform params, plus
    per-host-model and per-publish-type default dicts that are merged with
    the user's fslab.yaml at parse time.

    cmake path fields use CMake-style references ($ENV{VAR} / ${VAR} / /).
    See MetaSimEntry / FpgaSimEntry for the Makefile-style equivalents.
    """

    model_config = ConfigDict(extra="forbid")

    # --- Identity / config [REG-04] ----------------------------
    id: str
    label: str
    config_package: str
    config_class: str

    # --- cmake build fields ------------------------------------
    rpath_origin: bool = False
    required_env_vars: list[str] = Field(default_factory=list)
    extra_cxx_flags: list[str] = Field(default_factory=list)
    extra_include_dirs: list[str] = Field(default_factory=list)
    extra_link_dirs: list[str] = Field(default_factory=list)
    extra_libs: list[str] = Field(default_factory=list)
    extra_link_options: list[str] = Field(default_factory=list)
    board_dir: str = ""
    fpga_delivery_exts: list[str] = Field(default_factory=list)
    stamp_hook: str = ""
    cmake_fragment: str = ""

    # --- build pipeline ----------------------------------------
    bitbuilder: Optional[str] = Field(
        None,
        description=(
            "Id of an entry in the top-level bitbuilders catalog. "
            "None => this platform supports driver/metasim only; "
            "fslab build fpga raises [BB-05]."
        ),
    )
    bitbuilder_params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Per-platform parameters consumed by the bitbuilder's "
            "params_schema. Validated cross-field at MasterRegistry level "
            "[BB-12]."
        ),
    )

    local_platform_path: Optional[str] = Field(
        None,
        description=(
            "Local path to the platform HDK. Resolved with ${PLATFORMS_ROOT} "
            "substitution at BuildConfig construction."
        ),
    )
    local_build_script: Optional[str] = Field(
        None,
        description="Local path to build-bitstream.sh.",
    )
    local_project_staging_subdir: Optional[str] = Field(
        None,
        description=(
            "Project-relative path containing the literal placeholder "
            "'{quintuplet}'. The cmake stage outputs each per-build into "
            "this directory."
        ),
    )
    local_results_subdir: Optional[str] = Field(
        None,
        description="Project-relative directory where reverse-rsynced build artifacts land.",
    )

    host_models: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description=(
            "Keys are host_model ids the platform supports; values are "
            "per-host-model default dicts merged into the user's "
            "target.build.host AND target.run.host blocks at parse time. "
            "Same set governs both pipelines — a platform that supports "
            "ec2_launch for builds also supports it for runs."
        ),
    )
    publish: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description=(
            "Keys are publish.type ids the platform supports; values are "
            "per-type default dicts merged into the user's "
            "target.build.publish block at parse time."
        ),
    )

    # --- run pipeline ------------------------------------------
    runner: Optional[str] = Field(
        None,
        description=(
            "Id of an entry in the top-level runners catalog. "
            "None => this platform does not support `fslab sim fpga`; "
            "the user gets a clear error if they try."
        ),
    )
    run_artifact_sources: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description=(
            "Keys are artifact_source.type ids the platform supports for "
            "`target.run.artifact_source` (currently `aws_afi`). Values are "
            "per-type default dicts merged into the user's "
            "target.run.artifact_source block at parse time."
        ),
    )

    # ----------------------------------------------------------------------
    # Field validators
    # ----------------------------------------------------------------------

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v: str) -> str:
        return _validate_alpha_num(v, "id", "Platform")

    @field_validator("required_env_vars", mode="before")
    @classmethod
    def validate_env_var_names(cls, v: list[str]) -> list[str]:
        """[REG-09] POSIX env var names only."""
        for name in v:
            if not rx.ENV_VAR_RE.match(name):
                raise ValueError(
                    f"[REG-09] required_env_vars entry '{name}' is not a valid "
                    f"POSIX env var name. {regex_msg(rx.ENV_VAR_RE)}"
                    f"Did you mean '{name.upper().replace('-', '_')}'?"
                )
        return v

    @field_validator("extra_libs", mode="before")
    @classmethod
    def validate_lib_names(cls, v: list[str]) -> list[str]:
        """[REG-10] No -l prefix."""
        return _validate_lib_names(v, "extra_libs", "REG-10")

    @field_validator("extra_include_dirs", "extra_link_dirs", mode="before")
    @classmethod
    def validate_cmake_paths(cls, v: list[str], info) -> list[str]:
        """[REG-11] CMake-style path references."""
        return _validate_paths(
            v, info.field_name, "REG-11", rx.CMAKE_PATH_RE,
            "Each entry must start with '/' (absolute), '${' (cmake var), "
            "or '$ENV{' (cmake env var)."
        )

    @field_validator("extra_cxx_flags", "extra_link_options", mode="before")
    @classmethod
    def validate_flags(cls, v: list[str], info) -> list[str]:
        """[REG-12]"""
        return _validate_starts_with_dash(v, info.field_name, "REG-12")

    @field_validator("cmake_fragment", mode="before")
    @classmethod
    def validate_cmake_fragment(cls, v: str) -> str:
        """[REG-13]"""
        return _validate_no_jinja2(v, "cmake_fragment", "REG-13")

    # --- new build-pipeline validators -------------------------------------

    @field_validator("bitbuilder", mode="before")
    @classmethod
    def _validate_bitbuilder_id(cls, v: Optional[str]) -> Optional[str]:
        """[BB-01] (cross-existence enforced at MasterRegistry level)"""
        if v is None:
            return v
        return _validate_alpha_num(v, "bitbuilder", "Platform")

    @field_validator("local_platform_path", "local_build_script", mode="after")
    @classmethod
    def _validate_local_cmake_path(cls, v: Optional[str], info) -> Optional[str]:
        """[BB-07] CMake-style path references."""
        if v is None:
            return v
        if not rx.CMAKE_PATH_RE.match(v):
            raise ValueError(
                f"[BB-07] {info.field_name} '{v}' is not a recognised path "
                "reference. Each entry must start with '/' (absolute), "
                "'${' (cmake var), or '$ENV{' (cmake env var)."
            )
        return v

    @field_validator("local_project_staging_subdir", mode="after")
    @classmethod
    def _has_quintuplet_placeholder(cls, v: Optional[str]) -> Optional[str]:
        """[BB-06] Must contain '{quintuplet}' placeholder when set."""
        if v is None:
            return v
        if "{quintuplet}" not in v:
            raise ValueError(
                f"[BB-06] local_project_staging_subdir must contain the "
                f"literal placeholder '{{quintuplet}}', got: {v!r}"
            )
        return v

    @field_validator("host_models", mode="after")
    @classmethod
    def _validate_host_model_keys(
        cls, v: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """[BB-08] Every key must be a registered host model type."""
        unknown = set(v.keys()) - KNOWN_HOST_MODELS
        if unknown:
            raise ValueError(
                f"[BB-08] host_models keys {sorted(unknown)} are not registered. "
                f"Known: {sorted(KNOWN_HOST_MODELS)}."
            )
        return v

    @field_validator("publish", mode="after")
    @classmethod
    def _validate_publish_keys(
        cls, v: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """[BB-09] Every key must be a registered publish type."""
        unknown = set(v.keys()) - KNOWN_PUBLISH_TYPES
        if unknown:
            raise ValueError(
                f"[BB-09] publish keys {sorted(unknown)} are not registered. "
                f"Known: {sorted(KNOWN_PUBLISH_TYPES)}."
            )
        return v

    @field_validator("runner", mode="before")
    @classmethod
    def _validate_runner_id(cls, v: Optional[str]) -> Optional[str]:
        """[REG-01] (cross-existence enforced at MasterRegistry level via RUN-10)"""
        if v is None:
            return v
        return _validate_alpha_num(v, "runner", "Platform")

    @field_validator("run_artifact_sources", mode="after")
    @classmethod
    def _validate_run_artifact_source_keys(
        cls, v: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """[RUN-04] Every key must be a registered artifact_source type."""
        unknown = set(v.keys()) - KNOWN_ARTIFACT_SOURCE_TYPES
        if unknown:
            raise ValueError(
                f"[RUN-04] run_artifact_sources keys {sorted(unknown)} are not "
                f"registered. Known: {sorted(KNOWN_ARTIFACT_SOURCE_TYPES)}."
            )
        return v

    # ----------------------------------------------------------------------
    # Cross-field validators
    # ----------------------------------------------------------------------

    @model_validator(mode="after")
    def validate_env_vars_referenced_in_paths(self) -> "PlatformEntry":
        """[REG-09] $ENV{VAR} refs in cmake paths must be in required_env_vars."""
        all_paths = self.extra_include_dirs + self.extra_link_dirs
        referenced: set[str] = set()
        for path in all_paths:
            for match in re.finditer(r"\$ENV\{([^}]+)\}", path):
                referenced.add(match.group(1))

        undeclared = referenced - set(self.required_env_vars)
        if undeclared:
            raise ValueError(
                f"[REG-09] Platform '{self.id}': env var(s) referenced via $ENV{{}} "
                f"but not listed in required_env_vars: {', '.join(sorted(undeclared))}. "
                "Add them so cmake emits a FATAL_ERROR guard when they are unset."
            )
        return self

    @model_validator(mode="after")
    def _validate_bitbuilder_consistency(self) -> "PlatformEntry":
        """[BB-05] If bitbuilder is set, all four local_* paths must also be set."""
        if self.bitbuilder is None:
            return self

        missing: list[str] = []
        for f in (
            "local_platform_path",
            "local_build_script",
            "local_project_staging_subdir",
            "local_results_subdir",
        ):
            if getattr(self, f) is None:
                missing.append(f)
        if missing:
            raise ValueError(
                f"[BB-05] platform '{self.id}' has bitbuilder='{self.bitbuilder}' "
                f"but is missing required field(s): {missing}. These paths are "
                "needed to stage and run the build pipeline."
            )
        return self


# ---------------------------------------------------------------------------
# FeatureEntry
# ---------------------------------------------------------------------------

class FeatureEntry(BaseModel):
    """[REG-05] id, label, description required."""

    id: str
    label: str
    description: str

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v: str) -> str:
        return _validate_alpha_num(v, "id", "Feature")


# ---------------------------------------------------------------------------
# Simulator entries — MetaSimEntry and FpgaSimEntry
#
# Path fields here use MAKEFILE-style references: $(VAR) or ${VAR} or /.
# These values are written into Makefile.<id>.sim files where Make (not cmake)
# expands them at build-time.  Using cmake's $ENV{VAR} syntax here would write
# a literal '$ENV{XCELIUM_HOME}/include' into the Makefile, which Make does
# not understand.
# ---------------------------------------------------------------------------

class MetaSimEntry(BaseModel):
    """
    Describes a software meta-simulator (Verilator, VCS, Xcelium, …).

    Fields that differ from PlatformEntry
    --------------------------------------
    tool_cxxopts     Equivalent of VERILATOR_CXXOPTS / VCS_CXXOPTS / XCELIUM_CXXOPTS.
    rtlsim_define    Adds -D RTLSIM to CXXFLAGS. True for all SW metasim tools.
    cmake_targets    Explicit list of cmake targets to expose for this tool.
                     Each maps one cmake custom target to one make target in
                     the generated Makefile.<id>.sim.
    makefile_fragment Verbatim Makefile content appended after the common
                     variable block in Makefile.<id>.sim.  Use this for
                     Makefrag include directives and tool-specific target aliases
                     (e.g. "include $(midas_dir)/rtlsim/Makefrag-verilator").

    Path syntax (Makefile-style)
    ----------------------------
    extra_include_dirs and extra_link_dirs use $(VAR) / ${VAR} / / prefixes.
    Do NOT use cmake's $ENV{VAR} here — it has no meaning in Makefile context.
    required_env_vars still stores bare uppercase names; the cross-field check
    (REG-09x) looks for $(VAR) patterns in path fields.
    """

    # Identity
    id: str
    label: str

    # Tool configuration
    tool_cxxopts: list[str] = Field(default_factory=lambda: ["-O2"])
    rtlsim_define: bool = True

    # Shared build configuration (same semantics as PlatformEntry, different path syntax)
    rpath_origin: bool = True
    required_env_vars: list[str] = Field(default_factory=list)
    extra_cxx_flags: list[str] = Field(default_factory=list)
    extra_include_dirs: list[str] = Field(default_factory=list)
    extra_link_dirs: list[str] = Field(default_factory=list)
    extra_libs: list[str] = Field(default_factory=list)
    extra_link_options: list[str] = Field(default_factory=list)

    # cmake targets to expose for this tool
    cmake_targets: list[SimTarget] = Field(default_factory=list)

    # Escape hatches
    cmake_fragment: str = ""
    makefile_fragment: str = ""

    # ------------------------------------------------------------------ #
    # Field validators                                                     #
    # ------------------------------------------------------------------ #

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v: str) -> str:
        return _validate_alpha_num(v, "id", "MetaSim")

    @field_validator("required_env_vars", mode="before")
    @classmethod
    def validate_env_var_names(cls, v: list[str]) -> list[str]:
        """[REG-09m] POSIX env var names only."""
        for name in v:
            if not rx.ENV_VAR_RE.match(name):
                raise ValueError(
                    f"[REG-09m] required_env_vars entry '{name}' is not a valid "
                    "POSIX env var name. " +
                    regex_msg(rx.ENV_VAR_RE)
                )
        return v

    @field_validator("extra_libs", mode="before")
    @classmethod
    def validate_lib_names(cls, v: list[str]) -> list[str]:
        """[REG-10m] No -l prefix."""
        return _validate_lib_names(v, "extra_libs", "REG-10m")

    @field_validator("extra_include_dirs", "extra_link_dirs", mode="before")
    @classmethod
    def validate_makefile_paths(cls, v: list[str], info) -> list[str]:
        """[REG-11m] Makefile-style path refs: $(VAR), ${VAR}, or /."""
        return _validate_paths(
            v, info.field_name, "REG-11m", rx.MAKEFILE_PATH_RE,
            "Each entry must start with '/' (absolute), '$(' (Make variable), "
            "or '${' (Make variable brace form). "
            "Do not use cmake's '$ENV{VAR}' syntax here — it is not expanded by Make."
        )

    @field_validator("extra_cxx_flags", "extra_link_options", "tool_cxxopts", mode="before")
    @classmethod
    def validate_flags(cls, v: list[str], info) -> list[str]:
        """[REG-12m] Must start with '-'."""
        return _validate_starts_with_dash(v, info.field_name, "REG-12m")

    @field_validator("cmake_targets", mode="before")
    @classmethod
    def validate_cmake_targets_nonempty(cls, v: list) -> list:
        """[REG-15] cmake_targets must contain at least one entry."""
        if not v:
            raise ValueError(
                "[REG-15] cmake_targets must contain at least one SimTarget entry. "
                "The template uses this list to emit cmake custom targets — "
                "an empty list means no cmake target is ever generated for this tool."
            )
        return v

    @field_validator("cmake_fragment", mode="before")
    @classmethod
    def validate_cmake_fragment(cls, v: str) -> str:
        """[REG-13m] No unresolved Jinja2 markers in cmake_fragment."""
        return _validate_no_jinja2(v, "cmake_fragment", "REG-13m")

    @field_validator("makefile_fragment", mode="before")
    @classmethod
    def validate_makefile_fragment(cls, v: str) -> str:
        """[REG-14] No unresolved Jinja2 markers in makefile_fragment."""
        return _validate_no_jinja2(v, "makefile_fragment", "REG-14")

    # ------------------------------------------------------------------ #
    # Cross-field validation                                               #
    # ------------------------------------------------------------------ #

    @model_validator(mode="after")
    def validate_makefile_env_refs_declared(self) -> "MetaSimEntry":
        """
        [REG-09x] Every $(VAR) reference in extra_include_dirs / extra_link_dirs
        should have a corresponding entry in required_env_vars.

        Rationale: Make expands $(VAR) from the environment at build-time.
        If VAR is not set, Make silently substitutes an empty string, producing
        paths like '/include' or '' — the build may configure cleanly but fail
        at compile time with 'file not found'. Declaring env vars explicitly
        causes cmake to emit a FATAL_ERROR guard that surfaces the problem at
        configure-time with a clear message.
        """
        all_paths = self.extra_include_dirs + self.extra_link_dirs
        referenced: set[str] = set()
        for path in all_paths:
            for match in rx.MAKEFILE_VAR_RE.finditer(path):
                referenced.add(match.group(1))

        undeclared = referenced - set(self.required_env_vars)
        if undeclared:
            raise ValueError(
                f"[REG-09x] MetaSim '{self.id}': variable(s) referenced via $() "
                f"in path fields but not listed in required_env_vars: "
                f"{', '.join(sorted(undeclared))}. "
                "Add them so cmake emits a FATAL_ERROR guard when they are unset."
            )
        return self


class FpgaSimEntry(BaseModel):
    """
    Describes an FPGA-level meta-simulator (XSIM, …).

    Key differences from MetaSimEntry
    -----------------------------------
    main             MAIN= argument for the simif_dir sub-make invocation.
                     e.g. "f2_xsim" — selects the FPGA-level simif entry point.
    platform_override PLATFORM= override for the sub-make invocation.
                     e.g. "f2" — the driver is compiled targeting the F2
                     interface even though the broader project platform may differ.
    rtlsim_define    Not present — fpgasim drivers do not set -D RTLSIM.

    Path syntax is Makefile-style, identical to MetaSimEntry.
    """

    # Identity
    id: str
    label: str

    # FPGA sim specific
    main: str = ""
    """
    MAIN= argument forwarded to $(MAKE) -C $(simif_dir) in makefile_fragment.
    e.g. "f2_xsim". Leave empty to use the project PLATFORM value.
    """

    platform_override: str = ""
    """
    PLATFORM= argument forwarded to the sub-make. e.g. "f2" for XSIM.
    Leave empty to inherit the project PLATFORM.
    """

    # Build configuration (Makefile-style paths)
    rpath_origin: bool = False
    required_env_vars: list[str] = Field(default_factory=list)
    extra_cxx_flags: list[str] = Field(default_factory=list)
    extra_include_dirs: list[str] = Field(default_factory=list)
    extra_link_dirs: list[str] = Field(default_factory=list)
    extra_libs: list[str] = Field(default_factory=list)
    extra_link_options: list[str] = Field(default_factory=list)

    cmake_targets: list[SimTarget] = Field(default_factory=list)
    cmake_fragment: str = ""
    makefile_fragment: str = ""

    # ------------------------------------------------------------------ #
    # Field validators  (identical rules to MetaSimEntry)                  #
    # ------------------------------------------------------------------ #

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v: str) -> str:
        return _validate_alpha_num(v, "id", "FpgaSim")

    @field_validator("required_env_vars", mode="before")
    @classmethod
    def validate_env_var_names(cls, v: list[str]) -> list[str]:
        """[REG-09m] POSIX env var names only."""
        for name in v:
            if not rx.ENV_VAR_RE.match(name):
                raise ValueError(
                    f"[REG-09m] required_env_vars entry '{name}' is not a valid "
                    "POSIX env var name."
                )
        return v

    @field_validator("extra_libs", mode="before")
    @classmethod
    def validate_lib_names(cls, v: list[str]) -> list[str]:
        """[REG-10m] No -l prefix."""
        return _validate_lib_names(v, "extra_libs", "REG-10m")

    @field_validator("extra_include_dirs", "extra_link_dirs", mode="before")
    @classmethod
    def validate_makefile_paths(cls, v: list[str], info) -> list[str]:
        """[REG-11m] Makefile-style path refs."""
        return _validate_paths(
            v, info.field_name, "REG-11m", rx.MAKEFILE_PATH_RE,
            "Each entry must start with '/' (absolute), '$(' or '${'."
        )

    @field_validator("extra_cxx_flags", "extra_link_options", mode="before")
    @classmethod
    def validate_flags(cls, v: list[str], info) -> list[str]:
        """[REG-12m] Must start with '-'."""
        return _validate_starts_with_dash(v, info.field_name, "REG-12m")

    @field_validator("cmake_targets", mode="before")
    @classmethod
    def validate_cmake_targets_nonempty(cls, v: list) -> list:
        """[REG-15] At least one SimTarget required."""
        if not v:
            raise ValueError(
                "[REG-15] cmake_targets must contain at least one SimTarget entry."
            )
        return v

    @field_validator("cmake_fragment", mode="before")
    @classmethod
    def validate_cmake_fragment(cls, v: str) -> str:
        """[REG-13m]"""
        return _validate_no_jinja2(v, "cmake_fragment", "REG-13m")

    @field_validator("makefile_fragment", mode="before")
    @classmethod
    def validate_makefile_fragment(cls, v: str) -> str:
        """[REG-14]"""
        return _validate_no_jinja2(v, "makefile_fragment", "REG-14")

    @model_validator(mode="after")
    def validate_makefile_env_refs_declared(self) -> "FpgaSimEntry":
        """[REG-09x] $(VAR) refs in paths must be in required_env_vars."""
        all_paths = self.extra_include_dirs + self.extra_link_dirs
        referenced: set[str] = set()
        for path in all_paths:
            for match in rx.MAKEFILE_VAR_RE.finditer(path):
                referenced.add(match.group(1))

        undeclared = referenced - set(self.required_env_vars)
        if undeclared:
            raise ValueError(
                f"[REG-09x] FpgaSim '{self.id}': variable(s) referenced via $() "
                f"in path fields but not in required_env_vars: "
                f"{', '.join(sorted(undeclared))}."
            )
        return self


# ---------------------------------------------------------------------------
# Single-file registry
# ---------------------------------------------------------------------------

class RegistryFile(BaseModel):
    """Single fully-parsed registry.yaml file.

    [REG-06] IDs must be unique per category within a single file.
    """

    bridges: list[BridgeEntry] = Field(default_factory=list)
    bitbuilders: list[BitbuilderEntry] = Field(default_factory=list)
    runners: list[RunnerEntry] = Field(default_factory=list)
    platforms: list[PlatformEntry] = Field(default_factory=list)
    features: list[FeatureEntry] = Field(default_factory=list)
    metasimulators: list[MetaSimEntry] = Field(default_factory=list)
    fpgasimulators: list[FpgaSimEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def enforce_intra_file_uniqueness(self) -> "RegistryFile":
        """[REG-06] IDs must be unique within each category in a single file."""
        self._check_unique([b.id for b in self.bridges],        "bridges")
        self._check_unique([b.id for b in self.bitbuilders],    "bitbuilders")
        self._check_unique([r.id for r in self.runners],        "runners")
        self._check_unique([p.id for p in self.platforms],      "platforms")
        self._check_unique([f.id for f in self.features],       "features")
        self._check_unique([m.id for m in self.metasimulators], "metasimulators")
        self._check_unique([f.id for f in self.fpgasimulators], "fpgasimulators")
        return self

    @staticmethod
    def _check_unique(ids: list[str], category: str) -> None:
        seen: set[str] = set()
        for id_ in ids:
            if id_ in seen:
                raise ValueError(
                    f"[REG-06] Duplicate {category} id '{id_}' in same registry file."
                )
            seen.add(id_)


# ---------------------------------------------------------------------------
# Master (merged) registry
# ---------------------------------------------------------------------------

class MasterRegistry(BaseModel):
    """Single merged view of all loaded registry files.

    [REG-07] Last-definition-wins: a later file's entry overwrites an earlier
             one with the same id.
    """

    bridges: dict[str, BridgeEntry] = Field(default_factory=dict)
    bitbuilders: dict[str, BitbuilderEntry] = Field(default_factory=dict)
    runners: dict[str, RunnerEntry] = Field(default_factory=dict)
    platforms: dict[str, PlatformEntry] = Field(default_factory=dict)
    features: dict[str, FeatureEntry] = Field(default_factory=dict)
    metasimulators: dict[str, MetaSimEntry] = Field(default_factory=dict)
    fpgasimulators: dict[str, FpgaSimEntry] = Field(default_factory=dict)

    @classmethod
    def from_registry_files(
        cls, registry_files: list[RegistryFile]
    ) -> "MasterRegistry":
        """[REG-07] Merge registry files in order; last definition wins."""
        master = cls()
        for reg_file in registry_files:
            for entry in reg_file.bridges:
                master.bridges[entry.id] = entry
            for entry in reg_file.bitbuilders:
                master.bitbuilders[entry.id] = entry
            for entry in reg_file.runners:
                master.runners[entry.id] = entry
            for entry in reg_file.platforms:
                master.platforms[entry.id] = entry
            for entry in reg_file.features:
                master.features[entry.id] = entry
            for entry in reg_file.metasimulators:
                master.metasimulators[entry.id] = entry
            for entry in reg_file.fpgasimulators:
                master.fpgasimulators[entry.id] = entry

        master._cross_validate_bitbuilders()
        master._cross_validate_runners()
        return master

    # ----------------------------------------------------------------------
    # Cross-file validation
    # ----------------------------------------------------------------------

    def _cross_validate_bitbuilders(self) -> None:
        """[BB-10..BB-12] Cross-checks that span platforms + bitbuilders.

        Runs once after merging. Validates:
          * each platform.bitbuilder (when set) references a known bitbuilder
          * each bitbuilder.args_schema / params_schema resolve via the
            python-side registries
          * each platform.bitbuilder_params validates against the resolved
            params class
        """
        # [BB-11] Schemas resolvable
        for bb_id, bb in self.bitbuilders.items():
            if bb.args_schema not in BITBUILDER_ARGS_REGISTRY:
                raise ValueError(
                    f"[BB-11] bitbuilder '{bb_id}' references unknown "
                    f"args_schema '{bb.args_schema}'. Known: "
                    f"{sorted(BITBUILDER_ARGS_REGISTRY)}."
                )
            if bb.params_schema not in BITBUILDER_PARAMS_REGISTRY:
                raise ValueError(
                    f"[BB-11] bitbuilder '{bb_id}' references unknown "
                    f"params_schema '{bb.params_schema}'. Known: "
                    f"{sorted(BITBUILDER_PARAMS_REGISTRY)}."
                )

        # [BB-10] / [BB-12] Per-platform checks
        for p_id, p in self.platforms.items():
            if p.bitbuilder is None:
                continue

            bb = self.bitbuilders.get(p.bitbuilder)
            if bb is None:
                raise ValueError(
                    f"[BB-10] platform '{p_id}' references unknown "
                    f"bitbuilder '{p.bitbuilder}'. Known: "
                    f"{sorted(self.bitbuilders)}."
                )

            params_cls = resolve_params_schema(bb.params_schema)
            try:
                params_cls.model_validate(p.bitbuilder_params)
            except Exception as e:
                raise ValueError(
                    f"[BB-12] platform '{p_id}' bitbuilder_params do not "
                    f"validate against {bb.params_schema}: {e}"
                ) from e

    def _cross_validate_runners(self) -> None:
        """[RUN-10..RUN-11] Cross-checks that span platforms + runners.

        Runs once after merging. Validates:
          * each platform.runner (when set) references a known runner entry
          * each runner.args_schema / params_schema resolve via the
            python-side registries
        """
        # [RUN-11] Schemas resolvable
        for r_id, r in self.runners.items():
            if r.args_schema not in RUNNER_ARGS_REGISTRY:
                raise ValueError(
                    f"[RUN-11] runner '{r_id}' references unknown "
                    f"args_schema '{r.args_schema}'. Known: "
                    f"{sorted(RUNNER_ARGS_REGISTRY)}."
                )
            if r.params_schema not in RUNNER_PARAMS_REGISTRY:
                raise ValueError(
                    f"[RUN-11] runner '{r_id}' references unknown "
                    f"params_schema '{r.params_schema}'. Known: "
                    f"{sorted(RUNNER_PARAMS_REGISTRY)}."
                )

        # [RUN-10] Per-platform check
        for p_id, p in self.platforms.items():
            if p.runner is None:
                continue
            if p.runner not in self.runners:
                raise ValueError(
                    f"[RUN-10] platform '{p_id}' references unknown "
                    f"runner '{p.runner}'. Known: {sorted(self.runners)}."
                )
