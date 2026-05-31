"""
tests/test_schema_project.py
============================
Phase 1 — validation tests for fslab/schemas/project.py (and the bridge
config in resolvers.py that the project model composes).

Strategy
--------
Field-level rules (PROJ-01..06, PROJ-15) are exercised against the smallest
sub-model that owns them. Cross-registry rules (PROJ-07..14, PROJ-16, and the
build/run-pipeline checks) are exercised against the dynamic ``LiveFSLabConfig``
model with the ``MasterRegistry`` injected as validation context — the same
path the parser uses.

Assertions pin to the documented error codes (``[PROJ-09]`` etc.). Two checks
are exceptions: the PROJ-14 source-presence raises carry no code tag in the
current implementation, so those assert on the message text and are flagged
inline.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from pydantic import ValidationError

from fslab.schemas.project import ProjectConfig, DesignConfig, HostConfig
from fslab.schemas.registry import MasterRegistry, RegistryFile
from fslab.schemas.resolvers import UartBridgeConfig

from .conftest import make_registry_file_dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def validate(live_config_cls, project_dict, master):
    """Validate a project dict against the live model with registry context."""
    return live_config_cls.model_validate(project_dict, context={"registry": master})


def registry_with_platform_patch(patch: dict) -> MasterRegistry:
    """Build a MasterRegistry whose single f2 platform has ``patch`` applied."""
    reg = make_registry_file_dict()
    reg["platforms"][0].update(patch)
    return MasterRegistry.from_registry_files([RegistryFile.model_validate(reg)])


def add_run_block(project_dict: dict) -> dict:
    """Return a copy of *project_dict* with a valid ``target.run`` block."""
    d = copy.deepcopy(project_dict)
    d["target"]["run"] = {
        "host": {
            "type": "external",
            "host": "run-host.example.com",
            "user": "centos",
            "remote_platform_path": "/home/centos/aws-fpga-firesim-f2",
            "fpga_slot": {"id": 0, "runner_args": {}},
        },
        "artifact_source": {
            "type": "aws_afi",
            "agfi": "agfi-0123456789abcdef0",
        },
    }
    return d


# ===========================================================================
# Happy path
# ===========================================================================


class TestHappyPath:
    def test_valid_project_validates(self, live_config_cls, valid_project_dict, master_registry):
        cfg = validate(live_config_cls, valid_project_dict, master_registry)
        assert cfg.project.name == "my-design-02"

    def test_fslab_top_is_camelcased(self, live_config_cls, valid_project_dict, master_registry):
        cfg = validate(live_config_cls, valid_project_dict, master_registry)
        # "my-design-02" -> "MyDesign02" + "Top"
        assert cfg.project.fslab_top == "MyDesign02Top"

    def test_valid_project_with_run_block(self, live_config_cls, valid_project_dict, master_registry):
        cfg = validate(live_config_cls, add_run_block(valid_project_dict), master_registry)
        assert cfg.target.run is not None


# ===========================================================================
# Field-level rules on sub-models
# ===========================================================================


class TestProjectConfigFields:
    def _base(self, **over):
        d = {
            "name": "good-name",
            "package_name": "com.example",
            "config_class": "MyConfig",
            "project_dir": "/target/x",
        }
        d.update(over)
        return d

    def test_proj01_bad_name(self):
        with pytest.raises(ValidationError) as ei:
            ProjectConfig(**self._base(name="bad name!"))
        assert "PROJ-01" in str(ei.value)

    def test_proj02_bad_package_name(self):
        with pytest.raises(ValidationError) as ei:
            ProjectConfig(**self._base(package_name="0starts.with.digit"))
        assert "PROJ-02" in str(ei.value)

    def test_proj02_bad_config_class(self):
        with pytest.raises(ValidationError) as ei:
            ProjectConfig(**self._base(config_class="has-a-dash"))
        assert "PROJ-02" in str(ei.value)

    def test_valid_project_config_ok(self):
        cfg = ProjectConfig(**self._base())
        assert cfg.fslab_top == "GoodNameTop"


class TestDesignConfigFields:
    def _bb(self, **over):
        d = {
            "type": "blackbox",
            "top_module": "my_counter",
            "parameters": {},
            "blackbox_ports": {
                "clk": "in clock",
                "rst": "in reset",
                "d": "out logic",
            },
        }
        d.update(over)
        return d

    def test_proj03_bad_type(self):
        with pytest.raises(ValidationError) as ei:
            DesignConfig(**self._bb(type="systemverilog"))
        assert "PROJ-03" in str(ei.value)

    def test_proj05_bad_port_format(self):
        with pytest.raises(ValidationError) as ei:
            DesignConfig(**self._bb(blackbox_ports={"p": "inout logic"}))
        assert "PROJ-05" in str(ei.value)

    def test_proj05_bare_digit_width_rejected(self):
        # Legacy bare bit-widths ("out 1") are no longer accepted — the format
        # is Verilog-type based ("out logic"). Rejected at PROJ-05 (regex).
        with pytest.raises(ValidationError) as ei:
            DesignConfig(
                **self._bb(
                    blackbox_ports={"clk": "in clock", "rst": "in reset", "d": "out 1"}
                )
            )
        assert "PROJ-05" in str(ei.value)

    def test_proj15_bad_top_module(self):
        with pytest.raises(ValidationError) as ei:
            DesignConfig(**self._bb(top_module="1bad"))
        assert "PROJ-15" in str(ei.value)

    def test_proj07_blackbox_requires_ports(self):
        with pytest.raises(ValidationError) as ei:
            DesignConfig(type="blackbox", top_module="x", blackbox_ports=None)
        assert "PROJ-07" in str(ei.value)

    def test_proj08_chisel_forbids_ports(self):
        with pytest.raises(ValidationError) as ei:
            DesignConfig(
                type="chisel",
                top_module="x",
                blackbox_ports={"clk": "in clock"},
            )
        assert "PROJ-08" in str(ei.value)

    def test_proj09_width_token_references_unknown_param(self):
        with pytest.raises(ValidationError) as ei:
            DesignConfig(
                **self._bb(
                    parameters={},
                    blackbox_ports={
                        "clk": "in clock",
                        "rst": "in reset",
                        "d": "out logic[GHOST:0]",
                    },
                )
            )
        assert "PROJ-09" in str(ei.value)

    def test_proj09_width_token_with_known_param_ok(self):
        cfg = DesignConfig(
            **self._bb(
                parameters={"WIDTH": 8},
                blackbox_ports={
                    "clk": "in clock",
                    "rst": "in reset",
                    "d": "out logic[WIDTH:0]",
                },
            )
        )
        assert cfg.blackbox_ports["d"] == "out logic[WIDTH:0]"


class TestHostConfigFields:
    def test_proj04_bad_emulator(self):
        with pytest.raises(ValidationError) as ei:
            HostConfig(emulator="ghdl", driver_name="Drv")
        assert "PROJ-04" in str(ei.value)

    @pytest.mark.parametrize("emu", ["verilator", "vcs", "xcelium"])
    def test_proj04_valid_emulators(self, emu):
        cfg = HostConfig(emulator=emu, driver_name="Drv")
        assert cfg.emulator == emu


class TestBridgeNameField:
    def test_proj06_bad_bridge_name(self):
        with pytest.raises(ValidationError) as ei:
            UartBridgeConfig(type="uart", name="bad-name")
        assert "PROJ-06" in str(ei.value)


# ===========================================================================
# Cross-registry rules (full LiveFSLabConfig)
# ===========================================================================


class TestCrossRegistry:
    def test_proj10_duplicate_bridge_names(self, live_config_cls, valid_project_dict, master_registry):
        d = copy.deepcopy(valid_project_dict)
        d["bridges"] = [
            {"type": "uart", "name": "dup", "port_map": {}, "params": {}},
            {"type": "uart", "name": "dup", "port_map": {}, "params": {}},
        ]
        with pytest.raises(ValidationError) as ei:
            validate(live_config_cls, d, master_registry)
        assert "PROJ-10" in str(ei.value)

    def test_proj11_unknown_platform(self, live_config_cls, valid_project_dict, master_registry):
        d = copy.deepcopy(valid_project_dict)
        d["target"]["platform"] = "no_such_platform"
        with pytest.raises(ValidationError) as ei:
            validate(live_config_cls, d, master_registry)
        assert "PROJ-11" in str(ei.value)

    def test_proj12_bridge_type_not_in_registry(self, live_config_cls, valid_project_dict, master_registry):
        # `fased` is a valid discriminated-union arm but absent from the
        # minimal registry (which only declares `uart`).
        d = copy.deepcopy(valid_project_dict)
        d["bridges"] = [{"type": "fased", "name": "mem0", "port_map": {}, "params": {}}]
        with pytest.raises(ValidationError) as ei:
            validate(live_config_cls, d, master_registry)
        assert "PROJ-12" in str(ei.value)

    def test_proj13_wrong_direction(self, live_config_cls, valid_project_dict, master_registry):
        d = copy.deepcopy(valid_project_dict)
        # txd is a bridge OUTPUT port, but uart_rx is an "in" blackbox port.
        d["bridges"][0]["port_map"] = {"txd": "uart_rx"}
        with pytest.raises(ValidationError) as ei:
            validate(live_config_cls, d, master_registry)
        assert "PROJ-13" in str(ei.value)

    def test_proj13_value_not_in_blackbox_ports(self, live_config_cls, valid_project_dict, master_registry):
        d = copy.deepcopy(valid_project_dict)
        d["bridges"][0]["port_map"] = {"rxd": "ghost_port"}
        with pytest.raises(ValidationError) as ei:
            validate(live_config_cls, d, master_registry)
        assert "PROJ-13" in str(ei.value)

    def test_proj16_unknown_fpga_sim(self, live_config_cls, valid_project_dict, master_registry):
        d = copy.deepcopy(valid_project_dict)
        d["target"]["fpga_sim"] = "no_such_sim"
        with pytest.raises(ValidationError) as ei:
            validate(live_config_cls, d, master_registry)
        assert "PROJ-16" in str(ei.value)


# ===========================================================================
# PROJ-14 — source presence
# ===========================================================================


class TestDesignSources:
    def test_blackbox_requires_sources(self, live_config_cls, valid_project_dict, master_registry):
        d = copy.deepcopy(valid_project_dict)
        d["design"]["sources"] = []
        with pytest.raises(ValidationError) as ei:
            validate(live_config_cls, d, master_registry)
        assert "PROJ-14" in str(ei.value)

    def test_missing_source_file_rejected(self, live_config_cls, valid_project_dict, master_registry):
        d = copy.deepcopy(valid_project_dict)
        d["design"]["sources"] = ["user_rtl/does_not_exist.v"]
        with pytest.raises(ValidationError) as ei:
            validate(live_config_cls, d, master_registry)
        assert "PROJ-14" in str(ei.value)


# ===========================================================================
# Build-pipeline cross-checks
# ===========================================================================


class TestBuildPipeline:
    def test_fslot02_build_host_must_not_have_slot(self, live_config_cls, valid_project_dict, master_registry):
        d = copy.deepcopy(valid_project_dict)
        d["target"]["build"]["host"]["fpga_slot"] = {"id": 0, "runner_args": {}}
        with pytest.raises(ValidationError) as ei:
            validate(live_config_cls, d, master_registry)
        assert "FSLOT-02" in str(ei.value)

    def test_hmod05_host_type_not_supported(self, live_config_cls, valid_project_dict):
        # Platform supports only `external`; use a structurally-valid
        # `ec2_launch` host so HMOD-05 (not a union error) fires.
        master = registry_with_platform_patch({"host_models": {"external": {}}})
        d = copy.deepcopy(valid_project_dict)
        d["target"]["build"]["host"] = {
            "type": "ec2_launch",
            "region": "us-west-2",
            "iam_instance_profile": "fslab-build-role",
        }
        with pytest.raises(ValidationError) as ei:
            validate(live_config_cls, d, master)
        assert "HMOD-05" in str(ei.value)

    def test_pub03_publish_type_not_supported(self, live_config_cls, valid_project_dict):
        # Platform offers only `none`; request `aws_afi` to trip PUB-03.
        master = registry_with_platform_patch({"publish": {"none": {}}})
        d = copy.deepcopy(valid_project_dict)
        d["target"]["build"]["publish"] = {"type": "aws_afi", "s3_bucket_name": "my-bucket"}
        with pytest.raises(ValidationError) as ei:
            validate(live_config_cls, d, master)
        assert "PUB-03" in str(ei.value)


# ===========================================================================
# Run-pipeline cross-checks (gated on target.run)
# ===========================================================================


class TestRunPipeline:
    def test_fslot03_run_host_requires_slot(self, live_config_cls, valid_project_dict, master_registry):
        d = add_run_block(valid_project_dict)
        d["target"]["run"]["host"].pop("fpga_slot")
        with pytest.raises(ValidationError) as ei:
            validate(live_config_cls, d, master_registry)
        assert "FSLOT-03" in str(ei.value)

    def test_artsrc01_artifact_source_not_supported(self, live_config_cls, valid_project_dict):
        # Drop aws_afi from the platform's run_artifact_sources.
        master = registry_with_platform_patch({"run_artifact_sources": {}})
        d = add_run_block(valid_project_dict)
        with pytest.raises(ValidationError) as ei:
            validate(live_config_cls, d, master)
        assert "ARTSRC-01" in str(ei.value)

    def test_run20_platform_has_no_runner(self, live_config_cls, valid_project_dict):
        master = registry_with_platform_patch({"runner": None})
        d = add_run_block(valid_project_dict)
        with pytest.raises(ValidationError) as ei:
            validate(live_config_cls, d, master)
        assert "RUN-20" in str(ei.value)

    def test_runa01_bad_runner_args(self, live_config_cls, valid_project_dict, master_registry):
        d = add_run_block(valid_project_dict)
        d["target"]["run"]["host"]["fpga_slot"]["runner_args"] = {"max_cycles": -5}
        with pytest.raises(ValidationError) as ei:
            validate(live_config_cls, d, master_registry)
        assert "RUNA-01" in str(ei.value)
