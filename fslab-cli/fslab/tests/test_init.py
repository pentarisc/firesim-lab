"""
tests/test_init.py
==================
Tests for fslab/commands/init.py

Coverage targets
----------------
[CLI-11] _inline_yaml produces valid YAML with correct camelCase derivation
         Scaffold directories are created correctly
         Duplicate names raise an error without --force
"""

from __future__ import annotations

import yaml
from pathlib import Path

import pytest

from fslab.commands.init import _inline_yaml


# ===========================================================================
# name_camel derivation
# ===========================================================================


class TestInlineYaml:
    """[CLI-11] _inline_yaml must never reference undefined variables."""

    @pytest.mark.parametrize(
        "name, expected_camel",
        [
            ("my-design",       "MyDesign"),
            ("my-design-02",    "MyDesign02"),
            ("simple",          "Simple"),
            ("multi_word_name", "MultiWordName"),
            ("a-b-c",           "ABC"),
        ],
    )
    def test_camel_case_derivation(self, name: str, expected_camel: str) -> None:
        content = _inline_yaml(name=name, platform="f1")
        parsed = yaml.safe_load(content)
        top = parsed["project"]["fslab_top"]
        assert expected_camel in top, (
            f"Expected camelCase '{expected_camel}' inside fslab_top={top!r}"
        )

    def test_output_is_valid_yaml(self) -> None:
        content = _inline_yaml(name="test-proj", platform="f1")
        # Should not raise
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict)

    def test_name_appears_in_output(self) -> None:
        content = _inline_yaml(name="hello-world", platform="vitis_u250")
        assert "hello-world" in content

    def test_platform_appears_in_output(self) -> None:
        content = _inline_yaml(name="proj", platform="f2")
        parsed = yaml.safe_load(content)
        assert parsed["project"]["platform"] == "f2"

    def test_registries_list_present(self) -> None:
        content = _inline_yaml(name="proj", platform="f1")
        parsed = yaml.safe_load(content)
        assert "registries" in parsed
        assert isinstance(parsed["registries"], list)
        assert len(parsed["registries"]) >= 1

    def test_no_unformatted_braces(self) -> None:
        """Guard against the original bug: {name_camel} left un-substituted."""
        content = _inline_yaml(name="my-design-02", platform="f1")
        assert "{name_camel}" not in content
        assert "{name}" not in content
        assert "{platform}" not in content