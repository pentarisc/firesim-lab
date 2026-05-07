"""Deterministic ${KEY} placeholder substitution.

Lives here (rather than in `build/`) because it has no fpga-build-specific
semantics — it just substitutes a known set of keys and explicitly does not
touch the OS environment, unlike `os.path.expandvars`.
"""

from __future__ import annotations

from typing import Mapping


def substitute(value: str, subs: Mapping[str, str]) -> str:
    """Replace every `${KEY}` occurrence in `value` with `subs[KEY]`.

    Only the keys present in `subs` are touched — unknown placeholders are
    left intact so misspellings surface downstream rather than silently
    becoming empty strings (the failure mode of `os.path.expandvars`).
    """
    out = value
    for k, v in subs.items():
        out = out.replace(f"${{{k}}}", v)
    return out