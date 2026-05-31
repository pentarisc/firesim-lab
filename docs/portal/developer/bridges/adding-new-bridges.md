# Adding a New Bridge

This is the end-to-end recipe for adding a bridge to firesim-lab. It assumes you
have read {doc}`concepts` and understand the target/host split, tokens, and
MMIO. Each step names the language you work in and the file you create. The
running example is a hypothetical `gpio` bridge that exposes an N-bit output
port and an N-bit input port to the host driver — small enough to show the whole
pattern, structured like the real UART bridge.

```{tip}
The UART bridge is the canonical reference implementation. Keep these files
open while you work:
`lib/bridges/src/main/scala/firechip/bridgeinterfaces/UART.scala`,
`.../bridgestubs/uart/UARTBridge.scala`,
`.../goldengateimplementations/UARTBridge.scala`,
`lib/bridges/src/main/cc/bridges/uart.{h,cc}`, and the
`fslab-cli/fslab/templates/bridges/uart/` templates.
```

## Languages you will touch

| Step | Language | Why |
|---|---|---|
| 1. Target interface | Chisel/Scala | Define the RTL port bundle + constructor-arg case class |
| 2. Target stub | Chisel/Scala | The `BlackBox` Golden Gate replaces; emits annotations |
| 3. Host model | Chisel/Scala | The FPGA-hosted `BridgeModule`: tokens → MMIO registers |
| 4. C++ driver | C++ | The CPU-side `bridge_driver_t` that does real I/O |
| 5. Wiring templates | Jinja2 | Generated glue from user blackbox ports to the bridge |
| 6. Registry entry | YAML | Declare the bridge to `fslab` |
| 7. Use it | YAML | Reference the bridge from a project's `fslab.yaml` |

A working knowledge of the **Verilog/SystemVerilog** interface you are exposing
is assumed throughout — the port names you choose in steps 1 and 6 are what end
users map their blackbox pins onto. You only need **Python/Pydantic** if you
must extend the registry *schema* itself (see the note at the end).

## Step 1 — Target-side interface (Scala)

Create the port bundle and the constructor-argument case class under
`lib/bridges/src/main/scala/firechip/bridgeinterfaces/`. For `gpio`:

```scala
// Gpio.scala
package firechip.bridgeinterfaces

import chisel3._

class GpioPortIO(val width: Int) extends Bundle {
  val out = Output(UInt(width.W))
  val in  = Input(UInt(width.W))
}

class GpioBridgeTargetIO(val width: Int) extends Bundle {
  val clock = Input(Clock())
  val gpio  = Flipped(new GpioPortIO(width))
  val reset = Input(Bool())
}

// The single case class carrying static metadata to the host model.
case class GpioKey(width: Int)
```

```{warning}
Files in `bridgeinterfaces/` are also injected into the MIDAS compiler. Do
**not** import target-only generators/classes here, or compilation of the host
model will break. Keep these bundles self-contained.
```

The field names in `GpioPortIO` (`out`, `in`) become part of the public
contract. They are not the user-facing port names directly — those are declared
in the registry (step 6) — but they are what your wiring template connects.

## Step 2 — Target-side stub (Scala)

Create the `BlackBox` under
`lib/bridges/src/main/scala/firechip/bridgestubs/gpio/`. This is the module
Golden Gate finds and replaces.

```scala
// GpioBridge.scala
package firechip.bridgestubs.gpio

import chisel3._
import org.chipsalliance.cde.config.Parameters
import firesim.lib.bridgeutils._
import firechip.bridgeinterfaces._

class GpioBridge(width: Int)(implicit p: Parameters) extends BlackBox
    with Bridge[HostPortIO[GpioBridgeTargetIO]] {
  val moduleName     = "firechip.goldengateimplementations.GpioBridgeModule"
  val io             = IO(new GpioBridgeTargetIO(width))
  val bridgeIO       = HostPort(io)
  val constructorArg = Some(GpioKey(width))
  generateAnnotations()        // critical — marks this blackbox as a bridge
}

object GpioBridge {
  def apply(clock: Clock, gpio: GpioPortIO, reset: Bool, width: Int)
           (implicit p: Parameters): GpioBridge = {
    val ep = Module(new GpioBridge(width))
    ep.io.gpio.out := gpio.out
    gpio.in        := ep.io.gpio.in
    ep.io.clock    := clock
    ep.io.reset    := reset
    ep
  }
}
```

