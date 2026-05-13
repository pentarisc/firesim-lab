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
import shlex
from dataclasses import dataclass
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
from .publisher import PublishInputs, make_publisher


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BitstreamBuildFailed(Exception):
    """Raised when a bitstream build cannot proceed (missing prereqs).

    A non-zero exit from build-bitstream.sh itself does NOT raise — it is
    surfaced via `BuildResult.passed` so callers can still pull results
    back."""


# ---------------------------------------------------------------------------
# BuildResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildResult:
    """Outcome of a bitstream build, returned by `BitBuilder.build_bitstream`.

    `local_results_dir` is the timestamped cl_dir-shaped directory the
    bitbuilder rsynced back from the build host. The publisher needs this
    path to locate the DCP tarball. It is `None` when the build aborted
    before the result-pull step ran or the rsync itself failed.
    """

    passed: bool
    local_results_dir: Optional[Path]


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
    def build_bitstream(
        self,
        host: BuildHost,
        *,
        log_file: Optional[Union[str, Path]] = None,
    ) -> BuildResult:
        """Run the build. Returns a BuildResult with pass/fail and the local
        results directory (when results were successfully pulled back).

        The platform HDK is assumed already present on `host` — the caller
        runs `provider.ensure_platform(...)` before invoking this method.
        """


# ---------------------------------------------------------------------------
# F2BitBuilder
# ---------------------------------------------------------------------------


@register_bitbuilder_class
class F2BitBuilder(BitBuilder):
    """Build an AWS F2 DCP/tarball using aws-fpga-firesim-f2 on the remote.

    Steps (mirror fpga.mk's stamp + replace-rtl + fpga targets):

      1. cp -rf <template_cl> -T <cl_dir>     (preserves in-tree symlinks)
      2. ln -sf synth_cl_firesim.tcl synth_cl_<quintuplet>.tcl
      3. rsync local build/fpga/cl_<quintuplet>/ -> remote cl_dir/
      4. upload build-bitstream.sh
      5. run build-bitstream.sh --cl_dir ... --frequency ... --strategy ...
      6. reverse-rsync the entire cl_dir back to local results dir
         (best-effort, runs on both pass and fail)

    The platform-HDK upload (rsync of aws-fpga-firesim-f2 + cl_firesim
    template) is owned by the provider via `ensure_platform`; it calls
    back into `F2BitBuilder.upload_platform` only when needed.

    Post-build artifact handling (S3 upload of DCP, `create-fpga-image`,
    AGFI polling, etc.) lives in `publisher.py`, dispatched on
    `cfg.publish.type`. The bitbuilder's contract ends at returning a
    `BuildResult` with the local results directory populated.
    """

    # Subdirectories under the platform's HDK that should NOT be uploaded
    # (would otherwise drag in the user's local cl_* directories from
    # previous in-tree experiments). The cl_firesim template is then
    # rsynced separately.
    _PLATFORM_UPLOAD_EXCLUDES = ["hdk/cl/developer_designs/cl_*"]

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

    def build_bitstream(
        self,
        host: BuildHost,
        *,
        log_file: Optional[Union[str, Path]] = None,
    ) -> BuildResult:
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
            # ensure_platform (called by the orchestrator before this
            # method) may have skipped the upload; verify the cl template
            # is actually present and give a clear remediation hint if not.
            self._validate_remote_prereqs(host)

            self._stage_template(host)
            self._create_synth_symlink(host)
            self._overlay_project_staging(host)
            self._upload_build_script(host)

            # ---------- run the build (does NOT raise on non-zero) ----
            rc = self._run_build_script(host)

            # ---------- reverse-rsync results regardless of pass/fail ----
            local_results_dir = self._pull_results(host, build_passed=(rc == 0))

            if rc != 0:
                error(f"F2 bitstream build FAILED (build-bitstream.sh rc={rc})")
                return BuildResult(passed=False, local_results_dir=local_results_dir)

            info(f"F2 bitstream build SUCCEEDED for {cfg.quintuplet}")
            return BuildResult(passed=True, local_results_dir=local_results_dir)
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
        inner_cmd = (
            f"{shlex.quote(remote_script)} "
            f"--cl_dir {shlex.quote(cfg.remote_cl_dir)} "
            f"--frequency {cfg.fpga_frequency} "
            f"--strategy {cfg.build_strategy.name}"
        )
        # Wrap in `bash -lc` so the build script (which sources
        # hdk_setup.sh) inherits the FPGA Dev AMI's login-shell PATH/env
        # — vivado, XILINX_*, AWS_FPGA_REPO_DIR. Fabric's exec_command
        # defaults to a non-login, non-interactive shell, in which those
        # are absent and the build fails its prereq checks. Mirrors
        # _run_bootstrap in buildhost.py.
        cmd = f"bash -lc {shlex.quote(inner_cmd)}"
        info(f"Running build script on remote: {cmd}")
        # warn=True so we can still pull results on failure.
        # pty=True so Vivado output streams in real-time.
        result = self._run(host, cmd, warn=True, pty=True)
        return result.return_code

    def _pull_results(
        self, host: BuildHost, *, build_passed: bool
    ) -> Optional[Path]:
        """Reverse-rsync the entire remote cl_dir back to a timestamped local
        results directory. Best-effort: a failure here is logged but does not
        mask the actual build outcome.

        Returns the local destination path on successful rsync (so the
        publisher can locate the DCP tarball), or None if the rsync failed.
        """
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
            return local_dst
        except RsyncFailed as e:
            warning(f"Result rsync failed (build {suffix}): {e}")
            return None


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
) -> bool:
    """Resolve config, request a host, ensure the platform HDK is present,
    run the build, release the host, then run the configured publisher.

    `upload_platform` is a force-override: when True the provider's
    `ensure_platform` will rsync the HDK regardless of stamp state. When
    False, the provider decides:
      * external (user-managed)       — never auto-upload; mismatch is fatal.
      * ec2_launch + instance_id      — upload on missing/mismatched stamp.
      * ec2_launch ephemeral          — always upload (fresh instance).

    Returns True on a successful build (including a successful publish),
    False on build-script failure. Raises `BitstreamBuildFailed` /
    `InvalidBuildConfig` for setup errors, and propagates publisher
    exceptions (S3 / create-fpga-image / poll failures) verbatim.

    The publisher runs *after* the build host is released so that long
    S3 uploads / AFI polls don't keep an EC2 instance billing.

    `log_file`, when set, captures every remote-session call (run, put,
    rsync) issued during BOTH the platform-HDK upload and the build
    itself. Output also streams to the console in real time.
    """
    cfg = BuildConfig.from_validated(project, registry)
    provider = make_build_host_provider(cfg)
    builder = make_bitbuilder(cfg, registry)

    host = provider.request(cfg)
    try:
        host.connect()
        provider.ensure_platform(
            host, cfg,
            builder=builder,
            force_upload=upload_platform,
            log_file=log_file,
        )
        result = builder.build_bitstream(host, log_file=log_file)
    finally:
        provider.release(host)

    if not result.passed:
        return False

    if result.local_results_dir is None:
        # Build script succeeded but the result rsync failed. Without the
        # local results dir there is no DCP tar to publish; flag and skip.
        warning(
            "Build succeeded but result rsync failed; skipping publish step."
        )
        return True

    publisher = make_publisher(cfg)
    publisher.publish(PublishInputs(local_results_dir=result.local_results_dir))
    return True


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _host_label(host: BuildHost) -> str:
    """Best-effort label for log messages."""
    params = getattr(host, "params", None)
    if params is not None and hasattr(params, "host"):
        return f"{params.user}@{params.host}"
    return type(host).__name__
