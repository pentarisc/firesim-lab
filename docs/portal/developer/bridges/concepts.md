# Bridge Concepts

This page builds the mental model you need before writing a bridge. It explains
what a bridge *is* at the framework level, why it is split across the
target/host boundary, how Golden Gate finds it, how the two halves exchange
data through *tokens* and *MMIO*, and how firesim-lab wraps a plain
Verilog/SystemVerilog blackbox so a bridge can attach to it. The concrete
file-by-file recipe lives in {doc}`adding-new-bridges`; the registry schema in
{doc}`registry-yaml`.

## The target/host split

FireSim simulations are **decoupled**: the *target* (the hardware you are
simulating) does not run in lock-step with wall-clock time. Golden Gate (MIDAS)
applies a FAME-1 transform to your design so that every cycle of target time is
represented by a *token* — a bundle of all the values crossing a boundary in
that cycle. The target can stall for many host cycles waiting for a token and
the simulation stays cycle-accurate.

A bridge is exactly a place where the target reaches *across* that boundary to
something that does not exist in hardware — a terminal, a disk, a DRAM timing
model. Because of this, a bridge always has **two halves**:

- A **target-side** half that looks like ordinary RTL to your design. It is a
  Chisel `BlackBox`: a module with real ports but no hardware behind them.
  Golden Gate recognises it and *removes* it from the synthesised target.
- A **host-side** half that supplies the behaviour. Part of it is the
  **`BridgeModule`** — Chisel that *is* synthesised onto the FPGA and processes
  the tokens. The other part is the **C++ bridge driver** that runs on the CPU
  and performs the actual I/O.

```{note}
The names matter. "Target-side" code defines the *interface*; "host-side"
`BridgeModule` code defines the *FPGA model*; the "bridge driver" is the
*CPU software*. All three are part of one bridge.
```

## The data-flow picture

Here is the full path a byte travels for the UART bridge, from your RTL pin out
to the host terminal and back:

```text
   TARGET  (your RTL, FAME-1 transformed by Golden Gate)       HOST (CPU + FPGA)
 ┌──────────────────────────────────────────┐
 │  user DUT  (Verilog / SystemVerilog)      │
 │     │ txd (out)        rxd (in) ▲         │
 │     ▼                           │         │
 │  ┌──────────────────────────────┴──────┐ │   token stream    ┌────────────────────────┐
 │  │ UARTBridge                           │ │ == toHost  =====▶ │ UARTBridgeModule        │
 │  │  BlackBox + Bridge[HostPortIO[...]]  │ │ ◀== fromHost ==== │  (BridgeModule on FPGA) │
 │  │  (no hardware — a boundary marker)   │ │                   │  TX/RX FIFOs            │
 │  └──────────────────────────────────────┘ │                   │  control register file │
 └──────────────────────────────────────────┘                   └───────────┬────────────┘
                                                                             │ MMIO (AXI4-lite)
                                                                             │ read() / write()
                                                                 ┌───────────┴────────────┐
                                                                 │ uart_t  (C++ driver)    │
                                                                 │  bridge_driver_t::tick()│
                                                                 └───────────┬────────────┘
                                                                             │
                                                                  stdin / stdout / PTY / file
```

```{note}
This figure is ASCII for now. A source diagram (`docs/firesim-lab.drawio`)
exported to `docs/portal/_static/images/` should replace it later. **TODO:
author the bridge data-flow SVG.**
```

## Golden Gate and the bridge annotations

How does Golden Gate know that the `UARTBridge` blackbox is a *bridge* and not
just some unimplemented Verilog? Through **FIRRTL annotations**. The target-side
stub mixes in the `Bridge` trait and calls `generateAnnotations()`:

```scala
class UARTBridge(initBaudRate: BigInt, freqMHz: Int)(implicit p: Parameters)
    extends BlackBox
    with Bridge[HostPortIO[UARTBridgeTargetIO]] {
  // Name of the host-side BridgeModule that replaces this blackbox.
  val moduleName = "firechip.goldengateimplementations.UARTBridgeModule"
  val io       = IO(new UARTBridgeTargetIO)
  val bridgeIO = HostPort(io)

  // Metadata passed to the host-side model (see "constructor argument" below).
  val div = (BigInt(freqMHz) * 1000000 / initBaudRate).toInt
  val constructorArg = Some(UARTKey(div))

  // Critical: without this the blackbox is just an empty blackbox to Golden Gate.
  generateAnnotations()
}
```

