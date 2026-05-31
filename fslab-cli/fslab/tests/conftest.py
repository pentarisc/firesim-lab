"""
tests/conftest.py
=================
Shared pytest fixtures for the fslab test suite.

The fixtures here model the *current* two-pass configuration system:

  * a realistic single-file registry dict (mirrors lib/registry.yaml's f2
    platform, uart bridge, verilator metasim, xsim fpgasim, f2 bitbuilder
    and f2 runner) — the smallest registry that lets a full ``FSLabConfig``
    validate;
  * a valid ``fslab.yaml``-shaped project dict whose ``design.sources``
    point at a real file created under ``tmp_path`` (so the PROJ-14
    source-existence check passes);
  * the ``LiveFSLabConfig`` model produced by the parser's
    ``_get_live_config_model`` (so ``bridges`` are typed discriminated-union
    instances rather than plain dicts — required for the PROJ-10/12/13
    cross-checks to run).

Helper factory functions return *fresh* deep copies on every call so a test
can mutate its copy to trigger a single validation rule without leaking
state into other tests.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from fslab import __version__
from fslab.schemas.registry import MasterRegistry, RegistryFile
from fslab.schemas.parser import _get_live_config_model


# ---------------------------------------------------------------------------
# Registry building blocks
# ---------------------------------------------------------------------------


def make_registry_file_dict() -> dict:
    """A minimal-but-complete single registry file.

    Contains exactly the entries a full ``FSLabConfig`` needs to validate
    against: the ``f2`` platform (with build + run pipelines wired), the
    ``uart`` bridge, one metasim (``verilator``), one fpgasim (``xsim``),
    plus the ``f2`` bitbuilder and runner the platform references.

    Returned fresh each call so callers may mutate without side effects.
    """
    return copy.deepcopy(
        {
            "fslab_version": __version__,
            "bridges": [
                {
                    "id": "uart",
                    "label": "UART Bridge",
                    "description": "UART TX/RX over a token channel.",
                    "origin": "fslab",
                    "input_ports": ["rxd"],
                    "output_ports": ["txd"],
                    "cpp_type": "uart_t",
                    "cpp_headers": ["bridges/uart.h"],
                    "cpp_sources": ["bridges/uart.cc"],
                    "cpp_template": "bridges/uart/sim_loop.cc.j2",
                    "required_params": [],
                    "scala_templates": {
                        "top_imports": "bridges/uart/top_imports.scala.j2",
                        "ports": "bridges/uart/ports.scala.j2",
                        "wiring": "bridges/uart/wiring.scala.j2",
                    },
                },
            ],
            "bitbuilders": [
                {
                    "id": "f2",
                    "label": "AWS F2 BitBuilder",
                    "description": "Builds an AWS F2 DCP tarball.",
                    "python_class": "F2BitBuilder",
                    "args_schema": "F2BitbuilderArgs",
                    "params_schema": "F2BitbuilderParams",
                    "build_script_basename": "build-bitstream.sh",
                    "build_script_flags": ["--cl_dir", "--frequency", "--strategy"],
                    "template_cl_name": "cl_firesim",
                    "remote_cl_parent_subdir": "hdk/cl/developer_designs",
                    "artifact_glob": "build/checkpoints/*.tar",
                },
            ],
            "runners": [
                {
                    "id": "f2",
                    "label": "AWS F2 Runner",
                    "description": "Executes a built F2 bitstream on an AWS F2 host.",
                    "python_class": "F2Runner",
                    "args_schema": "F2RunnerArgs",
                    "params_schema": "F2RunnerParams",
                    "remote_slot_parent_subdir": "sim_slot_0",
                },
            ],
            "platforms": [
                {
                    "id": "f2",
                    "label": "AWS F2 FPGA",
                    "config_package": "firesim.midasexamples",
                    "config_class": "DefaultF2Config",
                    "rpath_origin": True,
                    "required_env_vars": [],
                    "extra_cxx_flags": [],
                    "extra_include_dirs": [
                        "${PLATFORMS_ROOT}/f2/aws-fpga-firesim-f2/sdk/userspace/include"
                    ],
                    "extra_link_dirs": [
                        "${PLATFORMS_ROOT}/f2/aws-fpga-firesim-f2/sdk/userspace/lib"
                    ],
                    "extra_libs": ["fpga_mgmt", "z"],
                    "extra_link_options": [],
                    "board_dir": "${PLATFORMS_ROOT}/f2/aws-fpga-firesim-f2/hdk/cl/developer_designs",
                    "fpga_delivery_exts": [".sv", ".defines.vh"],
                    "cmake_fragment": "",
                    "bitbuilder": "f2",
                    "bitbuilder_params": {},
                    "local_platform_path": "${PLATFORMS_ROOT}/f2/aws-fpga-firesim-f2",
                    "local_build_script": "${PLATFORMS_ROOT}/f2/build-bitstream.sh",
                    "local_project_staging_subdir": "build/fpga/cl_{quintuplet}",
                    "local_results_subdir": "build/fpga/results-build",
                    "host_models": {
                        "external": {},
                        "ec2_launch": {
                            "instance_type": "z1d.2xlarge",
                            "remote_platform_path": "/home/ubuntu/src/aws-fpga-firesim-f2",
                        },
                    },
                    "publish": {
                        "none": {},
                        "aws_afi": {"append_userid_region": True, "copy_to_regions": []},
                    },
                    "runner": "f2",
                    "run_artifact_sources": {"aws_afi": {}},
                },
            ],
            "features": [
                {
                    "id": "verilog-blackbox",
                    "label": "Verilog BlackBox DUT",
                    "description": "Generate a Chisel BlackBox wrapper.",
                },
            ],
            "metasimulators": [
                {
                    "id": "verilator",
                    "label": "Verilator MIDAS-Level Simulator",
                    "tool_cxxopts": ["-O2"],
                    "rtlsim_define": True,
                    "rpath_origin": True,
                    "required_env_vars": [],
                    "cmake_targets": [
                        {"name": "verilator", "comment": "[fslab] Building Verilator"},
                    ],
                    "makefile_fragment": "include $(midas_dir)/rtlsim/Makefrag-verilator\n",
                },
            ],
            "fpgasimulators": [
                {
                    "id": "xsim",
                    "label": "Xilinx XSIM FPGA-Level Simulator",
                    "main": "f2_xsim",
                    "platform_override": "f2",
                    "rpath_origin": False,
                    "required_env_vars": [],
                    "cmake_targets": [
                        {"name": "xsim", "comment": "[fslab] Compiling XSIM host driver"},
                    ],
                    "makefile_fragment": "xsim: $(xsim_drv)\n",
                },
            ],
        }
    )


def make_project_dict(project_dir: str | Path, *, sources: list[str] | None = None) -> dict:
    """A valid ``fslab.yaml``-shaped project dict for the ``f2`` platform.

    ``project_dir`` is stamped into ``project.project_dir`` so the PROJ-14
    source-existence check resolves ``design.sources`` against it. The caller
    is responsible for creating the source files (see the ``valid_project_dict``
    fixture). Returned fresh each call.
    """
    return copy.deepcopy(
        {
            "fslab_version": __version__,
            "project": {
                "name": "my-design-02",
                "package_name": "com.example",
                "config_class": "MyConfig",
                "project_dir": str(project_dir),
            },
            "design": {
                "type": "blackbox",
                "top_module": "my_counter",
                "parameters": {},
                "sources": list(sources) if sources is not None else ["user_rtl/top.v"],
                "blackbox_ports": {
                    "clk": "in clock",
                    "rst": "in reset",
                    "uart_tx": "out logic",
                    "uart_rx": "in logic",
                },
            },
            "target": {
                "platform": "f2",
                "clock_period": "1.0",
                "fpga_sim": "xsim",
                "build": {
                    "fpga_frequency": 90.0,
                    "build_strategy": "TIMING",
                    "bitbuilder_args": {},
                    "host": {
                        "type": "external",
                        "host": "build-host.example.com",
                        "user": "centos",
                        "remote_platform_path": "/home/centos/aws-fpga-firesim-f2",
                    },
                    "publish": {"type": "none"},
                },
            },
            "host": {
                "emulator": "verilator",
                "driver_name": "MyDriver",
            },
            "bridges": [
                {
                    "type": "uart",
                    "name": "serial_0",
                    "port_map": {"txd": "uart_tx", "rxd": "uart_rx"},
                    "params": {},
                },
            ],
        }
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry_file_dict() -> dict:
    """Fresh minimal registry-file dict (see :func:`make_registry_file_dict`)."""
    return make_registry_file_dict()


@pytest.fixture()
def master_registry(registry_file_dict: dict) -> MasterRegistry:
    """A validated, merged :class:`MasterRegistry` built from the minimal file."""
    return MasterRegistry.from_registry_files(
        [RegistryFile.model_validate(registry_file_dict)]
    )


@pytest.fixture(scope="session")
def live_config_cls():
    """The dynamically-built ``LiveFSLabConfig`` model used by the parser.

    Bridges become typed discriminated-union instances under this model, which
    is what the FSLabConfig cross-registry validator expects.
    """
    return _get_live_config_model()


@pytest.fixture()
def valid_project_dict(tmp_path: Path) -> dict:
    """A valid project dict whose single source file exists on disk.

    ``project.project_dir`` points at ``tmp_path`` and ``user_rtl/top.v`` is
    created there so the PROJ-14 source-existence check passes.
    """
    (tmp_path / "user_rtl").mkdir(parents=True, exist_ok=True)
    (tmp_path / "user_rtl" / "top.v").write_text(
        "module my_counter(input clk, input rst);\nendmodule\n", encoding="utf-8"
    )
    return make_project_dict(tmp_path)


# ---------------------------------------------------------------------------
# Filesystem-scaffold fixtures (state / CLI tests)
# ---------------------------------------------------------------------------


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    """A minimal on-disk fslab project: fslab.yaml + registry.yaml + a source.

    The YAML files carry current-schema content, but the state/hash tests only
    need real files to read — content shape is irrelevant to hashing.
    """
    (tmp_path / "user_rtl").mkdir(parents=True, exist_ok=True)
    (tmp_path / "user_rtl" / "top.v").write_text(
        "module my_counter(input clk, input rst);\nendmodule\n", encoding="utf-8"
    )

    (tmp_path / "fslab.yaml").write_text(
        yaml.safe_dump(make_project_dict(tmp_path), sort_keys=False),
        encoding="utf-8",
    )
    (tmp_path / "registry.yaml").write_text(
        yaml.safe_dump(make_registry_file_dict(), sort_keys=False),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def state_dir(project_root: Path) -> Path:
    """Return the ``.fslab/`` directory path (created on demand)."""
    d = project_root / ".fslab"
    d.mkdir(exist_ok=True)
    return d
