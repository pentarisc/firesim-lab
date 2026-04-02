"""
fslab/commands/init.py
======================
[CLI-11] ``fslab init`` – scaffold a new fslab project directory.

Creates:
    <name>/
    ├── fslab.yaml              (rendered from fslab.yaml.j2)
    ├── src/
    │   ├── main/
    │   │   ├── scala/          (Chisel design sources)
    │   │   ├── cc/             (C++ driver sources)
    │   │   └── verilog/        (Verilog blackbox wrappers)
    └── .gitignore
"""

from __future__ import annotations

from pathlib import Path
import traceback
import typer
from jinja2 import Environment, PackageLoader, select_autoescape

from fslab.utils.display import console, error, info, section, success, warning

app = typer.Typer(rich_markup_mode="rich")

# Platform → registry name mapping (extend as platforms are added)
_KNOWN_PLATFORMS = ["f1", "f2", "vitis_u250"]

# Sub-directories to scaffold under <name>/
_SCAFFOLD_DIRS = [
    "src/main/scala",
    "src/main/cc",
    "src/main/verilog",
    "generated-src",
    "user_rtl",
]

_DEFAULT_GITIGNORE = """\
# fslab-generated build artefacts
generated-src/
build/
target/
.fslab/logs/
*.class
*.jar

# Editors
.idea/
.vscode/
*.swp
"""


@app.callback(invoke_without_command=True)
def cmd_init(
    name: str = typer.Option(
        ...,
        "--name",
        "-n",
        prompt="Project name",
        help="Name of the new fslab project (used as directory name and YAML identifier).",
    ),
    platform: str = typer.Option(
        "f1",
        "--platform",
        "-p",
        help=f"Target platform.  Known values: {', '.join(_KNOWN_PLATFORMS)}.",
    ),
    output_dir: Path = typer.Option(
        Path("."),
        "--output",
        "-o",
        help="Parent directory in which to create the project folder.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite an existing project directory.",
    ),
) -> None:
    """
    Start a new fslab project.

    Creates the project directory structure and a pre-filled
    [bold]fslab.yaml[/] template.
    """
    section("fslab init")

    if platform not in _KNOWN_PLATFORMS:
        warning(
            f"Platform [bold]{platform}[/] is not in the known list "
            f"({', '.join(_KNOWN_PLATFORMS)}).  Proceeding anyway."
        )

    project_dir = output_dir.resolve() / name

    if project_dir.exists() and not force:
        error(
            f"Directory [path]{project_dir}[/] already exists.\n"
            "Use [bold]--force[/] to overwrite."
        )
        raise typer.Exit(code=1)

    # Create directory tree
    for sub in _SCAFFOLD_DIRS:
        (project_dir / sub).mkdir(parents=True, exist_ok=True)
        console.print(f"  [dim]mkdir[/] {project_dir / sub}")

    # .gitignore
    (project_dir / ".gitignore").write_text(_DEFAULT_GITIGNORE, encoding="utf-8")

    # Render fslab.yaml from template (placeholder)
    _write_default_yaml(project_dir=project_dir, name=name, platform=platform)

    success(
        f"Project [bold]{name}[/] created at [path]{project_dir}[/]\n"
        f"  Next: [dim]cd {name} && fslab generate[/]"
    )


def _write_default_yaml(*, project_dir: Path, name: str, platform: str) -> None:
    """Render the default fslab.yaml template into the new project."""
    try:
        env = Environment(
            loader=PackageLoader("fslab", "templates"),
            autoescape=select_autoescape(enabled_extensions=()),
            keep_trailing_newline=True,
        )
        tmpl = env.get_template("fslab.yaml.j2")
        content = tmpl.render(name=name, platform=platform, project_dir=project_dir)
        out = project_dir / "fslab.yaml"
        out.write_text(content, encoding="utf-8")
        console.print("  [dim]wrote[/] fslab.yaml")
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        error(
            f"Project template not found. Make sure firesim-lab is correctly setup."
        )
        raise typer.Exit(code=1)