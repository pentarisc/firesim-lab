"""
tests/test_schema_registry.py
=============================
Phase 1 — validation tests for fslab/schemas/registry.py.

Each entity (BridgeEntry, PlatformEntry, MetaSimEntry, FpgaSimEntry,
BitbuilderEntry, RunnerEntry, RegistryFile, MasterRegistry) is driven
directly with a focused minimal dict, then mutated to trip one rule.

Assertions pin to the error-code tag that the *implementation* emits. Note
that a few documented codes are emitted under a different tag or carry no tag:

  * Bitbuilder/Runner/Platform id-format checks reuse the shared
    ``_validate_alpha_num`` helper, so they emit ``[REG-01]`` (not the
    documented ``[BB-01]`` / ``[RUN-01]``). Tests assert ``REG-01`` and flag it.
  * REG-02 (missing bridge field), REG-05 (missing feature field), and REG-08
    (port name / uniqueness) raise without a tag — those assert behaviorally
    and are flagged inline.
"""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from fslab.schemas.registry import (
    BridgeEntry,
    FeatureEntry,
    MasterRegistry,
    PlatformEntry,
    RegistryFile,
    MetaSimEntry,
    FpgaSimEntry,
    BitbuilderEntry,
    RunnerEntry,
    SimTarget,
)


# ---------------------------------------------------------------------------
# Minimal base dicts
# ---------------------------------------------------------------------------


def bridge_base(**over) -> dict:
    d = {
        "id": "uart",
        "label": "UART",
        "description": "desc",
        "origin": "fslab",
        "input_ports": ["rxd"],
        "output_ports": ["txd"],
        "cpp_type": "uart_t",
        "cpp_headers": ["h"],
        "cpp_sources": ["s"],
        "cpp_template": "t.j2",
        "scala_templates": {"ports": "p.j2", "wiring": "w.j2"},
    }
    d.update(over)
    return d


def platform_base(**over) -> dict:
    d = {
        "id": "f2",
        "label": "AWS F2",
        "config_package": "firesim.midasexamples",
        "config_class": "DefaultF2Config",
    }
    d.update(over)
    return d


def metasim_base(**over) -> dict:
    d = {
        "id": "verilator",
        "label": "Verilator",
        "cmake_targets": [{"name": "verilator", "comment": "c"}],
    }
    d.update(over)
    return d


def fpgasim_base(**over) -> dict:
    d = {
        "id": "xsim",
        "label": "XSIM",
        "cmake_targets": [{"name": "xsim", "comment": "c"}],
    }
    d.update(over)
    return d


def bitbuilder_base(**over) -> dict:
    d = {
        "id": "f2",
        "label": "F2 BitBuilder",
        "description": "desc",
        "python_class": "F2BitBuilder",
        "args_schema": "F2BitbuilderArgs",
        "params_schema": "F2BitbuilderParams",
        "build_script_basename": "build-bitstream.sh",
    }
    d.update(over)
    return d


def runner_base(**over) -> dict:
    d = {
        "id": "f2",
        "label": "F2 Runner",
        "description": "desc",
        "python_class": "F2Runner",
        "args_schema": "F2RunnerArgs",
        "params_schema": "F2RunnerParams",
    }
    d.update(over)
    return d


# ===========================================================================
# BridgeEntry
# ===========================================================================


class TestBridgeEntry:
    def test_valid(self):
        b = BridgeEntry.model_validate(bridge_base())
        assert b.id == "uart"

    def test_reg01_bad_id(self):
        with pytest.raises(ValidationError) as ei:
            BridgeEntry.model_validate(bridge_base(id="bad id"))
        assert "REG-01" in str(ei.value)

    def test_bad_origin(self):
        with pytest.raises(ValidationError) as ei:
            BridgeEntry.model_validate(bridge_base(origin="vendor"))
        assert "origin" in str(ei.value)

    def test_reg02_missing_required_field_behavioral(self):
        d = bridge_base()
        del d["cpp_type"]
        # REG-02 carries no tag — pydantic emits a generic "Field required".
        with pytest.raises(ValidationError):
            BridgeEntry.model_validate(d)

    def test_reg03_top_imports_optional(self):
        # scala_templates without top_imports is valid (REG-03).
        b = BridgeEntry.model_validate(bridge_base())
        assert b.scala_templates.top_imports is None

    def test_reg08_invalid_port_name_behavioral(self):
        with pytest.raises(ValidationError) as ei:
            BridgeEntry.model_validate(bridge_base(input_ports=["1bad"]))
        assert "not a valid Verilog identifier" in str(ei.value)

    def test_reg08_duplicate_port_behavioral(self):
        with pytest.raises(ValidationError) as ei:
            BridgeEntry.model_validate(bridge_base(input_ports=["dup"], output_ports=["dup"]))
        assert "duplicated" in str(ei.value)


