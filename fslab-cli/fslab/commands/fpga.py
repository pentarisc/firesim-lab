"""
fslab/commands/fpga.py
======================
[CLI-15] ``fslab build``   – trigger FPGA synthesis with a Rich spinner.
[CLI-16] ``fslab archive`` – create a .tar.gz snapshot respecting ignore rules.
"""

from __future__ import annotations

import fnmatch
import os
import tarfile
import time
from pathlib import Path
from typing import Optional

import typer

from fslab.utils.display import console, error, info, section, success, warning
from fslab.utils.shell import run_with_spinner, run_or_die
from fslab.utils.state import StateManager

app = typer.Typer(rich_markup_mode="rich")

# ===========================================================================
# [CLI-16]  fslab archive
# ===========================================================================
@app.command("archive")
def cmd_archive(
    tag: str = typer.Option(
        ...,
        "--tag",
        "-t",
        prompt="Archive tag",
        help="Label for this archive snapshot (e.g. [italic]milestone-v1[/]).",
    ),
    yaml_path: Path = typer.Option(
        Path("fslab.yaml"),
        "--config",
        "-c",
        help="Path to the project YAML.",
    ),
    output_dir: Path = typer.Option(
        Path("archives"),
        "--output",
        "-o",
        help="Directory to write the archive into.",
    ),
) -> None:
    """
    Create a [italic].tar.gz[/] snapshot of the project.

    Respects [italic].fslabignore[/] (or [italic].gitignore[/] if the former
    is absent) to exclude large build artefacts (e.g. 50 GB FPGA targets).
    """
    section("fslab archive")

    project_root = yaml_path.resolve().parent
    ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    output_dir = output_dir if output_dir.is_absolute() else project_root / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    archive_name = f"{project_root.name}-{tag}-{ts}.tar.gz"
    archive_path = output_dir / archive_name

    # Load ignore patterns
    patterns = _load_ignore_patterns(project_root)
    if patterns:
        info(f"Using {len(patterns)} ignore pattern(s) from ignore file.")

    info(f"Creating [path]{archive_path.relative_to(project_root)}[/]…")

    file_count = 0
    with tarfile.open(archive_path, "w:gz") as tar:
        for abs_path in sorted(project_root.rglob("*")):
            rel = abs_path.relative_to(project_root)
            rel_str = str(rel)

            # Always exclude the archives/ directory itself and .fslab/logs
            if rel_str.startswith("archives") or rel_str.startswith(".fslab/logs"):
                continue

            if _is_ignored(rel_str, patterns):
                continue

            tar.add(abs_path, arcname=rel_str, recursive=False)
            file_count += 1

    success(
        f"Archive created: [path]{archive_path.name}[/]  "
        f"([dim]{file_count} files, "
        f"{archive_path.stat().st_size // 1024} KB[/])"
    )


def _load_ignore_patterns(project_root: Path) -> list[str]:
    """
    [CLI-16] Load exclusion patterns from ``.fslabignore`` (preferred) or
    ``.gitignore`` (fallback).

    Lines starting with ``#`` and blank lines are ignored.
    """
    for candidate in [".fslabignore", ".gitignore"]:
        ignore_file = project_root / candidate
        if ignore_file.exists():
            lines = ignore_file.read_text(encoding="utf-8").splitlines()
            return [
                ln.strip()
                for ln in lines
                if ln.strip() and not ln.startswith("#")
            ]
    return []


def _is_ignored(rel_path: str, patterns: list[str]) -> bool:
    """Return True if *rel_path* matches any glob pattern in *patterns*."""
    for pattern in patterns:
        # fnmatch handles simple globs; prefix-match handles directory patterns
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        # Treat patterns like "build/" as prefix matches
        stripped = pattern.rstrip("/")
        if rel_path == stripped or rel_path.startswith(stripped + os.sep):
            return True
    return False