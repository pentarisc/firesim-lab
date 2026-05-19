"""Monitor for in-flight detached FPGA runs.

Reads the local stamp written by `fslab sim fpga --detach`, SSHes to
the remote run host, and either tails the wrapper's driver.log (run
still running) or pulls results + runs cleanup (wrapper exited).

State machine (driven by `stamp.status`):

  launching/running  → tail driver.log over SSH until result.yaml
                       appears, then pull artifacts + run cleanup +
                       flip status to succeeded/failed.
  terminal           → print summary and exit immediately.

There is no finalize-phase poll loop (compare `bitstream.monitor`) —
the F2 run has no post-wrapper phase per D9; driver exit is terminal.

Ctrl+C semantics
----------------
In the tail phase, Ctrl+C closes the SSH session; the wrapper keeps
running on the remote (nohup'd). The local stamp is left untouched.
Re-run `fslab monitor run` to attach again.

Cleanup timing
--------------
Cleanup of the EC2 instance (terminate / stop) runs at the wrapper-exit
transition, AFTER artifacts are pulled. The wrapper itself does not
release the host — that's a local responsibility, gated on
`cleanup_done` in the stamp so a partially-completed cleanup can be
retried via `fslab abandon run`.

Terminal stamp disposal
-----------------------
Once the run reaches a terminal status AND `cleanup_done` is True, the
local stamp + staging dir are deleted: they exist for in-flight
tracking, not as historical record. The persistent forensic record
lives at `run/fpga/results/<ts>/{driver.log, result.yaml}` and is
never touched here. If cleanup_remote() failed during the monitor
attach (cleanup_done stays False), the stamp is preserved so the user
can `fslab abandon run` to retry — same contract as the build side.

Payload axis
------------
After the wrapper exits, monitor pulls each user-configured
`result_files[*]` entry into the timestamped results dir alongside
`driver.log` and `result.yaml`. The local `result.yaml` is enriched
with a `payloads:` forensics block before being written — matching
the foreground shape so downstream tooling sees one layout regardless
of how the run was launched.
"""

from __future__ import annotations

import shlex
import shutil
from pathlib import Path
from typing import Any

import yaml

from fslab.pipeline.host import RsyncFailed, cleanup_remote
from fslab.pipeline.monitor import (
    MonitorAborted,
    MonitorDetached,
    connect_external,
    tail_remote_log_until_result,
    verify_remote_id,
)
from fslab.pipeline.stamp import utc_now_iso
from fslab.utils.display import error, info, section, success, warning

from .payloads import forensics_block
from .run_stamp import (
    RunStamp,
    RunStatus,
    read_stamp,
    staging_path_for,
    stamp_path_for,
    wipe_stamp,
    write_stamp,
)
from .runconfig import RunConfig


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def monitor_run(project: Any, registry: Any) -> None:
    """Attach to this project's in-flight detached run (if any) and drive
    it to a terminal state — or until the user Ctrl+Cs.

    Raises:
      MonitorAborted  — unrecoverable state (no stamp, id mismatch, etc.)
      MonitorDetached — user Ctrl+C; the run is left running on the remote.
    """
    # RunConfig is resolved here so we have access to project_dir +
    # cfg.result_pulls() + cfg.resolved_payloads when pulling artifacts.
    # Resolution also gives a clear error if fslab.yaml has drifted out
    # from under the stamp (e.g. target.run was removed).
    cfg = RunConfig.from_validated(project, registry)

    stamp = read_stamp(cfg.project_dir)
    if stamp is None:
        raise MonitorAborted(
            f"No in-flight detached run found at "
            f"{stamp_path_for(cfg.project_dir)}. Run "
            f"`fslab sim fpga --detach` to launch one."
        )

    if stamp.status.is_terminal:
        _print_summary(stamp)
        _maybe_wipe_terminal_stamp(stamp, cfg.project_dir)
        return

    host = connect_external(
        host=stamp.remote.host,
        user=stamp.remote.user,
        ssh_key_path=stamp.remote.ssh_key_path,
    )
    try:
        verify_remote_id(
            host,
            stamp.remote.remote_stamp_path,
            stamp.run_id,
            id_field="run_id",
        )

        if stamp.status in (RunStatus.LAUNCHING, RunStatus.RUNNING):
            _attach_to_running(host, stamp, cfg)
            reread = read_stamp(cfg.project_dir)
            if reread is None:
                raise MonitorAborted("Stamp disappeared during monitoring.")
            stamp = reread

        _print_summary(stamp)
        _maybe_wipe_terminal_stamp(stamp, cfg.project_dir)
    finally:
        try:
            host.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Tail-and-wait (launching/running phase)
