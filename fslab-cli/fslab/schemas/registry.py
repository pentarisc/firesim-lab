"""
fslab/schemas/registry.py
=========================
Pydantic V2 models for parsing and validating one or more `registry.yaml`
files into a merged `MasterRegistry`.

Validation requirements satisfied here:
  REG-01  ID format regex
  REG-02  All required bridge fields must be present
  REG-03  Optional bridge fields (runtime_plusargs, module_macro_prefix,
          scala_templates.top_imports)
  REG-04  Platform required fields
  REG-05  Feature required fields
  REG-06  ID uniqueness *within a single* RegistryFile
  REG-07  Merge / last-definition-wins across multiple RegistryFiles
  REG-08  Port name uniqueness and Verilog port-name pattern
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

# [REG-01] IDs and c++ class names must consist solely of alphanumerics, underscores, or hyphens.
_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# [REG-08] Port names must be valid Verilog identifiers
#          (letter/underscore, then letters/digits/underscores/$).
_VERILOG_PORT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_$]*$")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _validate_alpha_num(key: str, value: str, entity: str) -> str:
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

    dut_imports: str
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
    Describes a target FPGA platform.

    [REG-04] id, label, config_class, and config_package are all required.
    """

    id: str           # [REG-04]
    label: str        # [REG-04]
    config_package: str  # [REG-04]
    config_class: str    # [REG-04]

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v: str) -> str:
        """[REG-01] Enforce platform ID format."""
        return _validate_alpha_num(v, "id", "Platform")


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
                master.bridges[bridge.id] = bridge      # [REG-07]

            for platform in reg_file.platforms:
                master.platforms[platform.id] = platform  # [REG-07]

            for feature in reg_file.features:
                master.features[feature.id] = feature    # [REG-07]

        return master