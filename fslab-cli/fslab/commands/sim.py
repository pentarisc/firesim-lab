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
from typing_extensions import Annotated

import typer
from functools import wraps

from fslab.utils.display import console, error, info, section, success
from fslab.utils.shell import run_or_die
from fslab.commands.build import BuildType

app = typer.Typer(rich_markup_mode="rich")
sim_app = typer.Typer()
app.add_typer(sim_app, name="sim")

_KNOWN_EMULATORS = ["verilator", "vcs", "xcelium"]

_EMU_METASIM_BINARY: dict[str, str] = {
    "verilator": "V",
    "vcs":        "",
    "xcelium":    "X",
}

# TODO: Fix this.
_EMU_FPGASIM_BINARY: dict[str, str] = {
    "f2": "F2"
}

# Binary name prefixes to driver_name for each emulator backend.
# Adjust to match your CMakeLists.txt target name prefixes.
_EMU_BINARY: dict[str, dict[str, str]] = {
    "metasim": _EMU_METASIM_BINARY,
    "fpgasim": _EMU_FPGASIM_BINARY
}

_FSLAB_YAML = Path("fslab.yaml")

# ------------------------------------------------------------------
# DEFINE SHARED OPTIONS ONCE USING ANNOTATED
# ------------------------------------------------------------------
SimArgsOpt = Annotated[Optional[str], typer.Option(
    "--args", "-a",
    help="Extra arguments forwarded verbatim to the simulation binary. Quote as a single string: [italic]--args '+permissive -c100000'[/]"
)]
SkipRtlOpt = Annotated[bool, typer.Option("--skip-rtl", help="Skip sbt / java RTL steps (build --skip-rtl).")]
SkipDriverOpt = Annotated[bool, typer.Option("--skip-driver", help="Skip C++ driver build (build --skip-driver).")]
ForceGenOpt = Annotated[bool, typer.Option("--force-gen", help="[CLI-07] Force regeneration even if config hash is unchanged.")]
YamlPathOpt = Annotated[Path, typer.Option("--config", "-c", help="Path to the project YAML.")]

@sim_app.callback(invoke_without_command=True)
def sim_callback(
    ctx: typer.Context,
    sim_args: SimArgsOpt = None,
    skip_rtl: SkipRtlOpt = False,
    skip_driver: SkipDriverOpt = False,
    force_gen: ForceGenOpt = False,
    yaml_path: YamlPathOpt = _FSLAB_YAML,
) -> None:
    """
    Run simulation. Build the project if necessary. Default is metasim. 
    """
    if ctx.invoked_subcommand is None:
        cmd_metasim(sim_args, skip_rtl, skip_driver, force_gen, yaml_path, BuildType.METASIM)


@sim_app.command("metasim")
def sim_metasim(
    sim_args: SimArgsOpt = None,
    skip_rtl: SkipRtlOpt = False,
    skip_driver: SkipDriverOpt = False,
    force_gen: ForceGenOpt = False,
    yaml_path: YamlPathOpt = _FSLAB_YAML,
) -> None:
    cmd_metasim(sim_args, skip_rtl, skip_driver, force_gen, yaml_path, BuildType.METASIM)

def cmd_metasim(
    sim_args: Optional[str],
    skip_rtl: bool,
    skip_driver: bool,
    force_gen: bool,
    yaml_path: Path,
    build_type: BuildType
) -> None:
    """
    [CLI-14] Run a cycle-accurate software simulation.

    Implicitly calls [bold]build[/] (which calls [bold]generate[/] if the
    config changed).  All skipping and forcing flags are forwarded through.
    """
    section("fslab sim metasim")

    yaml_path = yaml_path.resolve()
    project_root = yaml_path.parent

    # ------------------------------------------------------------------
    # [CLI-14] Implicit build (which internally calls generate)
    # ------------------------------------------------------------------
    info(f"Ensuring project is compiled for metasimulation…")
    _ensure_compiled(
        yaml_path=yaml_path,
        force_gen=force_gen,
        skip_rtl=skip_rtl,
        skip_driver=skip_driver,
        build_type=build_type
    )

    # ------------------------------------------------------------------
    # Locate the simulation binary
    # ------------------------------------------------------------------
    binary = _locate_sim_binary(emu=build_type.value, yaml_path=yaml_path)
    info(f"Simulation binary: [path]{binary.relative_to(project_root)}[/]")

    # ------------------------------------------------------------------
    # Build and run the simulation command
    # ------------------------------------------------------------------
    extra = sim_args.split() if sim_args else []
    sim_cmd = [str(binary)] + extra

    section(f"Running simulation ({BuildType.METASIM})")
    run_or_die(sim_cmd, cwd=project_root, label=f"[{BuildType.METASIM}]")

    success("Simulation complete.")

@sim_app.command("fpgasim")
def sim_fpgasim(
    sim_args: SimArgsOpt = None,
    skip_rtl: SkipRtlOpt = False,
    skip_driver: SkipDriverOpt = False,
    force_gen: ForceGenOpt = False,
    yaml_path: YamlPathOpt = _FSLAB_YAML,
) -> None:
    success("FPGA Simulation not yet implemented.")

@sim_app.command("fpga")
def sim_fpgasim(
    sim_args: SimArgsOpt = None,
    skip_rtl: SkipRtlOpt = False,
    skip_driver: SkipDriverOpt = False,
    force_gen: ForceGenOpt = False,
    yaml_path: YamlPathOpt = _FSLAB_YAML,
) -> None:
    success("FPGA hardware simulation not yet implemented.")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_compiled(
    *,
    yaml_path: Path,
    force_gen: bool,
    skip_rtl: bool,
    skip_driver: bool,
    build_type: BuildType
) -> None:
    """
    [CLI-14] Programmatically invoke the build logic.

    We call the shared ``_run_generate`` and the four build step helpers
    directly rather than re-invoking Typer's CLI machinery.  This avoids
    ``sys.exit`` being called inside a nested Typer invocation.
    """
    from fslab.commands.build import (
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
        _run_cmake_make(config=config, project_root=project_root, jobs=4, sm=sm, build_type=build_type)

    # Persist updated compile state
    import time
    sm.save(config_hash=config_hash, extra={"last_sim_compile": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


def _locate_sim_binary(*, emu: str, yaml_path: Path) -> Path:
    """
    Resolve the path to the compiled simulation binary.

    Search order (first match wins):
      1. ``build/<emu>/<emu_binary>``    – per-backend CMake sub-directory
      2. ``build/<emu_binary>``          – flat CMake output layout
      3. ``build/sim``                   – generic fallback target name
    """
    from fslab.schemas.parser import load_and_validate

    try:
        config, registry = load_and_validate(str(yaml_path))
    except Exception as exc:  # noqa: BLE001
        error(f"Configuration error:\n  {exc}")
        raise typer.Exit(code=1) from exc

    project_root = Path(config.project.project_dir).resolve()
    binary_name = f"{_EMU_BINARY.get(emu).get(config.host.emulator, '')}{config.host.driver_name}"

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
        + "\nRun [bold]fslab build[/] first."
    )
    raise typer.Exit(code=1)