# ===========================================================================
# PlatformEntry — driver cmake fields
# ===========================================================================


class TestPlatformCmakeFields:
    def test_valid(self):
        p = PlatformEntry.model_validate(platform_base())
        assert p.id == "f2"

    def test_reg01_bad_id(self):
        with pytest.raises(ValidationError) as ei:
            PlatformEntry.model_validate(platform_base(id="bad id"))
        assert "REG-01" in str(ei.value)

    def test_reg09_bad_env_var_name(self):
        with pytest.raises(ValidationError) as ei:
            PlatformEntry.model_validate(platform_base(required_env_vars=["lower_case"]))
        assert "REG-09" in str(ei.value)

    def test_reg09_undeclared_env_ref_in_path(self):
        with pytest.raises(ValidationError) as ei:
            PlatformEntry.model_validate(
                platform_base(extra_include_dirs=["$ENV{XILINX_XRT}/include"])
            )
        assert "REG-09" in str(ei.value)

    def test_reg09_declared_env_ref_ok(self):
        p = PlatformEntry.model_validate(
            platform_base(
                required_env_vars=["XILINX_XRT"],
                extra_include_dirs=["$ENV{XILINX_XRT}/include"],
            )
        )
        assert p.required_env_vars == ["XILINX_XRT"]

    def test_reg10_lib_with_dash_l(self):
        with pytest.raises(ValidationError) as ei:
            PlatformEntry.model_validate(platform_base(extra_libs=["-lfpga_mgmt"]))
        assert "REG-10" in str(ei.value)

    def test_reg11_relative_path(self):
        with pytest.raises(ValidationError) as ei:
            PlatformEntry.model_validate(platform_base(extra_include_dirs=["sdk/include"]))
        assert "REG-11" in str(ei.value)

    def test_reg11_cmake_var_ref_ok(self):
        p = PlatformEntry.model_validate(
            platform_base(extra_include_dirs=["${PLATFORMS_ROOT}/include"])
        )
        assert p.extra_include_dirs == ["${PLATFORMS_ROOT}/include"]

    def test_reg12_flag_without_dash(self):
        with pytest.raises(ValidationError) as ei:
            PlatformEntry.model_validate(platform_base(extra_cxx_flags=["O2"]))
        assert "REG-12" in str(ei.value)

    def test_reg13_jinja_in_cmake_fragment(self):
        with pytest.raises(ValidationError) as ei:
            PlatformEntry.model_validate(platform_base(cmake_fragment="x {{ leaked }}"))
        assert "REG-13" in str(ei.value)


# ===========================================================================
# PlatformEntry — build-pipeline fields
# ===========================================================================


class TestPlatformBuildPipeline:
    def test_bb05_bitbuilder_requires_local_paths(self):
        with pytest.raises(ValidationError) as ei:
            PlatformEntry.model_validate(platform_base(bitbuilder="f2"))
        assert "BB-05" in str(ei.value)

    def test_bb06_staging_subdir_needs_quintuplet(self):
        with pytest.raises(ValidationError) as ei:
            PlatformEntry.model_validate(
                platform_base(local_project_staging_subdir="build/fpga/cl_static")
            )
        assert "BB-06" in str(ei.value)

    def test_bb07_bad_local_platform_path(self):
        with pytest.raises(ValidationError) as ei:
            PlatformEntry.model_validate(platform_base(local_platform_path="relative/path"))
        assert "BB-07" in str(ei.value)

    def test_bb08_unknown_host_model_key(self):
        with pytest.raises(ValidationError) as ei:
            PlatformEntry.model_validate(platform_base(host_models={"bogus": {}}))
        assert "BB-08" in str(ei.value)

    def test_bb09_unknown_publish_key(self):
        with pytest.raises(ValidationError) as ei:
            PlatformEntry.model_validate(platform_base(publish={"bogus": {}}))
        assert "BB-09" in str(ei.value)

    def test_run04_unknown_artifact_source_key(self):
        with pytest.raises(ValidationError) as ei:
            PlatformEntry.model_validate(platform_base(run_artifact_sources={"bogus": {}}))
        assert "RUN-04" in str(ei.value)


