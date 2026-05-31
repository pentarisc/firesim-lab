"""
fslab – CLI orchestrator for the MIDASII/GoldenGate hardware compiler.
"""

from pathlib import Path


def _version_from_pyproject(path: Path) -> str | None:
    """Read ``[project].version`` from a pyproject.toml, or None on failure."""
    try:
        import tomllib  # Python 3.11+

        with path.open("rb") as fh:
            return tomllib.load(fh).get("project", {}).get("version")
    except ModuleNotFoundError:
        # Python < 3.11: no tomllib. Fall back to a narrow regex. 'version ='
        # only matches the [project] key, not 'target-version'/'python_version'.
        import re

        text = path.read_text(encoding="utf-8")
        match = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', text)
        return match.group(1) if match else None
    except Exception:
        return None


def _resolve_version() -> str:
    """Single source of truth for the fslab version = pyproject.toml.

    firesim-lab always installs fslab from on-disk source (``pip install -e .``)
    and the dev workflow bind-mounts the working tree, so the adjacent
    pyproject.toml is present and authoritative — and avoids the stale-metadata
    trap of an editable install whose frozen dist-info has drifted from source.
    Falls back to installed package metadata (true wheel installs, no source),
    then to a sentinel.
    """
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if pyproject.is_file():
        version = _version_from_pyproject(pyproject)
        if version:
            return version
    try:
        from importlib.metadata import version as _meta_version

        return _meta_version("fslab")
    except Exception:
        return "0.0.0+unknown"


__version__ = _resolve_version()
__author__ = "Pentarisc Systems"