"""
fslab/schemas/project.py
========================
Pydantic V2 models for parsing and validating the user's `fslab.yaml` project
file.  Cross-registry validation (semantic checks) is performed inside a Pydantic
model validator that reads the `MasterRegistry` from the validation *context*
dict supplied by the caller.

Validation requirements satisfied here:
  PROJ-01   project.name format
  PROJ-02   fslab_top / package_name / config_class format
  PROJ-03   design.type allowed values
  PROJ-04   host.emulator allowed values
  PROJ-05   blackbox_ports value format
  PROJ-06   bridge.name format
  PROJ-07   blackbox type requires blackbox_ports with >= 1 entry
  PROJ-08   chisel type forbids blackbox_ports
  PROJ-09   width tokens that look like identifiers must reference a parameter
  PROJ-10   bridge names must be unique within the project
  PROJ-11   target.platform must exist in MasterRegistry
  PROJ-12   bridge.type must exist in MasterRegistry
  PROJ-13   port_map values must exist in blackbox_ports; port_map keys must
            appear in the correct direction list of the registry bridge
  PROJ-14   design.sources must be present and contain
            at least one source file when design.type is 'blackbox'.
  PROJ-15   design.top_module must be a valid system/verilog module name.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator, computed_field
import fslab.utils.regexes as rx
from fslab.utils.display import regex_msg


# Allowed sets
_DESIGN_TYPES = {"chisel", "blackbox"}
_EMULATOR_TYPES = {"verilator", "vcs", "xcelium"}

# ---------------------------------------------------------------------------
# project: block
# ---------------------------------------------------------------------------

class ProjectConfig(BaseModel):
    """Top-level project metadata."""

    name: str
    package_name: str
    config_class: str
    project_dir: str

    @computed_field
    @property
    def fslab_top(self) -> str:
        parts = re.split(r'[-_]+', self.name)
        camel = ''.join(part.capitalize() for part in parts if part)
        return camel + "Top"

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """[PROJ-01] project.name must match ^[a-zA-Z0-9_-]+$"""
        if not rx.PROJECT_NAME_RE.match(v):
            raise ValueError(
                f"[PROJ-01] project.name '{v}' is invalid. " +
                regex_msg(rx.PROJECT_NAME_RE)
            )
        return v

    @field_validator("package_name", "config_class", mode="before")
    @classmethod
    def validate_module_identifiers(cls, v: str, info: Any) -> str:
        """[PROJ-02] package_name, config_class must be valid identifiers."""
        if not rx.MODULE_RE.match(v):
            raise ValueError(
                f"[PROJ-02] project.{info.field_name} '{v}' is invalid. " +
                regex_msg(rx.MODULE_RE)
            )
        return v

# ---------------------------------------------------------------------------
# design: block
# ---------------------------------------------------------------------------

class DesignConfig(BaseModel):
    """Describes the user's RTL design."""

    type: str
    top_module: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    blackbox_ports: Optional[dict[str, str]] = None  # only present for blackbox

    @field_validator("type", mode="before")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """[PROJ-03] design.type must be 'chisel' or 'blackbox'."""
        if v not in _DESIGN_TYPES:
            raise ValueError(
                f"[PROJ-03] design.type '{v}' is invalid. "
                f"Must be one of: {sorted(_DESIGN_TYPES)}"
            )
        return v

    @field_validator("blackbox_ports", mode="before")
    @classmethod
    def validate_blackbox_port_format(
        cls, v: Optional[dict[str, str]]
    ) -> Optional[dict[str, str]]:
        """
        [PROJ-05] Each value in blackbox_ports must match:
                  ^(in|out)\\s+(clock|reset|\\d+|[a-zA-Z_][a-zA-Z0-9_]*)$
        """
        if v is None:
            return v
        for port_name, port_def in v.items():
            if not rx.BB_PORT_RE.match(port_def):
                raise ValueError(
                    f"[PROJ-05] blackbox_ports['{port_name}'] = '{port_def}' is invalid. " +
                    regex_msg(rx.BB_PORT_RE)
                )
        return v

    @field_validator("top_module", mode="before")
    @classmethod
    def validate_top_module_name(cls, v: str, info: Any) -> str:
        """[PROJ-15] top_module be valid identifier."""
        if not rx.VERILOG_MODULE_RE.match(v):
            raise ValueError(
                f"[PROJ-15] design.{info.field_name} '{v}' is invalid. " +
                regex_msg(rx.VERILOG_MODULE_RE)
            )
        return v

    @model_validator(mode="after")
    def validate_blackbox_rules(self) -> "DesignConfig":
        """
        [PROJ-07] 'blackbox' design requires blackbox_ports with >= 1 entry.
        [PROJ-08] 'chisel'   design must NOT have blackbox_ports.
        [PROJ-09] Width tokens that are identifiers (not digits / clock / reset)
                  must reference an existing key in design.parameters.
        """
        if self.type == "blackbox":
            # [PROJ-07]
            if not self.blackbox_ports:
                raise ValueError(
                    "[PROJ-07] design.blackbox_ports must be present and contain "
                    "at least one entry when design.type is 'blackbox'."
                )

            clock_found = False
            reset_found = False

            # [PROJ-09]
            for port_name, port_def in self.blackbox_ports.items():
                # port_def is already validated by [PROJ-05], so split is safe
                _, width_token = port_def.split(maxsplit=1)
                # If the width token is not a numeric literal, 'clock', or 'reset',
                # it must be a parameter reference.
                is_literal = width_token.isdigit() or width_token in ("clock", "reset", "logic", "reg")
                if not is_literal and width_token not in self.parameters:
                    raise ValueError(
                        f"[PROJ-09] blackbox_ports['{port_name}'] references "
                        f"parameter '{width_token}' which is not defined in "
                        "design.parameters."
                    )
                if port_def == "in clock":
                    clock_found = True

                if port_def == "in reset":
                    reset_found = True

            if not clock_found:
                raise ValueError(
                    f"design.blackbox_ports must contain a clock port "
                     "defined as 'in clock'."
                )

            if not reset_found:
                raise ValueError(
                    f"design.blackbox_ports must contain a reset port "
                     "defined as 'in reset'."
                )

        elif self.type == "chisel":
            # [PROJ-08]
            if self.blackbox_ports is not None:
                raise ValueError(
                    "[PROJ-08] design.blackbox_ports must NOT be present "
                    "when design.type is 'chisel'."
                )

        return self