# ===========================================================================
# MetaSimEntry / FpgaSimEntry
# ===========================================================================


class TestMetaSimEntry:
    def test_valid(self):
        m = MetaSimEntry.model_validate(metasim_base())
        assert m.id == "verilator"

    def test_reg09m_bad_env_var(self):
        with pytest.raises(ValidationError) as ei:
            MetaSimEntry.model_validate(metasim_base(required_env_vars=["bad-name"]))
        assert "REG-09m" in str(ei.value)

    def test_reg10m_lib_with_dash_l(self):
        with pytest.raises(ValidationError) as ei:
            MetaSimEntry.model_validate(metasim_base(extra_libs=["-lz"]))
        assert "REG-10m" in str(ei.value)

    def test_reg11m_relative_path(self):
        with pytest.raises(ValidationError) as ei:
            MetaSimEntry.model_validate(metasim_base(extra_include_dirs=["rel/inc"]))
        assert "REG-11m" in str(ei.value)

    def test_reg11m_make_var_ref_ok(self):
        m = MetaSimEntry.model_validate(
            metasim_base(required_env_vars=["VCS_HOME"], extra_include_dirs=["$(VCS_HOME)/include"])
        )
        assert m.extra_include_dirs == ["$(VCS_HOME)/include"]

    def test_reg12m_tool_cxxopts_without_dash(self):
        with pytest.raises(ValidationError) as ei:
            MetaSimEntry.model_validate(metasim_base(tool_cxxopts=["O2"]))
        assert "REG-12m" in str(ei.value)

    def test_reg13m_jinja_in_cmake_fragment(self):
        with pytest.raises(ValidationError) as ei:
            MetaSimEntry.model_validate(metasim_base(cmake_fragment="{% if x %}y{% endif %}"))
        assert "REG-13m" in str(ei.value)

    def test_reg14_jinja_in_makefile_fragment(self):
        with pytest.raises(ValidationError) as ei:
            MetaSimEntry.model_validate(metasim_base(makefile_fragment="x {{ leaked }}"))
        assert "REG-14" in str(ei.value)

    def test_reg15_empty_cmake_targets(self):
        with pytest.raises(ValidationError) as ei:
            MetaSimEntry.model_validate(metasim_base(cmake_targets=[]))
        assert "REG-15" in str(ei.value)

    def test_reg09x_undeclared_make_var_ref(self):
        with pytest.raises(ValidationError) as ei:
            MetaSimEntry.model_validate(metasim_base(extra_include_dirs=["$(UNDECLARED)/inc"]))
        assert "REG-09x" in str(ei.value)


class TestFpgaSimEntry:
    def test_valid(self):
        f = FpgaSimEntry.model_validate(fpgasim_base())
        assert f.id == "xsim"

    def test_reg15_empty_cmake_targets(self):
        with pytest.raises(ValidationError) as ei:
            FpgaSimEntry.model_validate(fpgasim_base(cmake_targets=[]))
        assert "REG-15" in str(ei.value)

    def test_reg14_jinja_in_makefile_fragment(self):
        with pytest.raises(ValidationError) as ei:
            FpgaSimEntry.model_validate(fpgasim_base(makefile_fragment="{{ x }}"))
        assert "REG-14" in str(ei.value)


class TestSimTarget:
    def test_reg15_empty_name(self):
        with pytest.raises(ValidationError) as ei:
            SimTarget.model_validate({"name": ""})
        assert "REG-15" in str(ei.value)

    def test_make_target_defaults_to_name(self):
        st = SimTarget.model_validate({"name": "verilator"})
        assert st.make_target == "verilator"


# ===========================================================================
# BitbuilderEntry / RunnerEntry
# ===========================================================================


