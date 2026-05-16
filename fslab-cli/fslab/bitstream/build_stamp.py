"""Local stamp file for in-flight bitstream builds.

A single stamp lives at `<project>/build/fpga/.fslab/build.yaml` and
tracks the lifecycle of one background build. Schema is platform-generic;
the `cleanup` block is provider-discriminated (see
`buildhost.PROVIDER_REGISTRY`) and the `result` block is platform-
discriminated (the BitBuilder's `check_post_wrapper_status` reads whatever
shape its wrapper script produces).

The stamp is read+written by:

  - `fslab build fpga`     — initial write at launch (status=launching),
                             then status=running once verified-started.
  - `fslab monitor build`  — status transitions wrapper→finalizing→
                             succeeded/failed, post_wrapper updates each
                             poll cycle, cleanup_done flip after teardown.
  - `fslab abandon build`  — status=abandoned, cleanup_done flip after
                             teardown.

Single in-flight per project (design decision D2): the stamp path is
fixed, no multi-build directory, no build-id argument on monitor/abandon.

Build_id format: `<utc-ts>-<short-rand>` e.g. `20260514T161234Z-a3f2`.
Chronologically sortable for log readability, opaque enough to
discriminate restarts, no extra dependency.

Atomicity: writes go to `build.yaml.tmp` then `replace()` onto the final
path. A torn write can leave behind the .tmp file, never a half-baked
build.yaml — readers will see either the previous good stamp or the new
good stamp.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import yaml


# Relative path under the project root. Fixed by D2: one in-flight build
# per project, so there is no need to disambiguate by build_id in the path.
STAMP_REL_PATH = Path("build/fpga/.fslab/build.yaml")


# ---------------------------------------------------------------------------
# Status enum
# ---------------------------------------------------------------------------


class BuildStatus(str, Enum):
    """Stamp-level lifecycle status, orchestrator-driven.

      launching       fslab is staging inputs and about to launch the
                      wrapper on the remote. Stamp exists; remote may not
                      yet have a matching build_id stamp.
      running         Wrapper PID is alive on the remote. Monitor tails
                      build.log in this state.
      wrapper_failed  Wrapper exited non-zero — terminal. Cleanup may or
                      may not have run yet (see `cleanup_done`).
      finalizing      Wrapper exited 0; monitor is polling the platform's
                      post-wrapper status (e.g. AFI build for F2). Remote
                      EC2 has already been released — finalizing is
                      cloud-side work, no local resources held.
      succeeded       Post-wrapper terminal success, or the platform has
                      no post-wrapper step. Terminal.
      failed          Post-wrapper terminal failure. Terminal.
      abandoned       User invoked `fslab abandon build`. Terminal.
    """
    LAUNCHING = "launching"
    RUNNING = "running"
    WRAPPER_FAILED = "wrapper_failed"
    FINALIZING = "finalizing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATES


_TERMINAL_STATES = frozenset({
    BuildStatus.WRAPPER_FAILED,
    BuildStatus.SUCCEEDED,
    BuildStatus.FAILED,
    BuildStatus.ABANDONED,
})


# ---------------------------------------------------------------------------
# Nested blocks
# ---------------------------------------------------------------------------


@dataclass
class RemoteInfo:
    """Everything the monitor needs to reconnect to the remote and locate
    the wrapper's outputs. Captured at launch and never re-derived from
    live config — survives changes to fslab.yaml mid-build."""
    host: str
    user: str
    ssh_key_path: Optional[str]
    remote_log_path: str
    remote_result_yaml_path: str
    remote_pid_path: str
    remote_stamp_path: str


@dataclass
class BuildInfo:
    """Platform-agnostic build identification. Carried in the stamp so a
    user inspecting build.yaml can see what the in-flight build is for
    without cross-referencing fslab.yaml."""
    platform: str
    project_name: str
    quintuplet: str
    fpga_frequency: Optional[int] = None
    build_strategy: Optional[str] = None


@dataclass
class PostWrapper:
    """Generic post-wrapper poll state. Populated by the monitor each time
    BitBuilder.check_post_wrapper_status returns.

      last_checked_at  ISO8601-UTC timestamp of the last poll
      state            platform-specific string (e.g. for F2: 'pending',
                       'available', 'failed') — orchestrator does not
                       interpret this; it just surfaces it
      message          human-readable detail (e.g. AFI state description)
    """
    last_checked_at: Optional[str] = None
    state: Optional[str] = None
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# Top-level stamp
# ---------------------------------------------------------------------------


@dataclass
class BuildStamp:
    """All persisted state for one in-flight build.

    `cleanup` is provider-discriminated — its `provider` key matches a
    name in `buildhost.PROVIDER_REGISTRY`. The orchestrator never reads
    inside `cleanup` beyond that discriminator; it hands the whole dict
    to `cleanup_remote()`.

    `result` is platform-discriminated — populated by `result.yaml`
    pulled from the remote after wrapper exit. The orchestrator reads
    only `status`/`exit_code` from result.yaml itself; the rest is for
    the BitBuilder's `check_post_wrapper_status` and for user display.
    """
    build_id: str
    started_at: str
    status: BuildStatus

    remote: RemoteInfo
    build: BuildInfo
    cleanup: dict

    finished_at: Optional[str] = None
    exit_code: Optional[int] = None
    cleanup_done: bool = False
    result: dict = field(default_factory=dict)
    post_wrapper: PostWrapper = field(default_factory=PostWrapper)


# ---------------------------------------------------------------------------
# build_id generation
# ---------------------------------------------------------------------------


def make_build_id(now: Optional[datetime] = None) -> str:
    """Generate a chronologically-sortable, human-scannable build id.

    Format: `<utc-ts>-<short-rand>`, e.g. `20260514T161234Z-a3f2`.
    The 4-hex-char suffix uses `secrets.token_hex` (~16 bits of entropy)
    which is more than enough for the single-in-flight-per-project
    model — its purpose is to discriminate restarts in the same second,
    not to be cryptographically unguessable.

    `now` is injectable for tests; defaults to current UTC time.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(2)  # 4 hex characters
    return f"{ts}-{suffix}"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def stamp_path_for(project_dir: Path) -> Path:
    """Absolute path to this project's stamp file. Parent directory is
    not guaranteed to exist; callers that write must mkdir first (the
    `write_stamp` helper does this automatically)."""
    return project_dir / STAMP_REL_PATH