`generateAnnotations()` emits the annotations that tell Golden Gate: *this
blackbox is a bridge; replace it with the `BridgeModule` named in `moduleName`,
and hand that module the value in `constructorArg`.* Three pieces are essential:

- **`moduleName`** — the fully-qualified Scala class of the host-side
  `BridgeModule`.
- **`bridgeIO = HostPort(io)`** — declares that the blackbox's `io` should be
  split into a bidirectional token stream (inputs become one token direction,
  outputs the other).
- **`constructorArg`** — a single case class (e.g. `UARTKey(div)`) carrying any
  static metadata the host model needs.

## The target interface contract

The target-side *interface* is just a Chisel `Bundle`. For UART it is two
bundles:

```scala
class UARTPortIO extends Bundle {
  val txd = Output(Bool())
  val rxd = Input(Bool())
}

class UARTBridgeTargetIO extends Bundle {
  val clock = Input(Clock())
  val uart  = Flipped(new UARTPortIO)
  val reset = Input(Bool())   // optional; resets bridge-modelled target state
}
```

Two rules govern these files:

1. The bundle must be **isolated from target generators** — it is also injected
   into the MIDAS compiler, so it may not pull in target-only classes. (The
   source files carry this warning explicitly.)
2. The **constructor argument** is a single case class, even when it wraps one
   primitive: `case class UARTKey(div: Int)`. This is the only channel for
   compile-time metadata from the target into the host model.

The port names in this bundle (`txd`, `rxd`, …) are the contract the **end
user** maps their Verilog blackbox pins onto via `port_map` in `fslab.yaml`.
This is the link between the no-Chisel user and your Scala bridge.

## Tokens: `toHost` and `fromHost`

The host-side `BridgeModule` does the cycle-by-cycle work on the FPGA. It
receives the target's outputs as an input token (`toHost`) and supplies the
target's inputs as an output token (`fromHost`):

```scala
class UARTBridgeModule(key: UARTKey)(implicit p: Parameters)
    extends BridgeModule[HostPortIO[UARTBridgeTargetIO]]()(p) {
  lazy val module = new BridgeModuleImp(this) {
    val io    = IO(new WidgetIO())                  // AXI4-lite control + AXI4 DMA
    val hPort = IO(HostPort(new UARTBridgeTargetIO))

    // `fire` captures all the conditions under which we can consume an input
    // token and produce an output token in the same host cycle.
    val fire = hPort.toHost.hValid &&   // a valid input token is available
               hPort.fromHost.hReady && // there is room to enqueue an output token
               txfifo.io.enq.ready      // ... and room for new TX data
    hPort.toHost.hReady   := fire
    hPort.fromHost.hValid := fire

    val target = hPort.hBits.uart       // the decoded target payload
    // ... model logic reads target.txd, drives target.rxd ...
  }
}
```

Key ideas:

- **`hPort.hBits`** is the decoded target payload — the same fields as your
  `TargetIO` bundle.
- **`toHost`** carries the target's *outputs* to the model; **`fromHost`**
  carries the model's response back as the target's *inputs*.
- `hValid`/`hReady` are the token handshake. A simple bridge can do all its work
  in a single host cycle gated by one `fire` signal; complex bridges may take
  many host cycles per target cycle.
- The `reset` field of the target bundle simply appears as another bit in the
  input token; the model uses it to reset its own state.

## MMIO: the control register file

The `BridgeModule` exposes a memory-mapped interface to the C++ driver. You do
not hand-roll address decoding — you declare registers and Golden Gate builds
the control register file:

```scala
genROReg(txfifo.io.deq.bits,  "out_bits")   // read-only: head of TX FIFO
genROReg(txfifo.io.deq.valid, "out_valid")
Pulsify(genWORegInit(txfifo.io.deq.ready, "out_ready", false.B), pulseLength = 1)
genWOReg(rxfifo.io.enq.bits,  "in_bits")     // write-only: push RX byte
Pulsify(genWORegInit(rxfifo.io.enq.valid, "in_valid", false.B), pulseLength = 1)
genROReg(rxfifo.io.enq.ready, "in_ready")
genCRFile()                                  // REQUIRED: wires everything to AXI4-lite
```

- **`genROReg`** — a read-only register the driver can `read()`.
- **`genWOReg` / `genWORegInit`** — a write-only register the driver can
  `write()`.
