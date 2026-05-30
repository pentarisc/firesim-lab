# fslab Commands

`fslab` is the single entry point for the whole project lifecycle: scaffold a workspace, describe your design, generate the framework code, build, and simulate — in metasimulation or on a real AWS F2 FPGA. This section is the command reference. For a guided, copy-paste walkthrough of the same commands, see {doc}`/quickstart/index`.

Every command runs **inside the firesim-lab container**, where `fslab` is installed and your workspace is bind-mounted at `/target`. See {doc}`/installation/first-container-start` if you have not started the container yet.

## The lifecycle at a glance

The commands are designed to be run roughly in this order. Most of them are *idempotent* and call the earlier steps implicitly when needed, so you rarely run them all by hand.

```text
new ──▶ init ──▶ generate ──▶ build ──▶ sim          (metasimulation)
                                  │
                                  └── build fpga ──▶ sim fpga   (real F2 hardware)
```

- `fslab new` and `fslab init` are one-time-per-project.
- `fslab generate` and `fslab build` are hash-aware: they re-run only when `fslab.yaml` (or a registry) has changed.
- `fslab sim` and `fslab sim fpga` call `generate` and `build` for you, so day-to-day you mostly type `fslab sim`.

## Command reference

| Command | Purpose | Reference |
|---|---|---|
| `fslab new <project>` | Scaffold a new out-of-tree project folder. | {doc}`new` |
| `fslab init` | Parse your top module and write a starting `fslab.yaml`. | {doc}`init` |
| `fslab generate` | Render Jinja2 templates → Chisel shim, driver, build files. | {doc}`generate` |
| `fslab build [metasim\|driver\|fpgasim\|fpga]` | Run the RTL + driver build chain (and, for `fpga`, the bitstream build). | {doc}`build` |
| `fslab sim [metasim]` | Run a cycle-accurate software simulation. | {doc}`sim` |
| `fslab sim fpga` | Run a built bitstream on a real F2 host. | {doc}`sim-fpga` |
| `fslab monitor build \| run` | Attach to an in-flight background build or detached run. | {doc}`monitor` |
| `fslab abandon build \| run` | Tear down the remote and clear local state for an in-flight build/run. | {doc}`abandon` |
| `fslab clean` | Delete generated artefacts and build directories. | {doc}`clean` |
| `fslab archive` | Create a `.tar.gz` snapshot of the project. | {doc}`archive` |

## Where the `fslab.yaml` reference lives

`fslab.yaml` is the one file you hand-edit. Because it spans the whole lifecycle, its fields are documented next to the command that consumes them:

- {doc}`init` — the mandatory blocks (`project`, `design`, `host`, the basic `target` fields) plus `bridges` and `advanced`. This is what `fslab init` writes and what you edit for metasimulation.
- {doc}`build` — the `target.build` block (FPGA build frequency/strategy, build host acquisition, publish).
- {doc}`sim-fpga` — the `target.run` block (run host, FPGA slot, runner args, artifact source).

## Global options

These apply to the top-level `fslab` command:

| Option | Description |
|---|---|
| `--version`, `-v` | Print the `fslab` version and exit. |
| `--help` | Show help. Works on any subcommand: `fslab <command> --help`. |

Running `fslab` with no arguments prints the top-level help.

```{toctree}
:maxdepth: 1
:hidden:

new
init
generate
build
sim
sim-fpga
monitor
abandon
clean
archive
```
