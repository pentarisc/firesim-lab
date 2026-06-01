> [!NOTE]
> firesim-lab is pre-1.0 and under active development. It is usable today, but
> the CLI, `fslab.yaml` schema, and registry format may change between minor
> releases until 1.0 — pin a version for reproducible setups. See the
> [Versioning & Upgrading guide](https://firesim-lab.readthedocs.io/en/latest/installation/versioning.html)
> for how to upgrade safely.

# firesim-lab

**Cycle-accurate, FPGA-accelerated simulation of your Verilog / SystemVerilog
designs — without writing a single line of Chisel, Scala, or C++.**

firesim-lab is an opinionated framework built on top of UC Berkeley's
[FireSim](https://fires.im) and [Chipyard](https://chipyard.readthedocs.io)
that turns FPGA-accelerated RTL simulation into a turnkey, project-style
workflow. Drop your `.v` / `.sv` files into a generated project folder,
declare the bridges you need in a single YAML file, and run metasimulations
locally or full FPGA simulations on AWS F2 — with one CLI.

> **Scope — simulation only.**
> firesim-lab targets *design verification and performance analysis*, not
> physical implementation. If your goal is to synthesise a bitstream and
> deploy onto a physical FPGA board, use a standard vendor flow (Vivado,
> Quartus) or [F4PGA](https://f4pga.org/) instead.

---

## Why firesim-lab?

- **Zero Chisel, zero C++, zero Scala on the user side.** Write Verilog /
  SystemVerilog. Everything else — the Chisel shim, Golden Gate elaboration
  glue, the simulator driver — is generated from templates.
- **Pinned, self-contained Docker image.** A versioned image ships the full
  toolchain (Scala/SBT, Verilator, Python, FPGA tooling). On the host
  you need only Docker and `curl`. No multi-hour install scripts, no
  per-distro fiddling.
- **Bridges included out of the box.** UART, BlockDevice, and FASED memory
  timing models ship with the framework; more on the way. Bring your own
  via the registry — no upstream PR required.
- **Independent project folders.** `fslab new` scaffolds a clean,
  out-of-tree project. No in-tree source copying, no entanglement with the
  framework repo. Each project is its own git repo, CI/CD-friendly by
  construction.
- **Single source of configuration.** A single `fslab.yaml` describes the
  design, the bridges, the build, and the run. No scattered makefrags, no
  multi-file Scala configs.
- **Automatic top-module parsing.** `fslab init` parses your top module
  and pre-populates `fslab.yaml` with its ports. You map ports to bridges;
  the framework generates the Chisel wiring.
- **First-class AWS F2 remote builds and runs.** AGFI builds are submitted
  from the EC2 host *after* the local build, so artifacts never round-trip
  through your laptop. F2 runs use pre-baked AMIs with `aws-fpga-firesim-f2`
  already installed — uptime (and cost) stays low.
- **Detached runs and resumable monitoring.** `fslab build` and
  `fslab sim fpga` both support `--detach`; `fslab monitor` re-attaches
  to in-flight jobs from any shell. `fslab abandon` cleans up safely.
- **Extensible via local registries.** Add your own bridges in your own
  registry file and point the framework at them. No upstream integration
  hassle. (Contributions back upstream are welcome but never required.)
- **All standard FireSim bridges & FASED memory models** are available
  unchanged — firesim-lab uses Golden Gate (MIDAS) as-shipped.

---

## Prerequisites

firesim-lab ships its entire toolchain — Java/SBT/Scala, Verilator, the FireSim
Python environment, and FPGA tooling — inside a single Docker image, so the host
stays thin. You need:

- **Docker** (Engine on Linux, or Docker Desktop on macOS/Windows) with the
  Compose v2 plugin, plus **`curl`**.
- **A Linux shell to run from.** Linux and macOS have one natively; on **Windows**
  you run firesim-lab inside a **WSL2** Ubuntu distro (with Docker Desktop's WSL
  integration) — there are no Windows-native scripts.
- **~16 GB RAM and ~30 GB free disk** recommended — Chisel elaboration and
  Verilator builds are memory- and disk-heavy.
- **FPGA-accelerated simulation only:** an AWS account with F2 access and the
  required IAM setup. Metasimulation needs none of this.

Full platform-by-platform setup — Docker install, WSL2, Apple Silicon notes,
hardware sizing, and AWS — is in the
[setup guide](https://firesim-lab.readthedocs.io/en/latest/setup/index.html).

---

## Quick start

firesim-lab runs anywhere Docker does — **Linux, macOS, and Windows (via WSL2)**.
All you need is Docker running and a terminal with `curl`. For platform-specific
host setup — including installing Docker Desktop and setting up WSL2 on Windows —
follow the [installation guide](https://firesim-lab.readthedocs.io/en/latest/installation/index.html).

Once Docker is running, the workflow below is the same on every platform — run it
from your terminal on Linux/macOS, or from your WSL shell on Windows:

```bash
# 1. Install the launcher and pull the image (installs the latest release)
curl -sSL https://raw.githubusercontent.com/pentarisc/firesim-lab/main/docker/install.sh | bash

# 2. Enter a workspace and open the container shell
cd ~/my-workspace
firesim-lab

# 3. Inside the container — full project lifecycle
fslab new my-design
cd /target/my-design

# Copy your .v / .sv files into user_rtl/ and workload artefacts into payloads/
# Then:
fslab init --top-module MyTop --top-module-file user_rtl/MyTop.sv --platform f2
# (edit fslab.yaml: pick bridges, map ports, configure build/run)

fslab generate     # render Chisel shim, CMakeLists, driver, etc.
fslab build metasim
fslab sim metasim  # local Verilator/VCS metasimulation

# Or, for FPGA-accelerated simulation on AWS F2:
fslab build fpga
fslab sim fpga
```

The installer with no arguments pins to the **latest stable release**. To install
a specific version (or the moving `main` dev image) instead, pass it explicitly:

```bash
curl -sSL https://raw.githubusercontent.com/pentarisc/firesim-lab/main/docker/install.sh | bash -s -- v0.7.0
```

Full lifecycle reference is below.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Project lifecycle](#project-lifecycle)
3. [Environment variables](#environment-variables)
4. [Acknowledgements & non-affiliation](#acknowledgements--non-affiliation)
5. [Licensing](#licensing)

---

## Architecture

firesim-lab has three layers. The bottom two are baked into the Docker
image and never modified at runtime; the top one is your project, mounted
into the container as `/target`.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 1 — FireSim (upstream)                       /opt/firesim    │
│                                                                     │
│  Used as shipped. Provides Golden Gate (MIDAS), FASED memory model, │
│  Verilator/VCS simulation harness, and the FPGA build flows.        │
│  Pinned at a known-good commit in the image.                        │
└────────────────────────────┬────────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 2 — firesim-lab (this repo)             /opt/firesim-lab     │
│                                                                     │
│  Bridge library, Jinja2 templates, and the fslab CLI:               │
│    • lib/bridges/        — Scala stubs + Golden Gate BridgeModules  │
│                            + C++ drivers (UART, BlockDev, …)        │
│    • lib/registry.yaml   — registry of bridges, platforms, features │
│    • fslab-cli/          — the fslab CLI (Typer-based Python)       │
│    • fslab-cli/fslab/templates/                                     │
│                          — Jinja2 templates for the generated       │
│                            Chisel shim, CMakeLists, driver, and     │
│                            remote build/run scripts                 │
└────────────────────────────┬────────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 3 — Your project              /target/<my-project>           │
│                                                                     │
│  Scaffolded by `fslab new`. Lives in its own folder / git repo:     │
│    • fslab.yaml          — single source of configuration           │
│    • user_rtl/           — your .v / .sv sources                    │
│    • payloads/           — workload artefacts (loadmem, ROMs, …)    │
│    • generated-src/      — Chisel shim, FIRRTL, Golden Gate output  │
│    • build/              — driver, metasim binary, FPGA artefacts   │
│    • run/                — detached-run staging and results         │
│    • scripts/            — generated remote build/run wrappers      │
└─────────────────────────────────────────────────────────────────────┘
```

Why three layers?

| Concern                | How it is solved                                           |
|------------------------|------------------------------------------------------------|
| FireSim upgrades       | Bump the pinned commit in the image; nothing else changes  |
| Shared bridge code     | Lives once in `lib/`; every project inherits it            |
| Project isolation      | Each project is its own folder / repo                      |
| Docker immutability    | Layers 1 & 2 are read-only; only `/target` changes         |
| Reproducible toolchain | Pinned image tag → identical Scala/Verilator/SBT versions  |

---

## Project lifecycle

The `fslab` CLI (inside the container) drives the whole lifecycle. The
table below is the high-level map; each command has its own `--help` and
the detailed documentation will live alongside it (separate from this
README).

| Command                      | Purpose                                                                 |
|------------------------------|-------------------------------------------------------------------------|
| `fslab new <name>`           | Scaffold a new out-of-tree project under `/target/<name>`               |
| `fslab init`                 | Parse the top module and generate `fslab.yaml` with ports populated     |
| `fslab generate`             | Render templates → Chisel shim, CMakeLists, driver, helper scripts      |
| `fslab build metasim`        | Build a local Verilator/VCS metasimulation binary                       |
| `fslab build driver`         | Build only the simulator driver                                         |
| `fslab build fpgasim`        | Build the FPGA-side simulation binary                                   |
| `fslab build fpga [--detach]`| Build an FPGA bitstream on AWS F2 (foreground or background)            |
| `fslab sim metasim`          | Run a local metasimulation                                              |
| `fslab sim fpga [--detach]`  | Run a built bitstream on an F2 host (foreground or background)          |
| `fslab monitor build \| run` | Attach to an in-flight background build or detached run                 |
| `fslab abandon build \| run` | Discard local state for an in-flight job and clean up the remote        |
| `fslab archive`              | Snapshot the current build for later replay                             |
| `fslab clean [--all]`        | Remove `generated-src/` and `build/` (and optionally `.fslab/`)         |

Between `fslab init` and `fslab generate` you edit `fslab.yaml` to:

- list your RTL source files under `user_rtl/`,
- enable the bridges you need from the registry,
- map your top module's ports to bridge ports,
- (optionally) fill in the `target.build` and `target.run` blocks for FPGA
  builds and runs on AWS F2 — see
  [docs/run-pipeline-guide.md](docs/run-pipeline-guide.md).

The Docker launcher (`firesim-lab` on the host) provides the supporting
commands for the container itself:

| Command                      | Purpose                                                |
|------------------------------|--------------------------------------------------------|
| `firesim-lab`                | Start the container (or enter it if already running)   |
| `firesim-lab --down`         | Stop and remove the container                          |
| `firesim-lab --pull`         | Pull the latest image and restart                      |
| `firesim-lab --reconfigure`  | Re-prompt workspace settings                           |
| `firesim-lab --upgrade`      | Re-pin this workspace to the installed version         |
| `firesim-lab --status`       | Show container status for this workspace              |
| `firesim-lab --clean-cache`  | Remove SBT and ccache volumes (forces re-download)     |
| `firesim-lab --help`         | Show usage information                                 |

Each workspace gets its own container and its own `.firesim-lab.env`;
multiple workspaces can run side-by-side.

---

## Environment variables

The launcher sets these inside the container automatically; you should not
need to touch them. They are listed here only for reference.

| Variable                   | Default                     | Description                                                                 |
|----------------------------|-----------------------------|-----------------------------------------------------------------------------|
| `HOME`                     | `/home/firesim-lab`         | Fixed in-container home, so SBT / ccache / pip caches resolve consistently  |
| `FIRESIM_ROOT`             | `/opt/firesim`              | Layer 1 — pinned FireSim checkout (read-only)                               |
| `FIRESIM_LAB_ROOT`         | `/opt/firesim-lab`          | Layer 2 — this repo, baked into the image (read-only)                       |
| `TARGET_ROOT`              | `/target`                   | Layer 3 — bind-mounted workspace from the host                              |
| `SBT_OPTS`                 | `-Xmx8g -Xss8m …`           | JVM options for SBT (memory + non-interactive shell)                        |
| `VERILATOR_THREADS`        | host nproc                  | Verilator parallel-job count; prompted on first run, persisted in `.env`    |
| `ENABLE_CUSTOM_PLUGINS`    | `0`                         | Opt-in for loading user Python plugins (security-sensitive)                 |
| `CACHE_GID`                | `2543`                      | GID of the in-image `firesim-lab-cache` group owning the SBT/ccache caches  |
| `CONTAINER_MEMORY_LIMIT`   | `16g`                       | Docker memory ceiling for the container                                     |
| `CONTAINER_MEMORY_RESERVE` | `8g`                        | Docker memory reservation                                                   |

Host-side (consumed by the launcher, written to `<workspace>/.firesim-lab.env`):

| Variable             | Description                                                                          |
|----------------------|--------------------------------------------------------------------------------------|
| `FIRESIM_IMAGE`      | Pinned image tag (default `pentarisc/firesim-lab:latest`)                            |
| `CONTAINER_NAME`     | Derived from the workspace basename; one container per workspace                     |
| `HOST_WORKSPACE_DIR` | The workspace directory on the host, bind-mounted as `/target`                       |
| `HOST_AWS_DIR`       | Bind-mounted at `~/.aws` so `aws sso login` etc. persist credentials                 |
| `HOST_SSH_DIR`       | Bind-mounted at `~/.ssh` so ssh / scp / git / rsync find keys at the conventional path |
| `HOST_UID`, `HOST_GID` | Detected from the workspace mount; used by the entrypoint to drop privileges       |

---

## Acknowledgements & non-affiliation

firesim-lab stands on the shoulders of two outstanding open-source projects
from UC Berkeley:

- [**FireSim**](https://fires.im) — the cycle-accurate, FPGA-accelerated
  hardware simulation platform. firesim-lab uses Golden Gate (MIDAS) and
  FireSim's bridge infrastructure as shipped, with no modifications to the
  FAME-1 transform pipeline, decoupling, or multi-clock handling.
- [**Chipyard**](https://chipyard.readthedocs.io) — the integrated SoC
  research and development framework. The bridges vendored under `lib/`
  (UART, BlockDevice, …) originate from Chipyard / firechip.

We are deeply grateful to the FireSim and Chipyard teams for their work,
and for releasing it under permissive open-source licences that make
projects like this possible.

The one piece of firesim-lab that is *not* derived from upstream is the
`fslab` CLI and its associated project lifecycle (templates, registry,
build/run orchestration, AWS F2 remote build and run pipelines). These
were re-imagined from the ground up to make FireSim approachable to users
who want to simulate plain Verilog / SystemVerilog without learning Chisel
or Scala.

**firesim-lab is an independent project. It is not affiliated with,
endorsed by, or supported by the FireSim or Chipyard projects, UC Berkeley,
or the Berkeley Architecture Research group.** Issues with firesim-lab
should be reported here, not to the upstream projects.

---

## Licensing

This project is licensed under the Apache License 2.0.

It includes third-party components that are licensed under their respective
open-source licenses. All third-party license texts and attributions are
provided in the [licenses/](licenses/) directory.

Unless otherwise noted, modifications and original code in this repository
are licensed under the Apache License 2.0.
