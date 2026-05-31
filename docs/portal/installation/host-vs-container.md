# Host vs Container Environment

firesim-lab draws a hard line between the **host** — your laptop or workstation — and the **container** that does the real work. Understanding that line removes most of the "where do I run this?" confusion that trips up first-time users, especially on Windows where there is an extra WSL2 layer in between.

The short version: **the host runs Docker; everything else runs in the container.** You install almost nothing on the host. You type almost every firesim-lab command *inside* the container.

## Why the toolchain is containerized

A working FireSim/Chipyard environment normally requires a long, version-sensitive toolchain: a specific JDK, SBT and Scala, Verilator built from source, a Python environment for FireSim's tooling, ccache, and FPGA vendor pieces. Installing that natively is the single biggest source of "works on my machine" failures, and it differs across Linux distributions, macOS, and Windows.

firesim-lab sidesteps all of it by shipping the entire toolchain inside one **pinned Docker image**. Because the image is built once and tagged, every user on every platform runs *byte-for-byte the same* Scala, Verilator, and Python. Upgrading the toolchain means bumping an image tag, not rebuilding your machine. The host stays thin and disposable; the reproducibility lives in the image.

This is also what makes the "no-Chisel" promise practical: the Chisel/Scala compiler, FireSim's Golden Gate (MIDAS), and the C++ simulator build all run in the container, generated and driven by the `fslab` CLI. You never install or invoke them directly.

## The three tiers

At runtime the container presents three stacked layers. The bottom two are baked into the image and read-only; only the top one is yours.

```
┌─────────────────────────────────────────────────────────────┐
│  Tier 1 — FireSim (upstream)                 /opt/firesim    │
│  Golden Gate (MIDAS), FASED memory model, the Verilator/VCS  │
│  harness, FPGA build flows. Pinned, used as shipped.         │
├─────────────────────────────────────────────────────────────┤
│  Tier 2 — firesim-lab (this repo)        /opt/firesim-lab    │
│  Bridge library, Jinja2 templates, the fslab CLI.            │
├─────────────────────────────────────────────────────────────┤
│  Tier 3 — your project                  /target/<project>    │
│  Bind-mounted from the host. Your .v/.sv, fslab.yaml, and    │
│  all generated/build outputs. The only writable tier.        │
└─────────────────────────────────────────────────────────────┘
```

Tiers 1 and 2 are immutable: they ship in the image and are identical for every user. Tier 3 is your host workspace, mounted live into the container so that everything you and `fslab` produce — generated Chisel, FIRRTL, the driver, the metasim binary, FPGA artefacts — persists on the host after the container stops. The exact path mapping for Tier 3 and the other mounts is in {doc}`mountpoints`.

## What runs on the host

Only two things live on the host, and `install.sh` is responsible for both:

- **Docker** — the engine that runs the container. Docker Engine on Linux, Docker Desktop on macOS/Windows. This is the one real dependency, confirmed in {doc}`/setup/host-prerequisites`.
- **The `firesim-lab` launcher** — a small Bash script placed on your `PATH`. It starts/enters the container for the current workspace, writes per-workspace settings, and forwards a handful of lifecycle commands (`--down`, `--pull`, `--status`, `--reconfigure`, `--upgrade`, `--clean-cache`) to Docker Compose. It is the *only* firesim-lab command you run on the host.

Alongside the launcher, the installer also stages the Compose file and a self-contained `.aws` and `.ssh` directory under the install location — but these are configuration the launcher consumes, not tools you invoke.

## What runs inside the container

Everything else. Once `firesim-lab` drops you into the container shell, you are on Linux with the full toolchain on `PATH`:

- the **`fslab` CLI** — `fslab new`, `init`, `generate`, `build`, `sim`, and the rest of the project lifecycle;
- **SBT / Scala / the JDK**, which compile the generated Chisel shim and run Golden Gate;
- **Verilator** (and VCS where licensed) for metasimulation;
- the **FireSim Python environment** and FPGA tooling for the AWS F2 path;
- the **AWS CLI**, which is why `aws configure sso` / `aws sso login` are run *inside* the container even though no AWS CLI is installed on the host.

A useful rule of thumb: if a command is `firesim-lab ...` it runs on the host; if it is `fslab ...`, `aws ...`, `sbt ...`, or any build/sim tool, it runs in the container.

## The Windows extra layer

On Windows there is one more layer to keep straight. firesim-lab does not run on Windows directly — it runs inside a **WSL2** Linux distro, and Docker Desktop's WSL2 backend supplies the engine. So the "host" from firesim-lab's perspective is your WSL2 Ubuntu environment, not Windows itself. You install and launch firesim-lab from the WSL shell, and your workspace must live inside the WSL filesystem (under `~`), not on a Windows drive. The reasons — speed and correct file ownership — are covered in {doc}`index`.

## What you do *not* install on the host

To make the boundary concrete, none of the following belong on the host — they all live in the image:

- Java, SBT, Scala, or any JVM tooling
- Verilator or VCS
- Python or the FireSim Python environment
- The AWS CLI — even for FPGA builds and runs
- Xilinx Vivado or any FPGA vendor tooling

The full host-side checklist, including what *is* required (Docker, `curl`) and why `git` is optional, is in {doc}`/setup/host-prerequisites`.

## Where to go next

- {doc}`mountpoints` — the precise host↔container path mapping and the environment-variable reference.
- {doc}`first-container-start` — what happens the first time you launch the container.
