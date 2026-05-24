# Cycle-Accurate Simulation

If you have written Verilog, you have run simulation before — typically functional simulation in Verilator, Icarus, VCS, ModelSim, or Xcelium. firesim-lab runs the same *kind* of simulation: cycle-accurate, deterministic, every signal observable at every clock edge. What is new is the engine that executes it. Instead of running on your CPU, your design runs on an FPGA, configured by a system called Golden Gate so that what you observe on the FPGA matches *exactly* what the silicon would produce — cycle for cycle, signal for signal.

This page explains what that means, why it is not the same as putting your RTL on an FPGA in the usual way, and why the trade-off is worth the conceptual overhead.

## What "cycle-accurate" means here

Cycle-accurate simulation means: at every target clock edge, every signal in your design has the same value it would have in real silicon. Nothing is approximated, nothing is sampled, and there are no missing or fabricated cycles. It is exactly what a software RTL simulator gives you — only produced by an FPGA running thousands of times faster.

Distinguish this from three other things you may have encountered:

- **Functional simulation** of behavioural code that has not yet been written in synthesizable form — useful for algorithm bring-up, but not faithful to the design that will actually be built.
- **Gate-level simulation** with back-annotated SDF timing — slower than RTL simulation, faithful to placement and routing, and rarely used outside of sign-off.
- **FPGA prototyping** — synthesising your RTL onto an FPGA so it runs at near-native speed. Fast, but it stops being faithful to your design the moment any external interface (memory, I/O, peripherals) has different timing on the FPGA board than on the silicon target.

firesim-lab is none of these. It is cycle-accurate RTL simulation, executed on an FPGA, with deterministic timing for everything your design talks to.

## The cost of cycle-accuracy in software

Cycle-accurate software simulators are extraordinary tools. They are also slow. A Verilator simulation of a small RISC-V core might run at 100 kHz to 1 MHz of simulated clock; a complex SoC drops to tens of kilohertz. Booting Linux takes hours. Running a one-second benchmark of real-world activity at a simulated 1 GHz would take roughly two weeks at 1 kHz of host throughput.

For block-level verification, that is fine — you write directed tests that exercise a handful of corner cases. For *system-level* questions — does the memory hierarchy actually deliver the throughput my workload needs? does the OS bring up cleanly under the interrupt latencies my design imposes? — software simulation is a dead end. You cannot wait two weeks for one data point.

This is the problem FireSim was built to solve: keep the cycle-accuracy of software RTL simulation, but produce target cycles fast enough to run realistic workloads.

## Why this is not "just synthesise to an FPGA"

The intuitive solution — put your RTL on an FPGA and let it run — is FPGA prototyping. It works, and it is much faster than software simulation. But it has a fatal cost for verification: the prototype runs in the FPGA's wall-clock time, not your target's clock time.

That sounds harmless until you think about what your design talks to:

- **DRAM** on the FPGA prototype is the FPGA board's DRAM, not the DRAM your silicon would use. The latencies are wrong.
- **I/O peripherals** run at FPGA-host speeds, not target speeds. Behaviour that depends on relative timing breaks.
- **Anything you want to inject deterministically** — a specific memory access pattern, a faulted clock edge, a precise arrival time of an interrupt — you cannot, because the FPGA is too fast and not under your control at cycle granularity.

You end up with a fast simulator that lies about the things you most need accurate. That is unhelpful for performance analysis and dangerous for verification.

## What FireSim does: the FAME-1 trick

FireSim's Golden Gate compiler transforms your design before it goes on the FPGA. The transformed design no longer runs in real time — it runs *as a simulator of itself*. The FPGA's own clock becomes the **host clock**, and your design's clock becomes the **target clock**. One target cycle takes many host cycles to produce, but every signal at every target cycle is exactly what your unmodified RTL would have produced.

The formal name for this style of transformation is **FAME-1** — level 1 in the [FAME taxonomy](https://people.eecs.berkeley.edu/~krste/papers/fame-isca2010.pdf) (*FPGA Architecture Model Execution*) introduced by Tan, Waterman, Cook, Bird, Asanović, and Patterson at ISCA 2010. You do not have to read the paper to use firesim-lab, but two consequences matter:

1. Your design's timing is preserved exactly — bit for bit, cycle for cycle.
2. The FPGA is no longer running your design at silicon speed. It is *simulating* it at a host-imposed pace.

The exact ratio of host cycles to target cycles depends on your design and on how the FPGA's resources are scheduled, but it is typically a few to a few tens of host cycles per target cycle. With FPGAs running at hundreds of MHz, that puts simulated target rates in the high megahertz to low tens of megahertz — fast enough to boot Linux in minutes and run real benchmarks in hours, while keeping every cycle observable and reproducible.

The distinction between target time and host time runs through everything else in the framework. The next page, {doc}`target-vs-host`, makes it formal.

## What you give up

The price of cycle accuracy on an FPGA is that the FPGA is no longer running in real time. So:

- **Nothing physical can plug directly into your target.** A real UART running at 115200 baud does not know that your target is producing one cycle every fifty FPGA cycles. Any device that needs to talk to your design must be modelled on the *host* side, in software or on FPGA logic that itself understands the target/host timing relationship.
- **Memory becomes a model.** Real DRAM is too fast and too unforgiving for a decoupled target; FireSim ships a configurable memory timing model (FASED) that produces target-cycle-accurate DRAM responses on demand.

Both of these are addressed by the same mechanism: **bridges**. Every interface between your target design and the outside world goes through a bridge that mediates the two time domains. {doc}`bridges-overview` covers the full picture.

## When this pays off

Cycle-accurate, FPGA-accelerated simulation is worth the framework's conceptual overhead when:

- You need to run *long* workloads — minutes to hours of simulated time — with confidence the numbers match silicon: performance counters, cache behaviour, OS-level effects.
- You are verifying timing-sensitive behaviour that software simulation makes too slow to exercise — multi-core coherence under realistic memory pressure, interrupt latencies under a working OS, throughput under realistic I/O patterns.
- You want a single source of truth for both functional verification and performance analysis, without maintaining a separate prototype and a separate model.

If you only need block-level functional verification or a short directed-test sweep, you do not need this framework — Verilator alone is faster to set up and good enough for that job. The break-even comes the moment you need cycle accuracy *and* a workload longer than a few seconds of simulated activity.
