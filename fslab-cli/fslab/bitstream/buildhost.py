"""Build-pipeline-specific host extensions.

The pipeline-agnostic host layer (`Host`, `ExternalHost`, `Ec2LaunchHost`,
`HostProvider`, the concrete `ExternalHostProvider` / `Ec2LaunchHostProvider`,
the provider registry, and `cleanup_remote`) lives in
[fslab.pipeline.host](../pipeline/host.py). Callers that need the
transport classes import them from there directly. This module keeps
the build-specific layer on top:

  1. `BuildHostProvider` — adds `ensure_platform` and its HDK-upload
     helpers (`_do_upload`, `_run_bootstrap`, `_read_stamp`, `_write_stamp`,
     `_check_registry_default_conflict`). Mixed into the concrete
     providers below.
  2. `ExternalBuildHostProvider` / `Ec2LaunchBuildHostProvider` —
     concrete build-side providers, composed from the pipeline-side
     lifecycle classes plus `BuildHostProvider`.
  3. `make_build_host_provider` — build-side factory returning the
     above. Mirrors `pipeline.host.make_host_provider`.

Platform-HDK provisioning policy:
  `BuildHostProvider.ensure_platform()` is the single decision point for
  whether the platform HDK needs to be (re)uploaded before a build. It
  consults a small stamp file at `<remote_platform_path>/.firesim-lab-stamp.yaml`
  and the host's `_upload_mode` (set during pipeline `request()`) to
  decide between skip / upload / fail. See the docstring on the method
  for the policy matrix.

  Before any of that, `ensure_platform` also runs a divergence check:
  if the user has overridden `remote_platform_path` in fslab.yaml to a
  path different from the platform's registry default, and the registry
  default already has an HDK stamp on the remote (e.g. baked into the
  AMI), the build aborts with `RegistryDefaultPathConflict` — uploading
  to the override path would leave the remote with two HDK installations.
  `--upload-platform` bypasses this check.

Stamp-driven cleanup (background-build support) is inherited verbatim
from the pipeline-side providers; this module does not override it.
"""

from __future__ import annotations

import base64
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from fslab.pipeline.host import (
    Ec2LaunchHostProvider,
    ExternalHostProvider,
    Host,
    HostProvider,
    UploadMode,
)
from fslab.utils.display import info, warning

from .buildconfig import BuildConfig


# ---------------------------------------------------------------------------
# Build-specific exceptions
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# BuildHostProvider — mixin adding ensure_platform to a pipeline provider
# ---------------------------------------------------------------------------


class BuildHostProvider(HostProvider):
    """Build-pipeline extensions for `HostProvider`.

    Layered on top of the pipeline-side provider classes to add the
    HDK-upload pre-build step. The concrete build providers below are
    composed as `(BuildHostProvider, <pipeline-side provider>)` so they
    inherit `request` / `release` / `serialize_cleanup_state` /
    `cleanup_from_state` from the pipeline parent and `ensure_platform`
    + helpers from this mixin.

    This class is itself abstract — it does not implement the
    pipeline-side abstract methods. The concrete `ExternalBuildHostProvider`
    / `Ec2LaunchBuildHostProvider` resolve those via their second base.
    """

    _STAMP_FILENAME = ".firesim-lab-stamp.yaml"

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

    def ensure_platform(
        self,
        host: Host,
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

    def _do_upload(
        self,
        host: Host,
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
        host: Host,
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
        host: Host,
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
        host: Host,
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
        host: Host,
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
# Concrete build-side providers
# ---------------------------------------------------------------------------


class ExternalBuildHostProvider(BuildHostProvider, ExternalHostProvider):
    """For pre-provisioned hosts.

    Inherits `request` / `release` / `serialize_cleanup_state` /
    `cleanup_from_state` verbatim from `ExternalHostProvider`; adds
    `ensure_platform` via `BuildHostProvider`. Cleanup dispatch
    (`cleanup_remote`) targets `ExternalHostProvider` directly via the
    registry — the classmethod inherits cleanly through the MRO regardless.
    """


class Ec2LaunchBuildHostProvider(BuildHostProvider, Ec2LaunchHostProvider):
    """Provider for the `ec2_launch` host model on the build side.

    Inherits launch/teardown lifecycle from `Ec2LaunchHostProvider` and
    `ensure_platform` from `BuildHostProvider`. The pipeline-side parent
    is what's registered for cleanup dispatch.
    """


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_build_host_provider(cfg: BuildConfig) -> BuildHostProvider:
    """Pick a build-side provider for the current host_model discriminator.

    Mirrors `fslab.pipeline.host.make_host_provider` but returns the
    build-side subclasses so callers (the bitbuilder) can invoke
    `ensure_platform`. Adding a new host_model requires a new branch
    here in lockstep with a new schema class in fslab.schemas.host_model
    and an entry in KNOWN_HOST_MODELS.
    """
    host_type = cfg.host.type
    if host_type == "external":
        return ExternalBuildHostProvider()
    if host_type == "ec2_launch":
        return Ec2LaunchBuildHostProvider()
    raise NotImplementedError(f"Unknown host_model type: {host_type!r}")


__all__ = [
    "BuildHostProvider",
    "ExternalBuildHostProvider",
    "Ec2LaunchBuildHostProvider",
    "make_build_host_provider",
    "PlatformVersionMismatch",
    "RegistryDefaultPathConflict",
]
