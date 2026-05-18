"""Detached-run launcher.

`fslab sim fpga --detach` entry point. Resolves `RunConfig`, requests a
run host through the pipeline-level provider, stages the just-in-time
rendered run wrapper, rsyncs driver + wrapper to the remote slot dir,
nohup-launches the wrapper, writes the local stamp, verifies the wrapper
actually started, and returns the new `run_id` to the caller.

Cleanup is *not* performed on success — the wrapper keeps the host alive
until the driver exits. Cleanup runs later via `fslab monitor run`
(wrapper-exit transition) or `fslab abandon run`.

Mirrors `fslab.bitstream.bitbuilder.build_bitstream` in shape; the
shared mechanics (provider.serialize_cleanup_state, nohup launch with
`< /dev/null` stdin redirect, verify-started poll) come from Phase 1.
"""

from __future__ import annotations

import shlex
import shutil
import time
from pathlib import Path
from typing import Optional

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from fslab.pipeline.host import Host, make_host_provider
from fslab.pipeline.stamp import utc_now_iso
from fslab.schemas.artifact_source import AwsAfiArtifactSourceConfig
from fslab.utils.display import error, info, section, success, warning

from .runconfig import RunConfig
from .runner import RunSimulationFailed, make_run_id
from .run_stamp import (
    RemoteInfo,
    RunInfo,
    RunStamp,
    RunStatus,
    read_stamp,
    stamp_path_for,
    staging_path_for,
    wipe_stamp,
    write_stamp,
)


# Where on the remote the wrapper's PID + stamp + result.yaml live. Mirrors
# F2BitBuilder._REMOTE_FSLAB_SUBDIR on the build side (the dotted-fslab
# convention).
_REMOTE_FSLAB_SUBDIR = ".fslab"

# Filename for the rendered + uploaded wrapper.
_REMOTE_WRAPPER_NAME = "remote_run_f2.sh"

