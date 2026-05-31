# Command Implementation

This page is the deep reference for the command layer — the `commands/` package
plus the `cli.py` entry point and the `utils/` helpers every command leans on.
Read {doc}`index` first for the architecture; for *adding* a new command follow
the recipe in {doc}`extending`, then come back here for the conventions it
assumes.

The rule that organises this whole layer: **`cli.py` and `commands/*` hold no
hardware knowledge.** They parse arguments, load the validated config, decide
*which* steps run in *what* order, and shell out. Every path, flag, and tool
name comes from the validated config or the registry.

## The entry point: `cli.py`

`cli.py` builds the single top-level `typer.Typer()` application and mounts each
command group's router. It contains no business logic beyond the trivial
`clean` command and a version callback. The pattern is uniform:

```python
app = typer.Typer(
    name="fslab",
    rich_markup_mode="rich",   # Rich markup in help text
    no_args_is_help=True,      # bare `fslab` prints help
    add_completion=True,
)

from fslab.commands.build import app as build_top_app
app.add_typer(build_top_app)   # subcommands register under their own names
```

Two things to note:

- **`rich_markup_mode="rich"`** means help strings and docstrings may contain
  Rich markup (`[bold]…[/]`). This is why command docstrings throughout the
  package look like `Build the project with [bold]--detach[/]…`.
- **Rich tracebacks** are installed globally at import
  (`install_rich_traceback(... suppress=["typer", "click"])`) so an unexpected
  exception renders cleanly without framework frames. This is a developer
  safety net — your command code should still convert *expected* failures into
  `error()` + `typer.Exit` rather than letting them reach the traceback handler.

## Router topology

Each command module owns one or more `typer.Typer()` instances. There are two
shapes in use, and which you pick depends on whether the command has
subcommands:

**Flat command** — a module exposes an `app` and registers commands directly
on it (e.g. `fpga.py` → `fslab archive`, or the `clean` command in `cli.py`).

**Nested group with a default** — a module mounts a child router under a name
and gives it a callback with `invoke_without_command=True`, so a bare
`fslab build` runs a default while `fslab build fpga` runs the explicit
subcommand. `build.py` is the canonical example:

```python
app = typer.Typer()
build_app = typer.Typer()
app.add_typer(build_app, name="build")

@build_app.callback(invoke_without_command=True)
def build_callback(ctx: typer.Context, ...):
    if ctx.invoked_subcommand is None:
        cmd_compile(..., build_type=BuildType.METASIM)   # default = metasim

@build_app.command("metasim")
def build_metasim(...): cmd_compile(..., build_type=BuildType.METASIM)
@build_app.command("fpga")
def build_fpga(...): ...
```

`sim.py` mirrors this exactly (`fslab sim` defaults to `metasim`;
`fslab sim fpga` is the explicit FPGA run). `monitor.py` and `abandon.py` are
plain two-command groups (`build` / `run`).

The current command-to-module map:

| Module | Commands |
|---|---|
| `init.py` | `fslab new`, `fslab init` |
| `build.py` | `fslab generate`, `fslab build {metasim,driver,fpgasim,fpga}` |
| `sim.py` | `fslab sim {metasim,fpgasim,fpga}` |
| `fpga.py` | `fslab archive` |
| `monitor.py` | `fslab monitor {build,run}` |
| `abandon.py` | `fslab abandon {build,run}` |
| `cli.py` | `fslab clean`, `--version` |
| `context.py` | (no command) builds the Jinja2 render context |

## Argument and option conventions

Options are declared with `typer.Option`/`typer.Argument`. To keep an option's
definition identical across several commands, the package defines it **once** as
an `Annotated` alias and reuses it:

```python
from typing_extensions import Annotated

YamlPathOpt   = Annotated[Path, typer.Option("--config", "-c", help="Path to the project YAML.")]
ForceGenOpt   = Annotated[bool, typer.Option("--force-gen", help="Force regeneration…")]

@build_app.command("driver")
def build_driver(force_gen: ForceGenOpt = False, yaml_path: YamlPathOpt = _FSLAB_YAML, ...):
    ...
```

