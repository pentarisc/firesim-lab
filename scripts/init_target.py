#!/usr/bin/env python3
"""
new-target.py — FireSim-lab target project generator
=====================================================

Creates a self-contained FireSim target project OUTSIDE the firesim-lab tree.
The generated project has its own build.sbt, Makefile, and env.sh, mirroring
the same independence that firesim-lab has from firesim itself.

Dependency chain:
    firesim  <-ProjectRef-  firesim-lab  <-ProjectRef-  <your-target>

Usage examples
--------------
# List available bridges and features
python3 /firesim-lab/scripts/new-target.py --list

# Minimal — creates ./my-baremetal/
python3 /firesim-lab/scripts/new-target.py --name my-baremetal --bridge uart,fased

# Explicit output location, all options
python3 /firesim-lab/scripts/new-target.py \\
    --name my-soc \\
    --output-dir ~/projects \\
    --bridge uart,fased,blockdev \\
    --platform verilator,vcs \\
    --feature verilog-blackbox \\
    --axi-addr-width 40 --axi-data-width 128 --axi-id-width 6

# Non-Docker paths
python3 /firesim-lab/scripts/new-target.py \\
    --name my-baremetal \\
    --lab-root /opt/firesim-lab \\
    --firesim-root /opt/firesim

# Dry run
python3 /firesim-lab/scripts/new-target.py --name my-test --bridge uart --dry-run

Dependencies:  pip install jinja2 pyyaml click
"""

# Change this to appropriate method of specifying versions for python projects.
__version__ = '0.1'

import sys
import os
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import Optional

missing = []
for pkg in ("jinja2", "yaml", "click"):
    try:
        __import__(pkg)
    except ImportError:
        missing.append(pkg if pkg != "yaml" else "pyyaml")
if missing:
    print(f"[new-target] Missing: {', '.join(missing)}. Run: pip install {' '.join(missing)}")
    sys.exit(1)

import click
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

SCRIPT_DIR   = Path(__file__).resolve().parent
LAB_ROOT     = SCRIPT_DIR.parent
TEMPLATE_DIR = SCRIPT_DIR / "templates"
REGISTRY     = LAB_ROOT / "targets" / "common" / "registry.yaml"

DEFAULT_FIRESIM_ROOT = "/firesim"
DEFAULT_LAB_ROOT     = "/firesim-lab"
DEFAULT_SBT_VERSION  = "1.10.1"


def load_registry():
    if not REGISTRY.exists():
        raise click.ClickException(f"Registry not found at {REGISTRY}")
    with open(REGISTRY) as f:
        return yaml.safe_load(f)

def bridge_map(reg): return {b["id"]: b for b in reg.get("bridges", [])}
def feature_map(reg): return {f["id"]: f for f in reg.get("features", [])}

def slugify(name):
    parts = re.split(r"[-_]", name)
    return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])

def to_pascal(name):
    return "".join(p.capitalize() for p in re.split(r"[-_]", name))

def validate_name(name):
    if not re.match(r"^[a-z][a-z0-9-]*$", name):
        raise click.BadParameter(
            f"'{name}' — use lowercase letters, digits, hyphens only (e.g. my-baremetal).")
    return name

def render(env, tmpl, ctx):
    return env.get_template(tmpl).render(**ctx)

def write_file(path, content, dry_run, executable=False):
    if dry_run:
        click.echo(f"  [dry-run]  {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    click.echo(f"  wrote      {path}")

def make_gitkeep(path, dry_run):
    if dry_run:
        click.echo(f"  [dry-run]  {path}/.gitkeep")
        return
    path.mkdir(parents=True, exist_ok=True)
    (path / ".gitkeep").touch()
    click.echo(f"  wrote      {path}/.gitkeep")

def _flatten_multi(ctx, param, value):
    result = []
    for token in value:
        for part in token.replace(",", " ").split():
            if part:
                result.append(part)
    return tuple(result)


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--name", "-n", required=False, metavar="NAME",
    help="Target project name (lowercase, hyphens OK).  e.g. my-baremetal")
@click.option("--output-dir", "-o", default=".", show_default=True, metavar="DIR",
    help="Parent directory to create the project in.  Project appears at <DIR>/<name>/.")