# Verify-started poll cadence + timeout. The wrapper writes the remote
# stamp as its very first step, so it usually shows up within a second
# or two. 10s gives slow links + cold-cache file systems a comfortable
# margin without making startup feel laggy.
_VERIFY_TIMEOUT_S = 10
_VERIFY_POLL_S = 0.5


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def launch_detached(project: object, registry: object) -> str:
    """Launch the F2 run wrapper in the background on a remote host.

    Returns the new `run_id` (also recorded in the local stamp).
    Raises `RunSimulationFailed` for setup-time errors before the wrapper
    actually backgrounds.
    """
    cfg = RunConfig.from_validated(project, registry)
    agfi = _require_aws_afi_agfi(cfg)

    # In-flight guard. Defense-in-depth: the CLI does this check too
    # before any expensive work; here it covers direct callers.
    check_no_existing_run(cfg.project_dir)

    provider = make_host_provider(cfg)
    host = provider.request(cfg)
    cleanup_state: Optional[dict] = None
    stamp_written = False
    try:
        host.connect()
        # Snapshot cleanup state immediately — once a stamp is written,
        # all cleanup goes through cleanup_remote() from this dict, not
        # via provider.release().
        cleanup_state = provider.serialize_cleanup_state(host, cfg)

        run_id = make_run_id()

        # --- stage the wrapper locally + remote layout ---------------
        local_wrapper = _stage_wrapper(cfg, run_id)
        remote_slot_dir = cfg.remote_slot_dir
        remote_fslab_dir = f"{remote_slot_dir}/{_REMOTE_FSLAB_SUBDIR}"
        remote_driver_path = f"{remote_slot_dir}/{cfg.driver_basename}"
        remote_wrapper_path = f"{remote_slot_dir}/{_REMOTE_WRAPPER_NAME}"
        remote_log_path = f"{remote_fslab_dir}/driver.log"
        remote_result_path = f"{remote_fslab_dir}/result.yaml"
        remote_pid_path = f"{remote_fslab_dir}/pid"
        remote_stamp_path = f"{remote_fslab_dir}/run.yaml"

        host.run(
            f"mkdir -p {shlex.quote(remote_slot_dir)} "
            f"{shlex.quote(remote_fslab_dir)}"
        )

        # Truncate any stale driver.log + result.yaml from a previous run
        # on the same slot dir so monitor doesn't latch onto stale state.
        host.run(
            f": > {shlex.quote(remote_log_path)} && "
            f"rm -f {shlex.quote(remote_result_path)} "
            f"{shlex.quote(remote_pid_path)} "
            f"{shlex.quote(remote_stamp_path)}",
            warn=True,
        )

        info(f"Uploading driver: {cfg.local_driver_path.name}")
        host.put(str(cfg.local_driver_path), remote_driver_path)
        host.run(f"chmod +x {shlex.quote(remote_driver_path)}")

        info(f"Uploading wrapper: {local_wrapper.name}")
        host.put(str(local_wrapper), remote_wrapper_path)
        host.run(f"chmod +x {shlex.quote(remote_wrapper_path)}")

        # --- compose env vars + initial local stamp ----------------------
        env = _compose_wrapper_env(
            cfg=cfg,
            run_id=run_id,
            agfi=agfi,
            remote_slot_dir=remote_slot_dir,
            remote_stamp_path=remote_stamp_path,
            remote_log_path=remote_log_path,
            remote_result_path=remote_result_path,
        )
        stamp = _initial_local_stamp(
            cfg=cfg,
            run_id=run_id,
            agfi=agfi,
            host=host,
            cleanup_state=cleanup_state,
            remote_log_path=remote_log_path,
            remote_result_path=remote_result_path,
            remote_pid_path=remote_pid_path,
            remote_stamp_path=remote_stamp_path,
        )
        write_stamp(cfg.project_dir, stamp)
        stamp_written = True

        # --- nohup-launch the wrapper ------------------------------------
        _launch_remote_wrapper(
            host,
            wrapper_path=remote_wrapper_path,
            slot_dir=remote_slot_dir,
            log_path=remote_log_path,
            pid_path=remote_pid_path,
            env=env,
        )

        # --- verify-started + flip to running ----------------------------
        _verify_wrapper_started(
            host, run_id, remote_stamp_path, timeout_s=_VERIFY_TIMEOUT_S,
        )
        stamp.status = RunStatus.RUNNING
        write_stamp(cfg.project_dir, stamp)

        success(f"Detached run {run_id} launched on {_host_label(host)}.")
        info(
            f"Local stamp: {stamp_path_for(cfg.project_dir)} | "
            f"remote log: {remote_log_path}"
        )
        info("Re-attach with `fslab monitor run`.")
        return run_id

    except Exception:
        # Failure-mode cleanup. If we haven't written a stamp yet, fall
        # back to provider.release(); if we have, mark the stamp failed
        # and leave it in place so the user can see what happened and
        # invoke `fslab abandon run` to clean up the host.
        if stamp_written and cleanup_state is not None:
            try:
                stamp_now = read_stamp(cfg.project_dir)
                if stamp_now is not None:
                    stamp_now.status = RunStatus.FAILED
                    stamp_now.finished_at = utc_now_iso()
                    write_stamp(cfg.project_dir, stamp_now)
            except Exception as ce:
                warning(f"Could not mark stamp failed during cleanup: {ce}")
        else:
            try:
                provider.release(host)
            except Exception as re:
                warning(f"provider.release() failed during error recovery: {re}")
        raise

    finally:
        # Always close the SSH connection. On success the wrapper is
        # already nohup'd; on failure the connection is no longer needed.
        # This does NOT terminate the remote instance.
        try:
            host.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# In-flight guard
# ---------------------------------------------------------------------------


def check_no_existing_run(project_dir: Path) -> None:
    """Refuse to start a new detached run if a real in-flight stamp exists.

    A stamp counts as "in-flight" when it is non-terminal, or when it is
    terminal but `cleanup_done` is False (cleanup_remote previously
    failed and the remote may still be live). In those cases the user is
    pointed at `fslab monitor run` (to attach) or `fslab abandon run`
    (to discard).

    A stamp that is terminal AND has `cleanup_done == True` is history,
    not state — typically the residue of a `fslab monitor run` that
    encountered an unclean exit between cleanup and stamp wipe. We
    silently wipe it (plus the staging dir) and let the new run proceed,
    matching the contract that `cleanup_done` means "the remote is
    fully released."

    Mirrors `fslab.bitstream.bitbuilder.check_no_existing_build`.
    """
    stamp = read_stamp(project_dir)
    if stamp is None:
        return

    if stamp.status.is_terminal and stamp.cleanup_done:
        # Self-heal: the prior run is fully done; this stamp is just
        # leftover history.
        info(
            f"Discarding completed run stamp for {stamp.run_id} "
            f"({stamp.status.value}, cleanup_done) before starting new run."
        )
        try:
            wipe_stamp(project_dir)
        except OSError as e:
            warning(f"Could not remove stale stamp: {e}")

        staging = staging_path_for(project_dir)
        if staging.is_dir():
            try:
                shutil.rmtree(staging)
            except OSError as e:
                warning(f"Could not remove stale staging dir {staging}: {e}")
        return

    stamp_file = stamp_path_for(project_dir)
    raise RunSimulationFailed(
        f"In-flight run {stamp.run_id} ({stamp.status.value}) is "
        f"recorded at {stamp_file}.\n"
        f"  -> Run `fslab monitor run` to attach, or "
        f"`fslab abandon run` to discard and clean up the remote."
    )


