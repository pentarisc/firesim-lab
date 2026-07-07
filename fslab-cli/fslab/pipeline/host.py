"""Pipeline-agnostic host abstraction and provider registry.

The build pipeline (`fslab build fpga`) and the upcoming run pipeline
(`fslab sim fpga`) both need to:

  1. Request a remote host (an SSH-reachable machine — EC2-backed for
     fully-managed flows, externally-provisioned for user-managed flows).
  2. Run commands and rsync directories over SSH.
  3. Release the host afterwards (terminate / stop / leave-alone, per
     the configured lifecycle), or capture enough cleanup state at
     launch time so that a *different* later process (background-build
     monitor / `fslab abandon`) can release it from a stamp file.

Everything in this module is pipeline-agnostic: it knows nothing about
build configs, run configs, bitstreams, drivers, AGFIs, or platforms.
Build-pipeline-specific extensions (platform-HDK pre-build provisioning
via `ensure_platform`) live in `fslab.bitstream.buildhost` as a subclass
mixin layered on top of these base classes. The forthcoming run pipeline
will consume these base classes directly without extending them.

Two-axis lifecycle model:
  * In-memory:        `provider.request(cfg)` → use → `provider.release(host)`.
  * Stamp-driven:     `provider.serialize_cleanup_state(host, cfg)` →
                      persist dict in a local stamp →
                      `HostProvider.cleanup_from_state(state)` later from
                      any process. Registry-based dispatch is what makes
                      this work without re-deriving config.

The provider registry is keyed by host_model discriminator (`external`,
`ec2_launch`, ...) — the same string that appears in `host.type` in
fslab.yaml and in the stamp's `cleanup.provider` field. One name flows
end-to-end with no remapping.
"""

from __future__ import annotations

import abc
import shlex
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, IO, List, Literal, Optional, Type, Union

from fabric import Connection  # type: ignore[import-not-found]
from invoke import Result  # type: ignore[import-not-found]

from fslab.cloudutils.aws import fpga as aws_fpga
from fslab.schemas.host_model import Ec2LaunchHostConfig, ExternalHostConfig
from fslab.utils.display import console, info, warning
from fslab.utils.shell import run_or_die
from fslab.utils.streams import Tee


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


class UnknownProviderError(Exception):
    """Raised when `cleanup_remote` is handed a stamp whose
    `cleanup.provider` value does not match any registered provider.

    Most likely cause: the stamp was written by a newer fslab version
    that knows a provider this code doesn't, or the stamp file has been
    edited by hand and the discriminator no longer matches."""


# ---------------------------------------------------------------------------
# Upload-mode literal
# ---------------------------------------------------------------------------


# Carried on the Host instance so build-pipeline extensions (ensure_platform
# in fslab.bitstream.buildhost) can branch their HDK-upload policy without
# re-deriving lifecycle context. The run pipeline does not consult this.
#
#   reuse_strict  user owns the HDK on this box (external). Stamp mismatch
#                 is fatal; never auto-upload without --upload-platform.
#   reuse_soft    fslab manages the HDK on this box. Stamp mismatch
#                 auto-uploads with a warning; missing stamp triggers a
#                 first-time upload; match → skip. Used for both
#                 ec2_launch managed-reuse and ec2_launch ephemeral —
#                 the latter so that pre-baked AMIs (HDK + stamp baked
#                 in) can skip the multi-hour upload while stock AMIs
#                 still get a first-time upload via the missing-stamp
#                 path.
#   fresh         every build unconditionally re-uploads. Currently no
#                 provider sets this; kept in the enum for explicit
#                 force-upload semantics (--upload-platform achieves the
#                 same effect at the CLI level).
UploadMode = Literal["reuse_strict", "reuse_soft", "fresh"]


# ---------------------------------------------------------------------------
# Host (abstract)
# ---------------------------------------------------------------------------