@click.option("--lab-root", default=None, metavar="PATH",
    help=f"Path to firesim-lab. Written into env.sh + build.sbt.  [default: {DEFAULT_LAB_ROOT}]")
@click.option("--firesim-root", default=None, metavar="PATH",
    help=f"Path to firesim checkout. Written into env.sh + build.sbt.  [default: {DEFAULT_FIRESIM_ROOT}]")
@click.option("--sbt-version", default=DEFAULT_SBT_VERSION, show_default=True, metavar="VER",
    help="SBT version for project/build.properties.")
@click.option("--package", "-p", "scala_package", default=None, metavar="PKG",
    help="Scala package name  [default: lowerCamelCase of --name]")
@click.option("--design", "-d", default=None, metavar="CLASS",
    help="Top-level Chisel module class name  [default: <PascalName>Top]")
@click.option("--bridge", "-b", "bridges", multiple=True,
    metavar="BRIDGE[,BRIDGE...]", callback=_flatten_multi,
    help="Bridge(s) to enable. Repeat or comma-separate. Use --list to discover.")
@click.option("--feature", "-f", "features", multiple=True,
    metavar="FEATURE[,FEATURE...]", callback=_flatten_multi,
    help="Feature(s) to enable. Repeat or comma-separate. Use --list to discover.")
@click.option("--platform", multiple=True, default=["verilator"], show_default=True,
    metavar="PLATFORM[,PLATFORM...]", callback=_flatten_multi,
    help="Platform(s): verilator, vcs, f1. Repeat or comma-separate.")
@click.option("--blackbox-name", default=None, metavar="MODULE",
    help="Verilog BlackBox module name (implies --feature verilog-blackbox). [default: <PascalName>DUT]")
@click.option("--axi-addr-width", default=32, show_default=True,
    type=click.IntRange(12, 64), metavar="BITS", help="AXI4 address width.")
@click.option("--axi-data-width", default="64", show_default=True,
    type=click.Choice(["32","64","128","256","512"]), metavar="BITS", help="AXI4 data width.")
@click.option("--axi-id-width", default=4, show_default=True,
    type=click.IntRange(1, 16), metavar="BITS", help="AXI4 ID width.")
@click.option("--force", is_flag=True, default=False,
    help="Overwrite an existing project directory.")
@click.option("--dry-run", is_flag=True, default=False,
    help="Show what would be generated without writing files.")
@click.option("--list", "list_registry", is_flag=True, default=False,
    help="List available bridges and features, then exit.")
