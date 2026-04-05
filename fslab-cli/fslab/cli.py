"""
fslab/cli.py
============
[CLI-01] Main Typer application entry point.
[CLI-02] Configures a single global Rich Console used across the entire CLI.
[CLI-04] Sub-command routers are imported from their own modules and registered
         here – nothing business-logic lives in this file.

Usage (after `pip install -e .`):
    fslab --help
    fslab init --name my-design --platform f2
    fslab generate --force
    fslab build --skip-driver
    fslab sim --emu verilator
    fslab build --backend vivado
    fslab archive --tag milestone-v1
    fslab clean --all
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.traceback import install as install_rich_traceback

# ---------------------------------------------------------------------------
# [CLI-02] Install Rich as the global traceback renderer.
#          suppress=["typer", "click"] keeps internal framework frames out of
#          any unexpected exception displays during development.
# ---------------------------------------------------------------------------
install_rich_traceback(show_locals=False, suppress=["typer", "click"])

# ---------------------------------------------------------------------------
# Global Rich console – import this object in every submodule so all output
# shares the same stream, theme, and force-terminal setting.
# ---------------------------------------------------------------------------
console = Console()

# ---------------------------------------------------------------------------
# [CLI-01] Top-level Typer application.
#          no_args_is_help=True makes bare `fslab` print help instead of an
#          empty-looking shell.
# ---------------------------------------------------------------------------
app = typer.Typer(
    name="fslab",
    help=(
        "[bold green]fslab[/] – CLI orchestrator for MIDASII / GoldenGate.\n\n"
        "Run [bold]fslab <command> --help[/] for per-command usage."
    ),
    rich_markup_mode="rich",  # enables Rich markup in docstrings / help text
    no_args_is_help=True,
    add_completion=True,
)

# ---------------------------------------------------------------------------
# [CLI-04] Register sub-command routers.
#          Each commands/*.py file creates its own typer.Typer() instance and
#          registers it here via app.add_typer().
# ---------------------------------------------------------------------------

# Init scaffolding
from fslab.commands.init import app as init_app  # noqa: E402

app.add_typer(init_app, name="init", help="Start a new fslab project.")

# Generate & compile (share one router – both deal with code-gen / build)
from fslab.commands.build import app as build_top_app  # noqa: E402

app.add_typer(build_top_app)  # commands register themselves with their own names

# Simulation
from fslab.commands.sim import app as sim_top_app  # noqa: E402

app.add_typer(sim_top_app) # commands register themselves with their own names

# FPGA build & deploy
from fslab.commands.fpga import app as fpga_app  # noqa: E402

app.add_typer(fpga_app)  # 'build' and 'archive' live here


# ---------------------------------------------------------------------------
# [CLI-17] fslab clean – simple enough to live in cli.py directly
# ---------------------------------------------------------------------------
import shutil  # noqa: E402
from pathlib import Path  # noqa: E402


@app.command("clean")
def cmd_clean(
    all_: bool = typer.Option(
        False,
        "--all",
        help="Also remove the [bold].fslab/[/] state directory.",
    ),
) -> None:
    """
    Delete generated artefacts and CMake build directories.

    By default only removes [italic]generated-src/[/] and [italic]build/[/].
    Pass [bold]--all[/] to also wipe [italic].fslab/[/] (hash state, logs).
    """
    cwd = Path.cwd()
    targets = [
        cwd / "generated-src",
        cwd / "build",
    ]
    if all_:
        targets.append(cwd / ".fslab")

    removed_any = False
    for path in targets:
        if path.exists():
            console.print(f"  [yellow]Removing[/] {path.relative_to(cwd)}")
            shutil.rmtree(path)
            removed_any = True

    if removed_any:
        console.print("[bold green]✓[/] Clean complete.")
    else:
        console.print("[dim]Nothing to remove – workspace is already clean.[/]")


# ---------------------------------------------------------------------------
# Callback: version flag + global verbose option (forwarded via context)
# ---------------------------------------------------------------------------
from fslab import __version__  # noqa: E402


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", "-v", help="Print version and exit.", is_eager=True
    ),
) -> None:
    """fslab – hardware compiler orchestrator."""
    if version:
        console.print(f"fslab [bold cyan]{__version__}[/]")
        raise typer.Exit()
    # If no sub-command was given, Typer prints help automatically because
    # no_args_is_help=True is set on the app.


# ---------------------------------------------------------------------------
# Allow running as `python -m fslab`
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app()