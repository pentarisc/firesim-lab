"""
fslab/commands/abandon.py
=========================
CLI command tree for `fslab abandon build` — discard the local stamp
for an in-flight (or already-completed-but-uncleaned) bitstream build,
run cleanup against whatever remote resources the stamp references,
and clear the local remote-build-layer artefacts so a subsequent
`fslab build fpga --skip-compile` starts from a clean slate.

This is the user's escape hatch when:
  * A previous `fslab build fpga` aborted before reaching a clean state.
  * The user wants to walk away from a long-running build without
    finishing the monitor poll.
  * The stamp is corrupt and the in-flight guard on `fslab build fpga`
    is refusing to start a new build.
  * The previous remote build finished but the user wants to retry it
    via `--skip-compile` (spot termination, transient remote failure,
    etc.).

Cleanup is idempotent (terminating an already-terminated EC2 is a
no-op for AWS) so re-running `fslab abandon build` after a partial
failure is safe.

Scope of local cleanup
----------------------
Removed (the "remote-build layer"):
  * build/fpga/.fslab/        — stamp + monitor-pulled wrapper artefacts
  * build/fpga/reports/       — monitor-pulled Vivado reports
  * build/fpga/results-build/ — local results base (reserved by buildconfig)
  * .fslab/logs/fpga-build-*.log — per-launch fpga-build logs

Preserved (the "compile layer", so --skip-compile can reuse it):
  * generated-src/                       — Chisel / FIRRTL / Verilog output
  * build/ (excluding the four entries above) — CMake artefacts
  * build/fpga/cl_<quintuplet>/          — local project staging tree
                                            (input to remote build,
                                            produced by `make fpga`)
  * .fslab/state.json                    — compile-status gate for --skip-compile

Use `fslab clean` for the broader wipe (generated-src/ and build/).

On remote-cleanup failure the local stamp is *preserved* so the user
can retry — discarding the stamp before cleanup succeeds would orphan
the remote resource. Local artefact cleanup runs only after remote
cleanup succeeds (or when there was no remote to clean in the first
place).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

import typer
import yaml

from fslab.bitstream.build_stamp import (
    BuildStatus,
    read_stamp,
    stamp_path_for,
    wipe_stamp,
    write_stamp,
)
from fslab.pipeline.host import cleanup_remote
from fslab.pipeline.stamp import utc_now_iso
from fslab.runtime.run_stamp import (
    RunStatus,
    read_stamp as read_run_stamp,
    stamp_path_for as run_stamp_path_for,
    staging_path_for as run_staging_path_for,
    wipe_stamp as wipe_run_stamp,
    write_stamp as write_run_stamp,
)
from fslab.schemas.parser import load_and_validate
from fslab.utils.display import error, info, section, success, warning


app = typer.Typer(rich_markup_mode="rich")
abandon_app = typer.Typer()
app.add_typer(abandon_app, name="abandon", help="Abort and clean up remote builds, simulations etc.")


_YamlPathOpt = typer.Option(
    Path("fslab.yaml"),
    "--config",
    "-c",
    help="Path to the project YAML.",
)


@abandon_app.command("build")
def cmd_abandon_build(
    yaml_path: Path = _YamlPathOpt,
) -> None:
    """Abandon this project's in-flight bitstream build and wipe the
    local remote-build layer.

    Runs cleanup against the remote resource recorded in the local stamp
    (terminate / stop the EC2 instance), deletes the local stamp, and
    removes the remote-build-layer artefacts (pulled wrapper outputs,
    Vivado reports, fpga-build logs). Compile-layer artefacts are
    intentionally preserved so a subsequent `fslab build fpga
    --skip-compile` can reuse them.

    Safe to re-run if cleanup fails partway.

    [bold]This does NOT start a new build[/] — run `fslab build fpga`
    afterward when you're ready.
    """
    yaml_path = yaml_path.resolve()

    try:
        config, _registry = load_and_validate(str(yaml_path))
    except Exception as exc:  # noqa: BLE001
        error(f"Configuration error:\n  {exc}")
        raise typer.Exit(code=1) from exc

    project_dir = Path(str(config.project.project_dir)).expanduser().resolve()
    stamp_path = stamp_path_for(project_dir)
    stamp_present = stamp_path.is_file()
    artefacts_present = _remote_build_artefacts_present(project_dir)

    if not stamp_present and not artefacts_present:
        info(
            f"Nothing to abandon — no stamp at {stamp_path} and no local "
            f"remote-build artefacts found."
        )
        return

    # --- Remote-side cleanup (only if a stamp was written) ------------
    # If there's no stamp, there's no recorded remote resource to clean.
    # We still proceed to the local cleanup pass below so orphaned
    # artefacts (e.g. from a crash before the stamp was written) get
    # cleared.
    if stamp_present:
        # Try the normal read path first; fall back to a raw YAML read for
        # corrupt stamps (the whole point of `abandon` is to be the escape
        # hatch when normal read paths choke).
        cleanup_block: Optional[dict] = None
        try:
            stamp = read_stamp(project_dir)
        except Exception as e:  # noqa: BLE001
            warning(
                f"Local stamp at {stamp_path} could not be parsed ({e}). "
                f"Falling back to a raw read to recover the cleanup block."
            )
            stamp = None
            cleanup_block = _try_recover_cleanup_block(stamp_path)

        if stamp is not None:
            section(f"Abandoning build {stamp.build_id}")
            info(f"  status before:  {stamp.status.value}")
            info(f"  cleanup_done:   {stamp.cleanup_done}")
            cleanup_block = stamp.cleanup

        if cleanup_block is None:
            warning(
                "No cleanup block recoverable from the local stamp. "
                "Skipping cleanup_remote(); will delete local stamp and "
                "remote-build artefacts only. If you launched an EC2 "
                "instance for this build, you may need to terminate it "
                "manually via the AWS console."
            )
        else:
            # Run cleanup. On failure, preserve the local stamp AND skip
            # the local artefact wipe so the user can retry — otherwise
            # we'd lose the only handle on a possibly-still-live remote
            # resource (a half-cleaned EC2 instance billing forever).
            try:
                cleanup_remote({"cleanup": cleanup_block})
            except Exception as exc:  # noqa: BLE001
                error(
                    f"Cleanup failed: {exc}\n"
                    f"  Local stamp preserved at {stamp_path}.\n"
                    f"  Local remote-build artefacts not removed.\n"
                    f"  -> Resolve the cleanup error (e.g. `aws sso login` "
                    f"if SSO expired) and re-run `fslab abandon build`."
                )
                raise typer.Exit(code=1) from exc

        # If we have a parsed stamp, mark it abandoned BEFORE wiping so a
        # concurrent monitor probe sees a consistent terminal state. Then wipe.
        if stamp is not None:
            stamp.status = BuildStatus.ABANDONED
            stamp.cleanup_done = True
            stamp.finished_at = utc_now_iso()
            write_stamp(project_dir, stamp)
        wipe_stamp(project_dir)

    # --- Local cleanup pass -------------------------------------------
    # Wipes only remote-build-layer artefacts. Compile-layer artefacts
    # (generated-src/, build/, build/fpga/cl_<q>/) are intentionally
    # preserved so a subsequent `fslab build fpga --skip-compile` can
    # reuse them. Best-effort: each removal logs a warning on failure
    # but does not abort the command.
    _wipe_local_remote_build_artefacts(project_dir)

    success(
        "Build abandoned, remote cleaned up, local remote-build artefacts "
        "cleared. Compile artefacts preserved — retry with "
        "`fslab build fpga --skip-compile`."
    )


@abandon_app.command("run")
def cmd_abandon_run(
    yaml_path: Path = _YamlPathOpt,
) -> None:
    """Abandon this project's in-flight detached FPGA run.

    Runs cleanup against the remote resource recorded in the local run
    stamp (terminate / stop the EC2 instance via the same provider
    registry the monitor uses), then wipes the local stamp and the
    just-in-time staging dir.

    Preserves [italic]run/fpga/results/[/] — every prior run's results
    are append-only forensic records and survive abandon. Use
    `fslab clean` or remove the dir manually if you want them gone.

    Safe to re-run if cleanup fails partway: the local stamp is left
    in place when remote cleanup fails so the user can retry rather
    than orphaning the remote host.

    [bold]This does NOT start a new run[/] — invoke `fslab sim fpga` or
    `fslab sim fpga --detach` afterward when you're ready.
    """
    yaml_path = yaml_path.resolve()

    try:
        config, _registry = load_and_validate(str(yaml_path))
    except Exception as exc:  # noqa: BLE001
        error(f"Configuration error:\n  {exc}")
        raise typer.Exit(code=1) from exc

    project_dir = Path(str(config.project.project_dir)).expanduser().resolve()
    stamp_path = run_stamp_path_for(project_dir)
    staging_dir = run_staging_path_for(project_dir)
    stamp_present = stamp_path.is_file()
    staging_present = staging_dir.is_dir()

    if not stamp_present and not staging_present:
        info(
            f"Nothing to abandon — no stamp at {stamp_path} and no "
            f"staging dir at {staging_dir}."
        )
        return

    # --- Remote-side cleanup (only if a stamp was written) ------------
    if stamp_present:
        cleanup_block: Optional[dict] = None
        try:
            stamp = read_run_stamp(project_dir)
        except Exception as e:  # noqa: BLE001
            warning(
                f"Local run stamp at {stamp_path} could not be parsed ({e}). "
                f"Falling back to a raw read to recover the cleanup block."
            )
            stamp = None
            cleanup_block = _try_recover_cleanup_block(stamp_path)

        if stamp is not None:
            section(f"Abandoning run {stamp.run_id}")
            info(f"  status before: {stamp.status.value}")
            info(f"  cleanup_done:  {stamp.cleanup_done}")
            cleanup_block = stamp.cleanup

        if cleanup_block is None:
            warning(
                "No cleanup block recoverable from the local run stamp. "
                "Skipping cleanup_remote(); will delete local stamp and "
                "staging dir only. If you launched an EC2 instance for "
                "this run, you may need to terminate it manually via the "
                "AWS console."
            )
        else:
            try:
                cleanup_remote({"cleanup": cleanup_block})
            except Exception as exc:  # noqa: BLE001
                error(
                    f"Cleanup failed: {exc}\n"
                    f"  Local run stamp preserved at {stamp_path}.\n"
                    f"  Staging dir not removed.\n"
                    f"  -> Resolve the cleanup error (e.g. `aws sso login` "
                    f"if SSO expired) and re-run `fslab abandon run`."
                )
                raise typer.Exit(code=1) from exc

        if stamp is not None:
            stamp.status = RunStatus.ABANDONED
            stamp.cleanup_done = True
            stamp.finished_at = utc_now_iso()
            write_run_stamp(project_dir, stamp)
        wipe_run_stamp(project_dir)

    # --- Staging dir wipe (always reachable; idempotent) ---------------
    if staging_dir.is_dir():
        try:
            shutil.rmtree(staging_dir)
            info(f"Removed {staging_dir.relative_to(project_dir)}")
        except OSError as e:
            warning(f"Could not remove {staging_dir}: {e}")

    success(
        "Run abandoned, remote cleaned up, local stamp + staging cleared. "
        "Prior results in run/fpga/results/ are preserved."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _remote_build_artefacts_present(project_dir: Path) -> bool:
    """True iff any local remote-build-layer artefact exists.

    Mirrored in fslab.commands.build (the precondition for
    `--skip-compile`). Keep the two in sync.
    """
    candidates = [
        project_dir / "build" / "fpga" / ".fslab",
        project_dir / "build" / "fpga" / "reports",
        project_dir / "build" / "fpga" / "results-build",
    ]
    if any(p.is_dir() for p in candidates):
        return True

    logs_dir = project_dir / ".fslab" / "logs"
    if logs_dir.is_dir() and any(logs_dir.glob("fpga-build-*.log")):
        return True

    return False


def _wipe_local_remote_build_artefacts(project_dir: Path) -> None:
    """Remove local remote-build-layer artefacts. Best-effort.

    Compile-layer artefacts are NOT touched. Each removal is independent
    so a failure on one (e.g. a held file handle) doesn't block the
    others.
    """
    dirs_to_remove = [
        project_dir / "build" / "fpga" / ".fslab",
        project_dir / "build" / "fpga" / "reports",
        project_dir / "build" / "fpga" / "results-build",
    ]
    for d in dirs_to_remove:
        if d.is_dir():
            try:
                shutil.rmtree(d)
                info(f"Removed {d.relative_to(project_dir)}")
            except OSError as e:
                warning(f"Could not remove {d}: {e}")

    logs_dir = project_dir / ".fslab" / "logs"
    if logs_dir.is_dir():
        for log_file in sorted(logs_dir.glob("fpga-build-*.log")):
            try:
                log_file.unlink()
                info(f"Removed {log_file.relative_to(project_dir)}")
            except OSError as e:
                warning(f"Could not remove {log_file}: {e}")


def _try_recover_cleanup_block(stamp_path: Path) -> Optional[dict]:
    """Best-effort: try to extract `cleanup:` from a stamp that won't
    parse via `read_stamp`. Returns the dict if recoverable, else None."""
    try:
        with stamp_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    cleanup = data.get("cleanup")
    if isinstance(cleanup, dict) and cleanup.get("provider"):
        info("  Recovered cleanup block from raw stamp YAML.")
        return cleanup
    return None
