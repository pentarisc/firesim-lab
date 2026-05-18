"""Local stamp file for in-flight detached FPGA runs.

A single stamp lives at `<project>/run/fpga/.fslab/run.yaml` and tracks
the lifecycle of one background run. Run-side counterpart to
`fslab.bitstream.build_stamp.BuildStamp`; the two share the same
`cleanup:` block (provider-discriminated via
`fslab.pipeline.host.PROVIDER_REGISTRY`).

Lifecycle:

  - `fslab sim fpga --detach` — initial write at launch (status=launching),
                                flips to status=running once verify-started
                                succeeds.
  - `fslab monitor run`       — status transitions wrapper→succeeded/failed,
                                cleanup_done flip after teardown.
  - `fslab abandon run`       — status=abandoned, cleanup_done flip after
                                teardown.

Single in-flight per project (D8 in the run-pipeline handoff): the
stamp path is fixed, no multi-run directory, no run-id arg on monitor.

run_id format: `r-<utc-ts>-<short-rand>` e.g. `r-20260516T161234Z-a3f2`.
Chronologically sortable, prefix `r-` distinguishes it from build_ids
in logs.

Atomicity: writes go to `run.yaml.tmp` then `replace()` onto the final
path — same pattern as the build stamp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml


# Relative path under the project root. Fixed by D8: one in-flight run
# per project, so there is no need to disambiguate by run_id in the path.
STAMP_REL_PATH = Path("run/fpga/.fslab/run.yaml")

# Staging dir for the just-in-time-rendered run wrapper. Wiped at the
# start of every detached run (per D2 in the run-pipeline handoff).
STAGING_REL_PATH = Path("run/fpga/staging")


# ---------------------------------------------------------------------------
# Status enum
# ---------------------------------------------------------------------------


class RunStatus(str, Enum):
    """Stamp-level lifecycle status, orchestrator-driven.

      launching  fslab is staging inputs and about to launch the wrapper
                 on the remote. Stamp exists; remote may not yet have a
                 matching run_id stamp.
      running    Wrapper PID is alive on the remote. Monitor tails
                 driver.log in this state.
      succeeded  Wrapper exited 0 — terminal.
      failed     Wrapper exited non-zero — terminal.
      abandoned  User invoked `fslab abandon run`. Terminal.

    There is no FINALIZING / WRAPPER_FAILED split (compare BuildStatus)
    because the F2 run has no post-wrapper phase — driver exit is the
    completion signal (per D9 in the run-pipeline handoff).
    """
    LAUNCHING = "launching"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATES


_TERMINAL_STATES = frozenset({
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.ABANDONED,
})


# ---------------------------------------------------------------------------
# Nested blocks
# ---------------------------------------------------------------------------


@dataclass
class RemoteInfo:
    """Everything the monitor needs to reconnect to the remote and locate
    the wrapper's outputs. Captured at launch and never re-derived from
    live config — survives changes to fslab.yaml mid-run.

    Same shape as `fslab.bitstream.build_stamp.RemoteInfo` (run.yaml and
    build.yaml share the layout); we define it locally rather than
    importing the build-side dataclass to keep the package boundaries
    clean."""
    host: str
    user: str
    ssh_key_path: Optional[str]
    remote_log_path: str           # driver.log on the remote
    remote_result_yaml_path: str   # .fslab/result.yaml on the remote
    remote_pid_path: str           # .fslab/pid on the remote
    remote_stamp_path: str         # .fslab/run.yaml on the remote


@dataclass
class RunInfo:
    """Platform + identifying details for the in-flight run. Carried in
    the stamp so a user inspecting run.yaml can see what the run is for
    without cross-referencing fslab.yaml."""
    platform: str
    project_name: str
    quintuplet: str
    agfi: str
    runner_args: dict = field(default_factory=dict)
    """Serialized runner_args dict (the user-supplied YAML block).
    Carried for forensics; the wrapper itself receives the
    runner-relevant subset via env vars at launch time."""


# ---------------------------------------------------------------------------
# Top-level stamp
# ---------------------------------------------------------------------------


@dataclass
class RunStamp:
    """All persisted state for one in-flight detached run.

    `cleanup` is provider-discriminated — its `provider` key matches a
    name in `fslab.pipeline.host.PROVIDER_REGISTRY`. The orchestrator
    never reads inside `cleanup` beyond that discriminator; it hands the
    whole dict to `cleanup_remote()`.

    `result` is populated by `result.yaml` pulled from the remote after
    wrapper exit; freeform per platform.
    """
    run_id: str
    started_at: str
    status: RunStatus

    remote: RemoteInfo
    run: RunInfo
    cleanup: dict

    finished_at: Optional[str] = None
    exit_code: Optional[int] = None
    cleanup_done: bool = False
    result: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def stamp_path_for(project_dir: Path) -> Path:
    """Absolute path to this project's run stamp file. Parent directory
    is not guaranteed to exist; `write_stamp` creates it automatically."""
    return project_dir / STAMP_REL_PATH


def staging_path_for(project_dir: Path) -> Path:
    """Absolute path to this project's run-wrapper staging dir. Wiped at
    the start of every detached run."""
    return project_dir / STAGING_REL_PATH


# ---------------------------------------------------------------------------
# Read / write / wipe
# ---------------------------------------------------------------------------


def read_stamp(project_dir: Path) -> Optional[RunStamp]:
    """Read and decode the stamp file. Returns None if it doesn't exist.

    Raises `yaml.YAMLError` on parse failure and `KeyError`/`TypeError`
    on shape mismatch. Callers decide whether to treat as 'no stamp' or
    'corrupt stamp; require --abandon' — we don't swallow the error
    here so a human looks at it.
    """
    path = stamp_path_for(project_dir)
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return _stamp_from_dict(data)


def write_stamp(project_dir: Path, stamp: RunStamp) -> None:
    """Atomically write the stamp to disk.

    Mkdir -p the parent dir, write to `<path>.tmp`, then rename onto the
    final path. Survives crash mid-write: readers see either the
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
    """Delete the stamp file if it exists. Idempotent."""
    path = stamp_path_for(project_dir)
    path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _stamp_to_dict(stamp: RunStamp) -> dict:
    return {
        "run_id": stamp.run_id,
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
        "run": {
            "platform": stamp.run.platform,
            "project_name": stamp.run.project_name,
            "quintuplet": stamp.run.quintuplet,
            "agfi": stamp.run.agfi,
            "runner_args": stamp.run.runner_args,
        },
        "cleanup": stamp.cleanup,
        "result": stamp.result,
    }


def _stamp_from_dict(data: dict) -> RunStamp:
    remote = data["remote"]
    run = data["run"]
    return RunStamp(
        run_id=data["run_id"],
        started_at=data["started_at"],
        status=RunStatus(data["status"]),
        remote=RemoteInfo(
            host=remote["host"],
            user=remote["user"],
            ssh_key_path=remote.get("ssh_key_path"),
            remote_log_path=remote["remote_log_path"],
            remote_result_yaml_path=remote["remote_result_yaml_path"],
            remote_pid_path=remote["remote_pid_path"],
            remote_stamp_path=remote["remote_stamp_path"],
        ),
        run=RunInfo(
            platform=run["platform"],
            project_name=run["project_name"],
            quintuplet=run["quintuplet"],
            agfi=run["agfi"],
            runner_args=run.get("runner_args") or {},
        ),
        cleanup=data["cleanup"],
        finished_at=data.get("finished_at"),
        exit_code=data.get("exit_code"),
        cleanup_done=data.get("cleanup_done", False),
        result=data.get("result") or {},
    )
