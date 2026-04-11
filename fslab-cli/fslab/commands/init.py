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
from typing import Optional
import json
from jinja2 import Environment, PackageLoader, select_autoescape

from fslab.utils.display import console, error, info, section, success, warning, regex_msg
from fslab.utils.rtl_parser import extract_module_info
import fslab.utils.regexes as rx

new_app = typer.Typer(rich_markup_mode="rich")
init_app = typer.Typer(rich_markup_mode="rich")

# Platform → registry name mapping (extend as platforms are added)
_KNOWN_PLATFORMS = ["f2", "vitis_u250"]

_RTL_DIR = "user_rtl"

# Sub-directories to scaffold under <name>/
_SCAFFOLD_DIRS = [
    "src/main/scala",
    "src/main/cc",
    "generated-src",
    _RTL_DIR
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

@new_app.command("new")
def cmd_new(
    project_name: str = typer.Argument(
        ..., 
        help="The name of the new project folder to create"
    ),
) -> None:
    """
    Scaffold a new fslab project workspace.
    """
    # 1. Define our paths
    project_dir = Path(project_name)
    fslab_dir = project_dir / ".fslab"
    meta_file = fslab_dir / "meta.json"

    # 2. Safety check: Ensure we don't overwrite an existing folder
    if project_dir.exists():
        error(
            f"Directory [path]{project_name}[/] already exists.\n"
             "Please choose a different name or delete it."
        )
        raise typer.Exit(code=1)

    try:
        # 3. Create the main project directory and the hidden .fslab directory
        project_dir.mkdir(parents=True)
        fslab_dir.mkdir()

        # Create directory tree
        for sub in _SCAFFOLD_DIRS:
            (project_dir / sub).mkdir(parents=True, exist_ok=True)
            console.print(f"  [dim]mkdir[/] {project_dir / sub}")

        # .gitignore
        (project_dir / ".gitignore").write_text(_DEFAULT_GITIGNORE, encoding="utf-8")

        # 4. Generate the state/meta file inside .fslab/
        meta_data = {
            "project_name": project_name
            # You can add other defaults here in the future if needed
            # e.g., "fslab_version": "1.0.0"
        }
        
        with open(meta_file, "w") as f:
            json.dump(meta_data, f, indent=4)

        rtl_dir = project_dir / _RTL_DIR

        # 5. Success message with helpful next steps
        success(f"Successfully created fslab workspace: [bold]{project_name}[/]\n"
                "Next steps:\n"
                f"  1. [dim]cd [path]{project_name}[/][/]\n"
                f"  2. Copy your verilog files into [path]{rtl_dir}[/]\n"
                "  3. Run [dim]fslab init [-t|--top-module <name>] "
                "[-f|--top-module-file <file path>] "
                "[-p|--platform <name>][/]")

    except Exception as e:
        error(f"Fatal: Failed to create project folder: {e}")
        raise typer.Exit(code=1)

@init_app.command("init")
def cmd_init(
    top_module: Optional[str] = typer.Option(
        None,
        "--top-module",
        "-t",
        help="Top Verilog/SystemVerilog module name.",
    ),
    top_module_file: Optional[str] = typer.Option(
        None,
        "--top-module-file",
        "-f",
        help="Path to Verilog/SystemVerilog module file.",
    ),
    platform: str = typer.Option(
        "f2",
        "--platform",
        "-p",
        help=f"Target platform.  Known values: {', '.join(_KNOWN_PLATFORMS)}.",
    )
) -> None:
    """
    Initialize the fslab.yaml configuration file for the current project.
    """
    section("fslab init")

    if Path("fslab.yaml").exists():
        error("[dim]fslab.yaml[/] already present in current directory.")
        raise typer.Exit(code=1)

    meta_file = Path(".fslab/meta.json")

    # 1. Check if the file exists (Guardrail)
    if not meta_file.exists():
        error(
            "Not inside an fslab workspace (missing .fslab/meta.json). \n"
            "Did you forget to 'cd' into your project directory?")
        raise typer.Exit(code=1)

    # 2. If it exists, read the project name
    try:
        with open(meta_file, "r") as f:
            meta_data = json.load(f)
            
        project_name = meta_data.get("project_name")
        
        if not project_name:
            error("project_name key is missing from meta.json")
            raise typer.Exit(code=2)

    except (json.JSONDecodeError, ValueError) as e:
        error(f"Error reading .fslab/meta.json: {e}")
        raise typer.Exit(code=1) from e

    # 3. Success! We have the project name.
    success(f"Found fslab workspace: [bold]{project_name}[/]")

    project_dir = Path(".").resolve()

    ports: None
    params: None
    sources: None

    if top_module is not None:

        if not rx.VERILOG_MODULE_RE.match(top_module):
            error(
                f"top-module '{top_module}' is invalid. {regex_msg(rx.VERILOG_MODULE_RE)}"
            )
            raise typer.Exit(code=1)

        if top_module_file is None:
            error(
                f"top-module-file must be provided."
            )
            raise typer.Exit(code=1)

        file_path = resolve_top_module_file(top_module_file, project_dir)
        sources = [str(file_path.resolve())]

        info(f"Parsing module '{top_module}' from {top_module_file}...")
        params, ports = extract_module_info(str(file_path.resolve()), top_module)

        if ports is None:
            error(
                f"Ports not found in '{top_module}'. Please check the file '{file_path}'"
            )
            raise typer.Exit(code=1)

    if platform not in _KNOWN_PLATFORMS:
        warning(
            f"Platform [bold]{platform}[/] is not in the known list "
            f"({', '.join(_KNOWN_PLATFORMS)}).  Proceeding anyway."
        )

    # Render fslab.yaml from template (placeholder)
    _write_default_yaml(
        project_dir=project_dir,
        project_name=project_name,
        platform=platform,
        top_module=top_module,
        ports=ports,
        params=params,
        sources=sources
    )

    success(
        f"Project [bold]fslab.yaml[/] created at [path]{project_dir}[/]\n"
        f"  Next: edit the fslab.yaml and map ports.\n"
        f"  Then: run [dim]fslab generate[/]"
    )


def resolve_top_module_file(top_module_file: str, project_path: str) -> Path:
    project_path = Path(project_path)

    # Case 1: Absolute path
    p = Path(top_module_file)
    if p.is_absolute():
        if p.exists():
            return p
        else:
            error(f"File not found: {p}")
            raise typer.Exit(code=2)

    # Case 2: Relative path handling
    candidates = []

    # If already starts with "user_rtl/"
    if top_module_file.startswith(f"{_RTL_DIR}/"):
        candidates.append(project_path / top_module_file)
    else:
        # Try as-is relative to project_path
        candidates.append(project_path / top_module_file)
        # Try under user_rtl/
        candidates.append(project_path / _RTL_DIR / top_module_file)

    # Check all candidates
    for c in candidates:
        if c.exists():
            return c

    # If nothing worked
    error(
        f"Could not resolve file '{top_module_file}'. Tried:\n"
        f"\n".join(str(c) for c in candidates)
    )
    raise typer.Exit(code=2)

def _write_default_yaml(
    *, project_dir: Path, project_name: str, platform: str, top_module: str,
     ports: dict[str,str], params: dict[str,str], sources: [str]) -> None:
    """Render the default fslab.yaml template into the new project."""
    try:
        env = Environment(
            loader=PackageLoader("fslab", "templates"),
            autoescape=select_autoescape(enabled_extensions=()),
            keep_trailing_newline=True,
        )
        tmpl = env.get_template("fslab.yaml.j2")
        content = tmpl.render(
            project_name=project_name,
            platform=platform,
            project_dir=project_dir,
            top_module=top_module,
            ports=ports,
            params=params,
            sources=sources
        )
        out = project_dir / "fslab.yaml"
        out.write_text(content, encoding="utf-8")
        console.print("  [dim]wrote[/] fslab.yaml")
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        error(
            f"Project template not found. Make sure firesim-lab is correctly setup."
        )
        raise typer.Exit(code=1)