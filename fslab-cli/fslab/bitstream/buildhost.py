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

  `BuildHostProvider.ensure_platform` and its internal helpers
  (`_do_upload`, `_run_bootstrap`, `_read_stamp`, `_write_stamp`) also
  accept a `log_file` argument and thread it into every host call they
  make, so the pre-build platform-HDK upload is captured in the same
  log file as the build itself.

Platform-HDK provisioning:
  `BuildHostProvider.ensure_platform()` is the single decision point for
  whether the platform HDK needs to be (re)uploaded before a build. It
  consults a small stamp file at `<remote_platform_path>/.firesim-lab-stamp.yaml`
  and the provider's `_upload_mode` (set during `request()`) to decide
  between skip / upload / fail. See the docstring on the method for the
  policy matrix.

  Before any of that, `ensure_platform` also runs a divergence check:
  if the user has overridden `remote_platform_path` in fslab.yaml to a
  path different from the platform's registry default, and the registry
  default already has an HDK stamp on the remote (e.g. baked into the
  AMI), the build aborts with `RegistryDefaultPathConflict` — uploading
  to the override path would leave the remote with two HDK installations.
  `--upload-platform` bypasses this check.

Cleanup decoupling (background-build support):
  In addition to the in-memory `release(host)` path used by the original
  synchronous build, each provider exposes a pair of methods that let a
  later process (e.g. `fslab monitor build`, `fslab abandon build`)
  perform cleanup without any live config or in-memory provider state:

    serialize_cleanup_state(host, cfg) -> dict
        Capture, at launch time, everything cleanup_from_state needs.
        The result is persisted in the local stamp file.
    cleanup_from_state(state) classmethod
        Execute provider-appropriate cleanup using ONLY the captured
        state. Idempotent.

  Dispatch is registry-based, mirroring the BITBUILDER_CLASS_REGISTRY
  pattern: each concrete provider is `@register_provider(<name>)`-decorated
  with the same string that appears in `cfg.host.type` and in the stamp's
  `cleanup.provider` field. The top-level `cleanup_remote(stamp)` helper
  looks the class up and invokes its classmethod.
"""

from __future__ import annotations

import abc
import base64
import shlex
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, IO, List, Literal, Optional, Type, Union

import yaml
from fabric import Connection  # type: ignore[import-not-found]
from invoke import Result  # type: ignore[import-not-found]

from fslab.schemas.host_model import Ec2LaunchHostConfig, ExternalHostConfig
from fslab.utils.display import console, error, info, section, success, warning
from fslab.utils.shell import run_or_die
from fslab.utils.streams import Tee

from . import aws_fpga
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


class PlatformVersionMismatch(Exception):
    """Raised when the remote stamp's aws_fpga_version disagrees with the
    project's expected version under `reuse_strict` mode (external host)."""


class RegistryDefaultPathConflict(Exception):
    """Raised when the user's `remote_platform_path` override differs from
    the platform's registry default AND the registry-default path already
    contains an HDK stamp on the remote.

    Proceeding with the build would upload a second copy of the HDK to the
    user's override location, leaving the remote with two parallel
    installations. The user almost certainly intended to use the existing
    HDK at the registry-default path — typically the fix is to remove the
    override from fslab.yaml. `--upload-platform` bypasses the check for
    cases where the duplicate is actually wanted (e.g. HDK-development
    refresh into a custom location)."""


class UnknownProviderError(Exception):
    """Raised when `cleanup_remote` is handed a stamp whose
    `cleanup.provider` value does not match any registered provider.

    Most likely cause: the stamp was written by a newer fslab version
    that knows a provider this code doesn't, or the stamp file has been
    edited by hand and the discriminator no longer matches."""


# ---------------------------------------------------------------------------
# Upload-mode literal — used by ensure_platform's decision logic
# ---------------------------------------------------------------------------


# The provider tags each host returned by request() with one of these
# modes. ensure_platform branches on the value:
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
# BuildHost (abstract)
# ---------------------------------------------------------------------------


class BuildHost(abc.ABC):
    """A single machine on which a bitstream build runs.

    Concrete implementations decide how the connection is established. Once
    connected, all callers interact with the host through this small API.
    """

    # Set by the provider during request() so ensure_platform can branch
    # on lifecycle context without re-deriving it from host_model state.
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
# Ec2LaunchBuildHost — EC2-backed, SSH-reachable
# ---------------------------------------------------------------------------