# ---------------------------------------------------------------------------
# target: block
# ---------------------------------------------------------------------------

class TargetConfig(BaseModel):
    """FPGA target configuration."""

    platform: str
    clock_period: str
    fpga_sim: str


# ---------------------------------------------------------------------------
# host: block
# ---------------------------------------------------------------------------

class HostConfig(BaseModel):
    """Host-side emulation / compilation settings."""

    emulator: str
    driver_name: str
    cxx_standard: int = 17
    cxx_flags: str = ""
    sources: list[str] = Field(default_factory=list)
    includes: list[str] = Field(default_factory=list)
    libs: list[str] = Field(default_factory=list)

    @field_validator("emulator", mode="before")
    @classmethod
    def validate_emulator(cls, v: str) -> str:
        """[PROJ-04] host.emulator must be 'verilator', 'vcs', or 'xcelium'."""
        if v not in _EMULATOR_TYPES:
            raise ValueError(
                f"[PROJ-04] host.emulator '{v}' is invalid. "
                f"Must be one of: {sorted(_EMULATOR_TYPES)}"
            )
        return v


# ---------------------------------------------------------------------------
# bridges: list item
# ---------------------------------------------------------------------------

class BridgeConfig(BaseModel):
    """One entry in the project's bridges list."""

    type: str
    name: str
    port_map: dict[str, str] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """[PROJ-06] bridge.name must match ^[a-zA-Z_][a-zA-Z0-9_]*$"""
        if not rx.BRIDGE_NAME_RE.match(v):
            raise ValueError(
                f"[PROJ-06] bridge.name '{v}' is invalid. " +
                regex_msg(rx.BRIDGE_NAME_RE)
            )
        return v


# ---------------------------------------------------------------------------
# advanced: block
# ---------------------------------------------------------------------------

class AdvancedConfig(BaseModel):
    """Paths and generation parameters."""

    default_registry: Optional[str] = None
    custom_registries: list[str] = Field(default_factory=list)
    firesim_root: Optional[str] = None
    firesim_lab_root: Optional[str] = None
    gen_dir: str = "generated-src"
    gen_file_basename: str = "FireSim-generated"


# ---------------------------------------------------------------------------
# Top-level project config with cross-registry validation
# ---------------------------------------------------------------------------

