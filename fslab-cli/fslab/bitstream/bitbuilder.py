"""Bitstream builder.

Top-level entry point is `build_bitstream(project, registry, *, upload_platform=False, log_file=None)`,
which fslab's `build fpga` CLI subcommand calls directly. Internally it:

  1. Resolves a `BuildConfig` from the validated project + registry inputs.
  2. Picks a `BuildHostProvider` and `BitBuilder` for the platform.
  3. Requests a host, connects, asks the provider to ensure the platform
     HDK is present (stamp-aware decision), runs the build, and always
     releases the host.
  4. Runs the configured publisher AFTER releasing the host (so long
     S3 uploads / AFI polls don't keep an EC2 instance billing).

The platform-specific recipe lives in `F2BitBuilder.build_bitstream`.
The platform-specific *upload* recipe (which dirs to rsync, which
excludes apply) lives in `F2BitBuilder.upload_platform` and is invoked
by the provider's `ensure_platform` only when needed.

`--upload-platform` is now a force-override flag rather than a literal
"do the upload" switch — the provider decides by default. The user
still passes it when refreshing the HDK during HDK development work.
It also bypasses the registry-default conflict check (see
`ensure_platform`) for the rare case where installing a second HDK at
a custom override path is actually desired.

Post-build artifact handling (S3 upload, AGFI submission, etc.) lives in
`publisher.py` and is dispatched off `cfg.publish.type`.

BitBuilder selection
--------------------
Subclasses self-register via `@register_bitbuilder_class`. The registry
yaml (`bitbuilders[]`) names the class by string (`python_class:`) and
`make_bitbuilder` resolves the name through BITBUILDER_CLASS_REGISTRY.
The resolution mirrors how `bitbuilder_args.py` registers args/params
schema classes — three string-keyed registries, one per axis.

Logging:
  When `log_file` is passed, every remote-session call during the build
  AND during the pre-build platform-HDK upload — `host.run`, `host.put`,
  `host.rsync_to`, `host.rsync_from` — appends a structured record to
  that file. `run` and `put` write through fabric; rsync goes through
  `fslab.utils.shell.run_or_die`. The top-level `build_bitstream` threads
  `log_file` into both `provider.ensure_platform` (covering the upload
  path) and `builder.build_bitstream` (covering the build path).
"""

from __future__ import annotations

import abc
import enum
import shlex
import time
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from fslab.schemas.publish import AwsAfiPublishConfig
from fslab.utils.display import console, error, info, section, success, warning

