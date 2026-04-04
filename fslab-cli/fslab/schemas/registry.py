"""
fslab/schemas/registry.py
=========================
Pydantic V2 models for parsing and validating one or more `registry.yaml`
files into a merged `MasterRegistry`.

Validation requirements satisfied here
---------------------------------------
  REG-01  ID format regex
  REG-02  All required bridge fields must be present
  REG-03  Optional bridge fields (runtime_plusargs, module_macro_prefix,
          scala_templates.top_imports)
  REG-04  Platform required fields
  REG-05  Feature required fields
  REG-06  ID uniqueness *within a single* RegistryFile
  REG-07  Merge / last-definition-wins across multiple RegistryFiles
  REG-08  Port name uniqueness and Verilog port-name pattern

  --- Platform cmake build field validations (new) ---
  REG-09  required_env_vars entries must be valid POSIX environment variable
          names (^[A-Z_][A-Z0-9_]*$). Catches typos before cmake fails silently
          with an always-empty $ENV{} lookup.
  REG-10  extra_libs entries must NOT carry a "-l" prefix and must contain only
          characters valid in a library name. Catches the most common mistake
          when translating LDFLAGS (-lfoo → foo).
  REG-11  extra_include_dirs and extra_link_dirs entries must be either an
          absolute path (/…), a CMake cache-variable reference (${…}), or a
          CMake environment-variable reference ($ENV{…}). Relative paths are
          rejected because they are resolved relative to the cmake build dir,
          which is almost never what is intended for SDK/system paths.
  REG-12  extra_cxx_flags and extra_link_options entries must be non-empty and
          must start with "-". Catches entries where the leading dash was
          accidentally omitted (e.g. "O2" instead of "-O2").
  REG-13  cmake_fragment must not contain Jinja2 expression markers ({{ or }}).
          Catches accidental template variable leakage where a Jinja2 context
          variable was written into the fragment instead of being resolved
          before emission.
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

# [REG-01] IDs and C++ class names: alphanumerics, underscores, hyphens.
_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# [REG-08] Port names must be valid Verilog identifiers.
_VERILOG_PORT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_$]*$")

# [REG-09] POSIX environment variable names: uppercase letters, digits,
# underscore; must not start with a digit.
_ENV_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")

# [REG-10] Library names: alphanumerics, hyphens, underscores, dots.
# Dots appear in versioned soname stems (e.g. "xrt_coreutil", "stdc++.6").
# Note: stdc++ contains '+' so we allow that too.
_LIB_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.\+]+$")

# [REG-11] Valid path reference prefixes accepted in extra_include_dirs /
# extra_link_dirs:
#   /...          absolute path
#   ${VAR}/...    CMake cache variable reference
#   $ENV{VAR}/... CMake environment variable reference
_PATH_REF_RE = re.compile(r"^(/|\$\{|\$ENV\{)")

# [REG-13] Jinja2 expression/statement markers that must not appear verbatim
# inside cmake_fragment (would mean an unresolved template variable leaked in).
_JINJA2_EXPR_RE = re.compile(r"\{\{|\}\}|\{%-?|\{#")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _validate_alpha_num(value: str, key: str, entity: str) -> str:
    """[REG-01] Validate that a value matches the allowed character set."""
    if not _ID_RE.match(value):
        raise ValueError(
            f"[REG-01] {entity} {key} '{value}' is invalid. "
            r"Must match ^[a-zA-Z0-9_-]+$"
        )
    return value


# ---------------------------------------------------------------------------
# Bridge sub-models
# ---------------------------------------------------------------------------

class ScalaTemplates(BaseModel):
    """
    Paths to the Scala/Chisel Jinja2 templates for a bridge.

    [REG-03] top_imports is optional; all other template paths are required.
    """

    dut_imports: Optional[str] = None
    top_imports: Optional[str] = None  # [REG-03] explicitly optional
    ports: str
    wiring: str


class RuntimePlusarg(BaseModel):
    """A single runtime simulation plusarg exposed by a bridge."""

    flag: str
    description: str
    required_params: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Top-level registry entries
# ---------------------------------------------------------------------------

class BridgeEntry(BaseModel):
    """
    Describes a single hardware bridge available in the registry.

    [REG-02] All fields required except those listed in [REG-03].
    [REG-03] runtime_plusargs, module_macro_prefix, and
             scala_templates.top_imports are optional.
    """

    # --- Required fields (REG-02) ---
    id: str
    label: str
    description: str
    input_ports: list[str]
    output_ports: list[str]
    cpp_type: str
    cpp_headers: list[str]
    cpp_sources: list[str]
    cpp_template: str
    scala_templates: ScalaTemplates

    # --- Optional fields (REG-03) ---
    module_macro_prefix: Optional[str] = None
    runtime_plusargs: Optional[list[RuntimePlusarg]] = None

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v: str) -> str:
        """[REG-01] Enforce bridge ID format."""
        return _validate_alpha_num(v, "id", "Bridge")

    @field_validator("cpp_type", mode="before")
    @classmethod
    def validate_cpp_type(cls, v: str) -> str:
        """[REG-01] Enforce bridge cpp_type format."""
        return _validate_alpha_num(v, "cpp_type", "Bridge")

    @model_validator(mode="after")
    def validate_ports(self) -> "BridgeEntry":
        """
        [REG-08] Port names must:
          • Match the Verilog identifier pattern.
          • Be unique across input_ports *and* output_ports combined.
        """
        all_ports = list(self.input_ports) + list(self.output_ports)
        seen: set[str] = set()

        for port in all_ports:
            if not _VERILOG_PORT_RE.match(port):
                raise ValueError(
                    f"Bridge '{self.id}': port name '{port}' is not a valid "
                    r"Verilog identifier. Must match ^[a-zA-Z_][a-zA-Z0-9_$]*$"
                )
            if port in seen:
                raise ValueError(
                    f"Bridge '{self.id}': port name '{port}' is duplicated "
                    "across input_ports and/or output_ports."
                )
            seen.add(port)

        return self


class PlatformEntry(BaseModel):
    """
    Describes a target FPGA platform and the cmake build configuration
    needed to compile the host driver against its SDK.

    Required identity fields (REG-04)
    ----------------------------------
    id, label, config_package, config_class

    cmake build fields (validated by REG-09 … REG-13)
    ---------------------------------------------------
    These are emitted verbatim into the rendered CMakeLists.txt by the
    Jinja2 template.  All path strings support CMake variable references
    (${VAR}) and CMake env references ($ENV{VAR}); the template never
    resolves them — CMake does at configure-time (Decision 3a).

    The cmake_fragment escape hatch allows arbitrary CMake to be injected
    for constructs the structured fields cannot express (find_package, etc.).
    """

    # --- Identity / config (REG-04) ---
    id: str
    label: str
    config_package: str
    config_class: str

    # --- cmake build fields ---
    rpath_origin: bool = False

    required_env_vars: list[str] = Field(default_factory=list)
    """
    [REG-09] Environment variable names that must be set when cmake runs.
    The template emits a FATAL_ERROR guard for each one using $ENV{VAR}.
    Must be valid POSIX env var names so the $ENV{} lookup is not silently
    empty (e.g. 'XILINX_XRT', not 'xilinx_xrt' or 'XILINX-XRT').
    """

    extra_cxx_flags: list[str] = Field(default_factory=list)
    """
    [REG-12] Platform-specific compile flags, e.g. ["-idirafter /usr/include"].
    Each entry must start with '-'.
    """

    extra_include_dirs: list[str] = Field(default_factory=list)
    """
    [REG-11] Additional include search paths.
    Must be absolute paths (/…), CMake variable refs (${…}),
    or CMake env refs ($ENV{…}).
    """

    extra_link_dirs: list[str] = Field(default_factory=list)
    """
    [REG-11] Additional linker search paths.
    Same path-reference rules as extra_include_dirs.
    """

    extra_libs: list[str] = Field(default_factory=list)
    """
    [REG-10] Library names to link against — names only, NO '-l' prefix.
    e.g. ['fpga_mgmt', 'z'], NOT ['-lfpga_mgmt', '-lz'].
    """

    extra_link_options: list[str] = Field(default_factory=list)
    """
    [REG-12] Raw linker flags, e.g. ['-Wl,-rpath-link,/usr/lib/…'].
    Each entry must start with '-'.
    """

    cmake_fragment: str = ""
    """
    [REG-13] Verbatim CMake injected at the end of section 4b.
    Escape hatch for constructs not expressible via structured fields.
    Must not contain unresolved Jinja2 markers ({{ }}, {% %}, {# #}).
    """

    # ------------------------------------------------------------------ #
    # Field validators  (run before model_validator)                       #
    # ------------------------------------------------------------------ #

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v: str) -> str:
        """[REG-01] Enforce platform ID format."""
        return _validate_alpha_num(v, "id", "Platform")

    @field_validator("required_env_vars", mode="before")
    @classmethod
    def validate_env_var_names(cls, v: list[str]) -> list[str]:
        """
        [REG-09] Each entry must be a valid POSIX environment variable name
        (uppercase letters, digits, underscore; not starting with a digit).

        Why: CMake's $ENV{name} lookup is case-sensitive and silently returns
        an empty string for names that don't match the actual environment —
        a lowercase or hyphenated name will never resolve, causing cryptic
        linker errors rather than a clear cmake FATAL_ERROR.
        """
        for name in v:
            if not _ENV_VAR_RE.match(name):
                raise ValueError(
                    f"[REG-09] required_env_vars entry '{name}' is not a valid "
                    "POSIX environment variable name. "
                    r"Must match ^[A-Z_][A-Z0-9_]*$ "
                    "(uppercase letters, digits, underscore only). "
                    f"Did you mean '{name.upper().replace('-', '_')}'?"
                )
        return v

    @field_validator("extra_libs", mode="before")
    @classmethod
    def validate_lib_names(cls, v: list[str]) -> list[str]:
        """
        [REG-10] Library names must not carry a '-l' prefix and must contain
        only characters valid in a library soname stem.

        Why: target_link_libraries() expects bare names ('fpga_mgmt', not
        '-lfpga_mgmt'). Passing a '-l'-prefixed string compiles but produces
        a different target-property value, breaking downstream consumers and
        IDE integrations. Better to catch it here than at link time.
        """
        for lib in v:
            if lib.startswith("-l"):
                raise ValueError(
                    f"[REG-10] extra_libs entry '{lib}' must not start with "
                    "'-l'. CMake's target_link_libraries() takes bare library "
                    f"names. Use '{lib[2:]}' instead."
                )
            if not lib:
                raise ValueError(
                    "[REG-10] extra_libs contains an empty string entry."
                )
            if not _LIB_NAME_RE.match(lib):
                raise ValueError(
                    f"[REG-10] extra_libs entry '{lib}' contains characters "
                    "not valid in a library name. "
                    r"Allowed: alphanumerics, hyphens, underscores, dots, '+'."
                )
        return v

    @field_validator("extra_include_dirs", "extra_link_dirs", mode="before")
    @classmethod
    def validate_path_refs(cls, v: list[str], info) -> list[str]:
        """
        [REG-11] Path entries must be absolute paths or valid CMake variable /
        environment-variable references.

        Accepted prefixes:
          /          → absolute path        e.g. /usr/include
          ${         → CMake cache var ref  e.g. ${PLATFORMS_DIR}/sdk/include
          $ENV{      → CMake env var ref    e.g. $ENV{XILINX_XRT}/include

        Why: relative paths are resolved relative to the cmake *build* directory,
        not the source directory or SDK root — almost always the wrong behaviour
        for platform SDK headers. Catching this early prevents hard-to-diagnose
        'file not found' errors that only surface during compilation.
        """
        field_name = info.field_name
        for path in v:
            if not path:
                raise ValueError(
                    f"[REG-11] {field_name} contains an empty string entry."
                )
            if not _PATH_REF_RE.match(path):
                raise ValueError(
                    f"[REG-11] {field_name} entry '{path}' is not an absolute "
                    "path or a recognised CMake reference. "
                    "Each entry must start with '/' (absolute path), "
                    "'${' (CMake cache variable), or '$ENV{' (CMake env variable). "
                    "Relative paths are not allowed — they resolve against the "
                    "cmake build directory, not the SDK or source root."
                )
        return v

    @field_validator("extra_cxx_flags", "extra_link_options", mode="before")
    @classmethod
    def validate_flag_entries(cls, v: list[str], info) -> list[str]:
        """
        [REG-12] Every compile flag and linker option must start with '-'.

        Why: a missing leading dash (e.g. 'O2' instead of '-O2', or
        'Wl,-rpath,...' instead of '-Wl,-rpath,...') is silently passed to
        the compiler/linker as a filename, producing a confusing 'no such
        file or directory' error rather than a helpful validation message.
        """
        field_name = info.field_name
        for flag in v:
            if not flag:
                raise ValueError(
                    f"[REG-12] {field_name} contains an empty string entry."
                )
            if not flag.startswith("-"):
                raise ValueError(
                    f"[REG-12] {field_name} entry '{flag}' does not start "
                    "with '-'. All compiler flags and linker options must "
                    "begin with a dash. "
                    f"Did you mean '-{flag}'?"
                )
        return v

    @field_validator("cmake_fragment", mode="before")
    @classmethod
    def validate_cmake_fragment(cls, v: str) -> str:
        """
        [REG-13] cmake_fragment must not contain unresolved Jinja2 markers.

        Jinja2 expression ({{ }}), statement ({%  %}), and comment ({# #})
        delimiters inside the fragment indicate that a template variable was
        accidentally written as literal text instead of being resolved by the
        CLI before emission. This would produce syntactically invalid CMake.

        Why catch here rather than at render time: Jinja2 would raise a
        TemplateSyntaxError during rendering, but only for the specific
        project being generated. Validating in the registry schema surfaces
        the mistake as soon as the registry is loaded, regardless of whether
        the affected platform is currently in use.
        """
        if v and _JINJA2_EXPR_RE.search(v):
            raise ValueError(
                "[REG-13] cmake_fragment contains an unresolved Jinja2 "
                "marker ('{{', '}}', '{%', or '{#'). "
                "The fragment is emitted verbatim into CMakeLists.txt — "
                "Jinja2 syntax inside it will produce invalid CMake. "
                "If you intended to use a Jinja2 context variable, resolve "
                "it in the CLI's _build_template_context() and pass the "
                "result as a plain string in the fragment."
            )
        return v

    # ------------------------------------------------------------------ #
    # Model validator (runs after all field validators pass)               #
    # ------------------------------------------------------------------ #

    @model_validator(mode="after")
    def validate_env_vars_referenced_in_paths(self) -> "PlatformEntry":
        """
        [REG-09 + REG-11] Cross-field consistency check: every $ENV{VAR}
        reference that appears in extra_include_dirs or extra_link_dirs
        should have a corresponding entry in required_env_vars.

        This is a WARNING-level check rather than a hard error because there
        are legitimate cases where an env var is always set by the Docker
        image (e.g. PATH) and does not need an explicit guard.  We raise a
        ValueError so the operator is alerted — they can suppress it by
        either adding the var to required_env_vars or removing the reference.

        Why: a missing required_env_vars entry means cmake silently gets an
        empty string for $ENV{VAR}, producing a path like '/include' instead
        of '/opt/xilinx/xrt/include'. The build may still appear to configure
        correctly but fails at compile time with 'no such file'.
        """
        all_paths = self.extra_include_dirs + self.extra_link_dirs
        referenced: set[str] = set()

        for path in all_paths:
            # Extract VAR from $ENV{VAR} occurrences
            for match in re.finditer(r"\$ENV\{([^}]+)\}", path):
                referenced.add(match.group(1))

        declared = set(self.required_env_vars)
        undeclared = referenced - declared

        if undeclared:
            missing = ", ".join(sorted(undeclared))
            raise ValueError(
                f"[REG-09] Platform '{self.id}': the following environment "
                f"variable(s) are referenced via $ENV{{}} in path fields but "
                f"are not listed in required_env_vars: {missing}. "
                "Add them to required_env_vars so cmake emits a FATAL_ERROR "
                "guard if they are unset, rather than silently resolving to "
                "an empty path."
            )

        return self


class FeatureEntry(BaseModel):
    """
    Describes an optional hardware feature (e.g. verilog-blackbox).

    [REG-05] id, label, and description are all required.
    """

    id: str           # [REG-05]
    label: str        # [REG-05]
    description: str  # [REG-05]

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v: str) -> str:
        """[REG-01] Enforce feature ID format."""
        return _validate_alpha_num(v, "id", "Feature")


# ---------------------------------------------------------------------------
# Single-file registry model
# ---------------------------------------------------------------------------

class RegistryFile(BaseModel):
    """
    Represents a single, fully-parsed `registry.yaml` file.

    [REG-06] Within a single file, IDs must be unique per category
             (bridges, platforms, features).
    """

    bridges: list[BridgeEntry] = Field(default_factory=list)
    platforms: list[PlatformEntry] = Field(default_factory=list)
    features: list[FeatureEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def enforce_intra_file_uniqueness(self) -> "RegistryFile":
        """
        [REG-06] IDs must be unique *within* a single registry file for each
        category independently.
        """
        self._check_unique([b.id for b in self.bridges], "bridges")
        self._check_unique([p.id for p in self.platforms], "platforms")
        self._check_unique([f.id for f in self.features], "features")
        return self

    @staticmethod
    def _check_unique(ids: list[str], category: str) -> None:
        seen: set[str] = set()
        for id_ in ids:
            if id_ in seen:
                raise ValueError(
                    f"[REG-06] Duplicate {category} id '{id_}' found within "
                    "the same registry file."
                )
            seen.add(id_)


# ---------------------------------------------------------------------------
# Master (merged) registry
# ---------------------------------------------------------------------------

class MasterRegistry(BaseModel):
    """
    The single merged view of all loaded registry files.

    Bridges, platforms, and features are stored as dicts keyed by their `id`
    for O(1) look-up during project validation.

    [REG-07] When multiple registry files are loaded, a later file's entry
             completely overwrites an earlier file's entry for the same id.
    """

    bridges: dict[str, BridgeEntry] = Field(default_factory=dict)
    platforms: dict[str, PlatformEntry] = Field(default_factory=dict)
    features: dict[str, FeatureEntry] = Field(default_factory=dict)

    @classmethod
    def from_registry_files(cls, registry_files: list[RegistryFile]) -> "MasterRegistry":
        """
        Merge a list of RegistryFile objects in order.

        [REG-07] Last-definition-wins: each registry's entries overwrite any
                 identically-named entries from earlier registries.
        """
        master = cls()

        for reg_file in registry_files:
            for bridge in reg_file.bridges:
                master.bridges[bridge.id] = bridge        # [REG-07]

            for platform in reg_file.platforms:
                master.platforms[platform.id] = platform  # [REG-07]

            for feature in reg_file.features:
                master.features[feature.id] = feature     # [REG-07]

        return master