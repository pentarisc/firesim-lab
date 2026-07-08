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
  PROJ-14   design.sources must be present and contain at least one source
            file when design.type is 'blackbox'.
  PROJ-15   design.top_module must be a valid system/verilog module name.
  PROJ-16   target.fpga_sim must exist in MasterRegistry.fpgasimulators

  Build-pipeline cross-checks
  BBA-01    target.build.bitbuilder_args must validate against the platform's
            bitbuilder.args_schema (resolved via BITBUILDER_ARGS_REGISTRY)
  BBA-02    target.build requires the platform to have a bitbuilder configured
            for fpga build operations (warning only — sim/driver still works)
  HMOD-05   target.build.host.type must be in platform.host_models keys
  PUB-03    target.build.publish.type must be in platform.publish keys
  FSLOT-02  target.build.host.fpga_slot must NOT be set (slots are a run-side
            concept)

  Run-pipeline cross-checks (gated on target.run being supplied)
  RUN-20    target.run requires the platform to have a runner configured
  FSLOT-03  target.run.host.fpga_slot must be set
  RUNA-01   target.run.host.fpga_slot.runner_args must validate against the
            platform's runner.args_schema (resolved via RUNNER_ARGS_REGISTRY)
  HMOD-06   target.run.host.type must be in platform.host_models keys
  ARTSRC-01 target.run.artifact_source.type must be in
            platform.run_artifact_sources keys
