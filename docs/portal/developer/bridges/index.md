# Bridges

A **bridge** is the mechanism by which a simulated hardware target talks to the
outside world. On one side it presents an ordinary RTL interface to your design
(UART pins, an AXI4 memory port, a block-device channel); on the other side it
runs as software on the simulation host (writing bytes to a terminal, modelling
DRAM timing, serving a virtual disk). Bridges are what make a FireSim
simulation *interactive* and *useful* rather than a closed box that only toggles
wires.

This section is the **conceptual and how-to** half of the bridge
documentation, aimed at developers who want to **extend firesim-lab with a new
bridge**. For per-bridge specification sheets (exact ports, parameters, and
driver hooks of the bridges that ship today), see
{doc}`/developer/bridge-reference/index`.

```{note}
"No-Chisel" applies to the framework's *users*, not to its *bridge authors*.
A user wires their Verilog blackbox to an existing bridge through
`fslab.yaml` and never sees Scala. Adding a *new* bridge is a framework-level
task and does involve Chisel/Scala, C++, and a little Python/Jinja2.
```

## What a bridge is made of

Every bridge spans the target/host boundary, so it is never a single file. A
complete bridge is a small set of cooperating artifacts in two languages, glued
together by a registry entry:

| Artifact | Language | Lives in | Role |
|---|---|---|---|
| Target-side interface (`*TargetIO` bundle + constructor-arg case class) | Chisel/Scala | `lib/bridges/.../bridgeinterfaces/` | The RTL-facing port bundle and the metadata passed to the host model |
| Target-side stub (`BlackBox` + `Bridge` trait) | Chisel/Scala | `lib/bridges/.../bridgestubs/` | The placeholder Golden Gate replaces with the host model; emits the bridge annotations |
| Host-side model (`BridgeModule`) | Chisel/Scala | `lib/bridges/.../goldengateimplementations/` | The FPGA-hosted token-processing logic + MMIO control registers |
| Host-side driver (`bridge_driver_t`) | C++ | `lib/bridges/src/main/cc/bridges/` | The CPU-side software that does the bridge's real I/O each tick |
| Wiring templates (`ports`, `wiring`, `top_imports`, `sim_loop`) | Jinja2 | `fslab-cli/fslab/templates/bridges/<id>/` | Generated glue that connects the user's blackbox ports to the bridge |
| Registry entry | YAML | `lib/registry.yaml` | Declares the bridge to `fslab`: ports, params, C++ types, template paths |

So a bridge author touches, at minimum, **Chisel/Scala**, **C++**, **Jinja2**,
and **YAML** — plus a working mental model of the **Verilog/SystemVerilog**
interface the bridge exposes, since that contract is what end users map their
blackbox ports onto. Python/Pydantic only enters the picture if you need to
extend the registry *schema* itself (a rare, deeper change).

## The three bridges that ship today

firesim-lab vendors three bridges from Chipyard/FireSim and wires them into the
`fslab` generator:

- **UART** (`uart`, origin `fslab`) — serial TX/RX to the host terminal, a PTY,
  or files. The simplest end-to-end example and the one used throughout these
  chapters.
- **BlockDevice** (`iceblk`, origin `fslab`) — a virtual disk served from a
  host-side file.
- **FASED** (`fased`, origin `firesim`) — an AXI4 DRAM timing model. Its C++
  driver is built into `firesim-lib`, so its registry entry is thinner than a
  fully fslab-owned bridge.

The UART bridge's Scala sources are *the* FireSim bridge-walkthrough example,
carried verbatim (the source comments say so). Reading them alongside
{doc}`adding-new-bridges` is the fastest way to internalise the pattern.

## Where to go next

```{toctree}
:maxdepth: 2

concepts
adding-new-bridges
registry-yaml
blockdevice-integration
```

- {doc}`concepts` — the internal model: the target/host split, Golden Gate and
  FIRRTL annotations, token (`toHost`/`fromHost`) semantics, the MMIO control
  register file, and how firesim-lab wraps your blackbox so a bridge can attach.
- {doc}`adding-new-bridges` — the end-to-end recipe: every file you write, in
  order, to add a working bridge, with code derived from the UART bridge.
- {doc}`registry-yaml` — the bridge-relevant fields of `lib/registry.yaml` and
  the validation rules `fslab` enforces on them.
- {doc}`blockdevice-integration` — a worked integration guide for the
  BlockDevice bridge: the controller RTL you add to your DUT and the device
  driver you add to the target OS, with a SystemVerilog controller skeleton.
