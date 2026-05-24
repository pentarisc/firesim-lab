# Target vs Host

The previous page, {doc}`cycle-accurate-simulation`, used the words "target" and "host" loosely. They are the single most important pair of terms in FireSim's vocabulary — every other concept in this section, and most of the practical advice in the rest of the docs, is easier to follow once they are precise. This page makes them precise.

## Definitions

- **Target** — the design being simulated: your Verilog / SystemVerilog blackbox plus the generated Chisel shim around it. The target is what you would, in principle, hand to a fab.
- **Host** — the machinery that produces target cycles: the AWS F2 FPGA when you run `fslab sim fpga`, or Verilator/VCS on your workstation when you run `fslab sim metasim`.
- **Target time** — the clock domain your design sees. Target cycles, target nanoseconds. The thing your performance counters count.
- **Host time** — wall-clock time on the host. FPGA clock cycles, x86 wall-clock seconds. The thing `fslab sim` reports when it finishes.

The first two are *places*; the second two are *clocks at those places*.

## The relationship between them

Host time advances continuously, the way any clock does. Target time advances only when the host has finished producing the next target cycle.

```
host clock:    |||||||||||||||||||||||||||||||||||||||||||||||||||||||||||
target tick:   ^         ^          ^                ^      ^         ^
                <-- 9 --> <-- 10 --> <----- 16 -----> <- 6 -> <-- 9 -->
```

One target cycle takes some number of host cycles to produce. That number is not constant — it depends on what the host has to do for that cycle: resolve a memory access through a DRAM timing model, wait for the simulator driver to deliver UART input, decide what FASED returns for a queued AXI read. The ratio is typically a handful to a few tens of host cycles per target cycle, but it is variable, and that variability is by design.

The point of the decoupling is that the host can take as much host time as it needs to produce a target cycle, without distorting the target's view of timing. Every target signal at every target cycle is exactly what unmodified RTL would have produced — even though, in host time, that cycle may have taken a hundred wall-clock nanoseconds to compute.

## What lives on each side

**Target side:**

- Your `.v` / `.sv` blackbox.
- The generated Chisel shim (`Top.scala`, `DUT.scala`, `Config.scala`).
- Target clock domains and target reset.

**Host side:**

- All bridges (UART, BlockDevice, FASED memory model, …).
- The simulator driver — a C++ program that loads workloads, mediates bridges, and collects output.
- The simulator harness itself: Golden Gate's runtime in FPGA mode, Verilator or VCS in metasim mode.

The target side is what you authored (or what was generated from what you authored). The host side is everything FireSim provides to make the target simulatable.

## The bridge boundary

Every interface between target and host goes through a **bridge**. A bridge is the only place where target time and host time meet, and the only mechanism by which the host stalls the target while it figures out what to send next.

That is the entire purpose of a bridge: to be the seam between two time domains so that the host can take as long as it needs without lying to the target about when things happened. When you map a target port to a bridge in `fslab.yaml`, you are declaring *this target port talks to the host through this protocol-aware seam*. {doc}`bridges-overview` covers the full picture.

## Practical consequences

The target/host distinction lets you reason about a number of things that are otherwise confusing:

- **Waveforms** show target signals at target cycles. The host clock and host-side state (bridge buffers, driver state) are not on the same trace.
- **Performance counters** inside your design measure target cycles. That is the right unit for cycle-accurate performance analysis — what you would have measured in silicon.
- **Wall-clock time** (the runtime that `fslab sim` reports, or the elapsed seconds of an FPGA run) is *host* time. It tells you how fast the simulator is, not how fast your design is.
- **"Slow simulation"** can mean two very different things:
  - Slow target throughput — your design's target frequency, fixed by the target RTL. The host cannot make a 100 MHz target look like a 1 GHz target.
  - Slow host-to-target ratio — a bridge or memory model holding things up. This is what you change when you tune the simulator, not the design.

Telling these two apart is the most common payoff of the target/host distinction in practice.

## Multi-clock targets

A target design can have more than one clock domain. Each target clock domain is independently decoupled from host time — every target clock advances on its own schedule, governed by what the host has finished producing for that domain. From the host's point of view there is still one host clock; from each target domain's point of view, there is one target clock and zero awareness that other domains exist except through the explicit clock crossings inside the target.

The *structural* rules for declaring multiple target clocks (clock-generator modules, async crossings, recognised reset semantics) belong in {doc}`target-rtl-requirements`. The conceptual point — that each target clock domain has its own target time, all of them decoupled from the single host time — belongs here.

## Where next

The bridge boundary is where this section becomes operational rather than conceptual. {doc}`bridges-overview` is the natural next page.
