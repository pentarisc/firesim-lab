"""
tests/test_cli_commands.py
==========================
Phase 4 — end-to-end CLI tests for the hermetic commands (``new``, ``init``,
``generate``, ``clean``) driven through Typer's ``CliRunner``.

These hit the real filesystem under ``tmp_path`` but invoke no external
tooling (no sbt / java / cmake / Docker / AWS). Each command operates relative
to the current working directory, so every test ``chdir``s into its scratch
dir first.

The two-pass parser caches the first project path it validates in a module
global; the autouse ``_reset_parser_cache`` fixture clears it so each test's
distinct ``tmp_path`` does not trip the "project mismatch" guard.

Not covered here (deliberate):
  * ``fslab init`` with no ``--top-module`` — the current implementation leaves
    ``ports``/``params``/``sources`` unbound on that path and raises NameError.
    Flagged for a separate fix rather than pinned by a test.
"""

from __future__ import annotations

import yaml
import pytest
from typer.testing import CliRunner

from fslab.cli import app
from fslab.schemas import parser as _parser

from .conftest import make_project_dict, make_registry_file_dict


runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_parser_cache():
    """Clear the parser's process-global path lock around each test."""
    _parser._LOADED_PATH = None
    _parser._CACHED_DATA = None
    yield
    _parser._LOADED_PATH = None
    _parser._CACHED_DATA = None