"""

from __future__ import annotations

import re
from typing import Any, Optional, Annotated, Dict, Union, List
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
    computed_field,
)
import fslab.utils.regexes as rx
from fslab.utils.display import regex_msg

from fslab.schemas.artifact_source import ArtifactSourceConfig
from fslab.schemas.host_model import HostModelConfig
from fslab.schemas.publish import PublishConfig
from fslab.schemas.bitbuilder_args import (
    BITBUILDER_ARGS_REGISTRY,
    resolve_args_schema,
)
from fslab.schemas.runner_args import (
    RUNNER_ARGS_REGISTRY,
    resolve_args_schema as resolve_runner_args_schema,
)

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
                  ^(in|out)\\s+(clock|reset|enable|[a-zA-Z_][a-zA-Z0-9_[]:]*)$
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
                _, width_token = port_def.split(maxsplit=1)

                # Match patterns like:
                # logic
                # logic[3:0]
                # reg[WIDTH-1:0] (future-proofing)
                m = re.fullmatch(r'(\w+)(\[(.+):(.+)\])?', width_token)

                if not m:
                    raise ValueError(
                        f"[PROJ-09] Invalid port definition '{port_def}'"
                    )

                base_type = m.group(1)        # logic / reg / etc.
                msb = m.group(3)              # e.g. 3
                lsb = m.group(4)              # e.g. 0

                # Check base type
                is_literal = base_type in ("clock", "reset", "enable", "logic", "reg")

                # Check range if present
                if msb is not None and lsb is not None:
                    range_is_valid = (
                        (msb.isdigit() or msb in self.parameters) and
                        (lsb.isdigit() or lsb in self.parameters)
                    )
                else:
                    range_is_valid = True

                # Final validation
                if not (is_literal and range_is_valid):
                    raise ValueError(
                        f"[PROJ-09] blackbox_ports['{port_name}'] references "
                        f"invalid type or range '{width_token}'. "
                        f"Ensure base type is valid and range uses literals or defined parameters."
                    )

                if port_def == "in clock":
                    clock_found = True

                if port_def == "in reset":
                    reset_found = True

            if not clock_found:
                raise ValueError(
                    "design.blackbox_ports must contain a clock port "
                    "defined as 'in clock'."
                )

            if not reset_found:
                raise ValueError(
                    "design.blackbox_ports must contain a reset port "
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

class TargetBuildConfig(BaseModel):
    """`target.build:` section of the project YAML.

    New shape — four orthogonal axes:
      * fpga_frequency                    build-time parameter
      * bitbuilder_args                   per-bitbuilder user tunables
      * host                              host-acquisition strategy
                                          (discriminated union)
      * publish                           post-build artifact handling
                                          (discriminated union)
    """

    model_config = ConfigDict(extra="forbid")

    fpga_frequency: float = Field(
        ...,
        gt=0.0,
        le=300.0,
        description="Build frequency in MHz; must be in (0, 300].",
    )

    bitbuilder_args: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Per-bitbuilder user-tunable args. The pydantic schema for this "
            "block is selected via the platform's bitbuilder.args_schema and "
            "validated cross-field in FSLabConfig.cross_validate_with_registry "
            "(BBA-01, BBA-02)."
        ),
    )

    host: HostModelConfig = Field(
        ...,
        description=(
            "Host-acquisition config. Discriminated by `host.type`. "
            "Defaults from `platforms.<id>.host_models.<type>` are merged "
            "into the user dict at parse time. `fpga_slot` must be "
            "omitted under the build host (slots are a run-side concept) "
            "— see [FSLOT-02]."
        ),
    )

    publish: PublishConfig = Field(
        ...,
        description=(
            "Post-build artifact handling. Discriminated by `publish.type`. "
            "Defaults from `platforms.<id>.publish.<type>` are merged into "
            "the user dict at parse time."
        ),
    )


class TargetRunConfig(BaseModel):
    """`target.run:` section of the project YAML.

    Run-side counterpart to `target.build`. Two orthogonal axes:
      * host               host-acquisition strategy (HostModelConfig —
                           same discriminated union as build.host) with
                           an embedded `fpga_slot` sub-block carrying
                           the per-slot `runner_args` (max_cycles,
                           payloads, etc.)
      * artifact_source    where the bitstream comes from (today: aws_afi)

    Optional at the `TargetConfig` level — a project that only builds
    (no `fslab sim fpga`) doesn't have to populate this.

    The nested `host.fpga_slot` is single-instance today (one host, one
    slot, id 0); the placement under `host:` is the forward-compatible
    scaffold for multi-host / multi-slot — the eventual shape is
    `hosts: [{ ..., slots: [...] }]`, with the inner slot fields
    unchanged.
    """

    model_config = ConfigDict(extra="forbid")

    host: HostModelConfig = Field(
        ...,
        description=(
            "Run-host acquisition config. Discriminated by `host.type`. "
            "Same schema as target.build.host — assigned verbatim here. "
            "Defaults from `platforms.<id>.host_models.<type>` are merged "
            "into the user dict at parse time. `host.fpga_slot` must be "
            "set on the run side — see [FSLOT-03]."
        ),
    )

    artifact_source: ArtifactSourceConfig = Field(
        ...,
        description=(
            "Where the bitstream the runner loads comes from. Discriminated "
            "by `artifact_source.type`. Defaults from "
            "`platforms.<id>.run_artifact_sources.<type>` are merged into "
            "the user dict at parse time."
        ),
    )


class TargetConfig(BaseModel):
    """FPGA target configuration."""

    platform: str
    clock_period: str
    fpga_sim: str
    build: TargetBuildConfig
    run: Optional[TargetRunConfig] = Field(
        None,
        description=(
            "Run-side config for `fslab sim fpga`. Optional — projects "
            "that only build (no FPGA-accelerated simulation) leave it "
            "unset and the run-pipeline cross-checks are skipped."
        ),
    )


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
# advanced: block
# ---------------------------------------------------------------------------

class RegistryEntry(BaseModel):
    path: str
    plugin: Optional[str] = None


class AdvancedConfig(BaseModel):
    """Paths and generation parameters."""

    default_registry: Optional[str] = None
    custom_registries: Optional[Union[str, RegistryEntry]] = Field(default_factory=list)
    firesim_root: Optional[str] = None
    firesim_lab_root: Optional[str] = None
    platforms_root: Optional[str] = None
    gen_dir: str = "generated-src"
    gen_file_basename: str = "FireSim-generated"

    @field_validator("custom_registries", mode="before")
    @classmethod
    def normalize_registries(cls, registries):
        if registries is None:
            return []

        normalized = []
        for item in registries:
            if isinstance(item, str):
                # Convert standard strings into RegistryEntry objects
                normalized.append(RegistryEntry(path=item))
            else:
                # It's already a RegistryEntry object
                normalized.append(item)
        return normalized


# ---------------------------------------------------------------------------
# Top-level project config with cross-registry validation
# ---------------------------------------------------------------------------

class FSLabConfig(BaseModel):
    """Root model for `fslab.yaml`.

    The model_validator below performs all cross-registry semantic checks
    by reading the `MasterRegistry` from the Pydantic validation *context*
    dict (key: ``"registry"``).
    """

    # Pins the project to the fslab CLI version that generated it.  Kept
    # optional here so that legacy files (no version field) reach the dedicated
    # version gate in parser.py and get a friendly migration message rather
    # than a generic pydantic "field required" error.  Compatibility itself is
    # enforced in parser.py before this model is validated.
    fslab_version: Optional[str] = None

    project: ProjectConfig
    design: DesignConfig
    target: TargetConfig
    host: HostConfig
    bridges: List[Any] = Field(default_factory=list)
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

        Build-pipeline checks:
        [BBA-01]   target.build.bitbuilder_args must validate against the
                   platform's bitbuilder.args_schema. Skipped (warning) when
                   the platform has no bitbuilder configured.
        [HMOD-05]  target.build.host.type must be in platform.host_models keys.
        [PUB-03]   target.build.publish.type must be in platform.publish keys.
        [FSLOT-02] target.build.host.fpga_slot must NOT be set.
        """
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
        platform_entry = registry.platforms.get(self.target.platform)
        if platform_entry is None:
            available = sorted(registry.platforms.keys())
            raise ValueError(
                f"[PROJ-11] target.platform '{self.target.platform}' is not "
                f"defined in any loaded registry. Available platforms: {available}"
            )

        # --- [PROJ-16] FPGA Sim must exist in registry ---
        if self.target.fpga_sim not in registry.fpgasimulators:
            available = sorted(registry.fpgasimulators.keys())
            raise ValueError(
                f"[PROJ-16] target.fpga_sim '{self.target.fpga_sim}' is not "
                f"defined in any loaded registry. Available: {available}"
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

            required_params = reg_bridge.required_params
            missing_params = set(required_params) - set(bridge_cfg.params)
            if missing_params:
                raise ValueError(
                    f"Missing required parameters: {sorted(missing_params)}. "
                    f"Available: {sorted(reg_bridge.required_params)}"
                )

            # --- [PROJ-13] Port-map validation (blackbox designs only) ---
            if self.design.type == "blackbox" and self.design.blackbox_ports:
                bb_ports = self.design.blackbox_ports

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

        # ---------- Build-pipeline cross-checks ----------------------------
        # These run only when target.build is present, which it always is in
        # the new schema (TargetConfig.build is required).
        build = self.target.build

        # [HMOD-05] host.type ∈ platform.host_models
        host_type = build.host.type
        if host_type not in platform_entry.host_models:
            supported = sorted(platform_entry.host_models.keys()) or ["<none>"]
            raise ValueError(
                f"[HMOD-05] target.build.host.type='{host_type}' is not in "
                f"platform '{self.target.platform}'.host_models. "
                f"Supported: {supported}."
            )

        # [FSLOT-02] build hosts must not carry an fpga_slot — slots are
        # only meaningful on the run side (a build host compiles a
        # bitstream; it does not load one onto an FPGA).
        if build.host.fpga_slot is not None:
            raise ValueError(
                "[FSLOT-02] target.build.host.fpga_slot must NOT be set. "
                "Slots are a run-side concept — move the block under "
                "target.run.host.fpga_slot."
            )

        # [PUB-03] publish.type ∈ platform.publish
        pub_type = build.publish.type
        if pub_type not in platform_entry.publish:
            supported = sorted(platform_entry.publish.keys()) or ["<none>"]
            raise ValueError(
                f"[PUB-03] target.build.publish.type='{pub_type}' is not in "
                f"platform '{self.target.platform}'.publish. "
                f"Supported: {supported}."
            )

        # [BBA-01] bitbuilder_args validates against bitbuilder.args_schema
        if platform_entry.bitbuilder is None:
            # Platform has no bitbuilder configured. Skipping silently — this
            # is acceptable for sim/driver-only flows. fslab build fpga is
            # responsible for raising a clearer "no bitbuilder" error when
            # the user actually attempts to build.
            return self

        bb_entry = registry.bitbuilders.get(platform_entry.bitbuilder)
        if bb_entry is None:
            # Already caught at MasterRegistry cross-validation [BB-10],
            # but defend here in case validation order changes.
            raise ValueError(
                f"[BBA-01] platform '{self.target.platform}' references "
                f"unknown bitbuilder '{platform_entry.bitbuilder}'."
            )

        try:
            args_cls = resolve_args_schema(bb_entry.args_schema)
        except ValueError as e:
            raise ValueError(f"[BBA-01] {e}") from e

        try:
            args_cls.model_validate(build.bitbuilder_args or {})
        except Exception as e:
            raise ValueError(
                f"[BBA-01] target.build.bitbuilder_args do not validate "
                f"against {bb_entry.args_schema}: {e}"
            ) from e

        # ---------- Run-pipeline cross-checks ------------------------------
        # Gated on target.run being supplied — projects without an FPGA run
        # (build-only / metasim-only) skip the entire block.
        run = self.target.run
        if run is None:
            return self

        # [HMOD-06] host.type ∈ platform.host_models
        run_host_type = run.host.type
        if run_host_type not in platform_entry.host_models:
            supported = sorted(platform_entry.host_models.keys()) or ["<none>"]
            raise ValueError(
                f"[HMOD-06] target.run.host.type='{run_host_type}' is not in "
                f"platform '{self.target.platform}'.host_models. "
                f"Supported: {supported}."
            )

        # [FSLOT-03] run hosts must carry an fpga_slot block.
        if run.host.fpga_slot is None:
            raise ValueError(
                "[FSLOT-03] target.run.host.fpga_slot is required. Add a "
                "`fpga_slot:` block under `target.run.host` with at least "
                "`id: 0` (single-slot today). The block also holds the "
                "`runner_args:` sub-block previously at "
                "`target.run.runner_args`."
            )

        # [ARTSRC-01] artifact_source.type ∈ platform.run_artifact_sources
        art_type = run.artifact_source.type
        if art_type not in platform_entry.run_artifact_sources:
            supported = (
                sorted(platform_entry.run_artifact_sources.keys()) or ["<none>"]
            )
            raise ValueError(
                f"[ARTSRC-01] target.run.artifact_source.type='{art_type}' is "
                f"not in platform '{self.target.platform}'.run_artifact_sources. "
                f"Supported: {supported}."
            )

        # [RUN-20] target.run requires platform.runner
        if platform_entry.runner is None:
            raise ValueError(
                f"[RUN-20] target.run is set but platform "
                f"'{self.target.platform}' has no runner configured. "
                f"Either remove target.run from fslab.yaml or extend the "
                f"platform's registry entry with `runner:` + `run_artifact_sources:`."
            )

        # [RUNA-01] host.fpga_slot.runner_args validates against runner.args_schema
        runner_entry = registry.runners.get(platform_entry.runner)
        if runner_entry is None:
            # Already caught at MasterRegistry cross-validation [RUN-10],
            # but defend here in case validation order changes.
            raise ValueError(
                f"[RUNA-01] platform '{self.target.platform}' references "
                f"unknown runner '{platform_entry.runner}'."
            )

        try:
            run_args_cls = resolve_runner_args_schema(runner_entry.args_schema)
        except ValueError as e:
            raise ValueError(f"[RUNA-01] {e}") from e

        try:
            run_args_cls.model_validate(run.host.fpga_slot.runner_args or {})
        except Exception as e:
            raise ValueError(
                f"[RUNA-01] target.run.host.fpga_slot.runner_args do not "
                f"validate against {runner_entry.args_schema}: {e}"
            ) from e

        return self

    # ------------------------------------------------------------------
    # Design source validation
    # ------------------------------------------------------------------
    @model_validator(mode="after")
    def validate_design_sources(self) -> "FSLabConfig":
        """[PROJ-14] design.sources must be present and contain at least one
        source file when design.type is 'blackbox'."""
        if self.design and self.design.sources:
            proj_dir = getattr(self.project, "project_dir", None)
            proj_name = getattr(self.project, "name", "design")
            target_dir = Path(str(proj_dir or f"/target/{proj_name}"))

            for i, f in enumerate(self.design.sources):
                full_path = target_dir / f

                if not full_path.is_file():
                    raise ValueError(f"[PROJ-14] Source file '{full_path}' not found.")

                self.design.sources[i] = str(full_path)
        else:
            if self.design.type == "blackbox":
                raise ValueError("[PROJ-14] Source files must be provided when design type is 'blackbox'.")

        return self
