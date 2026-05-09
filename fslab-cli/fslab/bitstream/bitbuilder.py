"""Bitstream builder.

Top-level entry point is `build_bitstream(project, registry, *, upload_platform=False, log_file=None)`,
which fslab's `build fpga` CLI subcommand calls directly. Internally it:

  1. Resolves a `BuildConfig` from the validated project + registry inputs.
  2. Picks a `BuildHostProvider` and `BitBuilder` for the platform.
  3. Requests a host, connects, runs the build, and always releases.

The platform-specific recipe lives in `F2BitBuilder.build_bitstream`. It
mirrors firesim's F2BitBuilder up to (but not including) S3/AGFI submission —
deliberately out of scope for this iteration.

Logging:
  When `log_file` is passed, every remote-session call during the build —
  `host.run`, `host.put`, `host.rsync_to`, `host.rsync_from` — appends a
  structured record to that file. `run` and `put` write through fabric;
  rsync goes through `fslab.utils.shell.run_or_die`.
"""

from __future__ import annotations

import abc
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

from fslab.utils.display import console, error, info, section, success, warning

from .buildconfig import BuildConfig, InvalidBuildConfig
from .buildhost import (
    BuildHost,
    RsyncFailed,
    make_build_host_provider,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BitstreamBuildFailed(Exception):
    """Raised when a bitstream build cannot proceed (missing prereqs).

    A non-zero exit from build-bitstream.sh itself does NOT raise — it is
    surfaced via the boolean return of `build_bitstream`, so callers can
    still pull results back."""


# ---------------------------------------------------------------------------
# BitBuilder (abstract)
# ---------------------------------------------------------------------------


class BitBuilder(abc.ABC):
    """Platform-agnostic interface for running a bitstream build on a host.

    Subclasses implement the per-platform recipe. The contract is small on
    purpose: the caller owns the host lifecycle, the BitBuilder just uses
    whatever host it is handed.
    """

    def __init__(self, cfg: BuildConfig):
        self.cfg = cfg

    @abc.abstractmethod
    def build_bitstream(
        self,
        host: BuildHost,
        *,
        upload_platform: bool = False,
        log_file: Optional[Union[str, Path]] = None,
    ) -> bool:
        """Run the build. Returns True on success, False on script failure."""


# ---------------------------------------------------------------------------
# F2BitBuilder
# ---------------------------------------------------------------------------


class F2BitBuilder(BitBuilder):
    """Build an AWS F2 DCP/tarball using aws-fpga-firesim-f2 on the remote.

    Steps (mirror fpga.mk's stamp + replace-rtl + fpga targets):

      0. (optional) Rsync local platform HDK (aws-fpga-firesim-f2) to remote.
      1. cp -rf <template_cl> -T <cl_dir>     (preserves in-tree symlinks)
      2. ln -sf synth_cl_firesim.tcl synth_cl_<quintuplet>.tcl
      3. rsync local build/fpga/cl_<quintuplet>/ -> remote cl_dir/
      4. upload build-bitstream.sh
      5. run build-bitstream.sh --cl_dir ... --frequency ... --strategy ...
      6. reverse-rsync the entire cl_dir back to local results dir
         (best-effort, runs on both pass and fail)

    Out of scope (deferred): S3 upload of DCP, `aws ec2 create-fpga-image`,
    AGFI polling, hwdb entry generation, post_build_hook, SNS notifications.
    """

    # Subdirectories under the platform's HDK that should NOT be uploaded
    # when --upload-platform is set (would otherwise drag in the user's
    # local cl_* directories from previous in-tree experiments). The
    # cl_firesim template is then rsynced separately.
    _PLATFORM_UPLOAD_EXCLUDES = ["hdk/cl/developer_designs/cl_*"]

    def __init__(self, cfg: BuildConfig) -> None:
        super().__init__(cfg)
        # Per-build state set in build_bitstream(). Cleared in finally so a
        # second build using the same builder instance starts clean.
        self._log_file: Optional[Path] = None

    # ----------------------------------------------------------------------
    # Public entry point
    # ----------------------------------------------------------------------

    def build_bitstream(
        self,
        host: BuildHost,
        *,
        upload_platform: bool = False,
        log_file: Optional[Union[str, Path]] = None,
    ) -> bool:
        cfg = self.cfg
        self._log_file = Path(log_file).expanduser() if log_file else None

        info(
            f"Starting F2 bitstream build for {cfg.quintuplet} "
            f"(freq={cfg.fpga_frequency} MHz, strategy={cfg.build_strategy.name})"
        )
        if self._log_file:
            info(f"Logging command output to {self._log_file}")

        try:
            # ---------- pre-build setup (raises on failure) ----------
            if upload_platform:
                self._upload_platform(host)
            self._validate_remote_prereqs(host)

            self._stage_template(host)
            self._create_synth_symlink(host)
            self._overlay_project_staging(host)
            self._upload_build_script(host)

            # ---------- run the build (does NOT raise on non-zero) ----
            rc = self._run_build_script(host)

            # ---------- reverse-rsync results regardless of pass/fail ----
            self._pull_results(host, build_passed=(rc == 0))

            if rc != 0:
                error(f"F2 bitstream build FAILED (build-bitstream.sh rc={rc})")
                return False

            info(f"F2 bitstream build SUCCEEDED for {cfg.quintuplet}")
            return True
        finally:
            self._log_file = None

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

    def _upload_platform(self, host: BuildHost) -> None:
        """Mirror fpga.mk stamp behaviour: push the platform HDK base, then
        push the cl_firesim template separately so we can exclude developer
        cl_* dirs from the base sync. (For F2 the HDK is aws-fpga-firesim-f2.)"""
        cfg = self.cfg
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

    def _run_build_script(self, host: BuildHost) -> int:
        cfg = self.cfg
        remote_script = f"{cfg.remote_cl_dir}/{cfg.remote_build_script_name}"
        cmd = (
            f"{shlex.quote(remote_script)} "
            f"--cl_dir {shlex.quote(cfg.remote_cl_dir)} "
            f"--frequency {cfg.fpga_frequency} "
            f"--strategy {cfg.build_strategy.name}"
        )
        info(f"Running build script on remote: {cmd}")
        # warn=True so we can still pull results on failure.
        # pty=True so Vivado output streams in real-time.
        result = self._run(host, cmd, warn=True, pty=True)
        return result.return_code

    def _pull_results(self, host: BuildHost, *, build_passed: bool) -> None:
        """Reverse-rsync the entire remote cl_dir back to a timestamped local
        results directory. Best-effort: a failure here is logged but does not
        mask the actual build outcome."""
        cfg = self.cfg
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d--%H-%M-%S")
        suffix = "PASS" if build_passed else "FAIL"
        local_dst = (
            cfg.local_results_base
            / f"{ts}-{cfg.project_name}-{suffix}"
            / f"cl_{cfg.quintuplet}"
        )
        try:
            local_dst.mkdir(parents=True, exist_ok=True)
            self._rsync_from(
                host,
                cfg.remote_cl_dir + "/",
                str(local_dst) + "/",
                label="[rsync pull-results]",
            )
            info(f"Build artifacts synced to {local_dst}")
        except RsyncFailed as e:
            warning(f"Result rsync failed (build {suffix}): {e}")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_bitbuilder(cfg: BuildConfig) -> BitBuilder:
    """Pick the right BitBuilder subclass for the current platform."""
    if cfg.platform_id == "f2":
        return F2BitBuilder(cfg)
    raise NotImplementedError(
        f"BitBuilder for platform '{cfg.platform_id}' is not implemented yet."
    )


# ---------------------------------------------------------------------------
# Public entry point — what `fslab build fpga` should call
# ---------------------------------------------------------------------------


def build_bitstream(
    project: Any,
    registry: Any,
    *,
    upload_platform: bool = False,
    log_file: Optional[Union[str, Path]] = None,
) -> bool:
    """Resolve config, request a host, run the build, release the host.

    Returns True on success, False on build-script failure. Raises
    `BitstreamBuildFailed` / `InvalidBuildConfig` for setup errors.

    `log_file`, when set, captures every remote-session call (run, put,
    rsync) issued during the build. Output also streams to the console
    in real time.
    """
    cfg = BuildConfig.from_validated(project, registry)
    provider = make_build_host_provider(cfg)
    builder = make_bitbuilder(cfg)

    host = provider.request(cfg)
    try:
        host.connect()
        return builder.build_bitstream(
            host,
            upload_platform=upload_platform,
            log_file=log_file,
        )
    finally:
        provider.release(host)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _host_label(host: BuildHost) -> str:
    """Best-effort label for log messages."""
    params = getattr(host, "params", None)
    if params is not None and hasattr(params, "host"):
        return f"{params.user}@{params.host}"
    return type(host).__name__