The companion `object`'s `apply` is what the generated `Top.scala` calls; it
instantiates the blackbox and connects clock/reset and the data pins. Mirror the
UART stub's structure exactly — `moduleName`, `io`, `bridgeIO = HostPort(io)`,
`constructorArg`, then `generateAnnotations()`.

## Step 3 — Host-side model (Scala)

Create the `BridgeModule` under
`lib/bridges/src/main/scala/firechip/goldengateimplementations/`. Its class name
must match `moduleName` from step 2.

```scala
// GpioBridge.scala
package firechip.goldengateimplementations

import chisel3._
import chisel3.util._
import org.chipsalliance.cde.config.Parameters
import midas.widgets._
import firesim.lib.bridgeutils._
import firechip.bridgeinterfaces._

class GpioBridgeModule(key: GpioKey)(implicit p: Parameters)
    extends BridgeModule[HostPortIO[GpioBridgeTargetIO]]()(p) {
  lazy val module = new BridgeModuleImp(this) {
    val io    = IO(new WidgetIO())
    val hPort = IO(HostPort(new GpioBridgeTargetIO(key.width)))

    // Latch the target's output value; hold the value driven back into the target.
    val outReg = RegInit(0.U(key.width.W))
    val inReg  = RegInit(0.U(key.width.W))

    val target = hPort.hBits.gpio
    val fire   = hPort.toHost.hValid && hPort.fromHost.hReady
    hPort.toHost.hReady   := fire
    hPort.fromHost.hValid := fire

    when(fire) { outReg := target.out }
    target.in := inReg

    // MMIO registers visible to the C++ driver.
    genROReg(outReg, "out_value")      // driver read()s the target's output
    genWOReg(inReg,  "in_value")       // driver write()s the target's input

    genCRFile()                        // REQUIRED, last

    override def genHeader(base: BigInt,
                           memoryRegions: Map[String, BigInt],
                           sb: StringBuilder): Unit = {
      genConstructor(base, sb, "gpio_t", "gpio")
    }
  }
}
```

Points to get right:

- The class **extends `BridgeModule[HostPortIO[...]]`** parameterised on the
  *same* target IO type as the stub. A mismatch is not caught by the type system
  until Golden Gate elaborates — so copy it carefully.
- Declare every value the driver needs as a `gen*Reg`. Use `genROReg` for
  target→driver, `genWOReg`/`genWORegInit` for driver→target, and wrap pulse
  signals in `Pulsify`.
- Call **`genCRFile()` last**.
- In **`genHeader`**, the two string arguments to `genConstructor` are the C++
  type name (`"gpio_t"`) and a short instance label (`"gpio"`). The C++ type
  name must match `cpp_type` in the registry (step 6) and the class you write in
  step 4.

## Step 4 — Host-side C++ driver

Create `gpio.h` and `gpio.cc` under `lib/bridges/src/main/cc/bridges/`. The
header declares the MMIO struct and the driver class; the source implements
`tick()`.

```cpp
// gpio.h
#ifndef __GPIO_H
#define __GPIO_H
#include "core/bridge_driver.h"
#include <cstdint>
#include <vector>
#include <string>

struct GPIOBRIDGEMODULE_struct {
  uint64_t out_value;   // names MUST match the gen*Reg names from step 3
  uint64_t in_value;
};

class gpio_t final : public bridge_driver_t {
public:
  static char KIND;
  gpio_t(simif_t &simif,
         const GPIOBRIDGEMODULE_struct &mmio_addrs,
         int gpiono,
         const std::vector<std::string> &args);
  ~gpio_t() override;
  void tick() override;
private:
  const GPIOBRIDGEMODULE_struct mmio_addrs;
};
#endif // __GPIO_H
```

