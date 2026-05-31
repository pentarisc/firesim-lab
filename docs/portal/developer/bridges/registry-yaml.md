# Bridge Registry Fields

`lib/registry.yaml` is the central catalog `fslab` reads to discover available
bridges, validate `fslab.yaml`, and drive code generation. This page documents
the **bridge** section only — the fields of each entry under `bridges:` and the
rules `fslab` enforces on them. The other registry sections (`platforms`,
`bitbuilders`, `runners`, `metasimulators`, `fpgasimulators`, `features`) are
out of scope here and are covered by the full registry reference.

A bridge entry is parsed and validated by the `BridgeEntry` Pydantic model in
`fslab-cli/fslab/schemas/registry.py`. The validation rule codes below (e.g.
`[REG-01]`) are the ones that model emits, so an `fslab` error message points
straight back to this page.

## The UART entry, annotated

The smallest complete example is the UART bridge:

```yaml
bridges:
  - id: uart
    label: UART Bridge
    description: >
      Connects the target's UART TX/RX pins to the host via a token channel.
      Host driver writes received bytes to a file or stdout.
    origin: fslab
    input_ports:
      - "rxd"
    output_ports:
      - "txd"
    cpp_type: "uart_t"
    cpp_headers: ["bridges/uart.h"]
    cpp_sources: ["bridges/uart.cc"]
    runtime_plusargs:
    required_params: [freq_mhz, baud_rate]
    cpp_template: "bridges/uart/sim_loop.cc.j2"
    scala_templates:
      top_imports: "bridges/uart/top_imports.scala.j2"
      ports:       "bridges/uart/ports.scala.j2"
      wiring:      "bridges/uart/wiring.scala.j2"
```

## Field reference

### `id` *(required, string)*

The bridge's unique identifier — the value a user puts in `type:` in
`fslab.yaml`. Must match the ID character set `[REG-01]` (alphanumerics,
underscores, hyphens). Must be unique among bridges within a file `[REG-06]`;
across multiple registry files, a later definition with the same `id` overrides
an earlier one `[REG-07]`.

### `label` *(required, string)*

Human-readable name shown in CLI output and docs.

### `description` *(required, string)*

One-paragraph summary. YAML block scalars (`>`/`|`) are fine.

### `origin` *(required, enum)*

Who owns the bridge. One of:

- `fslab` — sources live in this repository (`lib/bridges`). The normal value
  for a bridge you author.
- `firesim` — the bridge's C++ driver is built into `firesim-lib` upstream, so
  the entry is thinner (FASED is the example). You still provide the registry
  entry and wiring templates.
- `custom` — a bridge contributed via an external/team registry overlay.

Any other value fails validation.

### `input_ports` / `output_ports` *(required, list of string)*

The user-facing **port keys** for the bridge. These are the keys a user supplies
under `port_map` in `fslab.yaml`, mapping each one to a pin on their Verilog top
module. Direction is from the *target's* point of view:

- `output_ports` — signals the target **drives out** to the bridge (e.g. UART
  `txd`).
- `input_ports` — signals the bridge **drives into** the target (e.g. UART
  `rxd`).

Every entry must be a valid Verilog identifier and unique across both lists
`[REG-08]`. For a wide/structured interface (AXI4, the BlockDevice channels),
list every individual port — see the `fased` and `iceblk` entries for the full
shape. The names here must line up with what your `ports` and `wiring` templates
reference as `instance.port_map.<key>`.

### `cpp_type` *(required, string)*

The C++ driver class name (e.g. `uart_t`). This single value ties three things
together and **must be identical** in all three:

1. the class you declare in `lib/bridges/src/main/cc/bridges/<id>.h`,
2. the first string argument to `genConstructor` in the host-side
   `BridgeModule`'s `genHeader`, and
3. this field.

Validated against the ID character set.

### `cpp_headers` / `cpp_sources` *(required, list of string)*

Header and source file paths for the C++ driver, **relative to
`lib/bridges/src/main/cc/`** (so `bridges/uart.h`, not an absolute path). The
generated `CMakeLists.txt` adds `cpp_sources` to the driver build, which is why
adding an `fslab`-origin bridge needs no manual CMake editing. `firesim`-origin
bridges still list the upstream header path so the generated driver can include
it.

### `cpp_template` *(required, string)*

