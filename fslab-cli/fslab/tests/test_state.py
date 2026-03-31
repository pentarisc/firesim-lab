"""
tests/test_state.py
===================
Tests for fslab/utils/state.py

Coverage targets
----------------
[CLI-05] StateManager.ensure_dirs() creates .fslab/ and .fslab/logs/
[CLI-06] compute_config_hash() is deterministic and path/content-sensitive
[CLI-07] is_generation_needed() correctly detects hash changes
         check_and_maybe_skip_generation() respects --force and --dry-run
"""

from __future__ import annotations

import json
import textwrap
import time
from pathlib import Path

import pytest

from fslab.utils.state import StateManager, check_and_maybe_skip_generation


# ===========================================================================
# [CLI-05] Directory bootstrap
# ===========================================================================


class TestEnsureDirs:
    def test_creates_state_dir(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        sm.ensure_dirs()
        assert sm.state_dir.is_dir()

    def test_creates_log_dir(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        sm.ensure_dirs()
        assert sm.log_dir.is_dir()

    def test_creates_gitignore(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        sm.ensure_dirs()
        gitignore = sm.state_dir / ".gitignore"
        assert gitignore.exists()
        assert "*" in gitignore.read_text()

    def test_idempotent(self, project_root: Path) -> None:
        """Calling ensure_dirs() twice must not raise."""
        sm = StateManager(project_root)
        sm.ensure_dirs()
        sm.ensure_dirs()  # second call – no exception

    def test_defaults_to_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        sm = StateManager()
        assert sm.project_root == tmp_path.resolve()


# ===========================================================================
# [CLI-06] Hash computation
# ===========================================================================


class TestComputeConfigHash:
    def test_returns_64_char_hex(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        h = sm.compute_config_hash(
            project_root / "fslab.yaml",
            [project_root / "registry.yaml"],
        )
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic_across_calls(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        args = (project_root / "fslab.yaml", [project_root / "registry.yaml"])
        h1 = sm.compute_config_hash(*args)
        h2 = sm.compute_config_hash(*args)
        assert h1 == h2

    def test_deterministic_regardless_of_registry_order(self, project_root: Path) -> None:
        """[CLI-06] Sorted paths → hash is order-independent."""
        extra = project_root / "extra_registry.yaml"
        extra.write_text("extra: true\n", encoding="utf-8")
        sm = StateManager(project_root)
        h1 = sm.compute_config_hash(
            project_root / "fslab.yaml",
            [project_root / "registry.yaml", extra],
        )
        h2 = sm.compute_config_hash(
            project_root / "fslab.yaml",
            [extra, project_root / "registry.yaml"],
        )
        assert h1 == h2, "Hash must be order-independent"

    def test_changes_when_fslab_yaml_content_changes(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        yaml = project_root / "fslab.yaml"
        registry = [project_root / "registry.yaml"]

        h1 = sm.compute_config_hash(yaml, registry)
        yaml.write_text(yaml.read_text() + "\n# changed\n", encoding="utf-8")
        h2 = sm.compute_config_hash(yaml, registry)

        assert h1 != h2, "Content change must change the hash"

    def test_changes_when_registry_content_changes(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        yaml = project_root / "fslab.yaml"
        reg = project_root / "registry.yaml"

        h1 = sm.compute_config_hash(yaml, [reg])
        reg.write_text(reg.read_text() + "\n# registry changed\n", encoding="utf-8")
        h2 = sm.compute_config_hash(yaml, [reg])

        assert h1 != h2

    def test_changes_when_registry_added(self, project_root: Path) -> None:
        """Adding a new registry file must change the hash."""
        sm = StateManager(project_root)
        yaml = project_root / "fslab.yaml"
        reg = project_root / "registry.yaml"

        h1 = sm.compute_config_hash(yaml, [])
        h2 = sm.compute_config_hash(yaml, [reg])

        assert h1 != h2

    def test_two_same_content_files_differ_by_path(self, project_root: Path) -> None:
        """Two files with identical content at different paths must yield different hashes."""
        a = project_root / "reg_a.yaml"
        b = project_root / "reg_b.yaml"
        content = "platforms:\n  f1: {}\n"
        a.write_text(content, encoding="utf-8")
        b.write_text(content, encoding="utf-8")

        sm = StateManager(project_root)
        yaml = project_root / "fslab.yaml"
        h_a = sm.compute_config_hash(yaml, [a])
        h_b = sm.compute_config_hash(yaml, [b])

        assert h_a != h_b, "Path must be mixed into the hash"

    def test_raises_file_not_found(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        with pytest.raises(FileNotFoundError, match="file not found"):
            sm.compute_config_hash(
                project_root / "nonexistent.yaml",
                [],
            )


# ===========================================================================
# [CLI-07] is_generation_needed
# ===========================================================================


class TestIsGenerationNeeded:
    def test_true_when_no_state_file(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        assert sm.is_generation_needed("abc123") is True

    def test_false_when_hash_matches(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        sm.ensure_dirs()
        sm.save("deadbeef")
        assert sm.is_generation_needed("deadbeef") is False

    def test_true_when_hash_differs(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        sm.ensure_dirs()
        sm.save("hash_v1")
        assert sm.is_generation_needed("hash_v2") is True

    def test_true_when_state_file_corrupt(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        sm.ensure_dirs()
        sm.state_file.write_text("{ not valid json !!!", encoding="utf-8")
        # Should not raise – falls back to treating as first run
        assert sm.is_generation_needed("any_hash") is True


# ===========================================================================
# [CLI-05] Save / load round-trip
# ===========================================================================


class TestSaveLoad:
    def test_round_trip(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        sm.ensure_dirs()
        sm.save("deadcafe", extra={"foo": "bar"})

        state = sm.load()
        assert state["config_hash"] == "deadcafe"
        assert state["foo"] == "bar"
        assert "saved_at" in state
        assert "fslab_version" in state

    def test_load_returns_empty_dict_when_missing(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        assert sm.load() == {}

    def test_atomic_write(self, project_root: Path) -> None:
        """After save(), no .tmp file should remain on disk."""
        sm = StateManager(project_root)
        sm.ensure_dirs()
        sm.save("abc")
        tmp = sm.state_file.with_suffix(".json.tmp")
        assert not tmp.exists(), ".tmp file should be cleaned up after atomic rename"

    def test_save_overwrites_previous(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        sm.ensure_dirs()
        sm.save("first_hash")
        sm.save("second_hash")
        assert sm.load()["config_hash"] == "second_hash"


# ===========================================================================
# [CLI-07] check_and_maybe_skip_generation convenience function
# ===========================================================================


class TestCheckAndMaybeSkipGeneration:
    def _call(self, project_root: Path, **kwargs):
        return check_and_maybe_skip_generation(
            fslab_yaml_path=project_root / "fslab.yaml",
            registry_yaml_paths=[project_root / "registry.yaml"],
            project_root=project_root,
            **kwargs,
        )

    def test_first_run_returns_should_generate_true(self, project_root: Path) -> None:
        should_gen, _, _ = self._call(project_root)
        assert should_gen is True

    def test_unchanged_returns_should_generate_false(self, project_root: Path) -> None:
        # Simulate a previous successful generate by saving the current hash
        sm = StateManager(project_root)
        current_hash = sm.compute_config_hash(
            project_root / "fslab.yaml",
            [project_root / "registry.yaml"],
        )
        sm.ensure_dirs()
        sm.save(current_hash)

        should_gen, _, _ = self._call(project_root)
        assert should_gen is False

    def test_force_always_returns_should_generate_true(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        current_hash = sm.compute_config_hash(
            project_root / "fslab.yaml",
            [project_root / "registry.yaml"],
        )
        sm.ensure_dirs()
        sm.save(current_hash)

        # Even though hash matches, --force must override
        should_gen, _, _ = self._call(project_root, force=True)
        assert should_gen is True

    def test_dry_run_returns_false_regardless_of_hash(self, project_root: Path) -> None:
        """[CLI-12] dry_run must never write files or trigger rendering."""
        should_gen, _, _ = self._call(project_root, dry_run=True)
        assert should_gen is False

    def test_dry_run_does_not_write_state_file(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        self._call(project_root, dry_run=True)
        # The state file must NOT have been created
        assert not sm.state_file.exists()

    def test_returns_state_manager_instance(self, project_root: Path) -> None:
        _, _, sm = self._call(project_root)
        assert isinstance(sm, StateManager)

    def test_returns_hex_hash_string(self, project_root: Path) -> None:
        _, h, _ = self._call(project_root)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ===========================================================================
# log_file() helper
# ===========================================================================


class TestLogFile:
    def test_returns_path_inside_log_dir(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        p = sm.log_file("sbt-package")
        assert p.parent == sm.log_dir

    def test_name_contains_label_and_timestamp(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        p = sm.log_file("my-step")
        assert "my-step" in p.name
        # Timestamp pattern: 8 digits T 6 digits
        import re
        assert re.search(r"\d{8}T\d{6}", p.name)

    def test_log_dir_created(self, project_root: Path) -> None:
        sm = StateManager(project_root)
        # log_dir does not exist yet
        assert not sm.log_dir.exists()
        sm.log_file("build")
        assert sm.log_dir.exists()