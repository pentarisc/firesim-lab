"""Build host abstraction.

This is the firesim-lab analogue of firesim's `BuildHost` + `BuildFarm`, with
two intentional improvements:

  1. `BuildHostProvider.request()` returns the BuildHost directly (firesim
     stores it internally and forces callers to look it up by IP).
  2. `BuildHost` owns its connection and exposes a small uniform interface
     (`run`, `put`, `rsync_to`, `rsync_from`, `close`). The BitBuilder never
     touches Fabric or subprocess directly — keeps the build recipe testable
     by swapping in a fake host.

Fabric 2.x is used for SSH; rsync goes through `run_or_die` from
`fslab.utils.shell`, which itself uses `subprocess.Popen` and supports
log-file streaming.

Logging:
  Every remote-session method (`run`, `put`, `rsync_to`, `rsync_from`)
  accepts an optional `log_file` argument and appends a structured record
  for that operation:
    * `run`    — header + teed stdout/stderr + exit footer
    * `put`    — header + transfer ok/FAILED footer (SFTP has no streams)
    * rsync    — handled inside `run_or_die`
"""

from __future__ import annotations

import abc
import shlex
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, IO, List, Optional, Union

from fabric import Connection  # type: ignore[import-not-found]
from invoke import Result  # type: ignore[import-not-found]

from fslab.schemas.project import BuildHostConfig
from fslab.utils.display import console, error, info, section, success, warning
from fslab.utils.shell import run_or_die
from fslab.utils.streams import Tee

from .buildconfig import BuildConfig


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RemoteCommandFailed(Exception):
    """Raised when a remote command exits non-zero and the caller did not opt
    into warn-mode. Carries the failing command + exit code + stderr tail."""

    def __init__(self, cmd: str, exit_code: int, stderr: str = ""):
        self.cmd = cmd
        self.exit_code = exit_code
        self.stderr = stderr
        tail = "\n".join(stderr.strip().splitlines()[-20:])
        super().__init__(
            f"Remote command failed (exit {exit_code}): {cmd}\n{tail}"
        )


class RsyncFailed(Exception):
    """Raised when an rsync invocation fails. Wraps the underlying
    `run_or_die` exception so callers can catch this domain-specific type."""


# ---------------------------------------------------------------------------
# BuildHost (abstract)
# ---------------------------------------------------------------------------


class BuildHost(abc.ABC):
    """A single machine on which a bitstream build runs.

    Concrete implementations decide how the connection is established. Once
    connected, all callers interact with the host through this small API.
    """

    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def close(self) -> None: ...

    @abc.abstractmethod
    def run(
        self,
        cmd: str,
        *,
        warn: bool = False,
        hide: bool = False,
        pty: bool = False,
        log_file: Optional[Union[str, Path]] = None,
    ) -> Result:
        """Execute `cmd` on the remote host.

        If `warn` is False, a non-zero exit raises `RemoteCommandFailed`.
        If `hide` is True, output is captured but not echoed to the console.
        If `pty` is True, allocate a pseudo-terminal — use this for long-
        running interactive commands (e.g. the build script) so output streams.
        If `log_file` is given, stdout+stderr are appended to that file (via
        Tee). When `hide=False`, output goes to both file and console; when
        `hide=True`, output goes to file only.
        """

    @abc.abstractmethod
    def put(
        self,
        local: str,
        remote: str,
        *,
        log_file: Optional[Union[str, Path]] = None,
    ) -> None:
        """Upload a single file via SFTP.

        SFTP transfers don't produce streamable output, so when `log_file`
        is given, only metadata records are written: a header for the
        transfer and a footer indicating success or failure.
        """

    @abc.abstractmethod
    def rsync_to(
        self,
        local: str,
        remote: str,
        *,
        exclude: Optional[List[str]] = None,
        follow_symlinks: bool = False,
        delete: bool = False,
        log_file: Optional[Union[str, Path]] = None,
        label: Optional[str] = None,
    ) -> None:
        """Upload a directory tree via rsync.

        `label` is an optional human-readable tag that the underlying
        runner uses in its console/log output. Defaults to '[rsync push]'.
        """

    @abc.abstractmethod
    def rsync_from(
        self,
        remote: str,
        local: str,
        *,
        exclude: Optional[List[str]] = None,
        log_file: Optional[Union[str, Path]] = None,
        label: Optional[str] = None,
    ) -> None:
        """Download a directory tree via rsync.

        `label` defaults to '[rsync pull]'.
        """

    # --- context manager ergonomics ---------------------------------------

    def __enter__(self) -> "BuildHost":
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# ExternalBuildHost — pre-provisioned, SSH-reachable
# ---------------------------------------------------------------------------