```cpp
// gpio.cc
#include "gpio.h"
#include "core/simif.h"

char gpio_t::KIND;

gpio_t::gpio_t(simif_t &simif,
               const GPIOBRIDGEMODULE_struct &mmio_addrs,
               int gpiono,
               const std::vector<std::string> &args)
    : bridge_driver_t(simif, &KIND), mmio_addrs(mmio_addrs) {}

gpio_t::~gpio_t() = default;

void gpio_t::tick() {
  uint32_t value = read(mmio_addrs.out_value);   // target output → host
  // ... do something with `value` (log it, drive a model, etc.) ...
  write(mmio_addrs.in_value, /* some host-computed input */ 0);
}
```

Notes:

- The `*_struct` field names must exactly match the register names from
  `gen*Reg` — `genConstructor` generates the struct definition and fills these
  addresses.
- `static char KIND;` must be defined once in the `.cc` (`char gpio_t::KIND;`)
  and passed to `bridge_driver_t(simif, &KIND)`. It is the tag
  `registry.get_bridges<gpio_t>()` uses.
- Use `read()`/`write()` for MMIO. For high-bandwidth bridges, `bridge_driver_t`
  also offers `pull()`/`push()` for CPU-mastered DMA (see the BlockDevice
  driver).
- Implement `init()` if you need one-time setup; call `terminate()` and set an
  exit code if your bridge should end the simulation.

## Step 5 — Wiring templates (Jinja2)

Create the per-bridge template directory
`fslab-cli/fslab/templates/bridges/gpio/`. These templates are rendered by
`fslab generate` into the project's Chisel shim and driver. Each receives an
`instance` object with `instance.name`, `instance.port_map` (bridge port →
blackbox port), and `instance.params.<name>.value`.

**`top_imports.scala.j2`** — imports spliced into `Top.scala`:

```scala
import firechip.bridgestubs.gpio.GpioBridge
import firechip.bridgeinterfaces.GpioPortIO
```

**`ports.scala.j2`** — port declarations spliced into the generated DUT `IO`
bundle. Use `port_map` so the names match the user's Verilog:

```scala
val {{ instance.port_map.gpio_out }} = Output(UInt({{ instance.params.width.value }}.W))
val {{ instance.port_map.gpio_in }}  = Input(UInt({{ instance.params.width.value }}.W))
```

**`wiring.scala.j2`** — wiring spliced into `Top.scala` (inside
`withClockAndReset`). It builds a `GpioPortIO`, connects it to the DUT's mapped
ports, and instantiates the bridge:

```scala
val {{ instance.name }}_wire = Wire(new GpioPortIO({{ instance.params.width.value }}))
{{ instance.name }}_wire.out      := dut.io.{{ instance.port_map.gpio_out }}
dut.io.{{ instance.port_map.gpio_in }} := {{ instance.name }}_wire.in

GpioBridge(clock, {{ instance.name }}_wire, reset.asBool, {{ instance.params.width.value }})(p)
```

**`sim_loop.cc.j2`** — an optional C++ snippet spliced into the driver's
simulation loop, once per bridge *type*. For most bridges leave it **empty**:
`bridge_driver_t::tick()` is already called automatically for every registered
bridge. Use it only for type-specific logic that must run inline in the loop.

```{note}
The registry also references an optional `dut_imports` template (imports for
`DUT.scala`). The shipping bridges leave it unset — the DUT only needs the
port declarations from `ports.scala.j2`, not imports.
```

## Step 6 — Registry entry (YAML)

Declare the bridge in `lib/registry.yaml` under `bridges:`. This is what makes
the bridge selectable from `fslab.yaml`. Field-by-field semantics are in
{doc}`registry-yaml`; the `gpio` entry looks like:

```yaml
  - id: gpio
    label: GPIO Bridge
    description: >
      Exposes an N-bit output and N-bit input port to the host driver.
    origin: fslab
    input_ports:
      - "gpio_in"
    output_ports:
      - "gpio_out"
    cpp_type: "gpio_t"
    cpp_headers: ["bridges/gpio.h"]
    cpp_sources: ["bridges/gpio.cc"]
    runtime_plusargs:
    required_params: [width]
    cpp_template: "bridges/gpio/sim_loop.cc.j2"
    scala_templates:
      top_imports: "bridges/gpio/top_imports.scala.j2"
      ports:       "bridges/gpio/ports.scala.j2"
      wiring:      "bridges/gpio/wiring.scala.j2"
```

