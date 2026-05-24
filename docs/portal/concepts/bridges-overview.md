# Bridges Overview

A bridge, as introduced in {doc}`target-vs-host`, is the seam between target time and host time — the only place where the simulator allows the two clock domains to interact. This page is the user-facing view: what a bridge is made of, which ones ship with firesim-lab today, how they appear in `fslab.yaml`, and what is in scope here versus on the developer-side pages.

## What a bridge is

Every bridge has three parts. As a user you interact with the first; the other two are handled by the framework (or by bridge authors — see {doc}`/developer/bridges/index`).

1. **A target-facing port group.** Your top-module port plugs into this. Bridge ports are decoupled (ready/valid-style handshakes), not raw combinational connections. The list of ports each bridge exposes is declared in `lib/registry.yaml` and reproduced as per-bridge spec sheets in {doc}`/developer/bridge-reference/index`.
2. **A host-side model.** Protocol-aware logic — typically a Chisel `BridgeModule` instantiated by Golden Gate on the FPGA side, paired with C++ code on the driver side — that decides what to drive back onto the target-facing port group on each target cycle.
3. **A driver-side handler.** Connects the host model to real software: a TTY for UART, a backing file for a block device, a configurable timing pattern for DRAM.

Golden Gate, FireSim's FAME-1 compiler, is what wires (1) to (2) and inserts the host-side stalling logic that lets (2) take as much host time as it needs without distorting target time. The formalism behind that stalling — *latency-insensitive bounded dataflow networks* — comes from [Bounded Dataflow Networks and Latency-Insensitive Circuits](https://people.csail.mit.edu/vmurali/papers/libdns.pdf) (Vijayaraghavan and Arvind, 2009); Golden Gate's use of LI-BDN to decompose target RTL into independently-schedulable bridge models is described in [Golden Gate: Bridging the Resource-Efficiency Gap Between ASICs and FPGA Prototypes](https://davidbiancolin.github.io/papers/goldengate-iccad19.pdf) (Magyar, Biancolin, Koenig, Seshia, Bachrach, Asanović, ICCAD 2019). You do not need either paper to use bridges, but they are the canonical references for the internals.

## Why bridges, not "I/O ports"

Because the target runs at a variable host:target cycle ratio, physical peripherals cannot plug directly into target signals — a real UART running at 115200 baud has no idea your target produces one cycle every fifty FPGA cycles. Anything outside the target must be modelled on the host side. Two consequences shape how your RTL talks to a bridge:

- **Bridge target-facing ports are decoupled.** You drive them via ready/valid handshakes. No combinational paths cross the bridge boundary. The full set of RTL-side rules lives in {doc}`target-rtl-requirements`.
- **What looks like real-time behaviour at the target — DRAM latency, UART line rate, block-device service time — is computed by a host-side model.** This is a feature, not a limitation: the model is configurable, deterministic, and reproducible across runs.

## Bridges that ship today

firesim-lab's default registry (`lib/registry.yaml`) ships three bridges. Each has a high-level role described below; for full port lists, required parameters, and runtime flags, see {doc}`/developer/bridge-reference/index`.

- **UART** (registry id `uart`). A character-stream bridge with a small set of ready/valid serial signals on the target side. The host driver writes received bytes to a TTY or log file and feeds the target from a TTY or input file. Useful for serial consoles, simple text-based I/O, and printf-style debugging from inside the target.
- **BlockDevice** (registry id `iceblk` — the id is a historical name from Chipyard's `IceBlk`). A request/response block-storage bridge. The target sees a tagged request channel, a write-data channel, a response channel, and an info channel; the host driver exposes a backing file as the block device. Useful for booting from disk images and for workloads that need persistent storage.
- **FASED memory timing model** (registry id `fased`). An AXI4 memory interface backed by a configurable DRAM timing model. The target sees a full AXI4 slave interface; the host model produces target-cycle-accurate responses with latencies that match a chosen DRAM technology and access pattern. Described in [FASED: FPGA-Accelerated Simulation and Evaluation of DRAM](https://davidbiancolin.github.io/papers/fased-fpga19.pdf) (Biancolin, Karandikar, Kim, Koenig, Waterman, Bachrach, Asanović, FPGA 2019).

Alongside bridges, the registry also lists a small set of **features** — non-bridge build-time scaffolding options such as `multi-clock` (adds a rational clock bridge and a multi-clock target scaffold) and `printf-synthesis` (enables Golden Gate's printf-synthesis pass so Chisel `printf()` calls bridge through to the host at run-time). Features are configured in the same registry but do not contribute a target-facing port group; we mention them here only so they do not surprise you when you read the registry.

## How a bridge appears in `fslab.yaml`

You select bridges and map your top-module ports to them in `fslab.yaml`. A typical entry, for a single UART:

```yaml
bridges:
  - type: "uart"
    name: "serial_0"
    port_map:
      txd: "uart_tx"     # bridge's "txd" pin <- your top-module's "uart_tx"
      rxd: "uart_rx"
    params:
      freq_mhz: 100
      baud_rate: 115200
```

The four keys, in order:

- **`type`** — the bridge's registry id (`uart`, `iceblk`, `fased`, or anything declared in a custom registry).
- **`name`** — a unique instance name. Two UARTs is two entries with two different `name`s.
- **`port_map`** — for each bridge-side port (left), the matching top-module port (right). `fslab init` pre-populates the top-module port names from your `.sv` for you under `design.blackbox_ports`; you fill in the mapping here.
- **`params`** — bridge-specific configuration. Each bridge declares its `required_params` in the registry, and validation fails at parse time if any are missing. The reference page for each bridge lists which parameters exist and what they mean.

The typical flow is `fslab init` (seeds `fslab.yaml` with the top-module ports populated) → hand-edit the `bridges:` block to choose bridges and wire ports → `fslab generate` to render the Chisel shim and CMake build. The end-to-end walkthrough is in {doc}`/quickstart/index`.

## The registry concept

A registry is a YAML file listing the bridges (and platforms, features, simulators, bitbuilders, runners) available to the framework. firesim-lab ships `lib/registry.yaml` with the built-in bridges; you can layer additional registries on top via `advanced.custom_registries` in your `fslab.yaml`:

```yaml
advanced:
  custom_registries:
    - path: "lib/custom_ip/registry.yaml"
```

Custom registries follow the same schema. Last definition wins on ID conflicts, so a custom registry can override a built-in bridge cleanly. This is firesim-lab's main extensibility hook for bridges: you author your own bridge and ship it inside your project without sending a patch upstream. The "how to author a bridge" walkthrough is in {doc}`/developer/bridges/index`; the per-bridge spec format is in {doc}`/developer/bridge-reference/index`.

:::{note}
A custom registry entry may optionally point at a Python `plugin:` file for parse-time validation. Plugins execute arbitrary Python — only enable plugins from sources you wrote yourself or fully trust.
:::

## What is generated, what is not

For built-in bridges, you do not write any code beyond filling in `fslab.yaml`. `fslab generate` renders:

- the Chisel shim that wires your blackbox port to the bridge's target-facing port,
- the Golden Gate annotations that identify the bridge boundary, and
- the driver-side invocation glue for built-in bridges.

If you author your own bridge, you also write the Scala `BridgeModule`, the host-side protocol model, and the C++ driver implementation. Those three pieces — and the templating contract `fslab generate` expects them to satisfy — are what {doc}`/developer/bridges/index` covers; the spec-sheet view of an existing bridge is in {doc}`/developer/bridge-reference/index`.

## Where next

The natural follow-up is {doc}`target-rtl-requirements` — having seen what a bridge is, the next thing to internalise is the set of RTL rules your target must obey to talk to one cleanly.
