# Introduction

`firesim-lab` lets you run **cycle-accurate, FPGA-accelerated simulations** of your Verilog and SystemVerilog designs — without writing a line of Chisel, Scala, or C++. You drop your `.v` / `.sv` sources into a project folder, declare the bridges you need in a single YAML file, and drive the entire lifecycle from one CLI (`fslab`): scaffold a project, build a metasimulation locally, or build and run on AWS F2.

:::{warning}
firesim-lab is under active development. APIs, file layouts, and template
output may change without notice. Pin to a specific image tag for any work
you want to reproduce later.
:::

## What problem this solves

[FireSim](https://fires.im) is a powerful, cycle-accurate, FPGA-accelerated simulation platform built at UC Berkeley — but using it directly assumes fluency in Chisel and Scala, comfort with the Chipyard SoC framework, and patience for a multi-hour, distro-sensitive install. That cost is reasonable for chip-design research groups. It is a hard sell for anyone who simply wants to verify a Verilog block or measure its performance against realistic workloads.

firesim-lab keeps the FireSim engine intact and replaces the user-facing surface with:

- a **single CLI** (`fslab`) covering scaffold → build → simulate → FPGA run,
- a **single config file** (`fslab.yaml`) per project,
- a **pinned Docker image** that ships the full toolchain so the host needs only Docker and `curl`,
- **generated Chisel shims** that wrap your blackbox so you never see Chisel yourself.

If you can write Verilog, you can use firesim-lab.

## Relationship to upstream projects

firesim-lab is a thin user-experience layer over two upstream projects, both used **as shipped**:

- [**FireSim**](https://fires.im) provides Golden Gate (MIDAS), the FAME-1 transform pipeline, FASED memory timing models, the Verilator/VCS simulation harness, and the AWS F2 build flow. firesim-lab makes no modifications to any of these.
- [**Chipyard**](https://chipyard.readthedocs.io) is the origin of several bridges vendored under `lib/` (UART, BlockDevice, and more to follow).

What firesim-lab *adds* is the project orchestration: the `fslab` CLI, the Jinja2 templates that produce the Chisel shim and build files, the local bridge registry, and the remote build/run pipelines for AWS F2. FireSim's own `manager` is **not** used; everything that drives a project lives in this repository.

For a deeper view of how the layers compose, see {doc}`/concepts/index`.

## How "no-Chisel" works

The "no-Chisel" promise applies to **you**, not the framework. Internally, Golden Gate still needs a Chisel/FIRRTL design to identify bridges, clock domains, and transform boundaries. firesim-lab handles this by *generating* a small Chisel shim around your blackbox:

1. You point `fslab init` at your top-module `.sv` file.
2. The CLI parses the module and populates `fslab.yaml` with its ports.
3. You map those ports to bridges (UART RX/TX, BlockDevice request/response, etc.) and select your platform.
4. `fslab generate` renders the Chisel shim (`Top.scala`, `DUT.scala`), `CMakeLists.txt`, the driver `.cc`, and any remote build/run scripts from Jinja2 templates.
5. `fslab build` runs Chisel/FIRRTL generation, Golden Gate elaboration, and finally the metasim or FPGA build.

The rendered shim and supporting files live under `src/main/scala/` (Chisel), `src/main/cc/` (driver), and the project root (`build.sbt`, `CMakeLists.txt`). The `generated-src/` and `build/` directories are *transient* — `generated-src/` is SBT and Golden Gate's working output, `build/` is CMake's — both are wiped by `fslab clean`. Most users never touch any of these, but the rendered ones are not off-limits. See [Extensibility and user control](#extensibility-and-user-control) below.

## Extensibility and user control

firesim-lab is opinionated about workflow, not about what you can change. Every rendered file in your project is a normal file on disk that you are free to edit:

- The **Chisel shim** under `src/main/scala/` (`<Top>.scala`, `<Top>BlackBox.scala`, `Config.scala`) — tweak the bridge wiring, add custom logic between your blackbox and the bridges, or change the elaboration entry point.
- The simulator **driver** at `src/main/cc/<driver>.cc` — add custom bridge handlers, instrumentation, or workload-side glue.
- The generated **`build.sbt`**, **`project/plugins.sbt`**, and **`CMakeLists.txt`** at the project root — adjust compile flags, link in extra libraries, change how sources are collected.
- The generated **remote build script** under `scripts/` — change how the F2 build host is provisioned. The detached-run wrapper under `run/fpga/staging/` is re-rendered per run.

These edits are **respected**, not silently clobbered. `fslab generate` computes a hash for every file it renders; on a subsequent run, if it detects that you have modified a rendered file, it refuses to overwrite and exits with a list of changed files. You then have three explicit choices:

- Review the changes, decide they should stay, and continue using your edited version.
- Re-render only the unchanged files, leaving your edits intact (the default path once the hash check passes).
- Discard your edits by passing `--force` to `fslab generate`, which overwrites everything against the current template output. (`fslab clean` only removes the *transient* `generated-src/` and `build/` trees — it does not touch your rendered `src/main/` files.)

The same hash-based check guards the `fslab build` and `fslab sim` paths, so an edit you made yesterday will not be silently undone by a build you kick off today.

Beyond generated files, the **bridge registry** is local: you can add your own bridges to your project's registry without sending a PR upstream. firesim-lab's project orchestration is built around your registry, not the framework's, so your custom bridges are first-class citizens.

What firesim-lab does *not* customise is **FireSim's Golden Gate (MIDAS)** — the FAME-1 transform pipeline, decoupling, and multi-clock handling are used exactly as upstream ships them. If you need to modify Golden Gate itself, you are in upstream FireSim territory, not firesim-lab.

## Simulation modes

- **Metasimulation** — All FireSim metasim backends are supported (Verilator and VCS). This is the default development loop: fast to iterate, runs entirely on your laptop or workstation inside the container.
- **FPGA-accelerated, AWS F2 only** — `fslab build fpga` produces an AGFI on AWS, and `fslab sim fpga` runs the bitstream on an F2 instance. Both can run foreground or detached (`--detach`), with `fslab monitor` re-attaching from any shell. AWS F1 has been dropped — Amazon deprecated F1 at the end of 2025.

All standard FireSim bridges and FASED memory timing models are available in both modes, unchanged.

## When to use firesim-lab — and when not

**Use it when** you want cycle-accurate simulation of a Verilog/SV design with realistic workloads, you do not want to learn Chisel or Scala, and you are comfortable with a Docker-based, AWS-backed workflow.

**Look elsewhere when:**

- Your goal is to **synthesise a bitstream for a physical FPGA board** (Vivado, Quartus, or [F4PGA](https://f4pga.org/) are the right tools — firesim-lab targets *simulation*, not deployment).
- You need **supernode, networked, or multi-node simulation topologies** — not yet supported here.
- You need **deep customisation of Golden Gate** (FAME-1, decoupling, multi-clock handling) — firesim-lab uses Golden Gate as shipped, by design.
- You require **non-AWS FPGA platforms** — currently AWS F2 is the only supported target.

## Current limitations and roadmap

Known limitations today:

- **Single-node only.** No supernode or networked topologies.
- **AWS F2 only** for FPGA simulation.
- **Limited bridge catalogue** (UART, BlockDevice, FASED). More are planned.
- **Tests and CI are immature.** Some unit tests exist but are outdated; framework-level test/CI rework is on the roadmap.
- **No `conf.py` / build config yet** for this documentation portal; the build is set up separately from authoring.

Planned, in rough priority order:

- More bridges out of the box (SerialIO, TracerV, AXI4 variants, GPIO).
- Bring-your-own-bridge polish via the local registry — already supported, but documentation and ergonomics need work.
- Framework-level test and CI rework.
- Additional FPGA platforms once a credible non-AWS alternative is in scope.
- Networked / multi-node topologies (long-term).

Roadmap items move as priorities shift. Check the {doc}`/changelog` for what actually landed in each release.

## Where to go next

- Build the mental model first: {doc}`/concepts/index`.
- Install the launcher and pull the image: {doc}`/installation/index`.
- Run the end-to-end walkthrough: {doc}`/quickstart/index`.
- Look up a specific command: {doc}`/commands/index`.
- Extending firesim-lab itself (bridges, container, CLI): {doc}`/developer/index`.