Three consistency rules the framework relies on:

- **`cpp_type`** must equal the C++ class (step 4) and the type string passed to
  `genConstructor` (step 3).
- **`cpp_headers` / `cpp_sources`** are paths relative to
  `lib/bridges/src/main/cc/`. The generated `CMakeLists.txt` adds the listed
  sources to the driver build, so a new fslab-owned bridge does **not** require
  editing CMake by hand.
- **`input_ports` / `output_ports`** are the user-facing port keys; every key
  must be a valid Verilog identifier and unique across the two lists. These are
  the keys the user supplies in `port_map`.

## Step 7 — Use the bridge (YAML)

A project then references the bridge in its `fslab.yaml`, mapping the registry's
port keys to its own blackbox pins:

```yaml
bridges:
  - type: "gpio"
    name: "leds_0"
    port_map:
      gpio_out: "led_bus"      # a port on the user's Verilog top module
      gpio_in:  "switch_bus"
    params:
      width: 8
```

After this, `fslab generate` renders the shim and driver, `fslab build` runs
Chisel/FIRRTL generation, Golden Gate elaboration, and the metasim/driver build,
and the bridge is live.

## What the build does with your files

It is worth knowing why this set of files is sufficient:

- The **Scala** sources live in `lib/bridges` and are exported as the
  `fslabBridges` SBT project. A user project's generated `build.sbt` depends on
  it, so `import firechip.bridgestubs.gpio._` resolves without the user copying
  anything.
- The generated **`DUT.scala`** and **`Top.scala`** splice your `ports` and
  `wiring` templates; Golden Gate sees the `GpioBridge` blackbox, reads its
  annotations, and substitutes `GpioBridgeModule`.
- `genHeader` emits the MMIO struct into the generated headers; the generated
  **`driver.cc`** includes `bridges/gpio.h`, collects instances with
  `registry.get_bridges<gpio_t>()`, and calls `tick()` each loop iteration.
- The generated **`CMakeLists.txt`** compiles `bridges/gpio.cc` (from your
  registry `cpp_sources`) into the driver.

## Origin and the FASED special case

The registry `origin` field records who owns a bridge: `fslab` (sources live in
this repo), `firesim` (the C++ driver is built into `firesim-lib`, so the
registry entry is thinner — FASED is the example), or `custom` (a bridge added
via an external registry overlay). For a brand-new bridge you author here, use
`origin: fslab` and follow steps 1–6 above. A `firesim`-origin bridge skips
steps 1–4 (they already exist upstream) and provides only the registry entry and
wiring templates.

## When you need Python

The steps above require **no Python**. You only edit Python if you are changing
the registry *schema* — for example adding a new field to `BridgeEntry`, a new
validation rule, or a new template hook. Those live in
`fslab-cli/fslab/schemas/registry.py` (the Pydantic models) and the generator
context builders. Adding a bridge that fits the existing schema never touches
them.

## Checklist

- [ ] `bridgeinterfaces/<Name>.scala` — target IO bundle + `*Key` case class
- [ ] `bridgestubs/<id>/<Name>Bridge.scala` — `BlackBox` + `Bridge` trait + companion `apply`
- [ ] `goldengateimplementations/<Name>Bridge.scala` — `BridgeModule` + `gen*Reg` + `genCRFile` + `genHeader`
- [ ] `cc/bridges/<id>.{h,cc}` — MMIO struct + `bridge_driver_t` with `KIND` and `tick()`
- [ ] `templates/bridges/<id>/{top_imports,ports,wiring}.scala.j2` (+ optional `sim_loop.cc.j2`)
- [ ] `registry.yaml` bridge entry with matching `cpp_type`, ports, and template paths
- [ ] A test project `fslab.yaml` that selects the bridge and maps its ports

For the exact meaning and validation of each registry field, continue to
{doc}`registry-yaml`. For the spec sheets of the bridges that ship today, see
{doc}`/developer/bridge-reference/index`.
