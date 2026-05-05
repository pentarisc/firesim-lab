"""
fslab/schemas/registry.py
=========================
Pydantic V2 models for parsing and validating one or more `registry.yaml`
files into a merged `MasterRegistry`.

Validation requirements
-----------------------
  REG-01  ID format regex
  REG-02  All required bridge fields present
  REG-03  Optional bridge fields (runtime_plusargs,
          scala_templates.top_imports)
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

  Metasim / fpgasim fields (new)
  REG-09m required_env_vars same POSIX name rule (reused for MetaSimEntry/FpgaSimEntry)
  REG-10m extra_libs same no-prefix rule
  REG-11m extra_include_dirs / extra_link_dirs: Makefile-style paths ($(VAR) / / prefix)
          NOTE: different from REG-11 which uses cmake $ENV{} syntax. Metasim/fpgasim
          paths are written into Makefile.sim where $(VAR) is expanded by Make, not cmake.
  REG-12m extra_cxx_flags / extra_link_options / tool_cxxopts must start with '-'
  REG-13m cmake_fragment must not contain Jinja2 markers (same as REG-13)
  REG-14  makefile_fragment must not contain Jinja2 markers
  REG-15  cmake_targets must be non-empty; each name must match ID regex
  REG-09x Cross-field: $(VAR) refs in Makefile-style paths must be in required_env_vars
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator
import fslab.utils.regexes as rx
from fslab.utils.display import regex_msg

_ORIGINS = {"firesim", "fslab", "custom"}

# ---------------------------------------------------------------------------
# Helper
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
# Top-level registry entries — Bridge, Platform, Feature  (unchanged)
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


class PlatformEntry(BaseModel):
    """
    Describes a target FPGA platform and the cmake build configuration
    needed to compile the host driver against its SDK.

    cmake path fields use CMake-style references ($ENV{VAR} / ${VAR} / /).
    See MetaSimEntry / FpgaSimEntry for the Makefile-style equivalents.
    """

    # Identity / config [REG-04]
    id: str
    label: str
    config_package: str
    config_class: str

    # cmake build fields
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
        """[REG-12] Must start with '-'."""
        return _validate_starts_with_dash(v, info.field_name, "REG-12")

    @field_validator("cmake_fragment", mode="before")
    @classmethod
    def validate_cmake_fragment(cls, v: str) -> str:
        """[REG-13] No unresolved Jinja2 markers."""
        return _validate_no_jinja2(v, "cmake_fragment", "REG-13")

    @model_validator(mode="after")
    def validate_env_vars_referenced_in_paths(self) -> "PlatformEntry":
        """
        [REG-09] Cross-field: every $ENV{VAR} in cmake path fields must have
        a matching entry in required_env_vars.
        """
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
# Single-file registry model
# ---------------------------------------------------------------------------

class RegistryFile(BaseModel):
    """
    Represents a single fully-parsed registry.yaml file.

    [REG-06] IDs must be unique per category within a single file.
    """

    bridges: list[BridgeEntry] = Field(default_factory=list)
    platforms: list[PlatformEntry] = Field(default_factory=list)
    features: list[FeatureEntry] = Field(default_factory=list)
    metasimulators: list[MetaSimEntry] = Field(default_factory=list)
    fpgasimulators: list[FpgaSimEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def enforce_intra_file_uniqueness(self) -> "RegistryFile":
        """[REG-06] IDs must be unique within each category in a single file."""
        self._check_unique([b.id for b in self.bridges],        "bridges")
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
    """
    Single merged view of all loaded registry files.

    [REG-07] Last-definition-wins: a later file's entry overwrites an
             earlier one with the same id.
    """

    bridges: dict[str, BridgeEntry] = Field(default_factory=dict)
    platforms: dict[str, PlatformEntry] = Field(default_factory=dict)
    features: dict[str, FeatureEntry] = Field(default_factory=dict)
    metasimulators: dict[str, MetaSimEntry] = Field(default_factory=dict)
    fpgasimulators: dict[str, FpgaSimEntry] = Field(default_factory=dict)

    @classmethod
    def from_registry_files(cls, registry_files: list[RegistryFile]) -> "MasterRegistry":
        """[REG-07] Merge registry files in order; last definition wins."""
        master = cls()
        for reg_file in registry_files:
            for entry in reg_file.bridges:
                master.bridges[entry.id] = entry
            for entry in reg_file.platforms:
                master.platforms[entry.id] = entry
            for entry in reg_file.features:
                master.features[entry.id] = entry
            for entry in reg_file.metasimulators:
                master.metasimulators[entry.id] = entry
            for entry in reg_file.fpgasimulators:
                master.fpgasimulators[entry.id] = entry
        return master