"""Runner classes and the foreground run orchestrator.

Run-side counterpart to `fslab.bitstream.bitbuilder`. One platform-
specific Runner subclass per platform that supports FPGA-accelerated
simulation. Today: F2 only.

Foreground vs detached
----------------------
Phase 3 implements `run_simulation_foreground` only — a direct SSH
session, driver run inline with pty=True so the user's terminal acts as
the simulated UART, results pulled back at the end. No stamp file, no
wrapper template, no monitor; the host is released via the standard
provider lifecycle when the driver exits or the user Ctrl+Cs.

Detached mode (`fslab sim fpga --detach`) and the matching
`fslab monitor run` / `fslab abandon run` commands land in Phases 4–5.

AGFI lifecycle
--------------
Foreground runs follow the FireSim convention:

  1. `sudo fpga-clear-local-image -S <slot> -A`   (idempotent reset)
  2. wait for the slot to actually report `cleared`
  3. `sudo fpga-load-local-image -S <slot> -I <agfi> -A`
  4. wait for the slot to actually report `loaded`
  5. exec driver (pty=True, stdout/stderr teed to a remote driver.log)
  6. `sudo fpga-clear-local-image -S <slot> -A`   (always, in finally)

`fpga-clear-local-image` / `fpga-load-local-image` return immediately;
the slot stays in (3) busy until the operation actually completes, and
any subsequent FPGA-touching command (including the driver) will fail
with `(3) busy` if launched too early. We mirror upstream FireSim's
busy-wait on `fpga-describe-local-image -R -H` after each operation.

`fpga-load-local-image` uses the EC2 instance profile to call
DescribeFpgaImages / AssociateFpgaImage — see D4 in the run-pipeline
handoff for the minimal IAM policy.

The driver itself runs under sudo via `sudo bash -lc "..."`. AWS F2
requires root for FPGA BAR access and AFI introspection (`fpga-pci-sv.h`
ioctls); without it the driver fails with "Unable to get AFI information
from slot ... Are you running as root?". `bash -lc` gives the driver a
login shell so /etc/profile.d/aws-fpga.sh (and anything else the AMI
sources for the FPGA tools) is in scope inside the elevated session.

Payload axis
------------
Payloads (and the optional `payloads/SHA256SUMS` manifest) are uploaded
into the per-slot remote dir immediately after the driver, before AGFI
clear. The remote `sha256sum -c` step runs inside the captured try
block so a manifest mismatch surfaces as `failure.stage = "hash_verify"`
in result.yaml — no FPGA cycles wasted on a payload we already know is
corrupt.

Local sha256 verification runs even earlier — in
`run_simulation_foreground` before host acquisition — so the user
fails fast on a stale local manifest without paying for an EC2 instance.
"""

from __future__ import annotations

import abc
import secrets
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from fslab.pipeline.host import (
    Host,
    RemoteCommandFailed,
    RsyncFailed,
    make_host_provider,
)
from fslab.pipeline.stamp import utc_now_iso
from fslab.schemas.artifact_source import AwsAfiArtifactSourceConfig
from fslab.schemas.runner_args import VerifyHash
from fslab.utils.display import error, info, section, success, warning

from .payloads import (
    HashVerificationFailed,
    forensics_block,
    local_verify,
    remote_verify_command,
    upload_pairs,
)
from .runconfig import RunConfig


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RunSimulationFailed(Exception):
    """Raised when a foreground run cannot complete (missing prereqs, host
    acquisition failed, AGFI load error, etc.). Driver non-zero exit is
    surfaced via `result.yaml` + the CLI's exit code, not via this exception."""


# ---------------------------------------------------------------------------
# Runner class registry (decorator-populated)
# ---------------------------------------------------------------------------


RUNNER_CLASS_REGISTRY: dict[str, type["Runner"]] = {}


def register_runner_class(cls: type["Runner"]) -> type["Runner"]:
    """Register a Runner subclass keyed by its class name.

    The runner catalog (registry.yaml `runners[].python_class`) references
    classes by string. `make_runner` resolves that string against this
    registry at run time.
    """
    RUNNER_CLASS_REGISTRY[cls.__name__] = cls
    return cls