class Host(abc.ABC):
    """A single machine on which a pipeline phase (build or run) executes.

    Concrete implementations decide how the connection is established. Once
    connected, all callers interact with the host through this small API.
    """

    # Set by the provider during request() so build-side ensure_platform
    # can branch on lifecycle context without re-deriving it from
    # host_model state. The run pipeline does not consult this.
    _upload_mode: UploadMode = "reuse_strict"

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

    def __enter__(self) -> "Host":
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# ExternalHost — pre-provisioned, SSH-reachable
# ---------------------------------------------------------------------------


class ExternalHost(Host):
    """Pre-provisioned host accessed over SSH using `fabric.Connection`.

    No host lifecycle (launch/terminate) is involved. Releasing this host
    just closes the connection.
    """

    def __init__(self, params: ExternalHostConfig):
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
                "ExternalHost is not connected; call connect() first."
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
# Ec2LaunchHost — EC2-backed, SSH-reachable
# ---------------------------------------------------------------------------


class Ec2LaunchHost(ExternalHost):
    """SSH-reachable host backed by an EC2 instance.

    Behaviourally identical to `ExternalHost` for run/put/rsync — the
    transport is the same. This class only adds the `instance_id` that the
    provider needs at release time to stop/terminate the right resource.
    """

    def __init__(self, params: ExternalHostConfig, instance_id: str):
        super().__init__(params)
        self.instance_id = instance_id


# ---------------------------------------------------------------------------
# Provider registry — for stamp-driven cleanup dispatch
# ---------------------------------------------------------------------------


PROVIDER_REGISTRY: dict[str, Type["HostProvider"]] = {}


def register_provider(
    name: str,
) -> Callable[[Type["HostProvider"]], Type["HostProvider"]]:
    """Register a HostProvider subclass under `name`.

    The registered name is what appears in the stamp file's
    `cleanup.provider` field and is the discriminator `cleanup_remote`
    uses to look the class back up. By convention the registered name
    matches the host_model type discriminator in fslab.yaml
    (`ec2_launch`, `external`, …), so the same string flows from user
    config → cleanup state → cleanup dispatch with no remapping.

    Build-pipeline subclasses (which add `ensure_platform` on top of the
    base provider) deliberately do NOT re-register — cleanup dispatch
    only needs the lifecycle base class's `cleanup_from_state`, which is
    a classmethod and inherits to the subclass anyway.
    """
    def decorator(
        cls: Type["HostProvider"],
    ) -> Type["HostProvider"]:
        PROVIDER_REGISTRY[name] = cls
        return cls
    return decorator


def cleanup_remote(stamp: dict) -> None:
    """Top-level cleanup helper, driven entirely by a stamp file.

    Pulls the `cleanup` block out of the stamp, looks up the provider by
    its `provider` discriminator, and delegates to the class's
    `cleanup_from_state` classmethod. Idempotent — running this twice on
    already-released resources must succeed (each provider's
    implementation guarantees this).
    """
    cleanup_state = stamp.get("cleanup")
    if not isinstance(cleanup_state, dict):
        raise ValueError(
            "stamp is missing a valid 'cleanup' block; cannot dispatch "
            "cleanup_remote."
        )
    provider_name = cleanup_state.get("provider")
    if not provider_name:
        raise ValueError(
            "stamp.cleanup is missing the 'provider' discriminator."
        )
    cls = PROVIDER_REGISTRY.get(provider_name)
    if cls is None:
        raise UnknownProviderError(
            f"No provider registered under name {provider_name!r}. "
            f"Known providers: {sorted(PROVIDER_REGISTRY)}"
        )
    cls.cleanup_from_state(cleanup_state)


# ---------------------------------------------------------------------------
# HostProvider (abstract)
# ---------------------------------------------------------------------------