def main(name, output_dir, lab_root, firesim_root, sbt_version,
         scala_package, design, bridges, features, platform,
         blackbox_name, axi_addr_width, axi_data_width, axi_id_width,
         force, dry_run, list_registry):
    """
    Generate a self-contained FireSim target project outside firesim-lab.

    \b
    The project is its own SBT root (own build.sbt, Makefile, env.sh),
    independent of firesim-lab just as firesim-lab is independent of firesim.

    \b
    Quick start:
      python3 /firesim-lab/scripts/new-target.py \\
          --name my-baremetal --bridge uart,fased --feature verilog-blackbox

    \b
    Discover available bridges/features:
      python3 /firesim-lab/scripts/new-target.py --list
    """
    reg    = load_registry()
    bmap   = bridge_map(reg)
    fmap   = feature_map(reg)

    # ── --list ────────────────────────────────────────────────────────────────
    if list_registry:
        click.echo("\n── Available Bridges ─────────────────────────────────────\n")
        for b in reg.get("bridges", []):
            click.echo(f"  {b['id']:<16}  {b['label']}")
            click.echo(f"  {'':16}  {b['description'].strip()}")
            if b.get("requires"):
                click.echo(f"  {'':16}  requires: {', '.join(b['requires'])}")
            click.echo()
        click.echo("── Available Features ────────────────────────────────────\n")
        for f in reg.get("features", []):
            click.echo(f"  {f['id']:<22}  {f['label']}")
            click.echo(f"  {'':22}  {f['description'].strip()}")
            click.echo()
        click.echo("── Platforms ─────────────────────────────────────────────\n")
        for p in reg.get("platforms", []):
            click.echo(f"  {p['id']:<16}  {p['label']}")
        click.echo()
        return

    if not name:
        raise click.UsageError("--name is required. Use --list to see options.")

    # ── Validate ──────────────────────────────────────────────────────────────
    name        = validate_name(name)
    bridge_ids  = list(bridges)
    feature_ids = list(features)

    if blackbox_name and "verilog-blackbox" not in feature_ids:
        feature_ids.append("verilog-blackbox")

    bad_b = [b for b in bridge_ids if b not in bmap]
    if bad_b:
        raise click.BadParameter(
            f"Unknown bridge(s): {', '.join(bad_b)}. Available: {', '.join(bmap)}",
            param_hint="--bridge")

    bad_f = [f for f in feature_ids if f not in fmap]
    if bad_f:
        raise click.BadParameter(
            f"Unknown feature(s): {', '.join(bad_f)}. Available: {', '.join(fmap)}",
            param_hint="--feature")

    valid_platforms = {"verilator", "vcs", "f1"}
    bad_p = [p for p in platform if p not in valid_platforms]
    if bad_p:
        raise click.BadParameter(
            f"Unknown platform(s): {', '.join(bad_p)}. Choose: {', '.join(sorted(valid_platforms))}",
            param_hint="--platform")

    # Resolve bridge dependencies
    for bid in list(bridge_ids):
        for req in bmap[bid].get("requires", []):
            if req not in bridge_ids:
                click.echo(f"  [info] Bridge '{bid}' requires '{req}' — adding automatically.")
                bridge_ids.append(req)
    seen: set = set()
    bridge_ids = [b for b in bridge_ids if not (b in seen or seen.add(b))]  # type: ignore

    # Derive names
    pascal                = to_pascal(name)
    scala_package         = scala_package or slugify(name)
    design                = design or f"{pascal}Top"
    blackbox_name         = blackbox_name or (f"{pascal}DUT" if "verilog-blackbox" in feature_ids else None)
    sbt_project           = slugify(name)
    target_config_class   = f"{pascal}TargetConfig"
    primary_platform      = list(platform)[0]
    platform_config_class = f"{pascal}{primary_platform.capitalize()}Config"
    axi_data_width_int    = int(axi_data_width)
    resolved_lab_root     = lab_root     or DEFAULT_LAB_ROOT
    resolved_firesim_root = firesim_root or DEFAULT_FIRESIM_ROOT

    project_dir = Path(output_dir).expanduser().resolve() / name

    if project_dir.exists() and not force:
        msg = f"Directory already exists: {project_dir}\nUse --force to overwrite."
        if dry_run:
            click.echo(f"  [dry-run] {msg}")
        else:
            raise click.ClickException(msg)

    # ── Summary ───────────────────────────────────────────────────────────────
    click.echo()
    click.echo("┌─ new-target.py ─────────────────────────────────────────────")
    click.echo(f"│  Target name    : {name}")
    click.echo(f"│  Output dir     : {project_dir}")
    click.echo(f"│  Scala package  : {scala_package}")
    click.echo(f"│  Design class   : {design}")
    click.echo(f"│  SBT project    : {sbt_project}")
    click.echo(f"│  Bridges        : {', '.join(bridge_ids) or '(none)'}")
    click.echo(f"│  Features       : {', '.join(feature_ids) or '(none)'}")
    click.echo(f"│  Platforms      : {', '.join(platform)}")
    if "fased" in bridge_ids or "verilog-blackbox" in feature_ids:
        click.echo(f"│  AXI4           : {axi_addr_width}b addr / {axi_data_width_int}b data / {axi_id_width}b id")
    if blackbox_name:
        click.echo(f"│  BlackBox       : {blackbox_name}")
    click.echo(f"│  firesim-lab    : {resolved_lab_root}")
    click.echo(f"│  firesim        : {resolved_firesim_root}")
    click.echo(f"│  SBT version    : {sbt_version}")
    click.echo(f"│  Dry run        : {'YES — no files written' if dry_run else 'no'}")
    click.echo("└─────────────────────────────────────────────────────────────")
    click.echo()

    # ── Jinja2 ────────────────────────────────────────────────────────────────
    if not TEMPLATE_DIR.exists():
        raise click.ClickException(f"Template directory not found: {TEMPLATE_DIR}")

    jenv = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )

    ctx = dict(
        target_name           = name,
        scala_package         = scala_package,
        scala_prefix          = pascal,
        design                = design,
        sbt_project           = sbt_project,
        target_config_class   = target_config_class,
        platform_config_class = platform_config_class,
        enabled_bridges       = [bmap[b] for b in bridge_ids],
        enabled_bridge_ids    = bridge_ids,
        enabled_features      = [fmap[f] for f in feature_ids],
        enabled_feature_ids   = feature_ids,
        enabled_platforms     = list(platform),
        use_blackbox          = "verilog-blackbox" in feature_ids,
        blackbox_name         = blackbox_name,
        axi_addr_w            = axi_addr_width,
        axi_data_w            = axi_data_width_int,
        axi_id_w              = axi_id_width,
        sim_class             = f"{pascal}Sim",
        lab_root              = resolved_lab_root,
        firesim_root          = resolved_firesim_root,
        sbt_version           = sbt_version,
        generated_date        = datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

    pd = project_dir
    click.echo("Generating project files:")

    # Root project files
    write_file(pd / "build.sbt",                    render(jenv, "build.sbt.j2",          ctx), dry_run)
    write_file(pd / "project" / "build.properties", render(jenv, "build.properties.j2",   ctx), dry_run)
    write_file(pd / "env.sh",                       render(jenv, "env.sh.j2",             ctx), dry_run, executable=True)
    write_file(pd / "Makefile",                     render(jenv, "Makefile.j2",           ctx), dry_run)
    write_file(pd / ".gitignore",
        "generated-src/\ntarget/\n.bsp/\n.metals/\n.idea/\n*.vpd\n*.vcd\n", dry_run)

    # Makefrag
    write_file(pd / "makefrag" / "config.mk",  render(jenv, "config.mk.j2",  ctx), dry_run)
    write_file(pd / "makefrag" / "build.mk",   render(jenv, "build.mk.j2",   ctx), dry_run)
    write_file(pd / "makefrag" / "driver.mk",  render(jenv, "driver.mk.j2",  ctx), dry_run)
    write_file(pd / "makefrag" / "metasim.mk", render(jenv, "metasim.mk.j2", ctx), dry_run)

    # Scala
    sdir = pd / "src" / "main" / "scala"
    write_file(sdir / "Generator.scala",      render(jenv, "Generator.scala.j2",  ctx), dry_run)
    write_file(sdir / "Configs.scala",        render(jenv, "Configs.scala.j2",    ctx), dry_run)
    write_file(sdir / f"{design}.scala",      render(jenv, "TargetTop.scala.j2",  ctx), dry_run)
    if "verilog-blackbox" in feature_ids and blackbox_name:
        write_file(sdir / f"{blackbox_name}.scala",
                   render(jenv, "BlackBoxDUT.scala.j2", ctx), dry_run)
        write_file(pd / "src" / "main" / "resources" / "vsrc" / f"{blackbox_name}.v",
                   render(jenv, "BlackBoxDUT.v.j2", ctx), dry_run)

    # GoldenGate placeholder
    make_gitkeep(pd / "src" / "main" / "goldengateimplementations" / "scala", dry_run)

    # C++ driver
    write_file(pd / "src" / "main" / "cc" / "firesim_top.cc",
               render(jenv, "firesim_top.cc.j2", ctx), dry_run)

    # ── Done ──────────────────────────────────────────────────────────────────
    click.echo()
    if dry_run:
        click.echo("✓  Dry run complete — no files written.")
        return

    click.echo(f"✓  Project created at {project_dir}")
    click.echo()
    click.echo("Next steps:")
    step = 1
    click.echo(f"  {step}. cd {project_dir}"); step += 1
    click.echo(f"  {step}. source env.sh                   # sets FIRESIM_ROOT, FIRESIM_LAB_ROOT"); step += 1
    if blackbox_name:
        click.echo(f"  {step}. # Edit src/main/scala/{design}.scala"); step += 1
        click.echo(f"  {step}. # Implement src/main/resources/vsrc/{blackbox_name}.v"); step += 1
    else:
        click.echo(f"  {step}. # Fill in src/main/scala/{design}.scala"); step += 1
    click.echo(f"  {step}. make elaborate"); step += 1
    click.echo(f"  {step}. make verilator"); step += 1
    click.echo(f"  {step}. make run                         # or: make run ARGS='+uart-out=/tmp/uart.log'")


if __name__ == "__main__":
    main()