class ExternalBuildHost(BuildHost):
    """Pre-provisioned host accessed over SSH using `fabric.Connection`.

    No host lifecycle (launch/terminate) is involved. Releasing this host
    just closes the connection.
    """

    def __init__(self, params: BuildHostConfig):
        self.params = params
        self._conn: Optional[Connection] = None

    # --- ssh key resolution helper ---------------------------------------

    def _resolved_ssh_key(self) -> Optional[Path]:
        if self.params.ssh_key is None:
            return None
        return Path(self.params.ssh_key).expanduser()

    # --- lifecycle --------------------------------------------------------

    def connect(self) -> None:
        if self._conn is not None:
            return
        connect_kwargs: dict = {}
        key_path = self._resolved_ssh_key()
        if key_path is not None:
            connect_kwargs["key_filename"] = str(key_path)
        # else: rely on the user's SSH agent / ~/.ssh/config

        conn = Connection(
            host=self.params.host,
            user=self.params.user,
            connect_kwargs=connect_kwargs,
        )
        # Force connection now so a bad key/host fails fast.
        conn.open()
        self._conn = conn
        info(f"Connected to {self.params.user}@{self.params.host}")

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
                console.print(f"Closed connection to {self.params.host}")

    def _require_conn(self) -> Connection:
        if self._conn is None:
            raise RuntimeError(
                "ExternalBuildHost is not connected; call connect() first."
            )
        return self._conn

    # --- log helpers (shared by run + put) -------------------------------

    def _open_log_with_header(
        self,
        log_file: Optional[Union[str, Path]],
        action_descr: str,
    ) -> Optional[IO[str]]:
        """Open log_file in append mode, write a header line, return the
        handle. Caller is responsible for closing. Returns None when
        log_file is None."""
        if log_file is None:
            return None
        log_path = Path(log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(log_path, "a", encoding="utf-8", buffering=1)
        f.write(
            f"\n--- {datetime.now().isoformat(timespec='seconds')} | "
            f"{self.params.host} | {action_descr} ---\n"
        )
        f.flush()
        return f

    # --- exec / file ops --------------------------------------------------

    def run(
        self,
        cmd: str,
        *,
        warn: bool = False,
        hide: bool = False,
        pty: bool = False,
        log_file: Optional[Union[str, Path]] = None,
    ) -> Result:
        conn = self._require_conn()
        console.print(f"[{self.params.host}] run: {cmd}")

        f = self._open_log_with_header(log_file, cmd)
        try:
            if f is None:
                # Default fabric behaviour: console-only (or silent if hide).
                result: Result = conn.run(cmd, warn=True, hide=hide, pty=pty)
            else:
                if hide:
                    out_stream: Any = f
                    err_stream: Any = f
                else:
                    out_stream = Tee(sys.stdout, f)
                    err_stream = Tee(sys.stderr, f)
                result = conn.run(
                    cmd,
                    warn=True,
                    pty=pty,
                    out_stream=out_stream,
                    err_stream=err_stream,
                )
                f.write(f"--- exit {result.return_code} ---\n")
        finally:
            if f is not None:
                f.close()

        if not warn and result.return_code != 0:
            raise RemoteCommandFailed(
                cmd, result.return_code, getattr(result, "stderr", "") or ""
            )
        return result

    def put(
        self,
        local: str,
        remote: str,
        *,
        log_file: Optional[Union[str, Path]] = None,
    ) -> None:
        conn = self._require_conn()
        console.print(f"[{self.params.host}] put: {local} -> {remote}")

        f = self._open_log_with_header(log_file, f"put: {local} -> {remote}")
        try:
            try:
                conn.put(local, remote=remote)
            except Exception as e:
                if f is not None:
                    f.write(f"--- transfer FAILED: {e} ---\n")
                raise
            if f is not None:
                f.write("--- transfer ok ---\n")
        finally:
            if f is not None:
                f.close()

    # --- rsync via run_or_die --------------------------------------------

    def _ssh_e_arg(self) -> str:
        # accept-new pins host key on first connection, enforces on subsequent.
        opts = [
            "ssh",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
        ]
        key_path = self._resolved_ssh_key()
        if key_path is not None:
            opts += ["-i", str(key_path)]
        return shlex.join(opts)

    def _rsync(
        self,
        src: str,
        dst: str,
        *,
        exclude: Optional[List[str]],
        follow_symlinks: bool,
        delete: bool,
        cwd: Optional[Union[str, Path]],
        label: str,
        log_file: Optional[Union[str, Path]],
    ) -> None:
        cmd: List[str] = [
            "rsync",
            "-az",
            "--info=stats1",
            "-e", self._ssh_e_arg(),
        ]
        # -L follows symlinks (firesim Alveo style); -l preserves them
        # (firesim F2 style — required so cl_firesim's in-tree symlinks
        # resolve on the remote host).
        cmd.append("-L" if follow_symlinks else "-l")
        if delete:
            cmd.append("--delete")
        for ex in exclude or []:
            cmd += ["--exclude", ex]
        cmd += [src, dst]

        try:
            run_or_die(
                cmd,
                cwd=cwd,
                label=label,
                log_file=log_file,
            )
        except Exception as e:
            # Wrap to preserve the `RsyncFailed` domain type. `_pull_results`
            # in F2BitBuilder catches this for best-effort failure handling.
            raise RsyncFailed(
                f"rsync failed: {label} {src} -> {dst}\n{e}"
            ) from e

    def rsync_to(
        self,
        local: str,
        remote: str,
        *,
        exclude: Optional[List[str]] = None,
        follow_symlinks: bool = False,
        delete: bool = False,
        log_file: Optional[Union[str, Path]] = None,
        label: Optional[str] = None,
    ) -> None:
        target = f"{self.params.user}@{self.params.host}:{remote}"
        self._rsync(
            local, target,
            exclude=exclude,
            follow_symlinks=follow_symlinks,
            delete=delete,
            # Local source as cwd — rsync doesn't need it functionally
            # (absolute paths), but it adds context to run_or_die's log.
            cwd=local,
            label=label or "[rsync push]",
            log_file=log_file,
        )

    def rsync_from(
        self,
        remote: str,
        local: str,
        *,
        exclude: Optional[List[str]] = None,
        log_file: Optional[Union[str, Path]] = None,
        label: Optional[str] = None,
    ) -> None:
        Path(local).mkdir(parents=True, exist_ok=True)
        source = f"{self.params.user}@{self.params.host}:{remote}"
        self._rsync(
            source, local,
            exclude=exclude,
            follow_symlinks=False,
            delete=False,
            cwd=None,  # source is remote; no local cwd to pin
            label=label or "[rsync pull]",
            log_file=log_file,
        )


# ---------------------------------------------------------------------------
# BuildHostProvider
# ---------------------------------------------------------------------------


class BuildHostProvider(abc.ABC):
    """Manages the lifecycle of build hosts.

    Replaces firesim's `BuildFarm`. The contract is intentionally smaller:
    request a host, release it later. Subclasses can add launch/terminate
    semantics (e.g. an EC2 provider) without changing this interface.
    """

    @abc.abstractmethod
    def request(self, cfg: BuildConfig) -> BuildHost:
        """Return a BuildHost ready to be `connect()`ed."""

    @abc.abstractmethod
    def release(self, host: BuildHost) -> None:
        """Release the host. For external (pre-provisioned) hosts this just
        closes the connection. For cloud providers, this would terminate the
        instance."""


class ExternalBuildHostProvider(BuildHostProvider):
    """For pre-provisioned hosts. No launch/terminate, just close on release."""

    def request(self, cfg: BuildConfig) -> BuildHost:
        return ExternalBuildHost(cfg.build_host)

    def release(self, host: BuildHost) -> None:
        host.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_build_host_provider(cfg: BuildConfig) -> BuildHostProvider:
    """Pick a provider for the current build config.

    For now there is only one — pre-provisioned. Cloud-aware providers
    (EC2, etc.) plug in here once they are needed.
    """
    return ExternalBuildHostProvider()