- **`Pulsify`** — drives a written register back to its default after N cycles,
  so a single write produces a single pulse (e.g. one FIFO dequeue).
- **`genCRFile()`** — must be called last; it materialises the AXI4-lite control
  register file from all the `gen*Reg` calls.

Finally the module emits the C++ header that the driver includes, via
`genHeader`:

```scala
override def genHeader(base: BigInt, memoryRegions: Map[String, BigInt], sb: StringBuilder): Unit = {
  genConstructor(base, sb, "uart_t", "uart")
}
```

`genConstructor` generates the `UARTBRIDGEMODULE_struct` (a struct of the MMIO
addresses for every register you declared) and the code that constructs the
matching C++ driver. The struct field names match the register names you passed
to `gen*Reg`.

## The C++ bridge driver

The host-side software is a subclass of **`bridge_driver_t`**. It holds the MMIO
address struct, and its `tick()` method does the bridge's real work each time
the simulation gives it a turn:

```cpp
class uart_t final : public bridge_driver_t {
public:
  static char KIND;                     // type tag used for safe casts
  uart_t(simif_t &simif,
         const UARTBRIDGEMODULE_struct &mmio_addrs,
         int uartno,
         const std::vector<std::string> &args);
  void tick() override;                 // advance the bridge one or more cycles
private:
  const UARTBRIDGEMODULE_struct mmio_addrs;
  void send();                          // write()s to MMIO registers
  void recv();                          // read()s from MMIO registers
};
```

Inside `tick()`, the driver `read()`s the read-only registers, decides what to
do (e.g. pull a byte from stdin, push a received byte to the terminal), and
`write()`s the write-only registers — all through the MMIO addresses captured in
`mmio_addrs`. `bridge_driver_t` also offers `init()` for one-time setup and
`terminate()`/`exit_code()` so a bridge can end the simulation.

The `static char KIND` is how the framework finds your bridges at runtime: the
generated driver calls `registry.get_bridges<uart_t>()` to collect every
instance, and `KIND` disambiguates the cast.

## How firesim-lab attaches a bridge to a plain blackbox

In stock FireSim you would instantiate the bridge by hand in Chisel. firesim-lab
does this *for* the user, from `fslab.yaml`, by generating a Chisel shim:

- **`DUT.scala`** — a Chisel `BlackBox` wrapper around the user's Verilog top
  module. Its `IO` bundle gets one block of port declarations per bridge
  instance, rendered from the bridge's `ports` template using the user's
  `port_map` and `params`.
- **`Top.scala`** — a `RawModule` that instantiates the `DUT` blackbox, sets up
  the clock/reset (`RationalClockBridge`, `ResetPulseBridge`), and wires each
  bridge to the DUT's ports, rendered from the bridge's `wiring` template. It
  also instantiates the `PeekPokeBridge`.
- **`driver.cc`** — the C++ entry point. It subclasses `firesim_lab_top_t`,
  collects every bridge via `registry.get_bridges<...>()`, and runs the
  simulation loop: step the clock, then `tick()` every registered bridge.

The generated simulation loop looks like this (abridged from `driver.cc.j2`):

```cpp
int simulation_run() override {
  while (!terminated && !finished_scheduled_tasks()) {
    peek_poke.step(get_largest_stepsize(), false);
    while (!peek_poke.is_done() && !terminated) {
      for (auto *bridge : registry.get_all_bridges()) {
        bridge->tick();
        if (bridge->terminate()) { terminated = true; break; }
      }
      // per-bridge-type snippet from the registry `cpp_template` is spliced here
    }
  }
}
```

The takeaway for a bridge author: **your bridge driver's `tick()` is called
automatically** once it is registered. The per-bridge `cpp_template`
(`sim_loop.cc.j2`) is an *optional* extra splice point inside the loop; for many
bridges it is empty because `tick()` already does everything.

## Putting it together

A bridge is therefore a contract enforced at three layers:

1. **Annotations** tie the target blackbox to a named `BridgeModule`.
2. **`genHeader`/`genConstructor`** tie that `BridgeModule`'s MMIO layout to a
   named C++ driver type.
3. **The registry entry** ties all of the above — plus the Jinja2 wiring
   templates — to an `fslab.yaml`-selectable `id`.

With the model in hand, continue to {doc}`adding-new-bridges` for the concrete,
file-by-file procedure, and {doc}`registry-yaml` for the field reference.
