"""
fslab/commands/compile.py
=========================
[CLI-12] ``fslab generate`` – parse YAML → hash check → Jinja2 rendering.
[CLI-13] ``fslab compile`` – calls generate implicitly, then runs the full
         build chain:
             sbt package
             java midas.chiselstage.Generator
             java midas.stage.GoldenGateMain
             cmake (configure) + make

All Java commands are parameterised from the validated Pydantic config so
hardcoded paths never appear here – they live in ``fslab.yaml`` / registries.

Assumed external API (from Prompt 1 / schemas layer)
-----------------------------------------------------
    from fslab.schemas.parser import load_and_validate
    config, registry = load_and_validate("fslab.yaml")

    config attributes used here (illustrative names – match your Pydantic models):
        config.project.name                   → str   e.g. "my-design-02" (auto populated)
        config.project.package_name           → str   e.g. "my.org"
        config.project.fslab_top              → str   e.g. "MyDesign02Top" (auto generated from name)
        config.project.config_class           → str   e.g. "MyDesign02TargetConfig"
        config.project.project_dir            → str   e.g. "/target/my-design02" (auto populated)
        registry.platforms[""].config_package → str   e.g. "firesim.midasexamples"
        registry.platforms[""].config_class   → str   e.g. "DefaultF2Config"
        config.gen_file_basename              → str   e.g. "FSLabTargetTop"

    registry attributes:
        registry.firesim_jar         → Path  e.g. /opt/firesim-lab/target/scala-2.13/firesim-lab.jar
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.progress import Progress, SpinnerColumn, TextColumn

from fslab.utils.display import console, error, info, section, success, warning
from fslab.utils.shell import run_or_die
from fslab.utils.state import StateManager, check_and_maybe_skip_generation
from fslab.schemas.parser import load_and_validate

# ---------------------------------------------------------------------------
# [CLI-04] This router registers BOTH `generate` and `compile` sub-commands.
#          It is mounted into the main app in cli.py.
# ---------------------------------------------------------------------------
app = typer.Typer(rich_markup_mode="rich")

# ---------------------------------------------------------------------------
# Paths resolved relative to the project root (CWD at invocation time).
# ---------------------------------------------------------------------------
_FSLAB_YAML = Path("fslab.yaml")


# ===========================================================================
# [CLI-12]  fslab generate
# ===========================================================================


@app.command("generate")
def cmd_generate(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="[CLI-07] Bypass hash check and regenerate even if config is unchanged.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what [italic]would[/] be generated without writing any files.",
    ),
    yaml_path: Path = typer.Option(
        _FSLAB_YAML,
        "--config",
        "-c",
        help="Path to the project YAML (default: [italic]fslab.yaml[/] in CWD).",
    ),
) -> None:
    """
    Parse [bold]fslab.yaml[/], validate against registries, check
    configuration hash, and render Jinja2 templates.

    Skips rendering if the config hash is unchanged (use [bold]--force[/]
    to override).
    """
    # Run the core logic; returns (should_generate, hash, state_mgr, config, registry)
    _run_generate(
        yaml_path=yaml_path,
        force=force,
        dry_run=dry_run,
    )


def _run_generate(
    *,
    yaml_path: Path = _FSLAB_YAML,
    force: bool = False,
    dry_run: bool = False,
) -> tuple[bool, str, StateManager]:
    """
    [CLI-12] Internal generate implementation called by both ``cmd_generate``
    and ``cmd_compile``.

    Returns
    -------
    (generation_ran: bool, config_hash: str, state_manager: StateManager)
    """
    section("fslab generate")

    # ------------------------------------------------------------------
    # Step 1 – Load & validate config via the Two-Pass parser
    # ------------------------------------------------------------------
    yaml_path = yaml_path.resolve()

    if not yaml_path.exists():
        error(
            f"Project config not found: [path]{yaml_path}[/]\n"
            "Are you inside the project directory?\n"
            "Run [bold]fslab init[/] to create a new project."
        )
        raise typer.Exit(code=1)

    info(f"Loading config from [path]{yaml_path}[/]")

    try:
        # [CLI-12] Delegate to the Two-Pass Pydantic parser (Prompt 1 output).
        # It raises pydantic.ValidationError or yaml.YAMLError on bad input.
        config, registry = load_and_validate(str(yaml_path))
    except Exception as exc:  # noqa: BLE001
        error(f"Configuration error:\n  {exc}")
        raise typer.Exit(code=1) from exc

    # ------------------------------------------------------------------
    # Step 2 – Collect registry file paths for hash computation
    # ------------------------------------------------------------------
    # The parser must expose which registry files it loaded.
    # We support both a single path and a list.
    registry_paths: list[Path] = _collect_registry_paths(registry)

    # ------------------------------------------------------------------
    # Step 3 – [CLI-06, CLI-07] Smart Generation hash check
    # ------------------------------------------------------------------
    should_generate, current_hash, sm = check_and_maybe_skip_generation(
        fslab_yaml_path=yaml_path,
        registry_yaml_paths=registry_paths,
        force=force,
        dry_run=dry_run,
        project_root=yaml_path.parent,
    )

    if not should_generate:
        return False, current_hash, sm

    # ------------------------------------------------------------------
    # Step 4 – Jinja2 template rendering
    # ------------------------------------------------------------------
    if dry_run:
        # Already handled inside check_and_maybe_skip_generation
        return False, current_hash, sm

    _render_templates(config=config, registry=registry, project_root=yaml_path.parent)

    # ------------------------------------------------------------------
    # Step 5 – Persist the new hash so subsequent runs can skip generation
    # ------------------------------------------------------------------
    sm.save(
        config_hash=current_hash,
        extra={
            "generated_for": str(yaml_path),
            "project_name": getattr(config, "name", "unknown"),
        },
    )

    success("Templates rendered successfully.")
    return True, current_hash, sm


# ===========================================================================
# [CLI-13]  fslab compile
# ===========================================================================


@app.command("compile")
def cmd_compile(
    skip_rtl: bool = typer.Option(
        False,
        "--skip-rtl",
        help="Skip the RTL generation steps (sbt + java).",
    ),
    skip_driver: bool = typer.Option(
        False,
        "--skip-driver",
        help="Skip the C++ driver build (cmake / make).",
    ),
    force_gen: bool = typer.Option(
        False,
        "--force-gen",
        help="[CLI-07] Force regeneration even if config hash is unchanged.",
    ),
    yaml_path: Path = typer.Option(
        _FSLAB_YAML,
        "--config",
        "-c",
        exists=True,
        readable=True,
        help="Path to the project YAML.",
    ),
    jobs: int = typer.Option(
        4,
        "--jobs",
        "-j",
        min=1,
        help="Parallel make jobs for the C++ driver build.",
    ),
) -> None:
    """
    Full compile pipeline.

    Implicitly calls [bold]generate[/] first (respects the config hash),
    then runs:

    \\b
      1. [bold]sbt package[/]                – assemble the Chisel design JAR
      2. [bold]java midas.chiselstage.Generator[/]  – emit FIRRTL
      3. [bold]java midas.stage.GoldenGateMain[/]   – run GoldenGate elaboration
      4. [bold]cmake / make[/]               – build the C++ simulation driver
    """
    yaml_path = yaml_path.resolve()
    project_root = yaml_path.parent

    # ------------------------------------------------------------------
    # [CLI-13] Step 0 – Implicit generate (with force-gen flag)
    # ------------------------------------------------------------------
    _, _hash, sm = _run_generate(
        yaml_path=yaml_path,
        force=force_gen,
    )

    # Re-load config after generation to get fresh validated models
    try:
        config, registry = load_and_validate(str(yaml_path))
    except Exception as exc:  # noqa: BLE001
        error(f"Configuration error:\n  {exc}")
        raise typer.Exit(code=1) from exc

    # ------------------------------------------------------------------
    # [CLI-13] Step 1 – sbt package
    # ------------------------------------------------------------------
    if not skip_rtl:
        section("Step 1 / 3 – sbt package")
        _run_sbt_package(config=config, project_root=project_root, sm=sm)

    # ------------------------------------------------------------------
    # [CLI-13] Step 2 – java midas.chiselstage.Generator (FIRRTL emission)
    # ------------------------------------------------------------------
    if not skip_rtl:
        section("Step 2 / 3 – java midas.chiselstage.Generator")
        _run_chisel_generator(config=config, registry=registry, project_root=project_root, sm=sm)

    # ------------------------------------------------------------------
    # [CLI-13] Step 3 – java midas.stage.GoldenGateMain (MIDAS elaboration)
    # ------------------------------------------------------------------
    if not skip_rtl:
        section("Step 3 / 3 – java midas.stage.GoldenGateMain")
        _run_golden_gate_main(config=config, registry=registry, project_root=project_root, sm=sm)

    # ------------------------------------------------------------------
    # [CLI-13] Step 4 – cmake configure + make (C++ driver)
    # ------------------------------------------------------------------
    if not skip_driver:
        section("Step 4 – cmake / make (C++ driver)")
        _run_cmake_make(config=config, project_root=project_root, jobs=jobs, sm=sm)

    # ------------------------------------------------------------------
    # Persist updated state (mark last successful compile)
    # ------------------------------------------------------------------
    sm.save(
        config_hash=_hash,
        extra={
            "last_compile": _timestamp(),
            "skip_rtl": skip_rtl,
            "skip_driver": skip_driver,
        },
    )

    success("[bold]Compile complete.[/]")


# ===========================================================================
# Private helpers – one per build step
# ===========================================================================


def _run_sbt_package(*, config: object, project_root: Path, sm: StateManager) -> None:
    """
    [CLI-13] Run ``sbt package`` in the project root to assemble the Chisel
    design JAR.  The JAR path is later passed to the Java classpath.
    """
    cmd = ["sbt", "package"]
    log = sm.log_file("sbt-package")
    info(f"Log → [path]{log.relative_to(project_root)}[/]")

    run_or_die(
        cmd,
        cwd=project_root,
        label="[sbt package]",
        log_file=log,
    )


def _build_classpath(config: object, registry: object) -> str:
    """
    Build the Java ``-cp`` argument by joining the firesim-lab JAR (from the
    registry) and the project-specific JAR (computed from config).

    Expected config/registry attributes (names match Pydantic models):
        registry.firesim_jar        → Path or str
        config.project.project_dir  → Path or str  (e.g. /target/my-design-02)
        config.project.name         → str          (e.g. my-design-02)

    The project JAR path follows the sbt convention:
        <project_dir>/target/scala-2.13/<name>.jar
    """
    firesim_jar = Path(str(getattr(registry, "firesim_jar",
                            "/opt/firesim-lab/target/scala-2.13/firesim-lab.jar")))

    target_dir = Path(str(getattr(config.project, "project_dir",
                            f"/target/{getattr(config.project, 'name', 'design')}")))
    design_name = getattr(config.project, "name", "design")
    design_jar = target_dir / "target" / "scala-2.13" / f"{design_name}.jar"

    return f"{firesim_jar}:{design_jar}"


def _run_chisel_generator(
    *, config: object, registry: object, project_root: Path, sm: StateManager
) -> None:
    """
    [CLI-13] Run ``java midas.chiselstage.Generator`` to emit FIRRTL.

    Full command (parameterised):
        java -cp <firesim_jar>:<design_jar> midas.chiselstage.Generator
             --target-dir  <target_dir>/generated-src
             --name        <project_name>
             --top-module  <package_name.fslab_top>
             --configs     <package_name.config_class>
    """
    project = config.project
    target_dir = Path(str(getattr(project, "project_dir",
                            f"/target/{getattr(project, 'name', 'design')}")))
    project_name   = getattr(project, "name",   "MyDesign")
    package_name = getattr(project, "package_name",   "com.mydesign")
    fslab_top   = getattr(project, "fslab_top",   "MyDesignTop")
    target_cfg   = getattr(project, "config_class", "MyDesignTargetConfig")
    generated_src = target_dir / "generated-src"

    classpath = _build_classpath(config, registry)

    cmd = [
        "java",
        "-cp", classpath,
        "midas.chiselstage.Generator",
        "--target-dir",  str(generated_src),
        "--name",        fslab_top,
        "--top-module",  f"{package_name}.{fslab_top}",
        "--configs",     f"{package_name}.{target_cfg}",
    ]

    log = sm.log_file("chisel-generator")
    info(f"Log → [path]{log.relative_to(project_root)}[/]")

    run_or_die(cmd, cwd=project_root, label="[chiselstage.Generator]", log_file=log)


def _run_golden_gate_main(
    *, config: object, registry: object, project_root: Path, sm: StateManager
) -> None:
    """
    [CLI-13] Run ``java midas.stage.GoldenGateMain`` (MIDAS elaboration).

    Full command (parameterised):
        java -cp <classpath> midas.stage.GoldenGateMain
             -i   <generated_src>/<fslab_top>.fir
             -td  <generated_src>
             -faf <generated_src>/<fslab_top>.anno.json
             -ggcp <registry.platforms.config_package>
             -ggcs <registry.platforms.config_class>
             --output-filename-base <gen_file_basename>
             --allow-unrecognized-annotations
             --no-dedup
    """
    project = config.project
    target_dir   = Path(str(getattr(config, "project_dir",
                                    f"/target/{getattr(project, 'name', 'design')}")))
    project_name   = getattr(project, "name",   "MyDesign")
    package_name = getattr(project, "package_name",   "com.mydesign")
    fslab_top   = getattr(project, "fslab_top",   "myDesign.MyDesignTop")
    target_cfg   = getattr(project, "config_class", "myDesign.MyDesignTargetConfig")
    platform     = getattr(config.target, "platform", "f2")
    config_package = None
    config_class = None

    for name, pf in registry.platforms.items():
        if name == platform:
            config_package = pf.config_package
            config_class = pf.config_class
            break

    out_base     = getattr(config, "gen_file_basename", "FireSim-generated")

    generated_src = target_dir / "generated-src"
    fir_file      = generated_src / f"{fslab_top}.fir"
    anno_file     = generated_src / f"{fslab_top}.anno.json"

    classpath = _build_classpath(config, registry)

    cmd = [
        "java",
        "-cp", classpath,
        "midas.stage.GoldenGateMain",
        "-i",   str(fir_file),
        "-td",  str(generated_src),
        "-faf", str(anno_file),
        "-ggcp", str(config_package),
        "-ggcs", str(config_class),
        "--output-filename-base", out_base,
        "--allow-unrecognized-annotations",
        "--no-dedup"
    ]

    log = sm.log_file("golden-gate-main")
    info(f"Log → [path]{log.relative_to(project_root)}[/]")

    run_or_die(cmd, cwd=project_root, label="[GoldenGateMain]", log_file=log)


def _run_cmake_make(
    *, config: object, project_root: Path, jobs: int, sm: StateManager
) -> None:
    """
    [CLI-13] Configure and build the C++ simulation driver.

    Steps:
        mkdir -p build && cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
        make -C build -j<jobs>
    """
    build_dir = project_root / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    log = sm.log_file("cmake-make")
    info(f"Log → [path]{log.relative_to(project_root)}[/]")

    # cmake configure
    run_or_die(
        ["cmake", "-S", ".", "-B", str(build_dir), "-DCMAKE_BUILD_TYPE=Release"],
        cwd=project_root,
        label="[cmake configure]",
        log_file=log,
    )

    # make
    run_or_die(
        ["make", "-C", str(build_dir), f"-j{jobs}"],
        cwd=project_root,
        label=f"[make -j{jobs}]",
        log_file=log,
    )


# ===========================================================================
# Jinja2 rendering placeholder [CLI-12, CLI-03]
# ===========================================================================


def _render_templates(*, config: object, registry: object, project_root: Path) -> None:
    """
    [CLI-03, CLI-12] Render all Jinja2 templates using the validated Pydantic
    models.

    Placeholder implementation – the real version will:
      1. Load templates from the ``fslab/templates/`` package directory.
      2. Build a context dict from config + registry fields.
      3. Write rendered output to the appropriate target paths.

    Templates to render:
      • wrapper.scala.j2   → generated-src/FslabGeneratedTop.scala
      • blackbox.scala.j2  → generated-src/FslabBlackBox.scala
      • CMakeLists.txt.j2  → CMakeLists.txt  (or build/CMakeLists.txt)
    """
    from jinja2 import Environment, PackageLoader, select_autoescape  # type: ignore
    from fslab.commands.context import _build_template_context

    info("Rendering Jinja2 templates…")

    # Locate the templates directory bundled inside the fslab package
    try:
        env = Environment(
            loader=PackageLoader("fslab", "templates"),
            autoescape=select_autoescape(enabled_extensions=()),
            keep_trailing_newline=True,
        )
    except Exception as exc:  # noqa: BLE001
        warning(f"Could not load template environment: {exc}. Skipping rendering.")
        return

    # Context built from Pydantic model fields
    ctx = _build_template_context(config=config, registry=registry)

    # Unpack keys used in output filenames
    fslab_top  = ctx["fslab_top"]   # config.project.fslab_top
    driver_name = ctx["driver_name"]  # config.host.driver_name

    # Map template names → output paths
    render_plan = {
        "build.sbt.j2" : project_root / "build.sbt",
        "plugins.sbt.j2":   project_root / "project" / "plugins.sbt",
        "CMakeLists.txt.j2":  project_root / "CMakeLists.txt",
        "Top.scala.j2":   project_root / "src" / "main" / "scala" / f"{fslab_top}.scala",
        "DUT.scala.j2":   project_root / "src" / "main" / "scala" / f"{fslab_top}BlackBox.scala",
        "Config.scala.j2":   project_root / "src" / "main" / "scala" / f"Config.scala",
        "driver.cc.j2":   project_root / "src" / "main" / "cc" / f"{driver_name}.cc",
        "user_rtl_readme.md.j2": project_root / "user_rtl" / "README.md"
    }

    for template_name, output_path in render_plan.items():
        try:
            tmpl = env.get_template(template_name)
        except Exception:  # noqa: BLE001
            warning(f"Template [italic]{template_name}[/] not found – skipping.")
            continue

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(tmpl.render(**ctx), encoding="utf-8")
        console.print(f"  [dim]wrote[/] [path]{output_path.relative_to(project_root)}[/]")


# ===========================================================================
# Utility helpers
# ===========================================================================


def _collect_registry_paths(registry: object) -> list[Path]:
    """
    Extract the list of registry YAML file paths from the loaded registry
    object.  Supports a single ``registry_path`` attribute, a list
    ``registry_paths``, or falls back to an empty list (hash computed from
    ``fslab.yaml`` alone).
    """
    # Prefer a list attribute
    if hasattr(registry, "registry_paths"):
        paths = getattr(registry, "registry_paths")
        if isinstance(paths, (list, tuple)):
            return [Path(str(p)) for p in paths]

    # Fall back to a single path
    if hasattr(registry, "registry_path"):
        return [Path(str(getattr(registry, "registry_path")))]

    # If the parser stores the source path on the config directly
    if hasattr(registry, "source_file"):
        return [Path(str(getattr(registry, "source_file")))]

    warning(
        "Could not determine registry file paths from the loaded registry object. "
        "Hash will be computed from fslab.yaml only."
    )
    return []


def _timestamp() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())