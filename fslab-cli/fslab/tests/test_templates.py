"""
tests/test_templates.py
=======================
Phase 3 — Jinja2 template rendering tests.

Builds the same render context that ``fslab generate`` builds
(``_build_template_context``) from a validated config + registry, then renders
each template through the same ``PackageLoader("fslab", "templates")``
environment ``_render_templates`` uses.

Two invariants are checked:
  * the rendered output contains the expected substitutions / per-bridge
    wiring, and
  * NO unrendered Jinja2 marker survives — the exact regression the original
    (now-removed) ``_inline_yaml`` test was guarding against.
"""

from __future__ import annotations

import copy

import pytest
from jinja2 import Environment, PackageLoader, select_autoescape

from fslab.commands.context import _build_template_context


# Evidence of unrendered template syntax. We check the expression and
# statement openers only: the closing delimiters (`}}`, `%}`) and the comment
# opener (`{#`) collide with legitimate shell in the rendered f2 build wrapper
# (`${BUILD_ID}}`, `${#DCP_CANDIDATES[@]}`). A leaked `{{`/`{%` is the real
# regression signal; comments are stripped by Jinja regardless of context.
JINJA_MARKERS = ("{{", "{%")

# The platform-independent templates plus the f2 background-build wrapper —
# mirrors the render_plan in build._render_templates.
ALL_TEMPLATES = [
    "build.sbt.j2",
    "plugins.sbt.j2",
    "CMakeLists.txt.j2",
    "Top.scala.j2",
    "DUT.scala.j2",
    "Config.scala.j2",
    "driver.cc.j2",
    "user_rtl_readme.md.j2",
    "remote_build/f2.sh.j2",
]


@pytest.fixture()
def ctx(live_config_cls, valid_project_dict, master_registry):
    """Render context for a valid f2 / uart project (with bridge params set)."""
    d = copy.deepcopy(valid_project_dict)
    # Give the uart bridge real params so the wiring snippet renders fully.
    d["bridges"][0]["params"] = {"freq_mhz": 100, "baud_rate": 115200}
    cfg = live_config_cls.model_validate(d, context={"registry": master_registry})
    return _build_template_context(cfg, master_registry)


@pytest.fixture()
def env():
    return Environment(
        loader=PackageLoader("fslab", "templates"),
        autoescape=select_autoescape(enabled_extensions=()),
        keep_trailing_newline=True,
    )


def render(env, ctx, name: str) -> str:
    return env.get_template(name).render(**ctx)


# ===========================================================================
# No leftover markers (the headline invariant)
# ===========================================================================


class TestNoLeftoverMarkers:
    @pytest.mark.parametrize("template_name", ALL_TEMPLATES)
    def test_no_unrendered_jinja(self, env, ctx, template_name):
        out = render(env, ctx, template_name)
        for marker in JINJA_MARKERS:
            assert marker not in out, f"{template_name} still contains {marker!r}"

    @pytest.mark.parametrize("template_name", ALL_TEMPLATES)
    def test_renders_nonempty(self, env, ctx, template_name):
        assert render(env, ctx, template_name).strip()


# ===========================================================================
# Substitution / wiring content
# ===========================================================================


class TestTopScala:
    def test_class_and_package(self, env, ctx):
        out = render(env, ctx, "Top.scala.j2")
        assert "class MyDesign02Top" in out
        assert "package com.example" in out

    def test_dut_instantiation_and_clock_reset(self, env, ctx):
        out = render(env, ctx, "Top.scala.j2")
        assert "new my_counter" in out
        assert "dut.io.clk := clock" in out
        assert "dut.io.rst := reset.asBool" in out

    def test_bridge_wiring_and_params(self, env, ctx):
        out = render(env, ctx, "Top.scala.j2")
        assert "serial_0" in out
        # freq_mhz / baud_rate flow through the uart wiring snippet.
        assert "115200" in out


class TestDutScala:
    def test_blackbox_class(self, env, ctx):
        out = render(env, ctx, "DUT.scala.j2")
        assert "class my_counter extends BlackBox" in out

    def test_clock_port_declared(self, env, ctx):
        out = render(env, ctx, "DUT.scala.j2")
        assert "val clk = Input(Clock())" in out

    def test_addpath_emitted(self, env, ctx):
        out = render(env, ctx, "DUT.scala.j2")
        assert "addPath(" in out
        assert "top.v" in out


class TestDriverCc:
    def test_driver_class(self, env, ctx):
        out = render(env, ctx, "driver.cc.j2")
        assert "class MyDriver : public firesim_lab_top_t" in out

    def test_bridge_registration(self, env, ctx):
        out = render(env, ctx, "driver.cc.j2")
        assert "registry.get_bridges<uart_t>()" in out

    def test_bridge_header_included(self, env, ctx):
        out = render(env, ctx, "driver.cc.j2")
        assert '#include "bridges/uart.h"' in out


class TestConfigScala:
    def test_package(self, env, ctx):
        assert "package com.example" in render(env, ctx, "Config.scala.j2")