@pytest.fixture()
def in_tmp(tmp_path, monkeypatch):
    """chdir into a fresh scratch directory for the duration of the test."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ===========================================================================
# fslab new
# ===========================================================================


class TestNew:
    def test_creates_workspace(self, in_tmp):
        result = runner.invoke(app, ["new", "proj"])
        assert result.exit_code == 0, result.output
        assert (in_tmp / "proj").is_dir()
        assert (in_tmp / "proj" / ".fslab" / "meta.json").is_file()
        assert (in_tmp / "proj" / "user_rtl").is_dir()
        assert (in_tmp / "proj" / ".gitignore").is_file()

    def test_meta_json_records_name(self, in_tmp):
        runner.invoke(app, ["new", "proj"])
        import json

        meta = json.loads((in_tmp / "proj" / ".fslab" / "meta.json").read_text())
        assert meta["project_name"] == "proj"

    def test_existing_dir_fails(self, in_tmp):
        (in_tmp / "proj").mkdir()
        result = runner.invoke(app, ["new", "proj"])
        assert result.exit_code == 1


# ===========================================================================
# fslab init
# ===========================================================================


class TestInit:
    def _make_workspace(self, root):
        """Minimal `fslab new` output: .fslab/meta.json + user_rtl/."""
        import json

        (root / ".fslab").mkdir()
        (root / ".fslab" / "meta.json").write_text(json.dumps({"project_name": "proj"}))
        (root / "user_rtl").mkdir()

    def test_without_workspace_fails(self, in_tmp):
        result = runner.invoke(app, ["init", "-t", "my_counter", "-f", "x.v"])
        assert result.exit_code == 1  # missing .fslab/meta.json

    def test_with_module_creates_yaml(self, in_tmp):
        self._make_workspace(in_tmp)
        (in_tmp / "user_rtl" / "top.v").write_text(
            "module my_counter(input clk, input rst, output txd);\nendmodule\n"
        )
        result = runner.invoke(
            app, ["init", "-t", "my_counter", "-f", "user_rtl/top.v", "-p", "f2"]
        )
        assert result.exit_code == 0, result.output
        yaml_path = in_tmp / "fslab.yaml"
        assert yaml_path.is_file()
        text = yaml_path.read_text()
        assert "my_counter" in text

    def test_without_module_creates_skeleton_yaml(self, in_tmp):
        # init with no --top-module must produce a skeleton fslab.yaml
        # (commented design block), not crash with NameError.
        self._make_workspace(in_tmp)
        result = runner.invoke(app, ["init", "-p", "f2"])
        assert result.exit_code == 0, result.output
        assert (in_tmp / "fslab.yaml").is_file()

    def test_existing_yaml_fails(self, in_tmp):
        self._make_workspace(in_tmp)
        (in_tmp / "fslab.yaml").write_text("project: {}\n")
        result = runner.invoke(app, ["init", "-t", "my_counter", "-f", "user_rtl/top.v"])
        assert result.exit_code == 1

    def test_bad_module_name_fails(self, in_tmp):
        self._make_workspace(in_tmp)
        result = runner.invoke(app, ["init", "-t", "1bad", "-f", "user_rtl/top.v"])
        assert result.exit_code == 1

    def test_missing_module_file_arg_fails(self, in_tmp):
        self._make_workspace(in_tmp)
        # --top-module given without --top-module-file.
        result = runner.invoke(app, ["init", "-t", "my_counter"])
        assert result.exit_code == 1


# ===========================================================================
# fslab generate
# ===========================================================================


def _write_project(root):
    """Write a valid fslab.yaml + registry.yaml into *root* and the source file."""
    (root / "user_rtl").mkdir(parents=True, exist_ok=True)
    (root / "user_rtl" / "top.v").write_text(
        "module my_counter(input clk, input rst);\nendmodule\n", encoding="utf-8"
    )

    reg_path = root / "registry.yaml"
    reg_path.write_text(yaml.safe_dump(make_registry_file_dict(), sort_keys=False))

    proj = make_project_dict(root)
    # The uart wiring snippet references freq_mhz / baud_rate, so supply them
    # (the real uart bridge declares these as required_params).
    proj["bridges"][0]["params"] = {"freq_mhz": 100, "baud_rate": 115200}
    proj["advanced"] = {"default_registry": str(reg_path)}
    (root / "fslab.yaml").write_text(yaml.safe_dump(proj, sort_keys=False))


class TestGenerate:
    def test_missing_yaml_fails(self, in_tmp):
        result = runner.invoke(app, ["generate"])
        assert result.exit_code == 1

    def test_renders_templates(self, in_tmp):
        _write_project(in_tmp)
        result = runner.invoke(app, ["generate"])
        assert result.exit_code == 0, result.output
        scala_dir = in_tmp / "src" / "main" / "scala"
        assert (scala_dir / "MyDesign02Top.scala").is_file()
        assert (in_tmp / "CMakeLists.txt").is_file()
        assert (in_tmp / "src" / "main" / "cc" / "MyDriver.cc").is_file()

    def test_rendered_top_has_no_leftover_jinja(self, in_tmp):
        _write_project(in_tmp)
        runner.invoke(app, ["generate"])
        top = (in_tmp / "src" / "main" / "scala" / "MyDesign02Top.scala").read_text()
        assert "{{" not in top and "{%" not in top


# ===========================================================================
# fslab clean
# ===========================================================================


class TestClean:
    def test_removes_generated_and_build(self, in_tmp):
        (in_tmp / "generated-src").mkdir()
        (in_tmp / "build").mkdir()
        (in_tmp / "generated-src" / "x.scala").write_text("x")
        result = runner.invoke(app, ["clean"])
        assert result.exit_code == 0, result.output
        assert not (in_tmp / "generated-src").exists()
        assert not (in_tmp / "build").exists()

    def test_keeps_state_without_all_flag(self, in_tmp):
        (in_tmp / ".fslab").mkdir()
        (in_tmp / "generated-src").mkdir()
        runner.invoke(app, ["clean"])
        assert (in_tmp / ".fslab").exists()

    def test_all_flag_removes_state(self, in_tmp):
        (in_tmp / ".fslab").mkdir()
        (in_tmp / "generated-src").mkdir()
        result = runner.invoke(app, ["clean", "--all"])
        assert result.exit_code == 0, result.output
        assert not (in_tmp / ".fslab").exists()

    def test_nothing_to_clean_is_ok(self, in_tmp):
        result = runner.invoke(app, ["clean"])
        assert result.exit_code == 0, result.output