# ---------------------------------------------------------------------------
# Wrapper staging + env composition
# ---------------------------------------------------------------------------


def _stage_wrapper(cfg: RunConfig, run_id: str) -> Path:
    """Wipe `run/fpga/staging/` and render the run wrapper into it.

    Per D2: staging is per-detached-run scratch; foreground runs don't
    touch it. The wrapper is re-rendered on every detach so the project-
    static Jinja substitutions (platform_id, project_name, fpga_slot)
    always reflect the current `fslab.yaml`.
    """
    staging = staging_path_for(cfg.project_dir)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    # Templates live in fslab-cli/fslab/templates/. Resolve relative to
    # this file: runtime/launch.py → fslab-cli/fslab/templates/.
    templates_root = Path(__file__).parent.parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_root)),
        autoescape=select_autoescape([]),
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
    )
    tmpl = env.get_template("remote_run/f2.sh.j2")
    rendered = tmpl.render(
        platform_id=cfg.platform_id,
        project_name=cfg.project_name,
        fpga_slot=0,  # Single-node, single-slot today; reserved for future.
        run_id=run_id,
    )

    out_path = staging / _REMOTE_WRAPPER_NAME
    out_path.write_text(rendered, encoding="utf-8")
    return out_path


def _compose_wrapper_env(
    *,
    cfg: RunConfig,
    run_id: str,
    agfi: str,
    remote_slot_dir: str,
    remote_stamp_path: str,
    remote_log_path: str,
    remote_result_path: str,
) -> dict:
    """Build the env-var dict the wrapper script consumes.

    Project-static fields (platform, project_name, fpga_slot) are baked
    into the script body by Jinja. Per-run values flow through this dict.
    """
    ra = cfg.runner_args
    extra_flags = list(getattr(ra, "extra_driver_flags", []) or [])
    return {
        "RUN_ID": run_id,
        "AGFI": agfi,
        "SLOT_DIR": remote_slot_dir,
        "STAMP_PATH": remote_stamp_path,
        "LOG_PATH": remote_log_path,
        "RESULT_PATH": remote_result_path,
        "DRIVER_BASENAME": cfg.driver_basename,
        "MAX_CYCLES": (
            str(ra.max_cycles)
            if getattr(ra, "max_cycles", None) is not None
            else ""
        ),
        # The wrapper word-splits EXTRA_FLAGS; pre-join here. Each entry
        # is expected to be a single flag token like "+plusarg=val".
        "EXTRA_FLAGS": " ".join(extra_flags),
    }


# ---------------------------------------------------------------------------
# Launch + verify-started
# ---------------------------------------------------------------------------


def _launch_remote_wrapper(
    host: Host,
    *,
    wrapper_path: str,
    slot_dir: str,
    log_path: str,
    pid_path: str,
    env: dict,
) -> None:
    """nohup-launch the wrapper script in the background on the remote.

    Mirrors the build-side `F2BitBuilder.launch_remote_wrapper`. The
    critical `< /dev/null` stdin redirect is what lets paramiko's exec
    channel close — without it, this `run()` call hangs locally while
    the wrapper happily executes on the remote.

    A `bash -lc` wrapper around the launch line ensures the wrapper
    runs under a login shell, matching the build-side fix in
    `_run_bootstrap` (the FPGA Developer AMI's PATH is otherwise
    incomplete for non-login shells).
    """
    env_prefix = " ".join(
        f"{k}={shlex.quote(str(v))}" for k, v in env.items()
    )
    launch_cmd = (
        f"cd {shlex.quote(slot_dir)} && "
        f"set -m; {env_prefix} "
        f"nohup bash {shlex.quote(wrapper_path)} "
        f"< /dev/null > {shlex.quote(log_path)} 2>&1 & "
        f"echo $! > {shlex.quote(pid_path)}"
    )
    host.run(f"bash -lc {shlex.quote(launch_cmd)}")

    r = host.run(f"cat {shlex.quote(pid_path)}", warn=True, hide=True)
    pid_str = (r.stdout or "").strip()
    if not pid_str.isdigit():
        raise RunSimulationFailed(
            f"Could not read wrapper PID from {pid_path}: got "
            f"{pid_str!r}. The wrapper may not have launched."
        )
    info(f"Wrapper launched on {_host_label(host)} (pid={pid_str})")