# ---------------------------------------------------------------------------
# Read / write / wipe
# ---------------------------------------------------------------------------


def read_stamp(project_dir: Path) -> Optional[BuildStamp]:
    """Read and decode the stamp file. Returns None if it doesn't exist.

    Raises `yaml.YAMLError` on parse failure and `KeyError`/`TypeError`
    on shape mismatch — callers (`fslab build fpga` in-flight guard)
    decide whether to treat as 'no stamp' or 'corrupt stamp; require
    --abandon'. We deliberately do NOT swallow the error: a stamp the
    code can't parse is one a human needs to look at.
    """
    path = stamp_path_for(project_dir)
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return _stamp_from_dict(data)


def write_stamp(project_dir: Path, stamp: BuildStamp) -> None:
    """Atomically write the stamp to disk.

    Mkdir -p the parent dir, write to `<path>.tmp`, then rename onto the
    final path. Survives crash mid-write: a reader sees either the
    previous good stamp or the new good stamp, never a torn file.
    """
    path = stamp_path_for(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            _stamp_to_dict(stamp),
            f,
            default_flow_style=False,
            sort_keys=False,
        )
    tmp.replace(path)


def wipe_stamp(project_dir: Path) -> None:
    """Delete the stamp file if it exists. Idempotent.

    Used by `fslab build fpga` when starting fresh (after the in-flight
    guard clears or `--abandon` has run), and by `fslab abandon build`
    after cleanup succeeds.
    """
    path = stamp_path_for(project_dir)
    path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


# Explicit dict construction (rather than `dataclasses.asdict`) so the
# YAML key order is fixed regardless of dataclass field-default ordering
# constraints (Optional fields with defaults have to come after required
# ones in @dataclass, but we want the user-facing YAML to put
# build_id/started_at/status at the top).
def _stamp_to_dict(stamp: BuildStamp) -> dict:
    return {
        "build_id": stamp.build_id,
        "started_at": stamp.started_at,
        "finished_at": stamp.finished_at,
        "status": stamp.status.value,
        "exit_code": stamp.exit_code,
        "cleanup_done": stamp.cleanup_done,
        "remote": {
            "host": stamp.remote.host,
            "user": stamp.remote.user,
            "ssh_key_path": stamp.remote.ssh_key_path,
            "remote_log_path": stamp.remote.remote_log_path,
            "remote_result_yaml_path": stamp.remote.remote_result_yaml_path,
            "remote_pid_path": stamp.remote.remote_pid_path,
            "remote_stamp_path": stamp.remote.remote_stamp_path,
        },
        "build": {
            "platform": stamp.build.platform,
            "project_name": stamp.build.project_name,
            "quintuplet": stamp.build.quintuplet,
            "fpga_frequency": stamp.build.fpga_frequency,
            "build_strategy": stamp.build.build_strategy,
        },
        "cleanup": stamp.cleanup,
        "result": stamp.result,
        "post_wrapper": {
            "last_checked_at": stamp.post_wrapper.last_checked_at,
            "state": stamp.post_wrapper.state,
            "message": stamp.post_wrapper.message,
        },
    }


def _stamp_from_dict(data: dict) -> BuildStamp:
    remote = data["remote"]
    build = data["build"]
    pw = data.get("post_wrapper") or {}
    return BuildStamp(
        build_id=data["build_id"],
        started_at=data["started_at"],
        status=BuildStatus(data["status"]),
        remote=RemoteInfo(
            host=remote["host"],
            user=remote["user"],
            ssh_key_path=remote.get("ssh_key_path"),
            remote_log_path=remote["remote_log_path"],
            remote_result_yaml_path=remote["remote_result_yaml_path"],
            remote_pid_path=remote["remote_pid_path"],
            remote_stamp_path=remote["remote_stamp_path"],
        ),
        build=BuildInfo(
            platform=build["platform"],
            project_name=build["project_name"],
            quintuplet=build["quintuplet"],
            fpga_frequency=build.get("fpga_frequency"),
            build_strategy=build.get("build_strategy"),
        ),
        cleanup=data["cleanup"],
        finished_at=data.get("finished_at"),
        exit_code=data.get("exit_code"),
        cleanup_done=data.get("cleanup_done", False),
        result=data.get("result") or {},
        post_wrapper=PostWrapper(
            last_checked_at=pw.get("last_checked_at"),
            state=pw.get("state"),
            message=pw.get("message"),
        ),
    )


# ---------------------------------------------------------------------------
# Utility: ISO8601-UTC timestamp string
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    """ISO8601 UTC timestamp string (seconds precision, `Z` suffix).

    Shared helper so every stamp-touching code path produces identical
    timestamp formatting — easy to grep, easy to compare lexically.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
