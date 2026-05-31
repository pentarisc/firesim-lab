# fslab Python Architecture

This section is the contributor's reference for the `fslab` CLI under
`fslab-cli/` — the Python program that orchestrates the entire firesim-lab
project lifecycle. It is written for developers who want to *extend* the CLI:
add a subcommand (a `debug` or `trace` flow), register a new bridge, teach the
framework about a new FPGA platform or host model, or change how code is
generated. It is **not** a usage guide — for the end-user command walkthrough
see the {doc}`/commands/index` section.

If you only want to add a Verilog blackbox and run it, you do not need anything
here. If you want to change what `fslab` *does*, start on this page, then follow
the links into the per-area detail pages and the {doc}`extending` recipes.

## What `fslab` is

`fslab` is a [Typer](https://typer.tiangolo.com/) application (entry point
`fslab.cli:app`, declared in `pyproject.toml` under `[project.scripts]`) that
runs **inside the firesim-lab Docker container**, with the user's project
directory bind-mounted at `/target`. It does not implement any hardware
compilation itself. Its job is to:

1. Parse and validate the user's `fslab.yaml` against one or more registry
   files (the **two-pass config system**).
2. Render Jinja2 templates into the Chisel shim, `build.sbt`, `CMakeLists.txt`,
   the C++ driver, and the remote build/run wrappers.
3. Shell out, in order, to `sbt`, the Golden Gate / MIDAS Java stages,
   `cmake`/`make`, the simulation binary, and (for FPGA) a remote build/run
   pipeline over SSH.
4. Track state in a hidden `.fslab/` directory so repeated invocations are
   idempotent and long-running remote jobs can be detached and re-attached.

Everything `fslab` runs is parameterised from the validated config and the
registry — there are no hardcoded toolchain paths in the command modules.

## Package map

```text
fslab/
├── cli.py            Typer app; registers every subcommand router. No logic.
├── commands/         One module per command group. Typer wiring + the
│                     orchestration of each command's steps.
│   ├── init.py         fslab new / fslab init
│   ├── build.py        fslab generate / fslab build {metasim,driver,fpgasim,fpga}
│   ├── sim.py          fslab sim {metasim,fpgasim,fpga}
│   ├── fpga.py         fslab archive
│   ├── monitor.py      fslab monitor {build,run}
│   ├── abandon.py      fslab abandon {build,run}
│   └── context.py      Builds the flat Jinja2 render context from config+registry.
├── schemas/          Pydantic v2 models + the two-pass parser. The semantic
│                     core: every validation rule lives here.
├── utils/            Cross-cutting helpers: Rich console, subprocess streaming,
│                     .fslab/ state + config hashing, SV port parsing, regexes.
├── bitstream/        FPGA bitstream build pipeline (the remote build).
├── runtime/          FPGA run pipeline (fslab sim fpga, detached runs).
├── pipeline/         Pipeline-agnostic SSH host abstraction + monitor primitives
│                     shared by bitstream/ and runtime/.
├── cloudutils/       Cloud-provider helpers (AWS EC2/FPGA).
└── templates/        Jinja2 source of truth for all generated files.
```

The first three directories (`commands/`, `schemas/`, `utils/`) plus
`templates/` cover metasimulation end to end. The `bitstream/`, `runtime/`,
`pipeline/`, and `cloudutils/` packages are the FPGA-acceleration layer — see
{doc}`orchestration`.

## The request lifecycle

Every command follows the same spine. Tracing one invocation top to bottom is
the fastest way to understand where new behaviour plugs in:

1. **Dispatch.** `cli.py` builds the top-level `typer.Typer()` and mounts each
   command group's own `Typer` router via `app.add_typer()`. A bare `fslab`
   prints help (`no_args_is_help=True`).
2. **Load + validate.** Commands call `load_and_validate("fslab.yaml")` from
   `schemas.parser`, which runs the two-pass system: **Pass 1** merges the
   default and custom registry files into a `MasterRegistry`; **Pass 2**
   validates the project YAML with that registry injected as Pydantic
   validation context, so cross-references (platform exists, bridge type
   exists, port maps line up) are checked during model construction. The result
   is cached per-process and locked to one project path.
3. **Hash-gated generate.** `fslab generate` computes a SHA-256 over
   `fslab.yaml` plus every loaded registry file (`utils.state.StateManager`).
   If it matches the value stored in `.fslab/state.json`, rendering is skipped
   unless `--force`. This is why `build` and `sim` can call `generate`
   unconditionally — it is a no-op when nothing changed.
4. **Render.** `commands.context._build_template_context()` flattens the
   validated models into a plain dict; `build._render_templates()` maps each
   template to an output path and writes it. Files the user has hand-edited
   since the last generate are detected by hash and protected unless `--force`.