class FSLabConfig(BaseModel):
    """
    Root model for `fslab.yaml`.

    The model_validator below performs all cross-registry semantic checks
    ([PROJ-10] through [PROJ-13]) by reading the `MasterRegistry` from the
    Pydantic validation *context* dict (key: ``"registry"``).

    Usage::

        config = FSLabConfig.model_validate(
            raw_yaml_dict,
            context={"registry": master_registry},
        )
    """

    project: ProjectConfig
    design: DesignConfig
    target: TargetConfig
    host: HostConfig
    bridges: list[BridgeConfig] = Field(default_factory=list)
    advanced: AdvancedConfig = Field(default_factory=AdvancedConfig)

    # ------------------------------------------------------------------
    # Cross-registry validation (Pass 2)
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def cross_validate_with_registry(self, info: Any) -> "FSLabConfig":
        """
        Performs semantic cross-checks that require the MasterRegistry.

        [PROJ-10] bridge names within the project must be unique.
        [PROJ-11] target.platform must be a known platform id.
        [PROJ-12] each bridge.type must be a known bridge id.
        [PROJ-13] port_map values must exist in blackbox_ports;
                  port_map keys must be in the correct direction list
                  of the registry bridge.
        [PROJ-16] target.fpga_sim MUST exist as a valid id in the
                  MasterRegistry.fpgasimulators.
        """
        # Validation context may be absent during unit-testing individual models.
        if info is None or info.context is None:
            return self

        registry = info.context.get("registry")
        if registry is None:
            return self

        # --- [PROJ-10] Unique bridge names ---
        bridge_names = [b.name for b in self.bridges]
        seen_names: set[str] = set()
        for name in bridge_names:
            if name in seen_names:
                raise ValueError(
                    f"[PROJ-10] Duplicate bridge name '{name}' found. "
                    "All bridge names within a project must be unique."
                )
            seen_names.add(name)

        # --- [PROJ-11] Platform must exist in registry ---
        if self.target.platform not in registry.platforms:
            available = sorted(registry.platforms.keys())
            raise ValueError(
                f"[PROJ-11] target.platform '{self.target.platform}' is not "
                f"defined in any loaded registry. Available platforms: {available}"
            )

        # --- [PROJ-16] FPGA Sim must exist in registry ---
        if self.target.fpga_sim not in registry.fpgasimulators:
            available = sorted(registry.fpgasimulators.keys())
            raise ValueError(
                f"[PROJ-11] target.platform '{self.target.fpgasimulators}' is not "
                f"defined in any loaded registry. Available platforms: {available}"
            )

        # --- Per-bridge checks ---
        for bridge_cfg in self.bridges:

            # --- [PROJ-12] Bridge type must exist in registry ---
            if bridge_cfg.type not in registry.bridges:
                available = sorted(registry.bridges.keys())
                raise ValueError(
                    f"[PROJ-12] bridges['{bridge_cfg.name}'].type "
                    f"'{bridge_cfg.type}' is not defined in any loaded registry. "
                    f"Available bridges: {available}"
                )

            reg_bridge = registry.bridges[bridge_cfg.type]

            # --- [PROJ-13] Port-map validation (blackbox designs only) ---
            if self.design.type == "blackbox" and self.design.blackbox_ports:
                bb_ports = self.design.blackbox_ports  # dict[str, str]

                for map_key, map_value in bridge_cfg.port_map.items():

                    # map_value must be a declared blackbox port
                    if map_value not in bb_ports:
                        raise ValueError(
                            f"[PROJ-13] bridges['{bridge_cfg.name}'].port_map "
                            f"value '{map_value}' does not exist in "
                            "design.blackbox_ports."
                        )

                    # Direction of the blackbox port:  "in ..." or "out ..."
                    direction = bb_ports[map_value].split()[0]  # 'in' or 'out'

                    if direction == "in":
                        # map_key must come from the bridge's input_ports
                        if map_key not in reg_bridge.input_ports:
                            raise ValueError(
                                f"[PROJ-13] bridges['{bridge_cfg.name}'].port_map "
                                f"key '{map_key}' maps to blackbox input port "
                                f"'{map_value}', but '{map_key}' is not listed in "
                                f"registry bridge '{bridge_cfg.type}'.input_ports "
                                f"{reg_bridge.input_ports}."
                            )
                    elif direction == "out":
                        # map_key must come from the bridge's output_ports
                        if map_key not in reg_bridge.output_ports:
                            raise ValueError(
                                f"[PROJ-13] bridges['{bridge_cfg.name}'].port_map "
                                f"key '{map_key}' maps to blackbox output port "
                                f"'{map_value}', but '{map_key}' is not listed in "
                                f"registry bridge '{bridge_cfg.type}'.output_ports "
                                f"{reg_bridge.output_ports}."
                            )

        return self

    # ------------------------------------------------------------------
    # Design source validation as the Design class does not have access to project_dir
    # ------------------------------------------------------------------
    @model_validator(mode="after")
    def validate_design_sources(self) -> "ProjectConfig":
        """ [PROJ-14] design.sources must be present and contain
            at least one source file when design.type is 'blackbox'.
        """
        # 1. Check if sources exist
        if self.design and self.design.sources:
            proj_dir = getattr(self.project, "project_dir", None)
            proj_name = getattr(self.project, "name", "design")
            target_dir = Path(str(proj_dir or f"/target/{proj_name}"))
            
            # 3. Validate and update in place
            for i, f in enumerate(self.design.sources):
                full_path = target_dir / f
                
                if not full_path.is_file():
                    raise ValueError(f"Source file '{full_path}' not found.")
                
                self.design.sources[i] = str(full_path)
        else :
            if self.design.type == "blackbox":
                raise ValueError("Source files must be provided when design type is 'blackbox'.")
                
        return self