class Ec2LaunchBuildHost(ExternalBuildHost):
    """SSH-reachable host backed by an EC2 instance.

    Behaviourally identical to `ExternalBuildHost` for run/put/rsync — the
    transport is the same. This class only adds the `instance_id` that the
    provider needs at release time to stop/terminate the right resource.
    """

    def __init__(self, params: ExternalHostConfig, instance_id: str):
        super().__init__(params)
        self.instance_id = instance_id


# ---------------------------------------------------------------------------
# Provider registry — for stamp-driven cleanup dispatch
# ---------------------------------------------------------------------------


PROVIDER_REGISTRY: dict[str, Type["BuildHostProvider"]] = {}


def register_provider(
    name: str,
) -> Callable[[Type["BuildHostProvider"]], Type["BuildHostProvider"]]:
    """Register a BuildHostProvider subclass under `name`.

    The registered name is what appears in the stamp file's
    `cleanup.provider` field and is the discriminator `cleanup_remote`
    uses to look the class back up. By convention the registered name
    matches the host_model type discriminator in fslab.yaml
    (`ec2_launch`, `external`, …), so the same string flows from user
    config → cleanup state → cleanup dispatch with no remapping.
    """
    def decorator(
        cls: Type["BuildHostProvider"],
    ) -> Type["BuildHostProvider"]:
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
# BuildHostProvider
# ---------------------------------------------------------------------------