# ---------------------------------------------------------------------------


def _attach_to_running(host: Any, stamp: RunStamp, cfg: RunConfig) -> None:
    """Tail the wrapper's driver.log on the remote until result.yaml
    appears, then transition the stamp + pull artifacts + run cleanup.
    """
    if stamp.status == RunStatus.LAUNCHING:
        # Promote to running so any concurrent monitor probe sees a
        # consistent state. We landed here past `verify_remote_id`, so
        # the wrapper is genuinely alive.
        stamp.status = RunStatus.RUNNING
        write_stamp(cfg.project_dir, stamp)

    section(f"Attached to run {stamp.run_id} on {stamp.remote.host}")
    info(f"Streaming {stamp.remote.remote_log_path} — Ctrl+C to detach.")

    tail_remote_log_until_result(
        host,
        log_path=stamp.remote.remote_log_path,
        result_path=stamp.remote.remote_result_yaml_path,
    )

    info("Wrapper exited — pulling artifacts and running cleanup…")
    _on_wrapper_exit(host, stamp, cfg)


def _on_wrapper_exit(host: Any, stamp: RunStamp, cfg: RunConfig) -> None:
    """Called once monitor detects the wrapper has exited. Pulls
    artifacts, runs cleanup, updates the stamp's terminal status."""
    result = _pull_result_yaml(host, stamp)
    _pull_artifacts(host, stamp, cfg, result)

    stamp.result = result
    stamp.finished_at = utc_now_iso()
    stamp.exit_code = result.get("exit_code")

    rc = result.get("exit_code")
    wrapper_ok = (
        result.get("status") == "succeeded"
        and (rc == 0 or rc is None)
    )
    stamp.status = RunStatus.SUCCEEDED if wrapper_ok else RunStatus.FAILED

    # Cleanup — the host is no longer needed regardless of wrapper outcome.
    # Idempotent: re-running this on already-terminated resources is fine.
    if not stamp.cleanup_done:
        try:
            cleanup_remote({"cleanup": stamp.cleanup})
            stamp.cleanup_done = True
        except Exception as e:
            warning(
                f"Cleanup failed: {e}. Run `fslab abandon run` to retry."
            )

    write_stamp(cfg.project_dir, stamp)


def _pull_result_yaml(host: Any, stamp: RunStamp) -> dict:
    """Read result.yaml from the remote into a dict. Tolerant of an
    unreadable/missing file (synthesises a failure marker so the rest of
    the flow can still update the stamp consistently)."""
    r = host.run(
        f"cat {shlex.quote(stamp.remote.remote_result_yaml_path)}",
        warn=True, hide=True,
    )
    if r.return_code != 0:
        warning(
            f"Could not read remote result.yaml (rc={r.return_code}). "
            f"Treating as failure."
        )
        return {
            "status": "failed",
            "exit_code": -1,
            "failure": {"stage": "result_yaml_read", "message": "unreadable"},
        }
    try:
        data = yaml.safe_load(r.stdout) or {}
        if isinstance(data, dict):
            return data
        warning("Remote result.yaml is not a mapping; treating as failure.")
    except yaml.YAMLError as e:
        warning(f"Could not parse remote result.yaml: {e}")
    return {
        "status": "failed",
        "exit_code": -1,
        "failure": {"stage": "result_yaml_parse", "message": "invalid yaml"},
    }


