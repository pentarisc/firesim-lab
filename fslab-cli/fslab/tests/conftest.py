"""
tests/conftest.py
=================
Shared pytest fixtures for the fslab test suite.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Project directory fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    """
    A minimal but valid fslab project directory.

    Layout
    ------
    tmp/
    ├── fslab.yaml
    ├── registry.yaml
    └── src/main/scala/
    """
    fslab_yaml = tmp_path / "fslab.yaml"
    fslab_yaml.write_text(
        textwrap.dedent("""\
            project:
              name: test-design
              platform: f1
              target_config: "testDesign.TestDesignTargetConfig"
              gg_package: "firesim.midasexamples"
              gg_config: "DefaultF2Config"
              gen_file_basename: "FSLabTargetTop"
              target_dir: /target/test-design

            registries:
              - registry.yaml

            build:
              scala_version: "2.13"
              sbt_options: []
        """),
        encoding="utf-8",
    )

    registry_yaml = tmp_path / "registry.yaml"
    registry_yaml.write_text(
        textwrap.dedent("""\
            firesim_jar: /opt/firesim-lab/target/scala-2.13/firesim-lab.jar

            platforms:
              f1:
                description: "AWS F1 FPGA"
              f2:
                description: "AWS F2 FPGA"
        """),
        encoding="utf-8",
    )

    (tmp_path / "src" / "main" / "scala").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def state_dir(project_root: Path) -> Path:
    """Return the .fslab/ directory path (created on demand)."""
    d = project_root / ".fslab"
    d.mkdir(exist_ok=True)
    return d