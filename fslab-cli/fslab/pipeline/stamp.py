"""Pipeline-agnostic stamp utilities.

Both the build-side stamp (`build/fpga/.fslab/build.yaml`) and the
forthcoming run-side stamp (`run/fpga/.fslab/run.yaml`) share a common
set of trivia: ISO-8601 UTC timestamps, atomic stamp writes, opaque
id generation. The full stamp dataclasses themselves live with their
respective pipelines (`fslab.bitstream.build_stamp` today; run side
will add a sibling) because the schemas differ — only the genuinely
common primitives are factored here.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now_iso() -> str:
    """ISO8601 UTC timestamp string (seconds precision, `Z` suffix).

    Shared helper so every stamp-touching code path produces identical
    timestamp formatting — easy to grep, easy to compare lexically.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
