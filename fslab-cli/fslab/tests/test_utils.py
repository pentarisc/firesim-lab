"""
tests/test_utils.py
===================
Phase 2 — tests for the small pure-utility modules:

  * utils/regexes.py      — the shared validation patterns
  * utils/placeholders.py — deterministic ${KEY} substitution
  * utils/streams.py      — the Tee fan-out write stream
  * utils/display.py      — regex_msg helper
"""

from __future__ import annotations

import io

import pytest

import fslab.utils.regexes as rx
from fslab.utils.placeholders import substitute
from fslab.utils.streams import Tee
from fslab.utils.display import regex_msg


# ===========================================================================
# regexes
# ===========================================================================


class TestRegexes:
    @pytest.mark.parametrize(
        "pattern, value, expected",
        [
            (rx.PROJECT_NAME_RE, "my-design_02", True),
            (rx.PROJECT_NAME_RE, "bad name", False),
            (rx.PROJECT_NAME_RE, "has.dot", False),
            (rx.MODULE_RE, "com.example.Foo", True),
            (rx.MODULE_RE, "0bad", False),
            (rx.BRIDGE_NAME_RE, "serial_0", True),
            (rx.BRIDGE_NAME_RE, "0serial", False),
            (rx.VERILOG_MODULE_RE, "my_counter$", True),
            (rx.VERILOG_MODULE_RE, "1bad", False),
            (rx.BB_PORT_RE, "in clock", True),
            (rx.BB_PORT_RE, "out logic", True),
            (rx.BB_PORT_RE, "out logic[7:0]", True),
            (rx.BB_PORT_RE, "inout logic", False),
            (rx.BB_PORT_RE, "out 1", False),
            (rx.ID_RE, "f2-x_1", True),
            (rx.ID_RE, "bad id", False),
            (rx.ENV_VAR_RE, "XILINX_XRT", True),
            (rx.ENV_VAR_RE, "lower", False),
            (rx.CMAKE_PATH_RE, "${PLATFORMS_ROOT}/x", True),
            (rx.CMAKE_PATH_RE, "$ENV{XRT}/x", True),
            (rx.CMAKE_PATH_RE, "relative/x", False),
            (rx.MAKEFILE_PATH_RE, "$(VCS_HOME)/x", True),
            (rx.MAKEFILE_PATH_RE, "$ENV{X}/y", False),
            (rx.PY_CLASS_NAME_RE, "F2BitBuilder", True),
            (rx.PY_CLASS_NAME_RE, "lowerStart", False),
            (rx.AMI_ID_RE, "ami-0123456789abcdef0", True),
            (rx.AMI_ID_RE, "ami-nothex!", False),
            (rx.AWS_REGION_RE, "us-west-2", True),
            (rx.AWS_REGION_RE, "US-WEST-2", False),
            (rx.AWS_INSTANCE_TYPE_RE, "f2.2xlarge", True),
            (rx.AWS_INSTANCE_TYPE_RE, "F2.2xlarge", False),
            (rx.EC2_INSTANCE_ID_RE, "i-0123456789abcdef0", True),
            (rx.EC2_INSTANCE_ID_RE, "i-XYZ", False),
            (rx.AGFI_RE, "agfi-0123456789abcdef0", True),
            (rx.AGFI_RE, "agfi-short", False),
        ],
    )
    def test_match(self, pattern, value, expected):
        assert bool(pattern.match(value)) is expected

    def test_jinja2_marker_detection(self):
        assert rx.JINJA2_EXPR_RE.search("x {{ var }}")
        assert rx.JINJA2_EXPR_RE.search("{% if x %}")
        assert rx.JINJA2_EXPR_RE.search("{# comment #}")
        assert not rx.JINJA2_EXPR_RE.search("plain text $(VAR)")

    def test_makefile_var_extraction(self):
        found = rx.MAKEFILE_VAR_RE.findall("$(VCS_HOME)/inc:$(XCELIUM_HOME)/lib")
        assert found == ["VCS_HOME", "XCELIUM_HOME"]


# ===========================================================================
# placeholders
# ===========================================================================


class TestSubstitute:
    def test_replaces_known_key(self):
        assert substitute("${ROOT}/sdk", {"ROOT": "/opt"}) == "/opt/sdk"

    def test_replaces_multiple_keys(self):
        out = substitute("${A}/${B}", {"A": "x", "B": "y"})
        assert out == "x/y"

    def test_unknown_placeholder_left_intact(self):
        # Misspellings must survive so they surface downstream.
        assert substitute("${MISSING}/x", {"ROOT": "/opt"}) == "${MISSING}/x"

    def test_does_not_touch_os_env_syntax(self):
        assert substitute("$HOME/x", {"HOME": "/h"}) == "$HOME/x"


# ===========================================================================
# streams.Tee
# ===========================================================================


class TestTee:
    def test_fans_out_to_all_streams(self):
        a, b = io.StringIO(), io.StringIO()
        tee = Tee(a, b)
        tee.write("hello")
        assert a.getvalue() == "hello"
        assert b.getvalue() == "hello"

    def test_returns_length_written(self):
        a = io.StringIO()
        assert Tee(a).write("abcd") == 4

    def test_tolerates_closed_stream(self):
        a, b = io.StringIO(), io.StringIO()
        b.close()
        tee = Tee(a, b)
        # Must not raise even though b is closed.
        tee.write("survives")
        assert a.getvalue() == "survives"

    def test_stream_protocol_flags(self):
        tee = Tee(io.StringIO())
        assert tee.writable() is True
        assert tee.isatty() is False


# ===========================================================================
# display.regex_msg
# ===========================================================================


class TestRegexMsg:
    def test_contains_pattern(self):
        msg = regex_msg(rx.PROJECT_NAME_RE)
        assert "Must match" in msg
