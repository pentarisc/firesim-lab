"""Pipeline-agnostic monitor primitives.

The build-side `monitor_build` and the upcoming run-side `monitor_run`
share a small set of mechanics — connect over SSH to a stamp-recorded
host, cross-check an opaque id against the remote stamp, tail a remote
log file until a result-yaml appears, and let Ctrl+C cleanly detach
without killing the remote workload. Those primitives live here so
each pipeline's user-facing entry point can stay focused on its own
state-machine transitions.
"""

from __future__ import annotations

import shlex
import time
from typing import Optional

import yaml

from fslab.schemas.host_model import ExternalHostConfig
from fslab.utils.display import info

from .host import ExternalHost, Host, RemoteCommandFailed


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MonitorAborted(Exception):
    """Raised when a monitor cannot proceed — no stamp, id mismatch,
    or other unrecoverable state. The CLI surfaces this as a non-zero
    exit with the message."""


class MonitorDetached(Exception):
    """Raised on Ctrl+C / clean detach. The CLI prints a friendly
    "detached" message and exits zero; the workload continues on the
    remote, ready to be re-attached via another `fslab monitor …`."""


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def connect_external(
    host: str,
    user: str,
    ssh_key_path: Optional[str],
) -> Host:
    """Open an SSH connection to a stamp-recorded host.

    Uses ExternalHost regardless of how the host was originally provisioned —
    the monitor only needs SSH/run/rsync; it never launches or terminates
    the host (that goes through `cleanup_remote` once the workload exits).

    `remote_platform_path` is irrelevant for monitor probes; a dummy
    absolute path is supplied to satisfy `ExternalHostConfig` validation.
    """
    params = ExternalHostConfig(
        type="external",
        host=host,
        user=user,
        ssh_key=ssh_key_path,
        remote_platform_path="/tmp",
    )
    h = ExternalHost(params)
    h.connect()
    return h


# ---------------------------------------------------------------------------
# Remote-id verification
# ---------------------------------------------------------------------------


def verify_remote_id(
    host: Host,
    remote_stamp_path: str,
    expected_id: str,
    *,
    id_field: str = "build_id",
) -> None:
    """Cross-check the remote stamp's id against the local one.

    Mismatch typically means the remote work dir was reused by another
    project, or someone manually changed state. Abort cleanly rather
    than streaming someone else's log lines.

    `id_field` selects between `build_id` (build pipeline) and `run_id`
    (run pipeline). Other pipelines can pick their own field name.
    """
    r = host.run(
        f"cat {shlex.quote(remote_stamp_path)}",
        warn=True, hide=True,
    )
    if r.return_code != 0:
        raise MonitorAborted(
            f"Remote stamp not found at {remote_stamp_path}. "
            f"The workload may have been cleaned up out of band, or the "
            f"host may have been re-provisioned. Run the matching "
            f"`fslab abandon` command to discard the local stamp."
        )
    try:
        data = yaml.safe_load(r.stdout) or {}
    except yaml.YAMLError as e:
        raise MonitorAborted(f"Could not parse remote stamp: {e}") from e
    remote_id = data.get(id_field) if isinstance(data, dict) else None
    if remote_id != expected_id:
        raise MonitorAborted(
            f"Remote {id_field} ({remote_id!r}) does not match local "
            f"({expected_id!r}). The remote work dir may have been reused "
            f"by another project. Run the matching `fslab abandon` command "
            f"to clean up and start fresh."
        )


# ---------------------------------------------------------------------------
# Tail-and-wait
# ---------------------------------------------------------------------------


def tail_remote_log_until_result(
    host: Host,
    log_path: str,
    result_path: str,
) -> None:
    """Tail `log_path` over SSH until `result_path` appears on the remote.

    Implementation: one combined remote command tails the log in the
    background while a polling loop waits for `result_path` to appear.
    Once it does, the loop kills tail and the command exits, returning
    control here so the caller can transition the stamp and pull
    artifacts. If `result_path` already exists when the command runs,
    cat the log to give the user the full record without an interactive
    tail.

    Ctrl+C closes the SSH channel. With pty=True, Fabric forwards Ctrl+C
    to the remote pty rather than re-raising it locally — the remote
    tail/poll dies by signal and surfaces as RemoteCommandFailed. Treat
    either as a detach: the workload is nohup'd on the remote, so it
    keeps running.
    """
    log_q = shlex.quote(log_path)
    result_q = shlex.quote(result_path)
    cmd = (
        f"if [ -f {result_q} ]; then "
        f"cat {log_q}; "
        f"else "
        f"tail -F {log_q} & TAIL_PID=$!; "
        f"while [ ! -f {result_q} ]; do sleep 2; done; "
        f"sleep 1; "
        f"kill $TAIL_PID 2>/dev/null; "
        f"wait $TAIL_PID 2>/dev/null; "
        f"true; "
        f"fi"
    )
    try:
        host.run(cmd, pty=True)
    except (KeyboardInterrupt, RemoteCommandFailed):
        info(
            "Detached. Workload continues on remote. "
            "Re-attach with the matching `fslab monitor` command."
        )
        raise MonitorDetached() from None


# ---------------------------------------------------------------------------
# Sleep helper
# ---------------------------------------------------------------------------


def interruptible_sleep(seconds: int) -> bool:
    """Sleep `seconds` seconds; return True if interrupted by Ctrl+C.

    Plain `time.sleep` would propagate the KeyboardInterrupt as an
    exception; using a sentinel return lets the caller decide whether
    to raise `MonitorDetached` with proper messaging."""
    try:
        time.sleep(seconds)
        return False
    except KeyboardInterrupt:
        return True
