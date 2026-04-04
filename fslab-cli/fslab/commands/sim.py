"""
fslab/commands/sim.py
=====================
[CLI-14] ``fslab sim`` – run a cycle-accurate simulation.

Implicitly calls the shared ``_run_compile()`` helper (which itself calls
``_run_generate()``) so the simulation is always run against an up-to-date
binary.  Both the generate and compile steps respect the config hash, so they
are effectively no-ops when nothing has changed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from fslab.utils.display import console, error, info, section, success
from fslab.utils.shell import run_or_die

app = typer.Typer(rich_markup_mode="rich")

_KNOWN_EMULATORS = ["verilator", "vcs", "xcelium"]

# Binary name produced by CMake for each emulator backend.
# Adjust to match your CMakeLists.txt target names.
_EMU_BINARY: dict[str, str] = {
    "verilator": "verilator-sim",
    "vcs":        "vcs-sim",
    "xcelium":    "xcelium-sim",
}


@app.callback(invoke_without_command=True)
def cmd_sim(
    emu: str = typer.Option(
        "verilator",
        "--emu",
        "-e",
        help=f"Emulator backend.  One of: {', '.join(_KNOWN_EMULATORS)}.",
    ),
    args: Optional[str] = typer.Option(
        None,
        "--args",
        "-a",
        help=(
            "Extra arguments forwarded verbatim to the simulation binary.  "
            "Quote as a single string: [italic]--args '+permissive -c100000'[/]"
        ),
    ),
    force_gen: bool = typer.Option(
        False,
        "--force-gen",
        help="[CLI-07] Force regeneration even if config hash is unchanged.",
    ),
    skip_rtl: bool = typer.Option(
        False,
        "--skip-rtl",
        help="Skip sbt / java RTL steps (compile --skip-rtl).",
    ),
    skip_driver: bool = typer.Option(
        False,
        "--skip-driver",
        help="Skip C++ driver build (compile --skip-driver).",
    ),
    yaml_path: Path = typer.Option(
        Path("fslab.yaml"),
        "--config",
        "-c",
        help="Path to the project YAML.",
    ),
) -> None:
    """
    [CLI-14] Run a cycle-accurate simulation.

    Implicitly calls [bold]compile[/] (which calls [bold]generate[/] if the
    config changed).  All skipping and forcing flags are forwarded through.
    """
    section("fslab sim")

    if emu not in _KNOWN_EMULATORS:
        error(
            f"Unknown emulator [bold]{emu}[/]. "
            f"Valid choices: {', '.join(_KNOWN_EMULATORS)}"
        )
        raise typer.Exit(code=1)

    yaml_path = yaml_path.resolve()
    project_root = yaml_path.parent

    # ------------------------------------------------------------------
    # [CLI-14] Implicit compile (which internally calls generate)
    # ------------------------------------------------------------------
    info(f"Ensuring project is compiled for [bold]{emu}[/]…")
    _ensure_compiled(
        yaml_path=yaml_path,
        force_gen=force_gen,
        skip_rtl=skip_rtl,
        skip_driver=skip_driver,
    )

    # ------------------------------------------------------------------
    # Locate the simulation binary
    # ------------------------------------------------------------------
    binary = _locate_sim_binary(emu=emu, project_root=project_root)
    info(f"Simulation binary: [path]{binary.relative_to(project_root)}[/]")

    # ------------------------------------------------------------------
    # Build and run the simulation command
    # ------------------------------------------------------------------
    extra = args.split() if args else []
    sim_cmd = [str(binary)] + extra

    section(f"Running simulation ({emu})")
    run_or_die(sim_cmd, cwd=project_root, label=f"[{emu}]")

    success("Simulation complete.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_compiled(
    *,
    yaml_path: Path,
    force_gen: bool,
    skip_rtl: bool,
    skip_driver: bool,
) -> None:
    """
    [CLI-14] Programmatically invoke the compile logic.

    We call the shared ``_run_generate`` and the four compile step helpers
    directly rather than re-invoking Typer's CLI machinery.  This avoids
    ``sys.exit`` being called inside a nested Typer invocation.
    """
    from fslab.commands.compile import (
        _run_generate,
        _run_sbt_package,
        _run_chisel_generator,
        _run_golden_gate_main,
        _run_cmake_make,
    )

    from fslab.schemas.parser import load_and_validate

    # --- generate (hash-aware) ---
    _, config_hash, sm = _run_generate(
        yaml_path=yaml_path,
        force=force_gen,
    )

    # Re-load config for the compile steps
    try:
        config, registry = load_and_validate(str(yaml_path))
    except Exception as exc:  # noqa: BLE001
        error(f"Configuration error:\n  {exc}")
        raise typer.Exit(code=1) from exc

    project_root = yaml_path.parent

    # --- RTL steps ---
    if not skip_rtl:
        _run_sbt_package(config=config, project_root=project_root, sm=sm)
        _run_chisel_generator(config=config, registry=registry, project_root=project_root, sm=sm)
        _run_golden_gate_main(config=config, registry=registry, project_root=project_root, sm=sm)

    # --- C++ driver ---
    if not skip_driver:
        _run_cmake_make(config=config, project_root=project_root, jobs=4, sm=sm)

    # Persist updated compile state
    import time
    sm.save(config_hash=config_hash, extra={"last_sim_compile": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


def _locate_sim_binary(*, emu: str, project_root: Path) -> Path:
    """
    Resolve the path to the compiled simulation binary.

    Search order (first match wins):
      1. ``build/<emu>/<emu_binary>``    – per-backend CMake sub-directory
      2. ``build/<emu_binary>``          – flat CMake output layout
      3. ``build/sim``                   – generic fallback target name
    """
    binary_name = _EMU_BINARY.get(emu, f"{emu}-sim")
    candidates = [
        project_root / "build" / emu / binary_name,
        project_root / "build" / binary_name,
        project_root / "build" / "sim",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate

    error(
        f"Simulation binary for [bold]{emu}[/] not found.\n"
        "Searched:\n"
        + "\n".join(f"  [path]{c.relative_to(project_root)}[/]" for c in candidates)
        + "\nRun [bold]fslab compile[/] first."
    )
    raise typer.Exit(code=1)