def _pull_artifacts(
    host: Any, stamp: RunStamp, cfg: RunConfig, result: dict,
) -> None:
    """Pull `driver.log`, `result.yaml`, and any user-configured
    `result_files` from the remote into a fresh timestamped results dir
    under `run/fpga/results/`. Best-effort: failures are warnings, not
    aborts, since the result.yaml in `result` already drives the state
    machine."""
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    results_dir = cfg.project_dir / "run" / "fpga" / "results" / ts
    results_dir.mkdir(parents=True, exist_ok=True)

    # Pull driver.log.
    try:
        host.rsync_from(
            stamp.remote.remote_log_path,
            str(results_dir / "driver.log"),
            label="[rsync pull-driver-log]",
        )
        info(f"Pulled driver.log → {results_dir / 'driver.log'}")
    except RsyncFailed as e:
        warning(f"Could not pull driver.log: {e}")

    # Pull each user-configured result_file. Missing files are warnings,
    # not aborts — the driver may legitimately skip writing them on an
    # early exit (e.g. a hash_verify failure aborts before the driver
    # has a chance to produce anything).
    for remote_abs, local_name in cfg.result_pulls():
        try:
            host.rsync_from(
                remote_abs,
                str(results_dir / local_name),
                label=f"[rsync pull-{local_name}]",
            )
            info(f"Pulled {local_name} → {results_dir / local_name}")
        except RsyncFailed as e:
            warning(f"Could not pull result_file {local_name!r}: {e}")

    # Enrich result.yaml with payload forensics so the local copy matches
    # the foreground shape (the wrapper does not emit this block — the
    # data is known at launch time and lives in cfg.resolved_payloads).
    result_enriched = dict(result)
    result_enriched["payloads"] = forensics_block(cfg.resolved_payloads)

    # Write the (enriched) result.yaml into the same results dir, for
    # parity with foreground mode and for offline inspection by tooling
    # that just opens the local results tree.
    try:
        (results_dir / "result.yaml").write_text(
            yaml.safe_dump(
                result_enriched, default_flow_style=False, sort_keys=False,
            ),
            encoding="utf-8",
        )
    except OSError as e:
        warning(f"Could not write local result.yaml: {e}")


# ---------------------------------------------------------------------------
# Terminal stamp disposal
# ---------------------------------------------------------------------------


def _maybe_wipe_terminal_stamp(stamp: RunStamp, project_dir: Path) -> None:
    """Once the run is terminal and the remote is fully cleaned up, the
    local stamp + staging dir are no longer load-bearing — delete them
    so the in-flight guard in `fslab sim fpga --detach` doesn't trip on
    history.

    If `cleanup_done` is False (cleanup_remote failed earlier), preserve
    everything so the user can `fslab abandon run` to retry the cleanup.
    The persistent forensic record lives under `run/fpga/results/<ts>/`
    and is untouched either way.
    """
    if not (stamp.status.is_terminal and stamp.cleanup_done):
        return

    try:
        wipe_stamp(project_dir)
    except OSError as e:
        warning(f"Could not remove local stamp: {e}")

    staging = staging_path_for(project_dir)
    if staging.is_dir():
        try:
            shutil.rmtree(staging)
        except OSError as e:
            warning(f"Could not remove staging dir {staging}: {e}")


# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------


def _print_summary(stamp: RunStamp) -> None:
    """User-facing summary printed at the end of every monitor invocation
    that doesn't end in Ctrl+C."""
    section(f"Run {stamp.run_id} — {stamp.status.value}")
    info(f"  started_at:   {stamp.started_at}")
    info(f"  finished_at:  {stamp.finished_at or '(not finished)'}")
    info(
        f"  exit_code:    "
        f"{stamp.exit_code if stamp.exit_code is not None else '(none)'}"
    )
    info(f"  cleanup_done: {stamp.cleanup_done}")
    info(f"  agfi:         {stamp.run.agfi}")
    if stamp.result:
        info("  result.yaml summary:")
        for k, v in stamp.result.items():
            info(f"    {k}: {v}")
    if stamp.status == RunStatus.SUCCEEDED:
        success("Run completed successfully.")
    elif stamp.status == RunStatus.FAILED:
        error("Run failed.")
    elif stamp.status == RunStatus.ABANDONED:
        info("Run was abandoned.")
