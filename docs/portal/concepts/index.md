# Concepts

This section builds the mental model behind `fslab`. The {doc}`/commands/index` reference tells you *what* each command does; this section explains *why* the workflow looks the way it does — what FireSim is actually doing under the hood, why your Verilog is wrapped in a generated Chisel shim, what a "bridge" really is, and what changes when you cross from metasimulation to FPGA.

You can skip this section and still ship working simulations. But the first time something behaves unexpectedly — a port that does not connect, a clock that does not advance, a memory access that takes two hundred cycles when you expected one — the diagnosis lives in this vocabulary.

:::{warning}
If you are about to write or port RTL for firesim-lab, read {doc}`target-rtl-requirements` first. Golden Gate and the FPGA flow impose concrete preconditions on the target design — clocking, reset, combinational structure, and the set of supported Verilog/SystemVerilog constructs — and getting these right up front saves a great deal of debugging later.
:::

## What this section covers

- **{doc}`cycle-accurate-simulation`** — What "cycle-accurate, FPGA-accelerated" actually means. Why your design runs in FAME-1 transformed form rather than as straight synthesised RTL, how that differs from a normal FPGA prototype, and why FPGA acceleration buys you orders of magnitude more simulated cycles per wall-clock hour than pure software simulation. Read this first if you are new to FireSim-style simulation.

- **{doc}`target-vs-host`** — The single most useful distinction in FireSim's vocabulary. *Target* time is what your design sees: its clock, its cycles, its notion of "now". *Host* time is what the FPGA or simulator spends producing those target cycles. Bridges, memory timing models, and all run-time observability live on the host side and talk to the target across a well-defined boundary. Once this distinction clicks, the rest of the framework falls into place.

- **{doc}`bridges-overview`** — Bridges are how anything *outside* your blackbox — a UART terminal, a block device, a DRAM timing model, a trace stream — is connected to it without baking that peripheral into the design under test. This page covers bridges from the user's perspective: which ones ship in the registry, how they appear in `fslab.yaml`, and how port mapping works. For the framework-internal view (Scala stubs, Golden Gate `BridgeModule`s, C++ drivers), follow the cross-link to {doc}`/developer/bridges/index`.

- **{doc}`target-rtl-requirements`** — The practical checklist. Before you write or port a target design, there are concrete rules it must follow: clocking and reset structure, no combinational loops across bridge boundaries, no tristate on the FPGA side, no simulation-only constructs in synthesizable paths, and a handful of other constraints inherited from Golden Gate and the FPGA flow. First-time users especially should read this page before opening their editor.

- **{doc}`metasim-vs-fpga`** — Metasimulation (Verilator or VCS) and FPGA simulation (AWS F2) produce the same target behaviour but differ in iteration speed, debug visibility, and cost. This page tells you which mode to pick for which job and what changes about the workflow — build commands, run commands, monitoring, and artefact layout — when you cross from one to the other.

## Suggested reading order

- **First-timer with no FireSim background:** read all five pages in order. The vocabulary builds — cycle-accurate sets the stage, target/host gives you the axes, bridges populates the host side, target-rtl-requirements is the practical checklist before you write code, and metasim-vs-FPGA puts it all in motion.

- **Coming from an existing FireSim or Chipyard project:** skim {doc}`cycle-accurate-simulation` and {doc}`target-vs-host` — the underlying concepts are unchanged from upstream. Spend your time on {doc}`bridges-overview`, which covers how firesim-lab exposes bridges declaratively through `fslab.yaml` and a local registry rather than through the FireSim manager, and on {doc}`metasim-vs-fpga` for the AWS F2 specifics. Scan {doc}`target-rtl-requirements` for confirmation of what firesim-lab inherits — the rules themselves are upstream-standard.

- **Planning to write your own bridge:** start with {doc}`target-vs-host`, {doc}`bridges-overview`, and {doc}`target-rtl-requirements` to anchor the user-facing vocabulary and target-side constraints, then jump to {doc}`/developer/bridges/index` for the Scala, Golden Gate, and driver-side details.

Once the mental model is in place, head to {doc}`/installation/index` to get the container running, then {doc}`/quickstart/index` for the end-to-end walkthrough.

```{toctree}
:maxdepth: 2

cycle-accurate-simulation
target-vs-host
bridges-overview
target-rtl-requirements
metasim-vs-fpga
```