def _verify_wrapper_started(
    host: Host,
    run_id: str,
    remote_stamp_path: str,
    *,
    timeout_s: int = 10,
) -> None:
    """Poll the remote stamp file until its run_id matches ours or the
    timeout elapses. Raises `RunSimulationFailed` on timeout — typically
    means the wrapper failed to start (permission denied, missing
    `fpga-load-local-image`, etc.; the user finds details in
    `driver.log` on the remote)."""
    deadline = time.monotonic() + timeout_s
    last_err = ""
    while time.monotonic() < deadline:
        r = host.run(
            f"cat {shlex.quote(remote_stamp_path)}",
            warn=True, hide=True,
        )
        if r.return_code == 0:
            try:
                data = yaml.safe_load(r.stdout) or {}
                if isinstance(data, dict) and data.get("run_id") == run_id:
                    return
                last_err = (
                    f"remote stamp present but run_id mismatch "
                    f"(remote: {data.get('run_id') if isinstance(data, dict) else '?'!r}, "
                    f"local: {run_id!r})"
                )
            except yaml.YAMLError as e:
                last_err = f"remote stamp could not be parsed: {e}"
        time.sleep(_VERIFY_POLL_S)
    raise RunSimulationFailed(
        f"Verify-started timed out after {timeout_s}s — the wrapper does "
        f"not appear to have begun. Last error: {last_err or 'no remote stamp'}.\n"
        f"  -> Check driver.log on the remote at the path recorded in "
        f"the local stamp; the wrapper itself logs to that file."
    )


# ---------------------------------------------------------------------------
# Initial stamp + helpers
# ---------------------------------------------------------------------------


def _initial_local_stamp(
    *,
    cfg: RunConfig,
    run_id: str,
    agfi: str,
    host: Host,
    cleanup_state: dict,
    remote_log_path: str,
    remote_result_path: str,
    remote_pid_path: str,
    remote_stamp_path: str,
) -> RunStamp:
    """Build the stamp written before the wrapper is launched. Status is
    `launching`; the orchestrator flips it to `running` once verify-
    started succeeds."""
    params = getattr(host, "params", None)

    # Serialise runner_args back to a plain dict for forensics.
    ra = cfg.runner_args
    runner_args_dict = ra.model_dump() if hasattr(ra, "model_dump") else {}

    return RunStamp(
        run_id=run_id,
        started_at=utc_now_iso(),
        status=RunStatus.LAUNCHING,
        remote=RemoteInfo(
            host=getattr(params, "host", "?") if params else "?",
            user=getattr(params, "user", "?") if params else "?",
            ssh_key_path=(
                str(params.ssh_key) if params and params.ssh_key else None
            ),
            remote_log_path=remote_log_path,
            remote_result_yaml_path=remote_result_path,
            remote_pid_path=remote_pid_path,
            remote_stamp_path=remote_stamp_path,
        ),
        run=RunInfo(
            platform=cfg.platform_id,
            project_name=cfg.project_name,
            quintuplet=cfg.quintuplet,
            agfi=agfi,
            runner_args=runner_args_dict,
        ),
        cleanup=cleanup_state,
    )


def _require_aws_afi_agfi(cfg: RunConfig) -> str:
    """Extract the AGFI from `cfg.artifact_source`.

    ARTSRC-01 has already validated the schema, but the F2 detached path
    is explicit about its dependence on aws_afi (forward-compatible with
    `local_tarball` and `hwdb_entry` types that will need rejection here
    when they land)."""
    src = cfg.artifact_source
    if not isinstance(src, AwsAfiArtifactSourceConfig):
        raise RunSimulationFailed(
            f"F2 detached runs require artifact_source.type='aws_afi'; "
            f"got '{getattr(src, 'type', '?')}'."
        )
    return src.agfi


def _host_label(host: Host) -> str:
    params = getattr(host, "params", None)
    if params is None:
        return "<remote>"
    user = getattr(params, "user", "?")
    h = getattr(params, "host", "?")
    return f"{user}@{h}"