class BuildHostProvider(abc.ABC):
    """Manages the lifecycle of build hosts.

    Replaces firesim's `BuildFarm`. The contract is intentionally smaller:
    request a host, ensure the platform HDK is present, run a build,
    release the host.

    Subclasses implement `request` / `release`; `ensure_platform` is
    shared base-class logic that consults a stamp file and the host's
    `_upload_mode` to decide between skip / upload / fail.

    For background builds (where the local process that launched the
    build is not the one that will eventually release the host),
    subclasses also implement `serialize_cleanup_state` and
    `cleanup_from_state`. These let a later process clean up using only
    the state persisted in the local stamp file — no live cfg, no live
    provider instance. `release()` is unchanged and still used by the
    synchronous build path.
    """

    @abc.abstractmethod
    def request(self, cfg: BuildConfig) -> BuildHost:
        """Return a BuildHost ready to be `connect()`ed.

        Concrete implementations must set `host._upload_mode` before
        returning so `ensure_platform` knows which policy to apply.
        """

    @abc.abstractmethod
    def release(self, host: BuildHost) -> None:
        """Release the host. For external (pre-provisioned) hosts this just
        closes the connection. For ephemeral EC2 hosts this terminates the
        instance; for managed-reuse EC2 hosts it stops the instance only
        if the provider started it (initial state was `stopped`)."""

    # ----------------------------------------------------------------------
    # Cleanup-state serialization (for background-build / stamp-driven flow)
    # ----------------------------------------------------------------------

    @abc.abstractmethod
    def serialize_cleanup_state(
        self, host: BuildHost, cfg: BuildConfig
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

    # ----------------------------------------------------------------------
    # Platform-HDK provisioning — shared across providers
    # ----------------------------------------------------------------------

    _STAMP_FILENAME = ".firesim-lab-stamp.yaml"

    def ensure_platform(
        self,
        host: BuildHost,
        cfg: BuildConfig,
        *,
        builder: Any,  # BitBuilder; typed `Any` to avoid circular import.
        force_upload: bool = False,
        log_file: Optional[Union[str, Path]] = None,
    ) -> None:
        """Decide whether to upload the platform HDK, then do so if needed.

        Before consulting `_upload_mode`, runs a registry-default conflict
        check: if the user overrode `remote_platform_path` in fslab.yaml
        and the registry-default path already carries an HDK stamp on the
        remote (e.g. baked into the AMI), abort with
        `RegistryDefaultPathConflict`. This prevents silently provisioning
        a second HDK at the user's override location. Skipped when
        `force_upload=True`, when there is no registry default (e.g.
        `external` host_model), or when the user's value matches the
        registry default. Uniform across host_models — for `external` the
        registry ships no default so the check naturally no-ops.

        Policy (consulted via `host._upload_mode`):

          fresh
              Always upload + (re)write stamp. No provider currently sets
              this; the mode is retained for explicit force-upload semantics.

          reuse_soft  (ec2_launch — both managed-reuse and ephemeral)
              Read stamp. Missing → upload (first time on this instance,
              or a stock AMI with no HDK baked in). Mismatched → upload +
              WARN (refreshing to the expected version). Match → skip.
              For ephemeral instances this is what lets a pre-baked AMI
              (with HDK installed and stamp file present) skip the
              multi-hour upload on every launch.

          reuse_strict  (external)
              Read stamp purely as a diagnostic. Mismatch → hard error
              (the user owns this box; never silently clobber GBs of HDK).
              Missing stamp or no expected version → noop (let the
              bitbuilder's later prereq check catch a truly missing HDK).

        `force_upload=True` (from `--upload-platform`) overrides everything
        — both the registry-default conflict check and the stamp-based
        policy — and forces an upload + restamp.

        `log_file`, when set, is threaded into every host call this method
        and its helpers make (stamp read/write, bootstrap, and the
        bitbuilder's upload_platform), so the pre-build upload appears in
        the same log file as the build itself.
        """
        mode: UploadMode = getattr(host, "_upload_mode", "reuse_strict")

        if force_upload:
            info("--upload-platform set: forcing HDK upload.")
            self._do_upload(host, cfg, builder, log_file=log_file)
            return

        # Registry-default conflict check. Runs uniformly across host_models;
        # naturally a no-op for `external` (registry ships no default there)
        # and for any user whose effective path matches the registry default.
        self._check_registry_default_conflict(host, cfg, log_file=log_file)

        stamp = self._read_stamp(host, cfg, log_file=log_file)
        expected_ver = getattr(cfg.host, "aws_fpga_version", None)
        stamp_ver = stamp.get("aws_fpga_version") if stamp else None

        if mode == "fresh":
            info("Fresh build host: uploading platform HDK.")
            self._do_upload(host, cfg, builder, log_file=log_file)
            return

        if mode == "reuse_soft":
            if stamp is None:
                info("No stamp on managed instance: uploading platform HDK.")
                self._do_upload(host, cfg, builder, log_file=log_file)
                return
            if expected_ver and stamp_ver != expected_ver:
                warning(
                    f"Stamp version mismatch ({stamp_ver!r} on remote vs "
                    f"expected {expected_ver!r}); refreshing platform HDK."
                )
                self._do_upload(host, cfg, builder, log_file=log_file)
                return
            info(
                f"Stamp verified (aws_fpga_version={stamp_ver!r}); "
                f"skipping HDK upload."
            )
            return

        # reuse_strict
        if stamp is None or expected_ver is None:
            # User-managed host with no version expectation — defer to the
            # bitbuilder's prereq check for the missing-HDK case.
            return
        if stamp_ver != expected_ver:
            raise PlatformVersionMismatch(
                f"Remote HDK version mismatch on user-managed host: stamp "
                f"reports aws_fpga_version={stamp_ver!r}, project expects "
                f"{expected_ver!r}.\n"
                f"  -> Pass --upload-platform to refresh the HDK from "
                f"{cfg.local_platform_path}, or update the remote install "
                f"to the expected version."
            )

    # --- stamp + upload internals ----------------------------------------

    # Path to the bootstrap script that runs on the remote after a fresh
    # HDK upload. Lives outside the python package — under
    # `fslab-cli/scripts/<platform>/bootstrap.sh` — so it stays editable
    # without touching the package tree and fits the firesim-lab dev
    # workflow (source tree is always available inside the container).
    # Resolved as ../../scripts/ec2_f2/bootstrap.sh relative to this file
    # (fslab-cli/fslab/bitstream/buildhost.py → fslab-cli/scripts/...).
    _BOOTSTRAP_SCRIPT_PATH: Path = (
        Path(__file__).parent.parent.parent
        / "scripts" / "ec2_f2" / "bootstrap.sh"
    )
    _REMOTE_BOOTSTRAP_PATH: str = "/tmp/firesim-lab-bootstrap.sh"

    def _do_upload(
        self,
        host: BuildHost,
        cfg: BuildConfig,
        builder: Any,
        *,
        log_file: Optional[Union[str, Path]] = None,
    ) -> None:
        builder.upload_platform(host, log_file=log_file)
        self._run_bootstrap(host, cfg, log_file=log_file)
        self._write_stamp(host, cfg, log_file=log_file)

    def _run_bootstrap(
        self,
        host: BuildHost,
        cfg: BuildConfig,
        *,
        log_file: Optional[Union[str, Path]] = None,
    ) -> None:
        """Push and run the post-upload bootstrap script.

        Soft-fail by design: a non-zero exit logs a warning but does not
        block the build — the bitbuilder's own prereq + build steps will
        surface a hard failure with platform-specific context. The
        bootstrap is a fast sanity probe (HDK sourceable, vivado on
        PATH); skipping it on a system where it can't run shouldn't gate
        the user.
        """
        if not self._BOOTSTRAP_SCRIPT_PATH.is_file():
            warning(
                f"Bootstrap script not found at {self._BOOTSTRAP_SCRIPT_PATH}; "
                f"skipping post-upload sanity check."
            )
            return
        host.put(
            str(self._BOOTSTRAP_SCRIPT_PATH),
            self._REMOTE_BOOTSTRAP_PATH,
            log_file=log_file,
        )
        # Wrap in `bash -lc` so the bootstrap (and the hdk_setup.sh it
        # sources) runs under a login shell. Fabric's `exec_command`
        # gives us a non-login, non-interactive shell by default, which
        # on the FPGA Developer AMI means /etc/profile.d/* and ~/.profile
        # are not sourced and `vivado` is not on PATH — making
        # hdk_setup.sh fail its `type vivado` check.
        bootstrap_cmd = (
            f"bash {shlex.quote(self._REMOTE_BOOTSTRAP_PATH)} "
            f"{shlex.quote(cfg.remote_platform_path)}"
        )
        r = host.run(
            f"bash -lc {shlex.quote(bootstrap_cmd)}",
            warn=True,
            log_file=log_file,
        )
        if r.return_code != 0:
            warning(
                f"Bootstrap reported issues (exit {r.return_code}); the build "
                f"will proceed but may fail at the build-script stage."
            )

    def _stamp_path(self, cfg: BuildConfig) -> str:
        return f"{cfg.remote_platform_path}/{self._STAMP_FILENAME}"

    def _stamp_path_for(self, remote_platform_path: str) -> str:
        """Variant of `_stamp_path` that takes the platform path directly.

        Used by `_check_registry_default_conflict` to probe the
        registry-default path (which differs from `cfg.remote_platform_path`
        when the user has supplied an override)."""
        return f"{remote_platform_path}/{self._STAMP_FILENAME}"

    def _check_registry_default_conflict(
        self,
        host: BuildHost,
        cfg: BuildConfig,
        *,
        log_file: Optional[Union[str, Path]] = None,
    ) -> None:
        """Abort if the user's override path differs from the registry
        default AND the registry default already carries a stamp file.

        Three early-exit conditions:
          1. No registry default available (e.g. `external` host_model,
             whose registry entry ships `host_models.external: {}`).
          2. Registry default equals the effective path — no override in
             play, nothing to conflict with.
          3. Registry-default path has no stamp file — nothing baked in,
             upload to override path is safe.

        The probe runs a single `test -f` on the remote; cheap enough to
        do on every build."""
        reg_default = cfg.registry_default_remote_platform_path
        if not reg_default:
            return
        if reg_default == cfg.remote_platform_path:
            return

        reg_stamp_path = self._stamp_path_for(reg_default)
        r = host.run(
            f"test -f {shlex.quote(reg_stamp_path)}",
            warn=True, hide=True,
            log_file=log_file,
        )
        if r.return_code != 0:
            # No stamp at registry-default path → no baked-in HDK to clash
            # with. Fall through and let the normal mode-based policy run.
            return

        raise RegistryDefaultPathConflict(
            f"Registry-default platform path already contains an HDK on "
            f"the remote, but the project's effective remote_platform_path "
            f"is set to a different location.\n"
            f"  registry default: {reg_default}\n"
            f"  effective path:   {cfg.remote_platform_path}\n"
            f"  -> Likely fix: remove the `remote_platform_path:` override "
            f"from your fslab.yaml so the registry default is used and the "
            f"baked-in HDK can be reused.\n"
            f"  -> To install a second HDK at the override path anyway "
            f"(e.g. during HDK development), pass --upload-platform."
        )

    def _read_stamp(
        self,
        host: BuildHost,
        cfg: BuildConfig,
        *,
        log_file: Optional[Union[str, Path]] = None,
    ) -> Optional[dict]:
        path = self._stamp_path(cfg)
        r = host.run(
            f"cat {shlex.quote(path)}",
            warn=True, hide=True,
            log_file=log_file,
        )
        if r.return_code != 0:
            return None
        try:
            data = yaml.safe_load(r.stdout) or {}
        except yaml.YAMLError as e:
            warning(f"Could not parse stamp file at {path}: {e}; treating as missing.")
            return None
        if not isinstance(data, dict):
            warning(f"Stamp file at {path} is not a mapping; treating as missing.")
            return None
        return data

    def _write_stamp(
        self,
        host: BuildHost,
        cfg: BuildConfig,
        *,
        log_file: Optional[Union[str, Path]] = None,
    ) -> None:
        """Write a minimal YAML stamp via a base64-encoded heredoc.

        Base64 sidesteps shell-quoting concerns for the embedded YAML
        content. The file is small (a few hundred bytes) so encoding cost
        is negligible.
        """
        path = self._stamp_path(cfg)
        expected_ver = (
            getattr(cfg.host, "aws_fpga_version", None) or "unknown"
        )
        stamp = {
            "aws_fpga_version": expected_ver,
            "platform": cfg.platform_id,
            "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "uploaded_by_user": getattr(cfg.host, "ssh_user", None)
                or getattr(cfg.host, "user", "unknown"),
        }
        yaml_text = yaml.safe_dump(stamp, default_flow_style=False, sort_keys=True)
        b64 = base64.b64encode(yaml_text.encode("utf-8")).decode("ascii")
        # mkdir -p the parent in case the platform path was created
        # earlier in the upload; idempotent anyway.
        host.run(
            f"mkdir -p {shlex.quote(cfg.remote_platform_path)} && "
            f"echo {b64} | base64 -d > {shlex.quote(path)}",
            log_file=log_file,
        )
        info(f"Wrote stamp: {path} (aws_fpga_version={expected_ver})")


# ---------------------------------------------------------------------------
# ExternalBuildHostProvider
# ---------------------------------------------------------------------------


@register_provider("external")
class ExternalBuildHostProvider(BuildHostProvider):
    """For pre-provisioned hosts. No launch/terminate, just close on release."""

    def request(self, cfg: BuildConfig) -> BuildHost:
        host_cfg = cfg.host
        if not isinstance(host_cfg, ExternalHostConfig):
            # Should be unreachable: make_build_host_provider only returns
            # this provider when host.type == "external".
            raise RuntimeError(
                f"ExternalBuildHostProvider received host of type "
                f"{host_cfg.type!r}; expected 'external'."
            )
        host = ExternalBuildHost(host_cfg)
        host._upload_mode = "reuse_strict"
        return host

    def release(self, host: BuildHost) -> None:
        host.close()

    # ----------------------------------------------------------------------
    # Stamp-driven cleanup
    # ----------------------------------------------------------------------

    def serialize_cleanup_state(
        self, host: BuildHost, cfg: BuildConfig
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
# Ec2LaunchBuildHostProvider
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
class Ec2LaunchBuildHostProvider(BuildHostProvider):
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

    def request(self, cfg: BuildConfig) -> BuildHost:
        host_cfg = cfg.host
        if not isinstance(host_cfg, Ec2LaunchHostConfig):
            raise RuntimeError(
                f"Ec2LaunchBuildHostProvider received host of type "
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

    def release(self, host: BuildHost) -> None:
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
        self, host: BuildHost, cfg: BuildConfig
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

    def _acquire_managed(self, host_cfg: Ec2LaunchHostConfig) -> BuildHost:
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
        host = Ec2LaunchBuildHost(params, instance_id=instance_id)
        host._upload_mode = "reuse_soft"
        return host

    # ----------------------------------------------------------------------
    # Ephemeral path
    # ----------------------------------------------------------------------

    def _acquire_ephemeral(self, host_cfg: Ec2LaunchHostConfig) -> BuildHost:
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
        host = Ec2LaunchBuildHost(params, instance_id=instance_id)
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
        ExternalBuildHost's SSH/rsync logic can be reused verbatim. The
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


def make_build_host_provider(cfg: BuildConfig) -> BuildHostProvider:
    """Pick a provider for the current host_model discriminator.

    Adding a new host_model requires a new branch here in lockstep with a
    new schema class in fslab.schemas.host_model and an entry in
    KNOWN_HOST_MODELS.
    """
    host_type = cfg.host.type
    if host_type == "external":
        return ExternalBuildHostProvider()
    if host_type == "ec2_launch":
        return Ec2LaunchBuildHostProvider()
    raise NotImplementedError(f"Unknown host_model type: {host_type!r}")
