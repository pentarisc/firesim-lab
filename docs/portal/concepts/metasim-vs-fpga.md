# Metasim vs FPGA Simulation

firesim-lab gives you two ways to run a target: software metasimulation (Verilator, VCS, or Xcelium on your workstation) and FPGA-accelerated simulation (an AWS F2 instance running a built bitstream). Both produce the *same* cycle-accurate target trace — that is the entire point of {doc}`cycle-accurate-simulation`. What differs is the wall-clock cost of producing those cycles, the setup required, the debug surface available, and the rhythm of the development loop.

This page tells you which mode to pick for which job and what changes about the workflow when you cross from one to the other.

## What differs

| | Metasim | FPGA |
|---|---|---|
| **Simulator** | Verilator, VCS, or Xcelium | AWS F2 hardware with a built AGFI |
| **Setup** | Container runtime only | Container runtime + AWS account + IAM setup |
| **Target frequency** | tens of kHz to ~1 MHz | high MHz to low tens of MHz |
| **Iteration loop** | one build (minutes), fast runs | bitstream build (hours, remote), then fast runs |
| **Debug visibility** | full waveforms; standard simulator features | `printf-synthesis`, AutoCounter, TracerV, per-bridge logging; waveforms only for explicitly captured signals |
| **Cost** | your workstation | EC2 build-host hours + EC2 F2 run-host hours |
| **Detached jobs** | foreground only | `--detach` and `fslab monitor` supported for build and run |

The cycle-accuracy claim is identical on both columns: every target signal at every target cycle matches what unmodified RTL would have produced. The differences are operational, not behavioural.

## How the workflow differs in `fslab`

The two paths share `fslab new`, `fslab init`, and `fslab generate`. They diverge at the build step.

**Build:**

- `fslab build metasim` runs entirely inside the container. One synchronous invocation, minutes for a small design.
- `fslab build fpga` triggers a remote AWS F2 build. The local stage prepares artefacts, then the bitstream build runs on an EC2 build host. Typical wall-clock time is several hours, so this is almost always invoked with `--detach`; re-attach later with `fslab monitor build`.

**Run:**

- `fslab sim metasim` runs the metasim binary locally inside the container. Foreground only.
- `fslab sim fpga` stages bitstream and driver to an F2 host, loads the AGFI, executes the driver, and collects artefacts. Supports `--detach` plus `fslab monitor run`; cleanup via `fslab abandon run`.

**Config:**

- The `target.build` and `target.run` blocks in `fslab.yaml` apply only to FPGA mode. They specify the build host (external SSH or `ec2_launch`), the publish target (`none`, `local_tarball`, or `aws_afi`), and the run host. Metasim ignores both blocks entirely.

**Artefact layout:**

- Metasim outputs land under `build/`.
- FPGA mode additionally produces `build/fpga/` (build artefacts, including the staged bitstream tarball or AGFI manifest) and `run/fpga/` (per-run results, including driver logs, uartlog, and any pulled-back payload outputs).

## When to pick which

- **Bring-up and block-level functional verification** → metasim. Fastest edit-build-run loop, full waveform visibility, no AWS spend.
- **OS boot, long workloads, realistic benchmarks** → FPGA. The factor of 100–1000× speed-up over metasim is what makes workloads measured in seconds-to-minutes of simulated time tractable.
- **Sanity-checking a fix or a config change** → metasim, even mid-FPGA-workflow. Faster, cheaper, and the cycle-accuracy guarantee means a fix that holds in metasim will hold on the FPGA.
- **Final performance numbers or sign-off-grade results** → FPGA, with workload length long enough to be representative of silicon behaviour.
- **CI / regression** → metasim for breadth (many short tests, low cost); FPGA only for the small subset of long tests where target-cycle volume matters.

## The intended development loop

You do not pick one mode and stick with it. The natural rhythm is **metasim first, FPGA after**: bring-up and iteration in metasim, where the loop is fast and the debug surface is rich, then FPGA validation against long workloads once the design is stable. The same RTL and the same `fslab.yaml` drive both — only the build/sim target you invoke changes.

:::{tip}
Cycle-accuracy holds across both modes: a fix verified in metasim will reproduce on the FPGA. Use that. Push as much of your bring-up loop into metasim as the workload length allows, and reserve FPGA time for what actually needs FPGA throughput.
:::

## A note on FPGA debug

When you do hit something on the FPGA that does not reproduce in metasim, the FPGA debug surface is narrower than waveforms but not empty. The common tools are:

- **`printf-synthesis`** — Chisel `printf()` calls in the target stream through to the host driver at run-time (enabled as a feature in the registry).
- **AutoCounter** — synthesised performance-counter readouts.
- **TracerV** — instruction-trace capture for processor targets.
- **Per-bridge logging** — UART output, bridge driver logs, FASED memory-system stats.
- **Captured waveforms** — only for signals you explicitly marked for capture before the bitstream build.

Detailed mechanics for each of these are out of scope for the Concepts section — see {doc}`/commands/index` for now; dedicated debug-tooling pages are on the roadmap.

## Where this section ends

You now have the full conceptual model: target and host, FAME-1, bridges, the RTL preconditions, and when to use which simulation mode. The rest of the portal is operational:

- {doc}`/installation/index` — install the launcher and pull the container image.
- {doc}`/quickstart/index` — walk through a project end-to-end.
- {doc}`/commands/index` — look up a specific `fslab` command.