class TestBitbuilderEntry:
    def test_valid(self):
        bb = BitbuilderEntry.model_validate(bitbuilder_base())
        assert bb.id == "f2"

    def test_bb01_bad_id(self):
        with pytest.raises(ValidationError) as ei:
            BitbuilderEntry.model_validate(bitbuilder_base(id="bad id"))
        assert "BB-01" in str(ei.value)

    def test_bb02_bad_python_class(self):
        with pytest.raises(ValidationError) as ei:
            BitbuilderEntry.model_validate(bitbuilder_base(python_class="lowercase"))
        assert "BB-02" in str(ei.value)

    def test_bb04_flag_without_double_dash(self):
        with pytest.raises(ValidationError) as ei:
            BitbuilderEntry.model_validate(bitbuilder_base(build_script_flags=["-cl_dir"]))
        assert "BB-04" in str(ei.value)


class TestRunnerEntry:
    def test_valid(self):
        r = RunnerEntry.model_validate(runner_base())
        assert r.id == "f2"

    def test_run01_bad_id(self):
        with pytest.raises(ValidationError) as ei:
            RunnerEntry.model_validate(runner_base(id="bad id"))
        assert "RUN-01" in str(ei.value)

    def test_run02_bad_args_schema_class(self):
        with pytest.raises(ValidationError) as ei:
            RunnerEntry.model_validate(runner_base(args_schema="lowercase"))
        assert "RUN-02" in str(ei.value)


# ===========================================================================
# FeatureEntry
# ===========================================================================


class TestFeatureEntry:
    def test_valid(self):
        f = FeatureEntry.model_validate({"id": "x", "label": "X", "description": "d"})
        assert f.id == "x"

    def test_reg05_missing_field_behavioral(self):
        # REG-05 carries no tag — generic "Field required".
        with pytest.raises(ValidationError):
            FeatureEntry.model_validate({"id": "x", "label": "X"})


# ===========================================================================
# RegistryFile — intra-file uniqueness (REG-06)
# ===========================================================================


class TestRegistryFile:
    def test_reg06_duplicate_platform_id(self):
        with pytest.raises(ValidationError) as ei:
            RegistryFile.model_validate(
                {"platforms": [platform_base(), platform_base()]}
            )
        assert "REG-06" in str(ei.value)

    def test_reg06_duplicate_bridge_id(self):
        with pytest.raises(ValidationError) as ei:
            RegistryFile.model_validate({"bridges": [bridge_base(), bridge_base()]})
        assert "REG-06" in str(ei.value)


# ===========================================================================
# MasterRegistry — merge + cross-checks
# ===========================================================================


class TestMasterRegistryMerge:
    def test_reg07_last_definition_wins(self):
        f1 = RegistryFile.model_validate({"platforms": [platform_base(label="OLD")]})
        f2 = RegistryFile.model_validate({"platforms": [platform_base(label="NEW")]})
        master = MasterRegistry.from_registry_files([f1, f2])
        assert master.platforms["f2"].label == "NEW"

    def test_bb10_platform_references_unknown_bitbuilder(self):
        rf = RegistryFile.model_validate(
            {
                "platforms": [
                    platform_base(
                        bitbuilder="ghost",
                        local_platform_path="/p",
                        local_build_script="/b.sh",
                        local_project_staging_subdir="build/cl_{quintuplet}",
                        local_results_subdir="build/results",
                    )
                ]
            }
        )
        with pytest.raises(ValueError) as ei:
            MasterRegistry.from_registry_files([rf])
        assert "BB-10" in str(ei.value)

    def test_bb11_bitbuilder_unknown_args_schema(self):
        rf = RegistryFile.model_validate(
            {"bitbuilders": [bitbuilder_base(args_schema="NotRegisteredArgs")]}
        )
        with pytest.raises(ValueError) as ei:
            MasterRegistry.from_registry_files([rf])
        assert "BB-11" in str(ei.value)

    def test_run10_platform_references_unknown_runner(self):
        rf = RegistryFile.model_validate({"platforms": [platform_base(runner="ghost")]})
        with pytest.raises(ValueError) as ei:
            MasterRegistry.from_registry_files([rf])
        assert "RUN-10" in str(ei.value)

    def test_run11_runner_unknown_args_schema(self):
        rf = RegistryFile.model_validate(
            {"runners": [runner_base(args_schema="NotRegisteredArgs")]}
        )
        with pytest.raises(ValueError) as ei:
            MasterRegistry.from_registry_files([rf])
        assert "RUN-11" in str(ei.value)
