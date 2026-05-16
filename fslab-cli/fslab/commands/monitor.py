"""
fslab/commands/monitor.py
=========================
CLI command tree for `fslab monitor build` — attach to (or pick up
a completed) background bitstream build launched by `fslab build fpga`.

This is the user-facing surface of the monitor; all the logic lives in
[fslab.bitstream.monitor](../bitstream/monitor.py). This module is
purely Typer wiring + standard config-load + exit-code translation.
"""

from __future__ import annotations

from pathlib import Path

import typer

from fslab.bitstream.monitor import (
    MonitorAborted,
    MonitorDetached,
    monitor_build,
)
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