# ---------------------------------------------------------------------------
# run_id generation
# ---------------------------------------------------------------------------


def make_run_id(now: Optional[datetime] = None) -> str:
    """Generate a chronologically-sortable, human-scannable run id.

    Format: `r-<utc-ts>-<short-rand>`, e.g. `r-20260516T154100Z-b7e1`.
    Prefixed with `r-` to be distinguishable from build_ids in logs
    (build_ids carry no prefix today; Phase 4+ may align both to use
    `b-` / `r-` prefixes).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(2)  # 4 hex characters
    return f"r-{ts}-{suffix}"


# ---------------------------------------------------------------------------
# Runner (abstract)
# ---------------------------------------------------------------------------


class Runner(abc.ABC):
    """Platform-agnostic interface for running a built bitstream on a host.

    Subclasses implement the per-platform recipe (AGFI load for F2; future
    Alveo program-FPGA via xclbin; etc.). The contract is intentionally
    small: the caller owns the host lifecycle, the Runner just uses
    whatever host it is handed.
    """

    def __init__(self, cfg: RunConfig):
        self.cfg = cfg

    @abc.abstractmethod
    def run_foreground(
        self,
        host: Host,
        *,
        run_id: str,
        results_dir: Path,
    ) -> dict:
        """Run the driver in foreground mode and pull results back.

        Returns a dict suitable for serialising into `result.yaml`.
        Driver non-zero exit codes are surfaced in the returned dict (not
        raised) so the caller can persist the result before deciding what
        to do.
        """


# ---------------------------------------------------------------------------
# F2Runner
# ---------------------------------------------------------------------------


@register_runner_class
class F2Runner(Runner):
    """AWS F2 runner.

    Single-slot only (slot 0). FireSim's multi-slot support is a future
    extension; today the firesim-lab framework is single-node and uses
    slot 0 unconditionally. When supernode/multi-slot lands, this becomes
    a runner_args field or a per-platform recipe param.
    """

    SLOT: int = 0

    # AGFI clear and load are normally <60s on F2, but a cold spot warm-up
    # plus an unlucky shared-tenant queue can stretch them. 1 hour is the
    # ceiling — beyond that, something is genuinely wrong and we'd rather
    # surface a timeout than hang forever (FireSim's `until ... done`
    # loops have no timeout at all).
    FPGA_STATE_TIMEOUT_S: int = 3600

    def run_foreground(
        self,
        host: Host,
        *,
        run_id: str,
        results_dir: Path,
    ) -> dict:
        cfg = self.cfg
        started_at = utc_now_iso()
        remote_slot_dir = cfg.remote_slot_dir
        remote_driver_log_path = f"{remote_slot_dir}/driver.log"

        section(f"Starting F2 run {run_id} on {self._host_label(host)}")

        # --- stage remote slot dir + driver binary -----------------------
        host.run(f"mkdir -p {shlex.quote(remote_slot_dir)}")
        # Truncate any stale driver.log from a previous run on the same
        # slot dir so the rsync at the end pulls only this run's output.
        host.run(f": > {shlex.quote(remote_driver_log_path)}", warn=True)

        info(f"Uploading driver: {cfg.local_driver_path.name}")
        host.put(
            str(cfg.local_driver_path),
            f"{remote_slot_dir}/{cfg.driver_basename}",
        )
        host.run(
            f"chmod +x {shlex.quote(f'{remote_slot_dir}/{cfg.driver_basename}')}"
        )

        # --- upload payloads (+ SHA256SUMS when in scope) ----------------
        # Pre-try, matching the driver upload: an upload-side failure is
        # a setup error, distinct from the hash-mismatch case below which
        # gets its own failure.stage.
        self._upload_payloads(host, remote_slot_dir)

        # --- load AGFI ---------------------------------------------------
        agfi = self._require_aws_afi_agfi()
        exit_code: Optional[int] = None
        failure_stage: Optional[str] = None
        failure_message: Optional[str] = None

        try:
            # --- remote sha256 verification (when in scope) -------------
            # Inside the captured try so a manifest mismatch ends up in
            # result.yaml as failure.stage=hash_verify rather than as an
            # unhandled exception. Runs before any FPGA mgmt command so
            # a corrupt payload never reaches the bitstream.
            self._remote_verify(host, remote_slot_dir)

            info(f"Clearing FPGA slot {self.SLOT}")
            host.run(f"sudo fpga-clear-local-image -S {self.SLOT} -A")
            self._wait_for_fpga_state(host, "cleared")

            info(f"Loading AGFI {agfi} into slot {self.SLOT}")
            host.run(
                f"sudo fpga-load-local-image -S {self.SLOT} -I "
                f"{shlex.quote(agfi)} -A"
            )
            self._wait_for_fpga_state(host, "loaded")

            # --- exec driver --------------------------------------------
            section("Running driver — Ctrl+C to abort")
            info(
                f"Output (stdout + stderr) is teed to "
                f"{remote_driver_log_path} on the remote and streamed here."
            )
            driver_argv = self._build_driver_argv()
            argv_str = " ".join(shlex.quote(a) for a in driver_argv)
            log_q = shlex.quote(remote_driver_log_path)
            slot_dir_q = shlex.quote(remote_slot_dir)
            # The whole pipeline (cd + driver + tee) runs under
            # `sudo bash -lc` so:
            #   * the driver gets root for FPGA BAR / AFI access;
            #   * `tee driver.log` runs as root too, which is fine — the
            #     slot dir is writable by the elevated session;
            #   * `bash -lc` is a login shell, so /etc/profile.d/aws-fpga.sh
            #     (and similar) are sourced inside the sudo'd environment;
            #   * `set -o pipefail` ensures the driver's exit code
            #     propagates through `| tee` instead of being masked by
            #     tee's exit code.
            inner = (
                f"cd {slot_dir_q} && set -o pipefail && "
                f"{argv_str} 2>&1 | tee {log_q}"
            )
            cmd = f"sudo bash -lc {shlex.quote(inner)}"
            try:
                result = host.run(cmd, pty=True, warn=True)
                exit_code = result.return_code
                if exit_code != 0:
                    failure_stage = "driver"
                    failure_message = f"driver exited with code {exit_code}"
            except KeyboardInterrupt:
                # Defensive: pty=True usually forwards Ctrl+C to the remote
                # rather than propagating locally, but if Fabric ever does
                # surface it we treat it as a user interrupt.
                exit_code = 130
                failure_stage = "driver"
                failure_message = "interrupted by user (KeyboardInterrupt)"
                warning("Run interrupted by Ctrl+C.")
            except RemoteCommandFailed as e:
                exit_code = e.exit_code
                failure_stage = "driver"
                failure_message = str(e)
        except _HashVerifyAborted as e:
            # Remote sha256 mismatch — captured here so the AGFI load
            # never runs against a known-corrupt payload.
            exit_code = 1
            failure_stage = "hash_verify"
            failure_message = str(e)
        except RemoteCommandFailed as e:
            # Failures before/around AGFI load — capture in result.yaml.
            exit_code = e.exit_code if exit_code is None else exit_code
            if failure_stage is None:
                failure_stage = "agfi_load"
            if failure_message is None:
                failure_message = str(e)
        finally:
            # --- pull results back --------------------------------------
            try:
                self._pull_results(host, results_dir)
            except RsyncFailed as e:
                warning(f"Could not pull all run results: {e}")

            # --- teardown (always; idempotent) --------------------------
            try:
                info(f"Clearing FPGA slot {self.SLOT} (teardown)")
                host.run(
                    f"sudo fpga-clear-local-image -S {self.SLOT} -A",
                    warn=True,
                )
            except Exception as e:
                warning(f"FPGA teardown reported issues: {e}")

        finished_at = utc_now_iso()
        status = "succeeded" if (exit_code == 0) else "failed"

        result_dict: dict = {
            "run_id": run_id,
            "status": status,
            "exit_code": exit_code,
            "started_at": started_at,
            "finished_at": finished_at,
            "artifact_source": {
                "type": cfg.artifact_source.type,
                "agfi": agfi,
            },
            "artifacts": {
                "driver_log": str(
                    (results_dir / "driver.log").relative_to(cfg.project_dir)
                ),
            },
            "payloads": forensics_block(cfg.resolved_payloads),
        }
        if failure_stage is not None:
            result_dict["failure"] = {
                "stage": failure_stage,
                "message": failure_message or "",
            }

        return result_dict

    # ----------------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------------

    def _upload_payloads(self, host: Host, remote_slot_dir: str) -> None:
        """Upload each configured payload (plus SHA256SUMS when in scope)
        into the per-slot remote dir. No-op when the user supplied no
        payloads."""
        resolved = self.cfg.resolved_payloads
        if not resolved.payloads and resolved.local_manifest_path is None:
            return
        for local, remote in upload_pairs(resolved, remote_slot_dir):
            info(f"Uploading payload: {local.name}")
            host.put(str(local), remote)

    def _remote_verify(self, host: Host, remote_slot_dir: str) -> None:
        """Run `sha256sum -c SHA256SUMS` on the remote when a manifest is
        in scope. Mismatch raises `_HashVerifyAborted` so the outer try
        captures it as `failure.stage = "hash_verify"`."""
        resolved = self.cfg.resolved_payloads
        cmd = remote_verify_command(resolved, remote_slot_dir)
        if cmd is None:
            # No manifest in scope. With IF_PRESENT (default) and no
            # manifest, the user's intent is "don't bother" — keep this
            # path silent. The runtime layer emits the warn-once when
            # payloads were supplied but no manifest exists.
            if (
                resolved.verify_hash is VerifyHash.IF_PRESENT
                and resolved.payloads
                and resolved.local_manifest_path is None
            ):
                warning(
                    "verify_hash=IF_PRESENT and no payloads/SHA256SUMS "
                    "manifest present — skipping payload hash verification."
                )
            return
        info("Verifying payload hashes on remote")
        try:
            host.run(cmd)
        except RemoteCommandFailed as e:
            raise _HashVerifyAborted(
                f"sha256sum -c reported a mismatch on the remote: {e}"
            ) from e

    def _wait_for_fpga_state(
        self,
        host: Host,
        want: str,
        *,
        timeout_s: Optional[int] = None,
    ) -> None:
        """Busy-wait until `fpga-describe-local-image -R -H` reports the
        slot in `want` state ("cleared" or "loaded"), or raise on timeout.

        `fpga-clear-local-image` / `fpga-load-local-image` return as soon
        as the request is queued; the FPGA stays in (3) busy until the
        operation actually completes. Launching the driver — or even
        another mgmt command — before the slot reports the desired state
        gives `Error: (3) busy`. Upstream FireSim parity: see
        `run_farm_deploy_managers.clear_fpgas` / `flash_fpgas` which use
        the same `until ... grep -q` pattern (without a timeout).
        """
        t = timeout_s if timeout_s is not None else self.FPGA_STATE_TIMEOUT_S
        info(
            f"Waiting for FPGA slot {self.SLOT} to report '{want}' "
            f"(timeout {t}s)"
        )
        # The inner pipeline `fpga-describe-local-image | grep` is gated
        # by `if`, which exempts it from `set -e` / pipefail concerns —
        # so this poll is safe to run under either shell mode.
        poll = (
            f'deadline=$(( $(date +%s) + {t} )); '
            f'while (( $(date +%s) < deadline )); do '
            f'  if sudo fpga-describe-local-image -S {self.SLOT} -R -H 2>/dev/null '
            f'     | grep -q {shlex.quote(want)}; then exit 0; fi; '
            f'  sleep 1; '
            f'done; '
            f'echo "[fslab] FPGA slot {self.SLOT} did not reach state '
            f'{want!r} within {t}s" >&2; '
            f'exit 1'
        )
        host.run(f"bash -c {shlex.quote(poll)}")

    def _require_aws_afi_agfi(self) -> str:
        """Extract the AGFI from `cfg.artifact_source`, asserting type.

        ARTSRC-01 + the schema's discriminated union have already
        validated this, but a typed runtime check makes the F2 assumption
        explicit (and is forward-compatible with `local_tarball` /
        `hwdb_entry` artifact_source types that the F2 runner will
        explicitly reject when they land)."""
        src = self.cfg.artifact_source
        if not isinstance(src, AwsAfiArtifactSourceConfig):
            raise RunSimulationFailed(
                f"F2Runner requires artifact_source.type='aws_afi'; got "
                f"'{getattr(src, 'type', '?')}'."
            )
        return src.agfi

    def _build_driver_argv(self) -> list[str]:
        """Construct the driver invocation argv from runner_args.

        Phase 3 minimal mapping:
          * +max-cycles=<N>   if runner_args.max_cycles is set
          * extra_driver_flags appended verbatim

        `tracing` / `autocounter` are accepted in runner_args but don't
        emit a driver-side flag today — driver-side names are deferred
        until the run pipeline exercises them. The flags only gate
        which optional artifact directories are pulled back (currently:
        no optional artifacts gated; everything lives in driver.log).
        """
        ra = self.cfg.runner_args
        argv: list[str] = [f"./{self.cfg.driver_basename}"]
        if getattr(ra, "max_cycles", None) is not None:
            argv.append(f"+max-cycles={ra.max_cycles}")
        argv.extend(list(getattr(ra, "extra_driver_flags", []) or []))
        return argv

    def _pull_results(self, host: Host, results_dir: Path) -> None:
        """Rsync the per-slot run dir's artifacts back into results_dir.

        Pulls a small explicit allow-list (driver.log + user-configured
        result_files) rather than the entire slot dir, because the slot
        dir also holds the uploaded driver binary + payloads which we
        don't want round-tripping back.
        """
        results_dir.mkdir(parents=True, exist_ok=True)
        remote_driver_log = f"{self.cfg.remote_slot_dir}/driver.log"
        host.rsync_from(
            remote_driver_log,
            str(results_dir / "driver.log"),
            label="[rsync pull-driver-log]",
        )
        # User-configured driver-produced files. Missing files are warned,
        # not fatal — the driver may legitimately skip writing them on
        # early exit (and a hash-verify abort never wrote anything).
        for remote_abs, local_name in self.cfg.result_pulls():
            try:
                host.rsync_from(
                    remote_abs,
                    str(results_dir / local_name),
                    label=f"[rsync pull-{local_name}]",
                )
            except RsyncFailed as e:
                warning(f"Could not pull result_file {local_name!r}: {e}")

    @staticmethod
    def _host_label(host: Host) -> str:
        params = getattr(host, "params", None)
        if params is None:
            return "<remote>"
        user = getattr(params, "user", "?")
        h = getattr(params, "host", "?")
        return f"{user}@{h}"


class _HashVerifyAborted(Exception):
    """Internal signal carried from `_remote_verify` up to the captured
    try in `run_foreground` so the resulting result.yaml records
    `failure.stage = "hash_verify"`. Not part of the public API."""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_runner(cfg: RunConfig) -> Runner:
    """Instantiate the Runner class named by the registry.

    Resolves `cfg.runner_python_class` through `RUNNER_CLASS_REGISTRY`.
    A registered class is expected — RUN-11 cross-validation ensures
    this at registry-load time for the args/params schemas, and the
    python_class is import-time-registered via `@register_runner_class`.
    """
    cls = RUNNER_CLASS_REGISTRY.get(cfg.runner_python_class)
    if cls is None:
        known = sorted(RUNNER_CLASS_REGISTRY)
        raise RunSimulationFailed(
            f"Runner class {cfg.runner_python_class!r} is not registered. "
            f"Known: {known}. The class is registered via "
            f"@register_runner_class at module import time — ensure "
            f"fslab.runtime.runner is imported before make_runner is called."
        )
    return cls(cfg)


# ---------------------------------------------------------------------------
# Foreground entry point
# ---------------------------------------------------------------------------


def run_simulation_foreground(project: object, registry: object) -> int:
    """Run `fslab sim fpga` in foreground mode end-to-end.

    Returns the driver's exit code (0 on success, non-zero on failure,
    130 on Ctrl+C). Raises `RunSimulationFailed` only for setup-time
    failures (config invalid, host acquisition failed, AGFI load itself
    failed); a driver that exits non-zero comes back as a non-zero
    return value plus a synthesized `result.yaml` recording the failure.
    """
    cfg = RunConfig.from_validated(project, registry)
    run_id = make_run_id()

    # Timestamped results dir under run/fpga/results/. Append-only — every
    # run gets a fresh dir, so failed runs are preserved for forensics.
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    results_dir = cfg.project_dir / "run" / "fpga" / "results" / ts
    results_dir.mkdir(parents=True, exist_ok=True)

    section(f"fslab sim fpga — {run_id}")
    info(f"  platform:        {cfg.platform_id}")
    info(f"  runner:          {cfg.runner_id} ({cfg.runner_python_class})")
    info(f"  host_model:      {cfg.host.type}")
    info(f"  artifact_source: {cfg.artifact_source.type}")
    info(f"  results dir:     {results_dir.relative_to(cfg.project_dir)}")

    # --- local sha256 verify (before paying for a host) ------------------
    # Catches a stale local manifest or corrupted local payload before
    # any network or EC2 cost is incurred. No-op when verify_hash=NO or
    # IF_PRESENT-with-no-manifest.
    try:
        local_verify(cfg.resolved_payloads)
    except HashVerificationFailed as e:
        error(str(e))
        _write_result_yaml(
            results_dir,
            {
                "run_id": run_id,
                "status": "failed",
                "exit_code": 1,
                "started_at": utc_now_iso(),
                "finished_at": utc_now_iso(),
                "failure": {"stage": "hash_verify_local", "message": str(e)},
                "payloads": forensics_block(cfg.resolved_payloads),
            },
        )
        raise RunSimulationFailed(
            "Local payload hash verification failed before host acquisition."
        ) from e

    # `make_host_provider` / `provider.request` are typed `cfg: Any` —
    # they only access `.host.type` and `.host.<fields>`. RunConfig has
    # the matching `.host` attribute, so we pass it through directly.
    provider = make_host_provider(cfg)
    host: Optional[Host] = None
    runner = make_runner(cfg)
    result_dict: dict = {}

    try:
        host = provider.request(cfg)
        host.connect()
        result_dict = runner.run_foreground(
            host, run_id=run_id, results_dir=results_dir,
        )
    except KeyboardInterrupt:
        warning("Setup interrupted by Ctrl+C before the driver started.")
        result_dict = {
            "run_id": run_id,
            "status": "failed",
            "exit_code": 130,
            "started_at": utc_now_iso(),
            "finished_at": utc_now_iso(),
            "failure": {"stage": "setup", "message": "KeyboardInterrupt"},
        }
    except RunSimulationFailed:
        raise
    except Exception as e:
        # Setup-time exceptions (host acquisition, ssh failure, etc.)
        # become a typed RunSimulationFailed after we persist result.yaml.
        result_dict = {
            "run_id": run_id,
            "status": "failed",
            "exit_code": None,
            "started_at": utc_now_iso(),
            "finished_at": utc_now_iso(),
            "failure": {"stage": "setup", "message": str(e)},
        }
        _write_result_yaml(results_dir, result_dict)
        if host is not None:
            try:
                provider.release(host)
            except Exception as rel_e:
                warning(f"Provider release failed: {rel_e}")
        raise RunSimulationFailed(str(e)) from e
    finally:
        if host is not None:
            try:
                provider.release(host)
            except Exception as e:
                warning(f"Provider release failed: {e}")

    _write_result_yaml(results_dir, result_dict)

    exit_code = result_dict.get("exit_code")
    rc = int(exit_code) if isinstance(exit_code, int) else 1
    if rc == 0:
        success(f"Run {run_id} completed successfully.")
    else:
        error(f"Run {run_id} ended with exit code {rc}.")
    info(f"Results: {results_dir.relative_to(cfg.project_dir)}")
    return rc


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _write_result_yaml(results_dir: Path, result_dict: dict) -> None:
    """Write the foreground-synthesized result.yaml.

    Shape matches what the (future) Phase 4 detached wrapper script
    produces on the remote, so downstream tooling sees a uniform layout.
    """
    path = results_dir / "result.yaml"
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(result_dict, f, default_flow_style=False, sort_keys=False)