class HostProvider(abc.ABC):
    """Manages the lifecycle of pipeline hosts.

    The contract is intentionally small: request a host, release it, and
    expose enough captured state at launch time that a separate later
    process can run cleanup from a stamp file alone (no live cfg, no
    in-memory provider state). Build-pipeline-specific work
    (platform-HDK upload via `ensure_platform`) is added by a subclass in
    `fslab.bitstream.buildhost`; the forthcoming run pipeline consumes
    this base class directly.

    `cfg` is intentionally typed as `Any` — concrete providers narrow it
    by `isinstance`-checking the `cfg.host` block they receive. This lets
    the same provider serve `BuildConfig` (build) and the future run-side
    `RunConfig` without coupling the pipeline layer to either.
    """

    @abc.abstractmethod
    def request(self, cfg: Any) -> Host:
        """Return a Host ready to be `connect()`ed.

        Concrete implementations must set `host._upload_mode` before
        returning. The run pipeline ignores `_upload_mode`; it is only
        consulted by the build pipeline's `ensure_platform`.
        """

    @abc.abstractmethod
    def release(self, host: Host) -> None:
        """Release the host. For external (pre-provisioned) hosts this just
        closes the connection. For ephemeral EC2 hosts this terminates the
        instance; for managed-reuse EC2 hosts it stops the instance only
        if the provider started it (initial state was `stopped`)."""

    # ----------------------------------------------------------------------
    # Cleanup-state serialization (for background / stamp-driven flows)
    # ----------------------------------------------------------------------

    @abc.abstractmethod
    def serialize_cleanup_state(
        self, host: Host, cfg: Any
    ) -> dict:
        """Capture everything `cleanup_from_state` will need to release this
        host without access to live config or in-memory provider state.

        Called once, immediately after `request()` succeeds — the returned
        dict is persisted in the local stamp file and never re-derived
        from `cfg` afterward. This makes cleanup robust to later changes
        in fslab.yaml or to running cleanup from a separate process.

        The returned dict MUST include a `'provider'` key matching the
        provider's registered name (see `register_provider`); the
        remaining fields are provider-specific.
        """

    @classmethod
    @abc.abstractmethod
    def cleanup_from_state(cls, state: dict) -> None:
        """Execute provider-appropriate cleanup using ONLY the captured
        state. No live cfg, no live host, no instance attributes.

        Must be idempotent — running this twice on resources already
        released must succeed (e.g. terminating an already-terminated
        EC2 instance is a no-op for AWS, just surface that gracefully).
        """


# ---------------------------------------------------------------------------
# ExternalHostProvider
# ---------------------------------------------------------------------------


@register_provider("external")
class ExternalHostProvider(HostProvider):
    """For pre-provisioned hosts. No launch/terminate, just close on release."""

    def request(self, cfg: Any) -> Host:
        host_cfg = cfg.host
        if not isinstance(host_cfg, ExternalHostConfig):
            # Should be unreachable: make_host_provider only returns this
            # provider when host.type == "external".
            raise RuntimeError(
                f"ExternalHostProvider received host of type "
                f"{host_cfg.type!r}; expected 'external'."
            )
        host = ExternalHost(host_cfg)
        host._upload_mode = "reuse_strict"
        return host

    def release(self, host: Host) -> None:
        host.close()

    # ----------------------------------------------------------------------
    # Stamp-driven cleanup
    # ----------------------------------------------------------------------

    def serialize_cleanup_state(
        self, host: Host, cfg: Any
    ) -> dict:
        host_cfg = cfg.host
        assert isinstance(host_cfg, ExternalHostConfig)
        return {
            "provider": "external",
            "host": host_cfg.host,
        }

    @classmethod
    def cleanup_from_state(cls, state: dict) -> None:
        info(
            f"External host {state.get('host', '?')!r} is user-managed; "
            f"nothing to clean up."
        )


# ---------------------------------------------------------------------------
# Ec2LaunchHostProvider
# ---------------------------------------------------------------------------


@dataclass
class _Ec2Lifecycle:
    """Records what the provider did at request() time so release() can
    undo only what it did. Two-axis state:

      action      'launched' | 'started' | 'connected'
      instance_id the resource to act on at release-time
    """
    action: str
    instance_id: str