This is why `--config/-c`, `--force-gen`, `--skip-rtl`, `--skip-driver`,
`--jobs/-j` behave identically across `build`, `sim`, and friends — they are the
same `Annotated` definitions. When you add a command that shares an existing
option, reuse the alias rather than re-declaring it, so help text and defaults
stay in lock-step. (`build.py` also keeps a `build_options` decorator variant of
the same idea; prefer the `Annotated` aliases for new code — they read cleaner.)

`--config/-c` defaults to `fslab.yaml` in the CWD and is `.resolve()`d at the
top of every command; the parent directory is the project root used for all
subsequent path math.

## The command body pattern

Almost every command follows the same five beats. `cmd_init`, `cmd_compile`,
`cmd_metasim`, and the monitor/abandon commands are all variations on it:

```python
@app.command("...")
def cmd_xxx(yaml_path: YamlPathOpt = _FSLAB_YAML, ...):
    section("fslab xxx")                      # 1. Rich section rule

    yaml_path = yaml_path.resolve()
    project_root = yaml_path.parent

    try:                                       # 2. load + validate
        config, registry = load_and_validate(str(yaml_path))
    except Exception as exc:                   # 3. translate ALL failures
        error(f"Configuration error:\n  {exc}")
        raise typer.Exit(code=1) from exc

    # 4. do the work — shell out via run_or_die, write state via StateManager
    ...

    success("…done.")                          # 5. report
```

The non-negotiable parts:

- **Never let a raw exception escape.** Wrap fallible work, print a styled
  `error(...)`, and `raise typer.Exit(code=N)`. Use distinct non-zero codes
  where the caller (CI, a wrapper script) might branch on them.
- **Use the shared display helpers** (`section`, `info`, `success`, `warning`,
  `error`) from `utils.display`. They share the one themed `console`. Do not
  `print()` and do not build a second `Console()`.
- **Resolve paths early** and pass `project_root` down, rather than relying on
  the process CWD deep in a call chain.

## How commands compose: implicit generate + compile

The commands are layered so the user can call the high-level one and get the
prerequisites for free, with the config-hash gate making re-runs cheap:

- `cmd_generate` → `_run_generate()` — load, validate, hash-check, render.
- `cmd_compile` → calls `_run_generate()` first, then the four build-step
  helpers (`_run_sbt_package`, `_run_chisel_generator`, `_run_golden_gate_main`,
  `_run_cmake_make`).
- `sim`'s `_ensure_compiled()` calls the **same** `_run_generate` and step
  helpers directly (imported from `build.py`) rather than re-invoking Typer.

That last point is a deliberate pattern: **to run another command's logic, call
its internal helper function, not its Typer command.** Re-entering Typer would
trigger a nested `sys.exit` and argument re-parse. Factor the real work into a
plain function (`_run_generate`, `cmd_compile`) and let both the Typer command
and other commands call it. The build steps are parameterised by a `BuildType`
enum (`METASIM` / `FPGASIM` / `DRIVER` / `FPGA`) that selects the `make` target.

## Running external tools: `utils.shell`

Every subprocess goes through `utils.shell`. Do not call `subprocess` directly
from a command — you would lose live streaming, logging, and clean error
handling.

- **`run_or_die(cmd, *, cwd, label, log_file)`** — the default for any *fatal*
  step (sbt, the Java stages, cmake, make, the sim binary). It streams stdout
  and stderr live, mirrors to `log_file`, and on a non-zero exit prints a styled
  panel and raises `SystemExit(returncode)` (which Typer surfaces cleanly).
  Empty-string arguments are filtered out, so conditional flags can be passed as
  `""`.
- **`run(cmd, ...) -> int`** — same streaming, but returns the exit code instead
  of dying. Use it for optional steps where you inspect the result yourself.
- **`run_with_spinner(cmd, *, spinner_text, log_file)`** — for long, noisy jobs
  (synthesis): output goes to the log only, a Rich `Live` spinner shows a
  one-line preview. `log_file` is required.

Implementation detail worth knowing if you touch `shell.py`: stdout and stderr
are drained by two daemon threads into a shared queue (the only deadlock-free,
cross-platform way to merge them), and the child exit code is propagated as the
generator's `StopIteration.value` — which is why `run()` drives the generator
with `while True / next()` rather than a `for` loop.

## Tracking state: `utils.state.StateManager`