5. **Compile.** `build.cmd_compile()` shells out — `sbt package`,
   `java midas.chiselstage.Generator`, `java midas.stage.GoldenGateMain`,
   then `cmake` + `make` — each through `utils.shell.run_or_die`, which streams
   stdout/stderr live and turns a non-zero exit into a clean styled error.
6. **Run / build.** `sim` locates and execs the simulation binary; `build fpga`
   hands off to the {doc}`orchestration` layer for the remote bitstream build;
   `sim fpga` hands off to the run pipeline.

## Cross-cutting design patterns

These recur everywhere; learn them once and the rest of the code reads easily.

- **Decorator-populated registries.** New bridge configs, bitbuilder/runner arg
  and param schemas, and runner classes are all registered by importing a
  module that runs a `@register_*` decorator. The parser then resolves a string
  name (from YAML or the registry) to the registered class. This is how the
  framework stays open to extension without editing a central switch. See
  {doc}`extending`.
- **Discriminated unions for closed sets.** Where the set of variants is
  framework-owned (host models, publish targets, artifact sources) Pydantic
  discriminated unions on a `type:` field are used instead of a plugin
  registry — adding a variant is deliberately a framework change.
- **One global Rich console.** `utils.display.console` and the
  `info`/`success`/`warning`/`error`/`section` helpers are imported everywhere
  so output styling is uniform. Never instantiate a second `Console()`.
- **`run_or_die` for fatal steps.** All required subprocess steps go through
  `utils.shell.run_or_die`; optional steps use `run()` and inspect the code.
- **`.fslab/` state + stamps.** Local orchestration state lives under `.fslab/`
  (config hash, generated-file hashes, logs). Long-running remote jobs
  additionally write a **stamp** (`build/fpga/.fslab/build.yaml`,
  `run/fpga/.fslab/run.yaml`) that lets `monitor` re-attach and `abandon` clean
  up — covered in {doc}`orchestration`.
- **Validation codes.** Every rule carries a stable code (`PROJ-11`, `REG-07`,
  `BB-05`, `HMOD-04`, …) used both in the raising message and the model
  docstrings. Quote the code when you add a rule; it makes errors greppable.

## Languages a CLI contributor must know

`fslab` sits at the seam of a hardware toolchain, so contributing touches more
than Python:

| Language | Where it shows up |
|---|---|
| **Python 3.10+** | The CLI itself: Typer (commands), Pydantic v2 (schemas), Jinja2 (rendering), Rich (output). |
| **Jinja2** | The templates under `templates/` — the source of truth for all generated files. |
| **Scala / Chisel** | The generated shim (`Top.scala`, `DUT.scala`, `Config.scala`) that emits the FIRRTL annotations Golden Gate needs. |
| **FIRRTL** | The intermediate the Chisel stage emits and Golden Gate consumes; you rarely write it but must understand the `Generator` → `GoldenGateMain` boundary. |
| **C++** | The host driver (`driver.cc`) and the bridge models under `lib/bridges/`. |
| **Verilog / SystemVerilog** | The user's blackbox, which `fslab init` parses with `pyslang` to populate ports. |
| **CMake + GNU Make** | The generated `CMakeLists.txt` and the per-simulator `Makefile.<id>.sim` fragments. |
| **YAML** | `fslab.yaml` and `registry.yaml` — the entire configuration surface. |
| **Bash** | The remote build/run wrappers rendered into the project and executed over SSH on the F2 host. |

You do not need all of these for every change. A new subcommand is pure Python;
a new bridge spans Python (config), Jinja2 (Scala/C++ snippets), Chisel, and
C++.

## Extension points at a glance

| You want to… | Touch | Detail |
|---|---|---|
| Add a command / flow (e.g. `debug`, `trace`) | new module in `commands/`, register in `cli.py` | {doc}`extending` |
| Add a bridge | `schemas/resolvers.py` + `lib/registry.yaml` + `templates/bridges/<id>/` | {doc}`extending`, {doc}`/developer/bridges/index` |
| Add a bitbuilder / runner tunable | `schemas/bitbuilder_args.py` / `schemas/runner_args.py` | {doc}`extending` |
| Add a host model | `schemas/host_model.py` (closed union) | {doc}`extending` |
| Change what gets generated | `templates/` + `commands/context.py` | {doc}`templates`, {doc}`/developer/jinja-templates` |
| Add a validation rule | the relevant model in `schemas/` | {doc}`schemas` |
| Change the remote build/run | `bitstream/`, `runtime/`, `pipeline/` | {doc}`orchestration` |

```{toctree}
:maxdepth: 2

commands
schemas
templates
orchestration
extending
```