@register_provider("ec2_launch")
class Ec2LaunchHostProvider(HostProvider):
    """Provider for the `ec2_launch` host model.

    Two sub-modes, selected by `host_cfg.instance_id`:

      * instance_id unset → **ephemeral**. RunInstances + wait_running +
        wait_ssh → use → TerminateInstances on release.

      * instance_id set   → **managed reuse**. DescribeInstances:
          - stopped  → StartInstances + wait_running + wait_ssh → use →
                       StopInstances on release.
          - running  → connect only → leave running on release (do not
                       touch state of an instance someone else may own).
          - terminal → fail.

    The provider keeps a single `_lifecycle` record between request() and
    release(); calling request() twice without an intervening release()
    is undefined.
    """

    def __init__(self) -> None:
        self._lifecycle: Optional[_Ec2Lifecycle] = None
        self._session: Any = None  # boto3.Session; lazy

    # ----------------------------------------------------------------------
    # request / release
    # ----------------------------------------------------------------------

    def request(self, cfg: Any) -> Host:
        host_cfg = cfg.host
        if not isinstance(host_cfg, Ec2LaunchHostConfig):
            raise RuntimeError(
                f"Ec2LaunchHostProvider received host of type "
                f"{host_cfg.type!r}; expected 'ec2_launch'."
            )

        # Build session + probe creds before any lifecycle mutation, so a
        # bad profile/SSO state fails before we launch resources.
        self._session = aws_fpga.make_session(
            region=host_cfg.region, profile=host_cfg.aws_profile
        )
        aws_fpga.check_credentials(self._session, host_cfg.aws_profile)

        if host_cfg.instance_id:
            return self._acquire_managed(host_cfg)
        return self._acquire_ephemeral(host_cfg)

    def release(self, host: Host) -> None:
        try:
            host.close()
        finally:
            lc = self._lifecycle
            self._lifecycle = None
            if lc is None:
                return
            try:
                if lc.action == "launched":
                    info(f"Terminating ephemeral instance {lc.instance_id}")
                    aws_fpga.terminate_instance(self._session, lc.instance_id)
                elif lc.action == "started":
                    info(f"Stopping managed instance {lc.instance_id}")
                    aws_fpga.stop_instance(self._session, lc.instance_id)
                # 'connected' (found running) → leave alone
            except Exception as e:
                warning(
                    f"Lifecycle teardown failed for instance "
                    f"{lc.instance_id} ({lc.action}): {e}"
                )

    # ----------------------------------------------------------------------
    # Stamp-driven cleanup
    # ----------------------------------------------------------------------

    def serialize_cleanup_state(
        self, host: Host, cfg: Any
    ) -> dict:
        """Snapshot the request()-time `_lifecycle` plus enough config to
        rebuild a boto3 session later. Must be called between a successful
        `request()` and the corresponding `release()`."""
        if self._lifecycle is None:
            raise RuntimeError(
                "serialize_cleanup_state called with no live _lifecycle; "
                "request() must have succeeded first and release() must "
                "not have run yet."
            )
        host_cfg = cfg.host
        if not isinstance(host_cfg, Ec2LaunchHostConfig):
            raise RuntimeError(
                f"serialize_cleanup_state received host of type "
                f"{host_cfg.type!r}; expected 'ec2_launch'."
            )
        return {
            "provider": "ec2_launch",
            "aws_profile": host_cfg.aws_profile,
            "region": host_cfg.region,
            "instance_id": self._lifecycle.instance_id,
            # 'action' mirrors _Ec2Lifecycle.action so the cleanup branch
            # set is the same as in release(): launched → terminate,
            # started → stop, connected → leave alone.
            "action": self._lifecycle.action,
        }

    @classmethod
    def cleanup_from_state(cls, state: dict) -> None:
        """Mirror of `release()`'s teardown branches, but driven purely by
        the captured state dict. Idempotent: terminating an already-
        terminated instance and stopping an already-stopped instance both
        return cleanly on AWS's side."""
        action = state["action"]
        instance_id = state["instance_id"]
        if action == "connected":
            info(
                f"Instance {instance_id} was already running when fslab "
                f"connected; leaving alone."
            )
            return

        session = aws_fpga.make_session(
            region=state["region"], profile=state.get("aws_profile")
        )
        aws_fpga.check_credentials(session, state.get("aws_profile"))

        if action == "launched":
            info(f"Terminating ephemeral instance {instance_id}")
            aws_fpga.terminate_instance(session, instance_id)
        elif action == "started":
            info(
                f"Stopping managed instance {instance_id} "
                f"(was 'stopped' before fslab started it)"
            )
            aws_fpga.stop_instance(session, instance_id)
        else:
            raise ValueError(
                f"Unknown ec2_launch cleanup action: {action!r}"
            )

    # ----------------------------------------------------------------------
    # Managed-reuse path
    # ----------------------------------------------------------------------

    def _acquire_managed(self, host_cfg: Ec2LaunchHostConfig) -> Host:
        instance_id = host_cfg.instance_id  # type: ignore[assignment]
        info(f"Looking up managed instance {instance_id} in {host_cfg.region}")
        inst = aws_fpga.describe_instance(self._session, instance_id)
        state = inst["State"]["Name"]
        info(f"Instance {instance_id} state: {state}")

        if state == "stopped":
            info(f"Starting instance {instance_id}")
            aws_fpga.start_instance(self._session, instance_id)
            inst = aws_fpga.wait_until_running(self._session, instance_id)
            self._lifecycle = _Ec2Lifecycle("started", instance_id)
        elif state == "running":
            warning(
                f"Instance {instance_id} is already running — connecting "
                f"without changing state; will NOT stop it on release."
            )
            self._lifecycle = _Ec2Lifecycle("connected", instance_id)
        elif state in ("pending", "stopping"):
            # Transitional states — wait for them to settle then re-check.
            info(f"Waiting for instance {instance_id} to leave '{state}'")
            if state == "stopping":
                aws_fpga.wait_until_stopped(self._session, instance_id)
                aws_fpga.start_instance(self._session, instance_id)
                self._lifecycle = _Ec2Lifecycle("started", instance_id)
            inst = aws_fpga.wait_until_running(self._session, instance_id)
            if self._lifecycle is None:
                self._lifecycle = _Ec2Lifecycle("connected", instance_id)
        else:
            raise RuntimeError(
                f"Instance {instance_id} is in unexpected state '{state}'; "
                f"cannot use for build."
            )

        public_addr = self._resolve_address(inst)
        ssh_user = host_cfg.ssh_user
        info(f"Waiting for SSH on {ssh_user}@{public_addr}:22")
        aws_fpga.wait_for_ssh(public_addr)

        params = self._build_external_params(host_cfg, public_addr)
        host = Ec2LaunchHost(params, instance_id=instance_id)
        host._upload_mode = "reuse_soft"
        return host

    # ----------------------------------------------------------------------
    # Ephemeral path
    # ----------------------------------------------------------------------

    def _acquire_ephemeral(self, host_cfg: Ec2LaunchHostConfig) -> Host:
        # Required-at-request-time field check (Optional in the schema
        # because the registry merge populates them; explicit check here
        # gives a clearer error than letting boto3 fail).
        missing = [
            name for name, val in (
                ("ami_id", host_cfg.ami_id),
                ("instance_type", host_cfg.instance_type),
            ) if not val
        ]
        if missing:
            raise RuntimeError(
                f"ec2_launch (ephemeral) requires {missing} to be set. "
                f"Supply via fslab.yaml or a custom registry."
            )

        info(
            f"Launching ephemeral {host_cfg.lifecycle} instance "
            f"({host_cfg.instance_type}, ami={host_cfg.ami_id}) "
            f"in {host_cfg.region}"
        )
        instance_id = aws_fpga.launch_instance(
            self._session,
            ami_id=host_cfg.ami_id,  # type: ignore[arg-type]
            instance_type=host_cfg.instance_type,  # type: ignore[arg-type]
            key_name=host_cfg.key_name,
            subnet_id=host_cfg.subnet_id,
            iam_instance_profile=host_cfg.iam_instance_profile,
            lifecycle=host_cfg.lifecycle,
            root_volume_gb=host_cfg.root_volume_gb,
            data_volume_gb=host_cfg.data_volume_gb,
            volume_type=host_cfg.volume_type,
            tags={
                "Name": "firesim-lab-build",
                "firesim-lab/managed": "true",
                "firesim-lab/lifecycle": host_cfg.lifecycle,
            },
        )
        self._lifecycle = _Ec2Lifecycle("launched", instance_id)

        inst = aws_fpga.wait_until_running(self._session, instance_id)
        public_addr = self._resolve_address(inst)
        ssh_user = host_cfg.ssh_user
        info(f"Waiting for SSH on {ssh_user}@{public_addr}:22")
        aws_fpga.wait_for_ssh(public_addr)

        params = self._build_external_params(host_cfg, public_addr)
        host = Ec2LaunchHost(params, instance_id=instance_id)
        # reuse_soft (not fresh) so a pre-baked AMI that already contains
        # the platform HDK and a matching stamp file at
        # `<remote_platform_path>/.firesim-lab-stamp.yaml` can skip the
        # multi-hour upload. A stock AMI with no stamp still gets a
        # first-time upload via reuse_soft's missing-stamp path; a stale
        # AMI (version mismatch) auto-refreshes with a warning. To bake
        # such an AMI: run one normal build to completion on a fresh
        # instance (which writes the stamp as the final step of upload),
        # then snapshot that instance into an AMI.
        host._upload_mode = "reuse_soft"
        return host

    # ----------------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------------

    @staticmethod
    def _resolve_address(inst: dict) -> str:
        """Prefer the public DNS name; fall back to public IP. Both can be
        empty for instances in private subnets, in which case the user is
        expected to be reachable to the private address (run fslab from
        inside the same VPC)."""
        return (
            inst.get("PublicDnsName")
            or inst.get("PublicIpAddress")
            or inst.get("PrivateIpAddress")
            or ""
        )

    @staticmethod
    def _build_external_params(
        host_cfg: Ec2LaunchHostConfig, address: str
    ) -> ExternalHostConfig:
        """Construct an ExternalHostConfig from the resolved EC2 state so
        ExternalHost's SSH/rsync logic can be reused verbatim. The
        provider already validated remote_platform_path is set (via the
        registry merge); a missing value here is a configuration bug."""
        if not address:
            raise RuntimeError(
                "EC2 instance has no reachable public address. If running "
                "from outside the VPC, ensure the instance has a public IP "
                "or DNS name."
            )
        if not host_cfg.remote_platform_path:
            raise RuntimeError(
                "host.remote_platform_path is unset for ec2_launch; the "
                "registry-default merge step did not populate it. Check "
                "lib/registry.yaml host_models.ec2_launch.remote_platform_path."
            )
        return ExternalHostConfig(
            type="external",
            host=address,
            user=host_cfg.ssh_user,
            ssh_key=host_cfg.ssh_key,
            remote_platform_path=host_cfg.remote_platform_path,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_host_provider(cfg: Any) -> HostProvider:
    """Pick a provider for the current host_model discriminator.

    Adding a new host_model requires a new branch here in lockstep with a
    new schema class in fslab.schemas.host_model and an entry in
    KNOWN_HOST_MODELS.

    `cfg` is duck-typed: any object exposing a `host` attribute with a
    `type` discriminator works (BuildConfig today; the future RunConfig
    once the run pipeline lands).
    """
    host_type = cfg.host.type
    if host_type == "external":
        return ExternalHostProvider()
    if host_type == "ec2_launch":
        return Ec2LaunchHostProvider()
    raise NotImplementedError(f"Unknown host_model type: {host_type!r}")