Path (relative to `fslab-cli/fslab/templates/`) to the per-bridge **C++ loop
snippet** template, spliced once per bridge *type* into the generated driver's
simulation loop. For most bridges the referenced file is **empty** — the
driver's `tick()` is already invoked automatically for every registered bridge,
so no inline snippet is needed. Provide non-empty content only for type-specific
logic that must run inline in the loop.

```{note}
The field is required by the schema even when the template is empty, so the
file must exist. The shipping `uart`/`iceblk` `sim_loop.cc.j2` files are present
but blank. (The `fased` entry points its `cpp_template` at the UART snippet
because both are empty — the path is a placeholder, not a dependency.)
```

### `scala_templates` *(required, object)*

Paths (relative to `fslab-cli/fslab/templates/`) to the Jinja2 snippets that
generate the Chisel shim. Fields:

| Key | Required? | Spliced into | Purpose |
|---|---|---|---|
| `ports` | yes | `DUT.scala` IO bundle | Declares the blackbox ports for this bridge, named via `port_map` |
| `wiring` | yes | `Top.scala` (`withClockAndReset`) | Wires the DUT ports to the bridge and instantiates it |
| `top_imports` | no | `Top.scala` imports | Import statements for the bridge classes |
| `dut_imports` | no | `DUT.scala` imports | Import statements for the DUT; usually unset |

`ports` and `wiring` are mandatory `[REG-03]`; `top_imports` and `dut_imports`
are optional and skipped when omitted. Inside these templates the loop variable
`instance` exposes `instance.name`, `instance.port_map.<key>`, and
`instance.params.<name>.value`.

### `required_params` *(optional, list of string)*

Names of parameters the user **must** supply under `params:` in `fslab.yaml`
when instantiating this bridge (e.g. UART needs `freq_mhz` and `baud_rate`;
`iceblk` needs `tag_bits` and `n_trackers`; FASED needs the AXI geometry
`addr_bits`, `data_bits`, `id_bits`, etc.). The wiring/ports templates read
these as `instance.params.<name>.value`. Omit the field (or leave it empty) for
a bridge with no required parameters.

### `runtime_plusargs` *(optional, list)*

Documentation for the `+plusargs` the bridge's C++ driver understands at run
time. Each entry has `flag` and `description` (and an optional
`required_params`). This is informational — it surfaces the bridge's runtime
knobs to users and tooling; it does not by itself wire anything. FASED's entry
shows the shape (`+mm-unified-latency=<cycles>`, `+dramsim`, …). Leave the field
blank for a bridge with no runtime flags.

## How the fields flow through `fslab`

Tracing one bridge instance from YAML to generated code makes the field roles
concrete:

1. **`fslab init`** reads `registry.yaml`, so the user can only select an `id`
   that exists, and `fslab` knows that bridge's `required_params` and port keys.
2. The user writes a `bridges:` entry in `fslab.yaml` with `type` (= `id`),
   `name`, a `port_map` keyed by `input_ports`/`output_ports`, and `params`
   covering `required_params`.
3. **`fslab generate`** resolves the entry against the registry and renders:
   - the `scala_templates.ports` snippet into `DUT.scala`,
   - the `scala_templates.wiring` (+ `top_imports`) into `Top.scala`,
   - the `cpp_template` snippet into `driver.cc`, which also `#include`s every
     `cpp_headers` path.
4. **`fslab build`** compiles `cpp_sources` into the driver (via the generated
   `CMakeLists.txt`) and runs Chisel/FIRRTL + Golden Gate, where the bridge's
   annotations connect the blackbox to its `BridgeModule` (whose C++ type is
   `cpp_type`).

## Validation cheat-sheet

| Code | Rule |
|---|---|
| `[REG-01]` | `id` and `cpp_type` must match the ID character set |
| `[REG-02]` | all required `BridgeEntry` fields must be present |
| `[REG-03]` | `runtime_plusargs`, `top_imports`, `dut_imports` are optional; `ports`/`wiring` are required |
| `[REG-06]` | `id` must be unique among bridges within one registry file |
| `[REG-07]` | across files, last definition of an `id` wins (overlay/override) |
| `[REG-08]` | every port name must be a valid Verilog identifier and unique across `input_ports`+`output_ports` |
| (enum) | `origin` must be one of `firesim`, `fslab`, `custom` |

For the procedure that produces the files these fields point at, see
{doc}`adding-new-bridges`. For the conceptual model behind them, see
{doc}`concepts`. For the spec sheets of the shipping bridges, see
{doc}`/developer/bridge-reference/index`.