from . import aws_fpga
from .build_stamp import (
    BuildInfo,
    BuildStamp,
    BuildStatus,
    RemoteInfo,
    make_build_id,
    read_stamp,
    stamp_path_for,
    utc_now_iso,
    write_stamp,
)
from .buildconfig import BuildConfig, InvalidBuildConfig
from .buildhost import (
    BuildHost,
    cleanup_remote,
    make_build_host_provider,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BitstreamBuildFailed(Exception):
    """Raised when a bitstream build cannot proceed (missing prereqs).

    Pre-launch setup (stage_for_remote_build, validate_remote_auth) raises
    this on failure. Once the wrapper has been backgrounded on the remote,
    its pass/fail outcome is surfaced via the local stamp's `status` field
    and the wrapper's `result.yaml`, not via this exception."""


# ---------------------------------------------------------------------------
# PostStatus — generic post-wrapper poll outcome (background-build flow)
# ---------------------------------------------------------------------------


class PostStatus(enum.Enum):
    """Result of one `check_post_wrapper_status` poll.

    The orchestrator (`fslab monitor build`) drives state transitions
    based on this enum and does NOT interpret the platform-specific
    `info_dict` that accompanies it. Platforms with no post-wrapper
    finalization phase simply return `DONE` on the first call.

      PENDING   the platform's async finalization step is still in
                progress (e.g. AWS AFI build is `pending`); monitor
                should sleep + re-poll.
      DONE      terminal success — final artifact is ready (or there
                was nothing to finalize). Monitor moves stamp to
                status=succeeded and exits the poll loop.
      FAILED    terminal failure on the finalization step itself.
                Monitor moves stamp to status=failed and exits.
    """
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# BitBuilder class registry (decorator-populated)
# ---------------------------------------------------------------------------


BITBUILDER_CLASS_REGISTRY: dict[str, type["BitBuilder"]] = {}


def register_bitbuilder_class(cls: type["BitBuilder"]) -> type["BitBuilder"]:
    """Register a BitBuilder subclass keyed by its class name.

    The bitbuilder catalog (registry.yaml `bitbuilders[].python_class`)
    references classes by string. `make_bitbuilder` resolves that string
    against this registry at build time.
    """
    BITBUILDER_CLASS_REGISTRY[cls.__name__] = cls
    return cls


# ---------------------------------------------------------------------------
# BitBuilder (abstract)
# ---------------------------------------------------------------------------


class BitBuilder(abc.ABC):
    """Platform-agnostic interface for running a bitstream build on a host.

    Subclasses implement the per-platform recipe. The contract is small on
    purpose: the caller owns the host lifecycle, the BitBuilder just uses
    whatever host it is handed.

    `upload_platform` is invoked by the provider's `ensure_platform` when
    the stamp policy decides an upload is needed. Splitting it out from
    `build_bitstream` lets the provider own the "should we upload?"
    decision while the bitbuilder keeps the platform-specific "how do we
    upload?" knowledge.
    """

    def __init__(self, cfg: BuildConfig):
        self.cfg = cfg

    @abc.abstractmethod
    def upload_platform(
        self,
        host: BuildHost,
        *,
        log_file: Optional[Union[str, Path]] = None,
    ) -> None:
        """Push the platform HDK to the remote host.

        Called by `BuildHostProvider.ensure_platform` (not by
        `build_bitstream` directly). Implementations are responsible for
        all the platform-specific layout — which subdirs to send, which
        excludes apply, where to place the cl template.

        `log_file`, when set, captures every remote-session call (run,
        put, rsync) issued during the upload — matching the behaviour of
        `build_bitstream`'s `log_file` parameter.
        """

    @abc.abstractmethod
    def stage_for_remote_build(
        self,
        host: BuildHost,
        *,
        log_file: Optional[Union[str, Path]] = None,
    ) -> None:
        """Push everything the background-build wrapper will need: cl
        template, project staging artifacts, build-bitstream.sh, and the
        rendered remote_build_<platform>.sh wrapper script.

        Called once per `fslab build fpga`, immediately before
        `launch_remote_wrapper`. The platform HDK is assumed already
        present on `host` (the caller runs `provider.ensure_platform` first).
        """

    @abc.abstractmethod
    def launch_remote_wrapper(
        self,
        host: BuildHost,
        *,
        env: dict,
        log_file: Optional[Union[str, Path]] = None,
    ) -> int:
        """Launch the wrapper script in the background on the remote, with
        the given env vars set. Returns the wrapper's PID.

        The SSH session that runs this method exits as soon as the
        backgrounded wrapper is disowned; the wrapper keeps running via
        nohup. Implementations also write the PID to a remote pid file
        so `fslab monitor build` can probe liveness later.
        """

    # ----------------------------------------------------------------------
    # Remote-auth sanity probe (default no-op)
    # ----------------------------------------------------------------------

    def validate_remote_auth(self, host: BuildHost) -> None:
        """Best-effort probe that the remote host has the credentials the
        wrapper script will need. Raises `BitstreamBuildFailed` if not.

        Default implementation is a no-op — platforms with no remote-auth
        requirements (or where the credential is self-evident from the
        host_model) skip this. F2 overrides to call
        `aws sts get-caller-identity` on the remote and fail fast if
        AWS credentials are missing.
        """
        return None

    # ----------------------------------------------------------------------
    # Post-wrapper status (background-build flow)
    # ----------------------------------------------------------------------

    def check_post_wrapper_status(
        self, result: dict, stamp: dict
    ) -> tuple["PostStatus", dict]:
        """Called by `fslab monitor build` after the remote wrapper exits
        with rc=0, to determine whether the platform's async finalization
        step (e.g. AFI build for F2) has completed.

        Returns `(status, info_dict)`:
          status     drives the orchestrator's poll loop (see `PostStatus`).
          info_dict  platform-specific extra info that the orchestrator
                     merges verbatim into `stamp.post_wrapper.*` for user
                     display — typical keys: `state`, `message`.

        Default implementation: the platform has no post-wrapper phase,
        so wrapper success is terminal — return `(DONE, {})` on first call.
        Platforms with a finalization phase (F2 → AFI) override this.

        `result` is the parsed contents of `result.yaml` produced by the
        wrapper. `stamp` is the local build stamp as a plain dict; pass it
        through so platform implementations can read fields they need (e.g.
        the AWS region/profile recorded in `stamp.cleanup`).
        """
        return PostStatus.DONE, {}


# ---------------------------------------------------------------------------
# F2BitBuilder
# ---------------------------------------------------------------------------


@register_bitbuilder_class
class F2BitBuilder(BitBuilder):
    """Build an AWS F2 DCP/tarball using aws-fpga-firesim-f2 on the remote.

    Background-build flow (per docs/background-build-monitor-handoff.md):

    `stage_for_remote_build` does:
      1. cp -rf <template_cl> -T <cl_dir>     (preserves in-tree symlinks)
      2. ln -sf synth_cl_firesim.tcl synth_cl_<quintuplet>.tcl
      3. rsync local build/fpga/cl_<quintuplet>/ -> remote cl_dir/
      4. upload build-bitstream.sh
      5. upload remote_build_f2.sh (the wrapper)
      6. mkdir -p <cl_dir>/.fslab/

    `launch_remote_wrapper` then nohup's the wrapper with the per-build
    env vars and captures the PID. The wrapper itself runs the build,
    uploads the DCP to S3, submits create-fpga-image, and writes
    result.yaml — all on the remote, with no local SSO dependency.

    Post-wrapper finalization (AFI build state polling) lives in
    `check_post_wrapper_status`, invoked by the monitor.

    The platform-HDK upload (rsync of aws-fpga-firesim-f2 + cl_firesim
    template) is owned by the provider via `ensure_platform`; it calls
    back into `F2BitBuilder.upload_platform` only when needed.
    """

    # Subdirectories under the platform's HDK that should NOT be uploaded
    # (would otherwise drag in the user's local cl_* directories from
    # previous in-tree experiments). The cl_firesim template is then
    # rsynced separately.
    _PLATFORM_UPLOAD_EXCLUDES = ["hdk/cl/developer_designs/cl_*"]

    # Local + remote layout for the background-build wrapper. The wrapper
    # is rendered into the project tree by `fslab generate` (see
    # `_render_templates` in commands/build.py) and uploaded fresh to
    # `${remote_cl_dir}/<_REMOTE_WRAPPER_NAME>` on every `fslab build fpga`.
    # The `.fslab/` subdir under `${remote_cl_dir}` holds all wrapper-
    # produced state: remote_stamp.yaml, build.log, pid, result.yaml.
    _LOCAL_WRAPPER_RELPATH = Path("scripts") / "remote_build_f2.sh"
    _REMOTE_WRAPPER_NAME = "remote_build_f2.sh"
    _REMOTE_FSLAB_SUBDIR = ".fslab"

    def __init__(self, cfg: BuildConfig) -> None:
        super().__init__(cfg)
        # Per-call state set by the public entry points (upload_platform /
        # build_bitstream). The internal _run/_put/_rsync_* wrappers read
        # this so step methods stay readable. Cleared in finally so a
        # second invocation using the same builder instance starts clean.
        self._log_file: Optional[Path] = None

    # ----------------------------------------------------------------------
    # Public entry points
    # ----------------------------------------------------------------------

    def upload_platform(
        self,
        host: BuildHost,
        *,
        log_file: Optional[Union[str, Path]] = None,
    ) -> None:
        """Mirror fpga.mk stamp behaviour: push the platform HDK base, then
        push the cl_firesim template separately so we can exclude developer
        cl_* dirs from the base sync. (For F2 the HDK is aws-fpga-firesim-f2.)

        Called by the provider's `ensure_platform`; the provider writes
        the .firesim-lab-stamp.yaml after this returns.
        """
        cfg = self.cfg
        self._log_file = Path(log_file).expanduser() if log_file else None

        try:
            local_template = (
                cfg.local_platform_path
                / cfg.remote_cl_parent_subdir
                / cfg.template_cl_name
            )
            if not local_template.is_dir():
                raise BitstreamBuildFailed(
                    f"cl template missing locally: {local_template}"
                )

            info(
                f"Uploading platform HDK -> {_host_label(host)}:{cfg.remote_platform_path}"
            )
            if self._log_file:
                info(f"Logging upload output to {self._log_file}")
            self._run(host, f"mkdir -p {shlex.quote(cfg.remote_platform_path)}")
            self._rsync_to(
                host,
                str(cfg.local_platform_path) + "/",
                cfg.remote_platform_path + "/",
                exclude=self._PLATFORM_UPLOAD_EXCLUDES,
                follow_symlinks=False,
                label="[rsync hdk-base]",
            )

            info(
                f"Uploading cl template -> {_host_label(host)}:{cfg.remote_template_cl}"
            )
            self._run(host, f"mkdir -p {shlex.quote(cfg.remote_template_cl)}")
            self._rsync_to(
                host,
                str(local_template) + "/",
                cfg.remote_template_cl + "/",
                follow_symlinks=False,
                label="[rsync cl-template]",
            )
        finally:
            self._log_file = None

    def stage_for_remote_build(
        self,
        host: BuildHost,
        *,
        log_file: Optional[Union[str, Path]] = None,
    ) -> None:
        cfg = self.cfg
        self._log_file = Path(log_file).expanduser() if log_file else None

        info(
            f"Staging F2 inputs for {cfg.quintuplet} on {_host_label(host)} "
            f"(freq={cfg.fpga_frequency} MHz, strategy={cfg.build_strategy.name})"
        )
        if self._log_file:
            info(f"Logging command output to {self._log_file}")

        try:
            # ensure_platform (called by the orchestrator before this method)
            # may have skipped the HDK upload; verify the cl template is
            # actually present and give a clear remediation hint if not.
            self._validate_remote_prereqs(host)

            self._stage_template(host)
            self._create_synth_symlink(host)
            self._overlay_project_staging(host)
            self._upload_build_script(host)
            self._upload_wrapper_script(host)
            self._prepare_remote_fslab_dir(host)
        finally:
            self._log_file = None

    def launch_remote_wrapper(
        self,
        host: BuildHost,
        *,
        env: dict,
        log_file: Optional[Union[str, Path]] = None,
    ) -> int:
        """nohup-launch the wrapper script in the background on the remote
        and return its PID. The SSH session exits as soon as the wrapper
        is disowned — `nohup` is what keeps it alive across the
        disconnect."""
        cfg = self.cfg
        self._log_file = Path(log_file).expanduser() if log_file else None
        try:
            wrapper_path = (
                f"{cfg.remote_cl_dir}/{self._REMOTE_WRAPPER_NAME}"
            )
            log_path = env["LOG_PATH"]
            pid_path = f"{cfg.remote_cl_dir}/.fslab/pid"

            # Compose `VAR=value VAR2=value2 ...` prefix for the launch.
            # All values are shell-quoted; keys are alphanumeric/underscore
            # by construction (Phase 5 orchestrator composes them).
            env_prefix = " ".join(
                f"{k}={shlex.quote(str(v))}" for k, v in env.items()
            )

            # `< /dev/null` on the nohup'd wrapper is critical: paramiko's
            # exec channel won't close until ALL three inherited fds
            # (stdin/stdout/stderr) are detached from it. Without the
            # stdin redirect, this run() call hangs locally even though
            # the wrapper is happily running on the remote.
            launch_cmd = (
                f"cd {shlex.quote(cfg.remote_cl_dir)} && "
                f"set -m; {env_prefix} " # set -m; ensures nohup returns immediately.
                f"nohup bash {shlex.quote(wrapper_path)} "
                f"< /dev/null > {shlex.quote(log_path)} 2>&1 & "
                f"echo $! > {shlex.quote(pid_path)}"
            )
            # Wrap in `bash -lc` so the wrapper (and the HDK setup scripts it
            # sources) runs under a login shell. Fabric's `exec_command` gives
            # a non-login, non-interactive shell by default, which on the FPGA
            # Developer AMI means /etc/profile.d/* and ~/.profile are not
            # sourced and `vivado` is not on PATH. Same fix as `_run_bootstrap`
            # in buildhost.py.
            self._run(host, f"bash -lc {shlex.quote(launch_cmd)}")

            # Read back the PID for the local stamp.
            r = self._run(
                host, f"cat {shlex.quote(pid_path)}",
                warn=True, hide=True,
            )
            pid_str = (r.stdout or "").strip()
            if not pid_str.isdigit():
                raise BitstreamBuildFailed(
                    f"Could not read wrapper PID from {pid_path}: got "
                    f"{pid_str!r}. The wrapper may not have launched."
                )
            pid = int(pid_str)
            info(f"Wrapper launched on {_host_label(host)} (pid={pid})")
            return pid
        finally:
            self._log_file = None

    # ----------------------------------------------------------------------
    # Remote-auth probe
    # ----------------------------------------------------------------------

    def validate_remote_auth(self, host: BuildHost) -> None:
        """F2 wrapper needs AWS credentials on the remote for S3 upload +
        create-fpga-image. Probe via `aws sts get-caller-identity`.

        For ec2_launch hosts this should always succeed (the schema
        requires `iam_instance_profile` and the provider attaches it at
        RunInstances). For external hosts it surfaces a missing/expired
        credential setup before we waste time uploading inputs.
        """
        r = host.run(
            "aws sts get-caller-identity --output text",
            warn=True, hide=True,
        )
        if r.return_code != 0:
            stderr_tail = "\n".join(
                (getattr(r, "stderr", "") or "").strip().splitlines()[-5:]
            )
            raise BitstreamBuildFailed(
                "Remote host has no usable AWS credentials.\n"
                f"  `aws sts get-caller-identity` exited {r.return_code} on "
                f"{_host_label(host)}.\n"
                f"  stderr tail:\n    {stderr_tail}\n"
                "  -> For ec2_launch hosts, check that "
                "host.iam_instance_profile is correct and attached.\n"
                "  -> For external hosts, the user-managed remote must "
                "have an attached instance profile or configured profile "
                "with EC2/FPGA + S3 permissions. See docs/aws-setup.md."
            )

    # ----------------------------------------------------------------------
    # Internal: thin wrappers around BuildHost methods. Each auto-applies
    # this build's log_file, so the step methods stay readable. Callers can
    # still override log_file per-call by passing it explicitly.
    # ----------------------------------------------------------------------

    def _run(self, host: BuildHost, cmd: str, **kwargs: Any) -> Any:
        kwargs.setdefault("log_file", self._log_file)
        return host.run(cmd, **kwargs)

    def _put(self, host: BuildHost, local: str, remote: str, **kwargs: Any) -> None:
        kwargs.setdefault("log_file", self._log_file)
        host.put(local, remote, **kwargs)

    def _rsync_to(self, host: BuildHost, local: str, remote: str, **kwargs: Any) -> None:
        kwargs.setdefault("log_file", self._log_file)
        host.rsync_to(local, remote, **kwargs)

    def _rsync_from(self, host: BuildHost, remote: str, local: str, **kwargs: Any) -> None:
        kwargs.setdefault("log_file", self._log_file)
        host.rsync_from(remote, local, **kwargs)

    # ----------------------------------------------------------------------
    # Steps
    # ----------------------------------------------------------------------

    def _validate_remote_prereqs(self, host: BuildHost) -> None:
        cfg = self.cfg
        r = self._run(
            host,
            f"test -d {shlex.quote(cfg.remote_template_cl)}",
            warn=True, hide=True,
        )
        if r.return_code != 0:
            raise BitstreamBuildFailed(
                f"Remote cl template not found at {cfg.remote_template_cl}.\n"
                f"  -> Pre-stage the platform HDK on the remote, or pass "
                f"--upload-platform to push it from {cfg.local_platform_path}."
            )

    def _stage_template(self, host: BuildHost) -> None:
        """cp -rf <template> -T <cl_dir>. The -T flag forces the destination
        to be the cl_dir itself (not nested), and we wipe any prior attempt
        first so re-runs are deterministic."""
        cfg = self.cfg
        self._run(host, f"mkdir -p {shlex.quote(cfg.remote_cl_parent)}")
        self._run(host, f"rm -rf {shlex.quote(cfg.remote_cl_dir)}")
        self._run(
            host,
            f"cp -rf {shlex.quote(cfg.remote_template_cl)} "
            f"-T {shlex.quote(cfg.remote_cl_dir)}",
        )
        info(f"Staged template at {cfg.remote_cl_dir}")

    def _create_synth_symlink(self, host: BuildHost) -> None:
        """F2's build_all.tcl sources synth_${CL}.tcl, where ${CL} is the cl
        directory name. Create a symlink so it finds the firesim synth script."""
        cfg = self.cfg
        scripts_dir = f"{cfg.remote_cl_dir}/build/scripts"
        symlink_name = f"synth_cl_{cfg.quintuplet}.tcl"
        self._run(
            host,
            f"cd {shlex.quote(scripts_dir)} && "
            f"ln -sf synth_cl_firesim.tcl {shlex.quote(symlink_name)}",
        )
        info(f"Created {symlink_name} -> synth_cl_firesim.tcl")

    def _overlay_project_staging(self, host: BuildHost) -> None:
        """Rsync local build/fpga/cl_<q>/ onto remote cl_dir/. This drops the
        generated design/* files and the compiled driver into place.
        Trailing slashes matter: copy contents, not the directory itself."""
        cfg = self.cfg
        self._rsync_to(
            host,
            str(cfg.local_project_staging_dir) + "/",
            cfg.remote_cl_dir + "/",
            follow_symlinks=False,
            label="[rsync project-staging]",
        )
        info(f"Overlaid project staging onto {cfg.remote_cl_dir}")

    def _upload_build_script(self, host: BuildHost) -> None:
        cfg = self.cfg
        remote_path = f"{cfg.remote_cl_dir}/{cfg.remote_build_script_name}"
        self._put(host, str(cfg.local_build_script), remote_path)
        self._run(host, f"chmod +x {shlex.quote(remote_path)}")

    def _upload_wrapper_script(self, host: BuildHost) -> None:
        """Upload the rendered remote_build_f2.sh wrapper to the remote
        cl_dir. The local file is produced by `fslab generate` (see the
        `remote_build/f2.sh.j2` entry in render_plan); we re-upload on
        every build so template edits take effect immediately."""
        cfg = self.cfg
        local_wrapper = cfg.project_dir / self._LOCAL_WRAPPER_RELPATH
        if not local_wrapper.is_file():
            raise BitstreamBuildFailed(
                f"Wrapper script not found at {local_wrapper}. Run "
                f"`fslab generate` to render it from "
                f"templates/remote_build/f2.sh.j2."
            )
        remote_path = f"{cfg.remote_cl_dir}/{self._REMOTE_WRAPPER_NAME}"
        self._put(host, str(local_wrapper), remote_path)
        self._run(host, f"chmod +x {shlex.quote(remote_path)}")
        info(f"Uploaded wrapper -> {remote_path}")

    def _prepare_remote_fslab_dir(self, host: BuildHost) -> None:
        """Pre-create `${remote_cl_dir}/.fslab/` so the wrapper's startup
        steps (writing remote_stamp.yaml, build.log, pid, result.yaml)
        don't race against directory creation."""
        cfg = self.cfg
        fslab_dir = f"{cfg.remote_cl_dir}/{self._REMOTE_FSLAB_SUBDIR}"
        self._run(host, f"mkdir -p {shlex.quote(fslab_dir)}")

    # ----------------------------------------------------------------------
    # Post-wrapper status: poll AWS describe-fpga-images for AFI state
    # ----------------------------------------------------------------------

    def check_post_wrapper_status(
        self, result: dict, stamp: dict
    ) -> tuple[PostStatus, dict]:
        """Translate AFI build state to `PostStatus`.

        The wrapper script submits `create-fpga-image` and writes the
        resulting AFI id into `result.yaml`; the actual image build runs
        in AWS-managed infra after the wrapper exits. We poll
        `describe-fpga-images` from the local CLI (instance is already
        torn down by the time monitor enters the finalizing state, so no
        cost for long AFI builds).

        State mapping:
          `available`         → DONE
          `pending`           → PENDING
          anything else       → FAILED  (includes `failed`, `unavailable`)
        """
        afi = (result.get("artifacts") or {}).get("afi")
        if not afi:
            return PostStatus.FAILED, {
                "state": "no-afi",
                "message": (
                    "Wrapper exited successfully but result.yaml has no "
                    "afi id under artifacts.afi — cannot poll AFI state."
                ),
            }

        # AWS session: prefer the profile/region recorded in the stamp's
        # cleanup block (captured at launch time, immune to fslab.yaml
        # drift). For external hosts with no cleanup metadata, fall back
        # to the default boto3 session.
        cleanup = stamp.get("cleanup") or {}
        profile = cleanup.get("aws_profile")
        region = cleanup.get("region")
        session = aws_fpga.make_session(region=region, profile=profile)
        aws_fpga.check_credentials(session, profile)
        ec2 = session.client("ec2")
        resp = ec2.describe_fpga_images(FpgaImageIds=[afi])
        images = resp.get("FpgaImages") or []
        if not images:
            return PostStatus.FAILED, {
                "state": "not-found",
                "message": f"AFI {afi} not visible via describe-fpga-images.",
            }
        img_state = (images[0].get("State") or {})
        code = img_state.get("Code", "")
        msg = img_state.get("Message", "")
        info_dict = {"state": code, "message": msg}
        if code == "available":
            return PostStatus.DONE, info_dict
        if code == "pending":
            return PostStatus.PENDING, info_dict
        return PostStatus.FAILED, info_dict


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_bitbuilder(cfg: BuildConfig, registry: Any) -> BitBuilder:
    """Pick the right BitBuilder subclass via the bitbuilder catalog.

    Looks up `registry.bitbuilders[cfg.bitbuilder_id].python_class` and
    resolves that class-name string through BITBUILDER_CLASS_REGISTRY.
    The bitbuilder id is sourced from `platforms[cfg.platform_id].bitbuilder`
    during BuildConfig construction; the existence of the entry is
    guaranteed by [BB-10] cross-validation, but the lookup is defensive
    in case validation order changes.
    """
    bb_entry = registry.bitbuilders.get(cfg.bitbuilder_id)
    if bb_entry is None:
        raise BitstreamBuildFailed(
            f"bitbuilder '{cfg.bitbuilder_id}' not found in the merged "
            f"registry. Known: {sorted(registry.bitbuilders)}."
        )

    cls = BITBUILDER_CLASS_REGISTRY.get(bb_entry.python_class)
    if cls is None:
        known = sorted(BITBUILDER_CLASS_REGISTRY)
        raise BitstreamBuildFailed(
            f"bitbuilder '{cfg.bitbuilder_id}' declares python_class="
            f"'{bb_entry.python_class}', which is not registered in "
            f"BITBUILDER_CLASS_REGISTRY. Known: {known}. Ensure the module "
            f"defining the class is imported before make_bitbuilder runs."
        )
    return cls(cfg)


# ---------------------------------------------------------------------------
# Public entry point — what `fslab build fpga` should call
# ---------------------------------------------------------------------------


def build_bitstream(
    project: Any,
    registry: Any,
    *,
    upload_platform: bool = False,
    log_file: Optional[Union[str, Path]] = None,
) -> str:
    """Launch a bitstream build *in the background* on the remote host,
    write a local stamp tracking it, and return.

    Flow (per the redesign in docs/background-build-monitor-handoff.md):
      1. Resolve `BuildConfig` from validated project + registry.
      2. Validate platform/publish compatibility (F2 + aws_afi only).
      3. In-flight guard (defense-in-depth): refuse if a stamp already
         exists. `cmd_compile` performs the same check up-front before
         any local compile work, so this firing here usually indicates a
         direct caller of `build_bitstream` or a race; either way the
         user resolves it via `fslab clean` / `fslab abandon build` per
         the hint in the raised exception.
      4. Acquire a host via the provider; capture cleanup state into a
         dict immediately so cleanup is stamp-driven from here on.
      5. ensure_platform — upload HDK if needed.
      6. `validate_remote_auth` — fail-fast if the remote can't reach AWS.
      7. `stage_for_remote_build` — push template, project staging,
         build-bitstream.sh, the wrapper script, and pre-create .fslab/.
      8. Generate `build_id`, compose the wrapper's env vars, write
         the local stamp at status=launching.
      9. `launch_remote_wrapper` — nohup the wrapper, capture PID.
     10. Verify-started: poll the remote stamp for matching build_id.
     11. Flip local stamp to status=running. Close SSH (does NOT
         terminate EC2). Return build_id.

    Cleanup is *not* performed on success — the wrapper keeps the EC2
    alive for the duration of the build. Cleanup runs later via the
    monitor (Phase 6) or `fslab abandon build` (Phase 7).

    `upload_platform` and `log_file` semantics are unchanged from the
    previous synchronous flow.

    Returns the new `build_id` (also recorded in the local stamp).
    Raises `BitstreamBuildFailed` / `InvalidBuildConfig` /
    `RegistryDefaultPathConflict` for setup errors before the wrapper
    actually backgrounds.
    """
    cfg = BuildConfig.from_validated(project, registry)
    _validate_platform_publish_compat(cfg)

    # In-flight guard: refuse outright on any existing stamp. cmd_compile
    # runs this same check before any local compile work so the user gets
    # the fail-fast UX; this call here is defense-in-depth.
    check_no_existing_build(cfg.project_dir)

    provider = make_build_host_provider(cfg)
    builder = make_bitbuilder(cfg, registry)

    host = provider.request(cfg)
    cleanup_state: Optional[dict] = None
    stamp_written = False
    try:
        host.connect()
        # Snapshot cleanup state immediately — once a stamp is written,
        # all cleanup goes through cleanup_remote() from this dict, not
        # via provider.release().
        cleanup_state = provider.serialize_cleanup_state(host, cfg)

        provider.ensure_platform(
            host, cfg,
            builder=builder,
            force_upload=upload_platform,
            log_file=log_file,
        )
        builder.validate_remote_auth(host)
        builder.stage_for_remote_build(host, log_file=log_file)

        build_id = make_build_id()
        wrapper_env = _compose_wrapper_env(cfg, build_id)
        stamp = _initial_local_stamp(cfg, build_id, host, cleanup_state, wrapper_env)
        write_stamp(cfg.project_dir, stamp)
        stamp_written = True

        builder.launch_remote_wrapper(host, env=wrapper_env, log_file=log_file)
        _verify_wrapper_started(
            host, build_id, wrapper_env["STAMP_PATH"], timeout_s=10,
        )

        stamp.status = BuildStatus.RUNNING
        write_stamp(cfg.project_dir, stamp)

        success(
            f"Background build {build_id} launched on {_host_label(host)}."
        )
        info(
            f"Local stamp: {stamp_path_for(cfg.project_dir)} | "
            f"remote log: {wrapper_env['LOG_PATH']}"
        )
        return build_id

    except Exception:
        # Failure-mode cleanup. Two branches:
        #  - stamp already written → use stamp-driven cleanup, leave the
        #    stamp in place (status=launching) so the user / monitor can
        #    see what happened.
        #  - no stamp yet → use the provider's in-memory release().
        if stamp_written and cleanup_state is not None:
            try:
                #cleanup_remote({"cleanup": cleanup_state})
                # Best-effort: mark the stamp so monitor doesn't try to
                # clean up again.
                stamp_now = read_stamp(cfg.project_dir)
                if stamp_now is not None:
                    stamp_now.status = BuildStatus.WRAPPER_FAILED
                    stamp_now.cleanup_done = True
                    stamp_now.finished_at = utc_now_iso()
                    write_stamp(cfg.project_dir, stamp_now)
            except Exception as ce:
                warning(
                    f"Cleanup-on-launch-failure failed: {ce} — the remote "
                    f"resource may still be live. Run `fslab abandon build`."
                )
        else:
            try:
                provider.release(host)
            except Exception as re:
                warning(f"provider.release() failed during error recovery: {re}")
        raise

    finally:
        # Always close the SSH connection. On success the wrapper is
        # already nohup'd; on failure the connection is no longer needed.
        # This does NOT terminate the EC2 instance.
        try:
            host.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Phase-5 helpers
# ---------------------------------------------------------------------------


def _validate_platform_publish_compat(cfg: BuildConfig) -> None:
    """Fail fast on the platform/publish combinations the background-build
    flow doesn't support yet. F2 today requires publish.type=aws_afi
    because the wrapper does S3 upload + create-fpga-image."""
    if cfg.platform_id == "f2" and not isinstance(cfg.publish, AwsAfiPublishConfig):
        raise BitstreamBuildFailed(
            f"F2 background-build flow requires publish.type=aws_afi. "
            f"Got publish.type={type(cfg.publish).__name__!r}.\n"
            f"  -> Set `target.build.publish.type: aws_afi` in fslab.yaml "
            f"and supply an `s3_bucket_name`."
        )


def check_no_existing_build(project_dir: Path) -> None:
    """Refuse to start a new build if any stamp exists for a prior build.

    Called early by `cmd_compile` (before any local compile work) and
    again from `build_bitstream` as defense-in-depth. Refusal cases and
    remediation hints:

      * Stamp non-terminal AND remote wrapper still alive
          → `fslab monitor build` to attach, or `fslab abandon build` to
            discard and clean up.
      * Stamp non-terminal but wrapper not alive
          (likely crashed before monitor could update)
          → `fslab abandon build` to release any orphaned remote resource.
      * Stamp terminal with cleanup_done=False
          → `fslab abandon build` to clean up the remote first.
      * Stamp terminal with cleanup_done=True
          → `fslab clean` to clear local staging and stamp.

    Raises `BitstreamBuildFailed` on any of the above; returns None if
    no stamp exists.
    """
    existing = read_stamp(project_dir)
    if existing is None:
        return

    stamp_file = stamp_path_for(project_dir)
    status = existing.status

    if not status.is_terminal:
        if _is_remote_wrapper_alive(existing):
            raise BitstreamBuildFailed(
                f"A build is already in flight: {existing.build_id} "
                f"(status={status.value}).\n"
                f"  Local stamp: {stamp_file}\n"
                f"  -> Use `fslab monitor build` to attach, or "
                f"`fslab abandon build` to discard and clean up."
            )
        raise BitstreamBuildFailed(
            f"Stale stamp for build {existing.build_id} "
            f"(status={status.value}); remote wrapper is not alive but "
            f"the local stamp is still present.\n"
            f"  Local stamp: {stamp_file}\n"
            f"  -> Run `fslab abandon build` to release any orphaned "
            f"remote resources and clear the stamp before starting a "
            f"new build."
        )

    if not existing.cleanup_done:
        raise BitstreamBuildFailed(
            f"Previous build {existing.build_id} ({status.value}) "
            f"completed but its remote resources were never cleaned up.\n"
            f"  Local stamp: {stamp_file}\n"
            f"  -> Run `fslab abandon build` to clean up the remote, "
            f"then start the new build."
        )

    raise BitstreamBuildFailed(
        f"Previous build {existing.build_id} ({status.value}) is "
        f"recorded at {stamp_file}.\n"
        f"  -> Run `fslab clean` to clear the prior build's staging "
        f"tree and stamp before starting a new build."
    )


def _is_remote_wrapper_alive(stamp: BuildStamp) -> bool:
    """SSH to the recorded remote, `kill -0 <pid>`. Returns False on any
    error (unreachable, no such PID, etc.) — caller treats False as
    'safe to assume the build is no longer in flight'."""
    from .buildhost import ExternalBuildHost
    from fslab.schemas.host_model import ExternalHostConfig
    try:
        params = ExternalHostConfig(
            type="external",
            host=stamp.remote.host,
            user=stamp.remote.user,
            ssh_key=stamp.remote.ssh_key_path,
            # Probe path doesn't need a real platform path; pass a dummy
            # absolute path to satisfy schema validation.
            remote_platform_path="/tmp",
        )
        probe_host = ExternalBuildHost(params)
        probe_host.connect()
        try:
            # Read the pid file and check the process.
            r = probe_host.run(
                f"cat {shlex.quote(stamp.remote.remote_pid_path)} "
                f"| xargs -I {{}} kill -0 {{}}",
                warn=True, hide=True,
            )
            return r.return_code == 0
        finally:
            probe_host.close()
    except Exception as e:
        warning(f"Could not probe remote PID liveness: {e}")
        return False


def _compose_wrapper_env(cfg: BuildConfig, build_id: str) -> dict:
    """Build the env-var dict the wrapper script consumes. Per-build
    values (build_id-stamped S3 key, AFI name) are composed here; the
    project-static config is rendered into the wrapper itself by
    `fslab generate`."""
    assert isinstance(cfg.publish, AwsAfiPublishConfig), (
        "Should have been caught by _validate_platform_publish_compat"
    )
    cl_dir = cfg.remote_cl_dir
    fslab_dir = f"{cl_dir}/{F2BitBuilder._REMOTE_FSLAB_SUBDIR}"

    s3_key = f"dcp/{cfg.project_name}-{cfg.quintuplet}-{build_id}.tar"
    afi_name_base = cfg.publish.hwdb_entry_name or cfg.project_name
    afi_name = f"{afi_name_base}-{build_id}"
    afi_description = (
        f"firesim-lab build: project={cfg.project_name} "
        f"quintuplet={cfg.quintuplet} build_id={build_id}"
    )

    return {
        "BUILD_ID": build_id,
        "S3_KEY": s3_key,
        "AFI_NAME": afi_name,
        "AFI_DESCRIPTION": afi_description,
        "CL_DIR": cl_dir,
        "REMOTE_BUILD_SCRIPT": f"{cl_dir}/{cfg.remote_build_script_name}",
        "LOG_PATH": f"{fslab_dir}/build.log",
        "RESULT_PATH": f"{fslab_dir}/result.yaml",
        "STAMP_PATH": f"{fslab_dir}/remote_stamp.yaml",
    }


def _initial_local_stamp(
    cfg: BuildConfig,
    build_id: str,
    host: BuildHost,
    cleanup_state: dict,
    env: dict,
) -> BuildStamp:
    """Build the stamp written before the wrapper is launched. Status is
    `launching` at this point; the orchestrator flips it to `running`
    once verify-started succeeds."""
    params = getattr(host, "params", None)
    return BuildStamp(
        build_id=build_id,
        started_at=utc_now_iso(),
        status=BuildStatus.LAUNCHING,
        remote=RemoteInfo(
            host=getattr(params, "host", "?") if params else "?",
            user=getattr(params, "user", "?") if params else "?",
            ssh_key_path=(
                str(params.ssh_key) if params and params.ssh_key else None
            ),
            remote_log_path=env["LOG_PATH"],
            remote_result_yaml_path=env["RESULT_PATH"],
            remote_pid_path=f"{cfg.remote_cl_dir}/{F2BitBuilder._REMOTE_FSLAB_SUBDIR}/pid",
            remote_stamp_path=env["STAMP_PATH"],
        ),
        build=BuildInfo(
            platform=cfg.platform_id,
            project_name=cfg.project_name,
            quintuplet=cfg.quintuplet,
            fpga_frequency=int(cfg.fpga_frequency)
                if cfg.fpga_frequency is not None else None,
            build_strategy=cfg.build_strategy.name
                if cfg.build_strategy is not None else None,
        ),
        cleanup=cleanup_state,
    )


def _verify_wrapper_started(
    host: BuildHost,
    build_id: str,
    remote_stamp_path: str,
    *,
    timeout_s: int = 10,
) -> None:
    """Poll the remote stamp file until its build_id matches ours or the
    timeout elapses. Raises BitstreamBuildFailed on timeout — typically
    means the wrapper failed to start (permission denied, missing dep)."""
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
                if isinstance(data, dict) and data.get("build_id") == build_id:
                    return
                last_err = (
                    f"build_id mismatch: remote={data.get('build_id')!r}, "
                    f"local={build_id!r}"
                )
            except yaml.YAMLError as e:
                last_err = f"remote stamp not valid YAML: {e}"
        else:
            last_err = f"remote stamp not present (cat rc={r.return_code})"
        time.sleep(1)
    raise BitstreamBuildFailed(
        f"Wrapper did not write a matching remote stamp within {timeout_s}s. "
        f"This usually means the wrapper failed to start (permission denied, "
        f"missing dependency, etc.).\n"
        f"  Last probe result: {last_err}\n"
        f"  Check the remote log file for details."
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _host_label(host: BuildHost) -> str:
    """Best-effort label for log messages."""
    params = getattr(host, "params", None)
    if params is not None and hasattr(params, "host"):
        return f"{params.user}@{params.host}"
    return type(host).__name__
