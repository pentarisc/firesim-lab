"""Build host abstraction.

This is the firesim-lab analogue of firesim's `BuildHost` + `BuildFarm`, with
two intentional improvements:

  1. `BuildHostProvider.request()` returns the BuildHost directly (firesim
     stores it internally and forces callers to look it up by IP).
  2. `BuildHost` owns its connection and exposes a small uniform interface
     (`run`, `put`, `rsync_to`, `rsync_from`, `close`). The BitBuilder never
     touches Fabric or subprocess directly — keeps the build recipe testable
     by swapping in a fake host.

Fabric 2.x is used for SSH; rsync is invoked via `subprocess` because Fabric 2
dropped `rsync_project`.
"""

from __future__ import annotations

import abc
import shlex
import subprocess
from pathlib import Path
from typing import Any, List, Optional

from fabric import Connection  # type: ignore[import-not-found]
from invoke import Result  # type: ignore[import-not-found]

from fslab.schemas.project import BuildHostConfig
from fslab.utils.display import console, error, info, section, success, warning

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
    """Raised when an rsync subprocess invocation returns non-zero."""


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
    ) -> Result:
        """Execute `cmd` on the remote host.

        If `warn` is False, a non-zero exit raises `RemoteCommandFailed`.
        If `hide` is True, output is captured but not streamed.
        If `pty` is True, allocate a pseudo-terminal — use this for long-
        running interactive commands (e.g. the build script) so output streams.
        """

    @abc.abstractmethod
    def put(self, local: str, remote: str) -> None:
        """Upload a single file."""

    @abc.abstractmethod
    def rsync_to(
        self,
        local: str,
        remote: str,
        *,
        exclude: Optional[List[str]] = None,
        follow_symlinks: bool = False,
        delete: bool = False,
    ) -> None:
        """Upload a directory tree via rsync."""

    @abc.abstractmethod
    def rsync_from(
        self,
        remote: str,
        local: str,
        *,
        exclude: Optional[List[str]] = None,
    ) -> None:
        """Download a directory tree via rsync."""

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

    # --- exec / file ops --------------------------------------------------

    def run(
        self,
        cmd: str,
        *,
        warn: bool = False,
        hide: bool = False,
        pty: bool = False,
    ) -> Result:
        conn = self._require_conn()
        console.print(f"[{self.params.host}] run: {cmd}")
        # Always pass warn=True to fabric so we can format our own exception
        # with stderr tail rather than fabric's default UnexpectedExit.
        result: Result = conn.run(cmd, warn=True, hide=hide, pty=pty)
        if not warn and result.return_code != 0:
            raise RemoteCommandFailed(
                cmd, result.return_code, getattr(result, "stderr", "") or ""
            )
        return result

    def put(self, local: str, remote: str) -> None:
        conn = self._require_conn()
        console.print(f"[{self.params.host}] put: {local} -> {remote}")
        conn.put(local, remote=remote)

    # --- rsync via subprocess --------------------------------------------

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
    ) -> None:
        cmd: List[str] = [
            "rsync",
            "-az",
            "--info=stats1,progress2",
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

        console.print(f"rsync: {' '.join(shlex.quote(c) for c in cmd)}")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RsyncFailed(
                f"rsync failed (rc={proc.returncode})\n"
                f"CMD: {' '.join(shlex.quote(c) for c in cmd)}\n"
                f"STDERR:\n{proc.stderr}"
            )

    def rsync_to(
        self,
        local: str,
        remote: str,
        *,
        exclude: Optional[List[str]] = None,
        follow_symlinks: bool = False,
        delete: bool = False,
    ) -> None:
        target = f"{self.params.user}@{self.params.host}:{remote}"
        self._rsync(
            local, target,
            exclude=exclude,
            follow_symlinks=follow_symlinks,
            delete=delete,
        )

    def rsync_from(
        self,
        remote: str,
        local: str,
        *,
        exclude: Optional[List[str]] = None,
    ) -> None:
        Path(local).mkdir(parents=True, exist_ok=True)
        source = f"{self.params.user}@{self.params.host}:{remote}"
        self._rsync(
            source, local,
            exclude=exclude,
            follow_symlinks=False,
            delete=False,
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