`StateManager(project_root)` owns the hidden `.fslab/` directory and everything
idempotency-related:

- **`compute_config_hash(yaml, registry_paths)`** — SHA-256 over `fslab.yaml`
  plus every loaded registry file (paths mixed into the digest so a moved file
  is not a false "unchanged"). `_collect_registry_paths()` in `build.py` is what
  hands it the registry list.
- **`is_generation_needed(hash)`** / the `check_and_maybe_skip_generation()`
  helper — compare against the stored hash and decide whether rendering runs.
  `--force` bypasses; `--dry-run` reports without writing.
- **`check_user_modifications(render_plan)`** — before overwriting generated
  files, hash them against what was last written and refuse (unless `--force`)
  if the user hand-edited a generated file. This is the guard behind the
  "Detected changes to bootstrapped files" error.
- **`save(config_hash, generated_files, extra)`** — atomic write of
  `state.json` (`*.tmp` then rename). `extra` is where commands stash
  breadcrumbs like `compile_status`, `last_build_type`, `last_compile` — and
  `build fpga --skip-compile` reads exactly those keys back to decide whether a
  prior FPGA compile is reusable.
- **`log_file(name)`** — returns a timestamped path under `.fslab/logs/`. Use it
  for every `run_or_die(..., log_file=...)` call so logs are consistent and
  swept by `fslab clean --all`.

`.fslab/` also gets an auto-written `.gitignore` so logs and state never land in
the user's repo.

## FPGA commands and the orchestration handoff

`build fpga`, `sim fpga`, `monitor`, and `abandon` are thin Typer wrappers over
the {doc}`orchestration` layer. The command's job is narrow: load config,
enforce preconditions, translate the orchestration layer's exceptions into exit
codes. For example, `build_fpga`:

1. If `--skip-compile`, verify `state.json` records a prior successful FPGA
   compile and the remote-build slate is clean (no stamp, no pulled artefacts,
   no fpga-build logs) — otherwise error out with the remediation (`fslab
   abandon build`).
2. Otherwise run `check_no_existing_build()` then `cmd_compile(... FPGA)`.
3. Call `build_bitstream(...)`, catching `InvalidBuildConfig` /
   `BitstreamBuildFailed` → styled error + `Exit(1)`.
4. Default: attach `monitor_build`, catching `MonitorAborted` /
   `MonitorDetached`. `--detach` returns immediately.

`monitor.py` and `abandon.py` follow the identical "load → call into
`bitstream`/`runtime` → map `MonitorAborted`/`MonitorDetached`/`KeyboardInterrupt`
to exit codes" shape. The state-machine logic lives in the orchestration
packages, never in the command module — see {doc}`orchestration`.

## init: the one command that writes config, not from config

`init.py` is the exception to "load config first" — it *creates* the project, so
there is no `fslab.yaml` yet:

- **`fslab new <name>`** scaffolds the directory tree (`src/main/{scala,cc}`,
  `generated-src`, `user_rtl`, `payloads`), writes a `.gitignore`, and records
  `{"project_name": …}` in `.fslab/meta.json`.
- **`fslab init`** reads `meta.json`, optionally parses the user's top module
  with `utils.rtl_parser.extract_module_info()` (which uses `pyslang` to pull
  parameters and ports, rejecting SystemVerilog structs), validates names
  against the `utils.regexes` patterns, and renders `fslab.yaml.j2` directly via
  a local Jinja2 `Environment` (this is the only render outside the main
  pipeline — see {doc}`templates`).

When you extend `init`, keep RTL parsing in `utils.rtl_parser` and name
validation in `utils.regexes`; the command should orchestrate, not contain the
regexes.

## Checklist for a new command

- New module under `commands/`; create your own `typer.Typer()` and mount it in
  `cli.py` next to the others.
- Reuse the shared `Annotated` option aliases for any common flag.
- Follow the section → load → translate → work → report body shape.
- Shell out only through `utils.shell`; log under `.fslab/logs/` via
  `StateManager.log_file()`.
- Convert every expected failure to `error()` + `typer.Exit(code=N)`.
- If you re-use another command's logic, call its internal helper, never its
  Typer command.
- Remote work → build on the {doc}`orchestration` stamp/monitor/abandon model.
