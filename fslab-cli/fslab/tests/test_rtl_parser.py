"""
tests/test_rtl_parser.py
========================
Phase 2 — tests for fslab/utils/rtl_parser.py (the port/parameter extractor
that backs ``fslab init``).

``extract_module_info(path, module_name)`` returns ``[params, ports]`` where
each is a ``{name: str}`` dict, or ``[None, None]`` when the module is not
found or contains an unsupported SystemVerilog struct port.

pyslang is required; the suite skips cleanly if it is unavailable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pyslang")

from fslab.utils.rtl_parser import extract_module_info


BASIC_V = """\
module my_counter #(parameter WIDTH = 8) (
    input  clk,
    input  rst,
    output [WIDTH-1:0] count
);
endmodule
"""

STRUCT_SV = """\
typedef struct packed { logic a; logic b; } pkt_t;

module s_mod (
    input  clk,
    output pkt_t p
);
endmodule
"""


def _write(tmp_path: Path, name: str, text: str) -> str:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


class TestExtractModuleInfo:
    def test_ports_extracted(self, tmp_path):
        params, ports = extract_module_info(_write(tmp_path, "top.v", BASIC_V), "my_counter")
        assert ports is not None
        assert set(ports) == {"clk", "rst", "count"}

    def test_port_directions(self, tmp_path):
        _, ports = extract_module_info(_write(tmp_path, "top.v", BASIC_V), "my_counter")
        assert ports["clk"].startswith("in")
        assert ports["rst"].startswith("in")
        assert ports["count"].startswith("out")

    def test_parameters_extracted(self, tmp_path):
        params, _ = extract_module_info(_write(tmp_path, "top.v", BASIC_V), "my_counter")
        assert "WIDTH" in params

    def test_module_not_found(self, tmp_path):
        result = extract_module_info(_write(tmp_path, "top.v", BASIC_V), "no_such_module")
        assert result == [None, None]

    def test_struct_port_rejected(self, tmp_path):
        result = extract_module_info(_write(tmp_path, "s.sv", STRUCT_SV), "s_mod")
        assert result == [None, None]
