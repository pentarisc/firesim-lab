"""Monitor for in-flight bitstream builds.

Reads the local stamp written by `fslab build fpga`, SSHes to the
remote build host, and either tails the wrapper's log (build still
running) or runs the post-wrapper finalization poll (build done, AFI
building). Drives state transitions on the local stamp throughout.

State machine (driven by `stamp.status`):

  launching/running  → tail log over SSH until result.yaml appears,
                       then pull artifacts + run cleanup + flip to
                       finalizing (or wrapper_failed on rc!=0).
  finalizing         → poll BitBuilder.check_post_wrapper_status until
                       DONE/FAILED.
  terminal           → print summary and exit immediately.

Generic monitor primitives (SSH connect, remote-id verification,
tail-until-result, interruptible sleep, MonitorAborted / MonitorDetached)
live in `fslab.pipeline.monitor` so the upcoming run pipeline can reuse
them. This module owns only the build-specific state-machine glue and
the artifact-pull layout.

Ctrl+C semantics
----------------
In the tail phase, Ctrl+C closes the SSH session; the wrapper keeps
running on the remote (nohup). The local stamp is left untouched.
Re-run `fslab monitor build` to attach again.

In the finalize phase, Ctrl+C exits the poll loop. Stamp stays at
`finalizing` so the next monitor run resumes from where it stopped.

Cleanup timing
--------------
Cleanup of the EC2 instance (terminate / stop) runs at the wrapper-exit
transition — BEFORE the finalize poll loop starts. AFI polling therefore
happens with no remote resources held; the ~30-60 min AFI build has no
cost penalty even if the user stays attached.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import yaml

from fslab.pipeline.host import Host, RsyncFailed, cleanup_remote
from fslab.pipeline.monitor import (
    MonitorAborted,
    MonitorDetached,
    connect_external,
    interruptible_sleep,
    tail_remote_log_until_result,
    verify_remote_id,
)
from fslab.utils.display import error, info, section, success, warning

from .bitbuilder import PostStatus, make_bitbuilder
from .build_stamp import (
    BuildStamp,
    BuildStatus,
    _stamp_to_dict,
    read_stamp,
    stamp_path_for,
    utc_now_iso,
    write_stamp,
)
from .buildconfig import BuildConfig


# Default cadence for the finalize-phase poll. AWS AFI builds typically
# take 30-60 minutes, so 60 s strikes a balance between responsiveness
# and avoiding describe-fpga-images rate-limiting noise.
_FINALIZE_POLL_SECONDS = 60


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def monitor_build(project: Any, registry: Any) -> None:
    """Attach to the project's in-flight build (if any) and drive it to
    a terminal state — or until the user Ctrl+Cs.

    Raises:
      MonitorAborted — unrecoverable state (no stamp, mismatch, etc.)
      MonitorDetached — user Ctrl+C; the build is left running.
    """
    cfg = BuildConfig.from_validated(project, registry)

    stamp = read_stamp(cfg.project_dir)
    if stamp is None:
        raise MonitorAborted(
            f"No in-flight build found at "
            f"{stamp_path_for(cfg.project_dir)}. Run `fslab build fpga` "
            f"to launch one."
        )

    # Terminal status: nothing to do beyond a summary print.
    if stamp.status.is_terminal:
        _print_summary(stamp)
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
            stamp.build_id,
            id_field="build_id",
        )

        # If the wrapper is still alive on the remote, tail its log.
        if stamp.status in (BuildStatus.LAUNCHING, BuildStatus.RUNNING):
            _attach_to_running(host, stamp, cfg.project_dir)
            # _attach_to_running returns once the wrapper has exited
            # and the stamp has been transitioned (wrapper_failed or
            # finalizing). Re-read from disk.
            reread = read_stamp(cfg.project_dir)
            if reread is None:
                raise MonitorAborted("Stamp disappeared during monitoring.")
            stamp = reread

        # Run the post-wrapper poll loop iff the wrapper succeeded.
        if stamp.status == BuildStatus.FINALIZING:
            builder = make_bitbuilder(cfg, registry)
            _finalize_poll_loop(stamp, builder, cfg.project_dir)
            reread = read_stamp(cfg.project_dir)
            if reread is not None:
                stamp = reread

        _print_summary(stamp)
    finally:
        try:
            host.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Tail-and-wait (launching/running phase)
# ---------------------------------------------------------------------------


def _attach_to_running(
    host: Host, stamp: BuildStamp, project_dir: Path,
) -> None:
    """Tail the wrapper's log on the remote until result.yaml appears.

    Delegates the SSH tail/poll-loop mechanic to
    `fslab.pipeline.monitor.tail_remote_log_until_result`; this function
    owns the surrounding stamp transitions and the post-exit artifact
    pull + cleanup transition.
    """
    if stamp.status == BuildStatus.LAUNCHING:
        # Promote to running so any concurrent monitor probe sees a
        # consistent state. We landed here past `verify_remote_id`,
        # so the wrapper is genuinely alive.
        stamp.status = BuildStatus.RUNNING
        write_stamp(project_dir, stamp)

    section(f"Attached to build {stamp.build_id} on {stamp.remote.host}")
    info(f"Streaming {stamp.remote.remote_log_path} — Ctrl+C to detach.")

    tail_remote_log_until_result(
        host,
        log_path=stamp.remote.remote_log_path,
        result_path=stamp.remote.remote_result_yaml_path,
    )

    info("Wrapper exited — pulling artifacts and running cleanup…")
    _on_wrapper_exit(host, stamp, project_dir)


def _on_wrapper_exit(
    host: Host, stamp: BuildStamp, project_dir: Path,
) -> None:
    """Called once monitor detects the wrapper has exited. Pulls
    artifacts, runs cleanup, updates the stamp's status / finished_at /
    exit_code / result."""
    result = _pull_result_yaml(host, stamp)
    _pull_artifacts(host, stamp, project_dir)

    stamp.result = result
    stamp.finished_at = utc_now_iso()
    stamp.exit_code = result.get("exit_code")

    rc = result.get("exit_code")
    wrapper_ok = (
        result.get("status") == "succeeded"
        and (rc == 0 or rc is None)
    )
    stamp.status = (
        BuildStatus.FINALIZING if wrapper_ok else BuildStatus.WRAPPER_FAILED
    )

    # Cleanup — EC2 is no longer needed regardless of wrapper outcome.
    # Idempotent: re-running this on already-terminated resources is fine.
    if not stamp.cleanup_done:
        try:
            cleanup_remote({"cleanup": stamp.cleanup})
            stamp.cleanup_done = True
        except Exception as e:
            warning(
                f"Cleanup failed: {e}. Run `fslab abandon build` to retry."
            )

    write_stamp(project_dir, stamp)


def _pull_result_yaml(host: Host, stamp: BuildStamp) -> dict:
    """Read result.yaml from the remote into a dict. Tolerant of an
    unreadable/missing file (synthesizes a failure marker so the rest
    of the flow can still update the stamp consistently)."""
    r = host.run(
        f"cat {shlex.quote(stamp.remote.remote_result_yaml_path)}",
        warn=True, hide=True,
    )
    if r.return_code != 0:
        warning(
            f"Could not read remote result.yaml "
            f"(rc={r.return_code}). Treating as failure."
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


def _remote_dir_exists(host: Host, remote_path: str) -> bool:
    """Return True iff `remote_path` exists and is a directory on the
    remote. Uses `test -d` so a missing path is just rc!=0, not an SSH
    error."""
    probe = host.run(
        f"test -d {shlex.quote(remote_path)}", warn=True, hide=True,
    )
    return probe.return_code == 0


def _pull_artifacts(
    host: Host, stamp: BuildStamp, project_dir: Path,
) -> None:
    """Rsync the wrapper's `.fslab/` dir and the build's `reports/` back
    to the project. Best-effort — failures here are logged but don't
    abort the monitor (result.yaml is what drives the state machine).

    The `reports/` dir is gated on an SSH pre-check: failed builds may
    abort before Vivado writes the reports tree, and rsyncing a missing
    path is both noisy and leaves an empty local `reports/` on disk.
    """
    local_fslab_dir = project_dir / "build" / "fpga" / ".fslab"
    local_fslab_dir.mkdir(parents=True, exist_ok=True)

    # The remote `.fslab/` dir is the parent of the log path. It is
    # written by the wrapper before anything else, so we always attempt
    # the pull — failures here are genuinely exceptional.
    remote_fslab_dir = str(Path(stamp.remote.remote_log_path).parent)
    try:
        host.rsync_from(
            remote_fslab_dir + "/",
            str(local_fslab_dir) + "/",
            label="[rsync pull-fslab]",
        )
        info(f"Pulled wrapper artifacts → {local_fslab_dir}")
    except RsyncFailed as e:
        warning(f"Could not pull wrapper artifacts: {e}")

    # `build/reports/` lives under the remote cl_dir, which is two
    # levels up from the log file (cl_dir/.fslab/build.log). Only mkdir
    # the local target and run rsync if the remote dir actually exists.
    remote_cl_dir = str(Path(stamp.remote.remote_log_path).parent.parent)
    remote_reports = f"{remote_cl_dir}/build/reports"
    if not _remote_dir_exists(host, remote_reports):
        info(
            f"No build reports pulled from {remote_reports} "
            f"(directory does not exist — likely a failed build)."
        )
        return

    local_reports = project_dir / "build" / "fpga" / "reports"
    local_reports.mkdir(parents=True, exist_ok=True)
    try:
        host.rsync_from(
            remote_reports + "/",
            str(local_reports) + "/",
            label="[rsync pull-reports]",
        )
        info(f"Pulled build reports → {local_reports}")
    except RsyncFailed as e:
        warning(f"Could not pull build reports: {e}")


# ---------------------------------------------------------------------------
# Finalize-phase poll loop
# ---------------------------------------------------------------------------


def _finalize_poll_loop(
    stamp: BuildStamp, builder: Any, project_dir: Path,
) -> None:
    """Poll the BitBuilder's `check_post_wrapper_status` until DONE/FAILED.

    Each iteration: invoke the method, merge `info_dict` into
    `stamp.post_wrapper.*`, persist, sleep. Ctrl+C exits cleanly with
    status=finalizing intact so the next monitor invocation resumes.
    """
    section(f"Finalizing build {stamp.build_id} (post-wrapper poll)")

    while True:
        try:
            post_status, post_info = builder.check_post_wrapper_status(
                stamp.result, _stamp_to_dict(stamp),
            )
        except Exception as e:
            # Transient failures (rate limit, momentary auth blip) shouldn't
            # crash the monitor — log and retry.
            warning(
                f"Post-wrapper status check raised: {e} — retrying in "
                f"{_FINALIZE_POLL_SECONDS}s."
            )
            if interruptible_sleep(_FINALIZE_POLL_SECONDS):
                raise MonitorDetached() from None
            continue

        stamp.post_wrapper.last_checked_at = utc_now_iso()
        stamp.post_wrapper.state = post_info.get("state")
        stamp.post_wrapper.message = post_info.get("message")

        if post_status == PostStatus.DONE:
            stamp.status = BuildStatus.SUCCEEDED
            write_stamp(project_dir, stamp)
            return
        if post_status == PostStatus.FAILED:
            stamp.status = BuildStatus.FAILED
            write_stamp(project_dir, stamp)
            return

        # PENDING — persist progress + sleep + retry.
        write_stamp(project_dir, stamp)
        info(
            f"  post_wrapper.state={post_info.get('state', '?')!r} "
            f"{post_info.get('message', '')}"
        )
        if interruptible_sleep(_FINALIZE_POLL_SECONDS):
            info(
                "Detached during finalize. The post-wrapper phase continues "
                "on AWS-managed infra. Re-attach: `fslab monitor build`."
            )
            raise MonitorDetached() from None


# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------


def _print_summary(stamp: BuildStamp) -> None:
    """User-facing summary printed at the end of every monitor invocation
    that doesn't end in Ctrl+C."""
    section(f"Build {stamp.build_id} — {stamp.status.value}")
    info(f"  started_at:     {stamp.started_at}")
    info(f"  finished_at:    {stamp.finished_at or '(not finished)'}")
    info(
        f"  exit_code:      "
        f"{stamp.exit_code if stamp.exit_code is not None else '(none)'}"
    )
    info(f"  cleanup_done:   {stamp.cleanup_done}")
    if stamp.result:
        info("  result.yaml summary:")
        for k, v in stamp.result.items():
            info(f"    {k}: {v}")
    if stamp.post_wrapper.state is not None:
        info("  post_wrapper:")
        info(f"    state:           {stamp.post_wrapper.state}")
        info(f"    message:         {stamp.post_wrapper.message}")
        info(f"    last_checked_at: {stamp.post_wrapper.last_checked_at}")
    if stamp.status == BuildStatus.SUCCEEDED:
        success("Build completed successfully.")
    elif stamp.status in (BuildStatus.WRAPPER_FAILED, BuildStatus.FAILED):
        error("Build failed.")
    elif stamp.status == BuildStatus.ABANDONED:
        info("Build was abandoned.")
