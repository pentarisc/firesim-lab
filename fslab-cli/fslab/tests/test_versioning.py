"""
tests/test_versioning.py
========================
Tests for the version-compatibility gate that protects project (``fslab.yaml``)
and registry (``registry.yaml``) files from being run by an incompatible
``fslab`` CLI.

Two layers:
  * unit tests for :mod:`fslab.utils.versioning` (the MINOR-boundary policy);
  * integration tests that drive the parser's file-loading gate end-to-end,
    asserting a friendly :class:`VersionMismatchError` rather than a downstream
    schema error.

The parser caches the first project path it validates in a module global; the
autouse ``_reset_parser_cache`` fixture clears it so each test's distinct
``tmp_path`` does not trip the "project mismatch" guard.
"""

from __future__ import annotations

from pathlib import Path

import yaml
import pytest

from fslab import __version__
from fslab.schemas import parser as _parser
from fslab.schemas.parser import load_and_validate
from fslab.utils.versioning import (
    VersionMismatchError,
    check_project_version,
    check_registry_version,
    is_compatible,
)

from .conftest import make_project_dict, make_registry_file_dict


@pytest.fixture(autouse=True)
def _reset_parser_cache():
    """Clear the parser's process-global path lock around each test."""
    _parser._LOADED_PATH = None
    _parser._CACHED_DATA = None
    yield
    _parser._LOADED_PATH = None
    _parser._CACHED_DATA = None


# ---------------------------------------------------------------------------
# Unit — is_compatible (MINOR boundary)
# ---------------------------------------------------------------------------

class TestIsCompatible:
    def test_same_version(self):
        assert is_compatible("0.7.0", "0.7.0")

    def test_patch_difference_is_compatible(self):
        assert is_compatible("0.7.0", "0.7.9")
        assert is_compatible("0.7.5", "0.7.0")

    def test_minor_difference_is_incompatible(self):
        assert not is_compatible("0.7.0", "0.8.0")
        assert not is_compatible("0.8.0", "0.7.0")

    def test_major_difference_is_incompatible(self):
        assert not is_compatible("0.7.0", "1.7.0")

    def test_missing_declared_is_incompatible(self):
        assert not is_compatible(None, "0.7.0")
        assert not is_compatible("", "0.7.0")

    def test_unparseable_is_incompatible(self):
        assert not is_compatible("not-a-version", "0.7.0")

    def test_build_metadata_and_v_prefix_ignored(self):
        assert is_compatible("v0.7.0", "0.7.0")
        assert is_compatible("0.7.0+fs.deadbeef", "0.7.3")

    def test_defaults_to_current_cli_version(self):
        # No explicit current → compares against the running CLI version.
        assert is_compatible(__version__)


# ---------------------------------------------------------------------------
# Unit — check_* raise with a friendly, migration-pointing message
# ---------------------------------------------------------------------------

class TestCheckRaises:
    def test_project_ok_when_matching(self):
        check_project_version(__version__, source="fslab.yaml")  # no raise

    def test_registry_ok_when_matching(self):
        check_registry_version(__version__, source="registry.yaml")  # no raise

    def test_project_raises_on_mismatch(self):
        with pytest.raises(VersionMismatchError) as exc:
            check_project_version("0.1.0", source="fslab.yaml")
        msg = str(exc.value)
        assert "fslab.yaml" in msg
        assert "0.1.0" in msg
        assert "Versioning & Upgrading" in msg

    def test_project_raises_on_missing(self):
        with pytest.raises(VersionMismatchError) as exc:
            check_project_version(None, source="fslab.yaml")
        assert "predates version stamping" in str(exc.value)

    def test_registry_raises_on_mismatch(self):
        with pytest.raises(VersionMismatchError):
            check_registry_version("0.1.0", source="registry.yaml")


# ---------------------------------------------------------------------------
# Integration — the parser file-loading gate
# ---------------------------------------------------------------------------

def _write_project(root: Path, *, project_version, registry_version) -> Path:
    """Write a valid on-disk project + local registry, overriding the
    ``fslab_version`` of each. Returns the fslab.yaml path."""
    (root / "user_rtl").mkdir(parents=True, exist_ok=True)
    (root / "user_rtl" / "top.v").write_text(
        "module my_counter(input clk, input rst);\nendmodule\n", encoding="utf-8"
    )

    reg = make_registry_file_dict()
    if registry_version is _OMIT:
        reg.pop("fslab_version", None)
    else:
        reg["fslab_version"] = registry_version
    reg_path = root / "registry.yaml"
    reg_path.write_text(yaml.safe_dump(reg, sort_keys=False), encoding="utf-8")

    proj = make_project_dict(root)
    proj["bridges"][0]["params"] = {"freq_mhz": 100, "baud_rate": 115200}
    proj["advanced"] = {"default_registry": str(reg_path)}
    if project_version is _OMIT:
        proj.pop("fslab_version", None)
    else:
        proj["fslab_version"] = project_version
    proj_path = root / "fslab.yaml"
    proj_path.write_text(yaml.safe_dump(proj, sort_keys=False), encoding="utf-8")
    return proj_path


_OMIT = object()  # sentinel: drop the field entirely (simulate a legacy file)


class TestParserGate:
    def test_matching_versions_load(self, tmp_path):
        proj_path = _write_project(
            tmp_path, project_version=__version__, registry_version=__version__
        )
        config, registry = load_and_validate(str(proj_path))
        assert config.fslab_version == __version__

    def test_incompatible_project_version_refused(self, tmp_path):
        proj_path = _write_project(
            tmp_path, project_version="0.1.0", registry_version=__version__
        )
        with pytest.raises(VersionMismatchError) as exc:
            load_and_validate(str(proj_path))
        assert "fslab.yaml" in str(exc.value)

    def test_missing_project_version_refused(self, tmp_path):
        proj_path = _write_project(
            tmp_path, project_version=_OMIT, registry_version=__version__
        )
        with pytest.raises(VersionMismatchError):
            load_and_validate(str(proj_path))

    def test_incompatible_registry_version_refused(self, tmp_path):
        # Project is fine; the custom registry is stale.
        proj_path = _write_project(
            tmp_path, project_version=__version__, registry_version="0.1.0"
        )
        with pytest.raises(VersionMismatchError) as exc:
            load_and_validate(str(proj_path))
        assert "registry" in str(exc.value)
