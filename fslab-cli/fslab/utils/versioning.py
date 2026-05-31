"""
fslab/utils/versioning.py
=========================
Version-compatibility checks for the user's project (`fslab.yaml`) and registry
(`registry.yaml`) files against the running fslab CLI.

Policy (pre-1.0 SemVer)
-----------------------
A file is compatible with the CLI iff they share the same ``MAJOR.MINOR``:

* ``0.7.x`` projects/registries run on a ``0.7.y`` CLI (patch differences are
  always compatible — patches never change the schema).
* A differing ``MAJOR`` or ``MINOR`` is refused. Pre-1.0 SemVer permits breaking
  changes on minor bumps, so ``0.6.x`` / ``0.8.x`` are treated as incompatible.

Files with **no** ``fslab_version`` field are refused: they predate version
stamping and must be migrated explicitly. In all refusal cases the user is
directed to the *Versioning & Upgrading* guide; migration is manual by design.

The single source of truth for the CLI version is ``fslab.__version__``
(resolved from the installed package metadata, i.e. ``pyproject.toml``).
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from fslab import __version__

# Field name carried by both fslab.yaml and registry.yaml.
VERSION_FIELD = "fslab_version"

# Matches a leading 'X.Y' (optionally 'vX.Y'), ignoring any patch / pre-release
# / build-metadata suffix. Only MAJOR.MINOR participates in the compatibility
# decision, so the rest is deliberately discarded.
_VERSION_RE = re.compile(r"^\s*v?(\d+)\.(\d+)")


class VersionMismatchError(Exception):
    """Raised when a project or registry file's version is incompatible.

    Carries a human-readable, migration-pointing message; CLI command handlers
    surface it directly rather than as a traceback.
    """


def _major_minor(version: Optional[str]) -> Optional[Tuple[int, int]]:
    """Return ``(major, minor)`` for a version string, or ``None`` if absent
    or unparseable."""
    if not version:
        return None
    match = _VERSION_RE.match(str(version))
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def is_compatible(declared: Optional[str], current: str = __version__) -> bool:
    """True iff ``declared`` shares the running CLI's MAJOR.MINOR.

    A missing or unparseable ``declared`` version is never compatible.
    """
    declared_mm = _major_minor(declared)
    current_mm = _major_minor(current)
    if declared_mm is None or current_mm is None:
        return False
    return declared_mm == current_mm


def _build_message(kind: str, declared: Optional[str], source: str) -> str:
    declared_str = declared if declared else "(none — predates version stamping)"
    if kind == "project":
        what = "project file (fslab.yaml)"
        hint = (
            "Migrate the file to the new schema and set its "
            f"'{VERSION_FIELD}' field, as described in the "
            "'Versioning & Upgrading' guide in the documentation."
        )
    else:
        what = "registry file"
        hint = (
            "Reconcile any registry schema changes and set its "
            f"'{VERSION_FIELD}' field, as described in the "
            "'Versioning & Upgrading' guide in the documentation."
        )
    return (
        f"Incompatible {what}:\n"
        f"  file                : {source}\n"
        f"  declared {VERSION_FIELD} : {declared_str}\n"
        f"  this fslab version  : {__version__}\n"
        "\n"
        f"firesim-lab requires the {kind} file to match this CLI's MAJOR.MINOR "
        f"version.\n{hint}"
    )


def check_project_version(declared: Optional[str], *, source: str) -> None:
    """Raise :class:`VersionMismatchError` if a project's version is
    incompatible with the running CLI."""
    if is_compatible(declared):
        return
    raise VersionMismatchError(_build_message("project", declared, source))


def check_registry_version(declared: Optional[str], *, source: str) -> None:
    """Raise :class:`VersionMismatchError` if a registry file's version is
    incompatible with the running CLI."""
    if is_compatible(declared):
        return
    raise VersionMismatchError(_build_message("registry", declared, source))
