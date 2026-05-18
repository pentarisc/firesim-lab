"""
fslab/commands/monitor.py
=========================
CLI command tree for `fslab monitor build` and `fslab monitor run`.

Both subcommands are Typer wiring + standard config-load + exit-code
translation; the actual state-machine logic lives in
[fslab.bitstream.monitor](../bitstream/monitor.py) (build) and
[fslab.runtime.monitor_run](../runtime/monitor_run.py) (run). The
shared SSH primitives (connect / verify_remote_id / tail-until-result /
interruptible-sleep / MonitorAborted / MonitorDetached) live in
[fslab.pipeline.monitor](../pipeline/monitor.py).
"""

from __future__ import annotations

from pathlib import Path

import typer

from fslab.bitstream.monitor import monitor_build
from fslab.pipeline.monitor import MonitorAborted, MonitorDetached
from fslab.runtime.monitor_run import monitor_run
from fslab.schemas.parser import load_and_validate
from fslab.utils.display import error, info


app = typer.Typer(rich_markup_mode="rich")
monitor_app = typer.Typer()
app.add_typer(monitor_app, name="monitor", help="Monitor remote builds, simulations etc.")


_YamlPathOpt = typer.Option(
    Path("fslab.yaml"),
    "--config",
    "-c",
    help="Path to the project YAML.",
)


@monitor_app.command("build")
def cmd_monitor_build(
    yaml_path: Path = _YamlPathOpt,
) -> None:
    """Attach to this project's in-flight bitstream build.

    Reads the local stamp at [italic]build/fpga/.fslab/build.yaml[/],
    connects to the remote, and either:

    \\b
      • Tails the wrapper's log if the build is still running.
      • Polls AFI status if the wrapper has exited and the AFI is
        still being built (post-wrapper phase).
      • Prints a summary if the build has already reached a terminal
        state (succeeded / failed / abandoned).

    Press [bold]Ctrl+C[/] to detach. The build continues on the remote
    and can be re-attached with another `fslab monitor build`.
    """
    yaml_path = yaml_path.resolve()

    try:
        config, registry = load_and_validate(str(yaml_path))
    except Exception as exc:  # noqa: BLE001
        error(f"Configuration error:\n  {exc}")
        raise typer.Exit(code=1) from exc

    try:
        monitor_build(config, registry)
    except MonitorAborted as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    except MonitorDetached:
        # Clean detach — the message was already printed by the monitor.
        raise typer.Exit(code=0)
    except KeyboardInterrupt:
        # Belt-and-suspenders: any code path inside monitor_build that
        # synchronously surfaces a KeyboardInterrupt (rather than
        # converting to MonitorDetached itself) should still produce a
        # clean exit, not a traceback. The build is nohup'd on the
        # remote and keeps running.
        info("Detached. Re-attach with `fslab monitor build`.")
        raise typer.Exit(code=0)


@monitor_app.command("run")
def cmd_monitor_run(
    yaml_path: Path = _YamlPathOpt,
) -> None:
    """Attach to this project's in-flight detached FPGA run.

    Reads the local stamp at [italic]run/fpga/.fslab/run.yaml[/],
    connects to the remote run host, and either:

    \\b
      • Tails the wrapper's driver.log if the run is still in progress.
      • Pulls results and releases the host if the wrapper has exited
        (writes a terminal stamp).
      • Prints a summary if the run has already reached a terminal
        state (succeeded / failed / abandoned).

    Press [bold]Ctrl+C[/] to detach. The run continues on the remote
    and can be re-attached with another `fslab monitor run`.
    """
    yaml_path = yaml_path.resolve()

    try:
        config, registry = load_and_validate(str(yaml_path))
    except Exception as exc:  # noqa: BLE001
        error(f"Configuration error:\n  {exc}")
        raise typer.Exit(code=1) from exc

    try:
        monitor_run(config, registry)
    except MonitorAborted as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    except MonitorDetached:
        raise typer.Exit(code=0)
    except KeyboardInterrupt:
        info("Detached. Re-attach with `fslab monitor run`.")
        raise typer.Exit(code=0)
