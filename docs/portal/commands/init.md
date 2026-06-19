# fslab init

Initialise a project: parse your top module and write a starting `fslab.yaml`. Run it once, from inside a workspace created by {doc}`new`.

This page also documents the `fslab.yaml` blocks you edit for metasimulation — `project`, `design`, `host`, the basic `target` fields, `bridges`, and `advanced`. The FPGA-only blocks are documented with the commands that consume them: `target.build` in {doc}`build`, `target.run` in {doc}`sim-fpga`.

## Synopsis

```bash
fslab init [-t <module>] [-f <file>] [-p <platform>]
```

## Options

| Option | Default | Description |
|---|---|---|
| `-t`, `--top-module <name>` | — | Name of your top-level Verilog/SystemVerilog module. Must be a valid HDL identifier. |
| `-f`, `--top-module-file <path>` | — | Path to the file containing that module. A bare filename is resolved under `user_rtl/`. Required when `--top-module` is given. |
| `-p`, `--platform <id>` | `f2` | Target platform written to `target.platform`. |

All three options are optional, but `--top-module` and `--top-module-file` go together: if you pass one you must pass the other.

## What it does

`fslab init` must be run inside a workspace (it looks for `.fslab/meta.json`) and refuses to overwrite an existing `fslab.yaml`. It then:

1. Reads the project name from `.fslab/meta.json`.
2. If `--top-module` was given, parses the named module out of the source file — extracting its **parameters** and **ports** — and resolves the source path (a bare name is looked up under `user_rtl/`).
3. Renders `fslab.yaml`. When a module was parsed, the `design:` block is populated with the real ports and parameters; otherwise the file is written with a fully commented-out `design:` example for you to fill in by hand.

The `--platform` value is written verbatim into `target.platform`. `fslab init` only soft-warns if the value is not one it recognises; the authoritative check happens at {doc}`generate` time against the registry (see {doc}`/developer/bridges/registry-yaml`). The default `f2` is correct for both metasim and AWS F2.

:::{note}
`fslab init` does **not** generate any framework code — it only writes `fslab.yaml`. Code generation happens in {doc}`generate`. After `init` you almost always edit `fslab.yaml` (at minimum to mark the clock and reset, and to add `host` and `bridges`) before generating.
:::

## Example

```bash
fslab init -t AXIUARTPrinter -f AXIUARTPrinter.v
```

This parses `user_rtl/AXIUARTPrinter.v`, pulls in its parameters (`ADDR_W`, `DATA_W`, …) and every port, and writes a `design:` block with each port as `in logic` / `out logic[...]`. Your next edits are covered in {doc}`/quickstart/metasim`.

---

## The `fslab.yaml` reference

`fslab.yaml` is the single source of truth for your project. The blocks below are the ones relevant to project setup and metasimulation. Validation rules carry codes like `[PROJ-05]`; those codes appear verbatim in error messages, so they are noted here for cross-reference.

### `project:` — required

Top-level metadata.

```yaml
project:
  name:         "uart-print-test"
  package_name: "my.org"
  config_class: "NoConfig"
  project_dir:  "/target/uart-print-test"
```

`name`
: Project name. Must match `^[a-zA-Z0-9_-]+$` `[PROJ-01]`. The framework derives the Chisel top-module name from it (`uart-print-test` → `UartPrintTestTop`). Populated automatically from the workspace.

`package_name`
: Scala package for the generated shim. A dotted identifier (e.g. `my.org`) `[PROJ-02]`.

`config_class`
: Name of the generated target config class `[PROJ-02]`. The default placeholder is fine for most projects.

`project_dir`
: Absolute in-container path to the project (e.g. `/target/uart-print-test`). Populated automatically; leave it as written.

### `design:` — required

Describes your RTL. For no-Chisel projects, `type` is `blackbox`.

```yaml
design:
  type: "blackbox"
  top_module: "AXIUARTPrinter"
  parameters:
    ADDR_W: 32
    DATA_W: 64
  sources:
    - "user_rtl/AXIUARTPrinter.v"
  blackbox_ports:
    clk: "in clock"
    rst: "in reset"
    uart_txd: "out logic"
    uart_rxd: "in logic"
    m_axi_araddr: "out logic[31:0]"
```

`type`
: `blackbox` or `chisel` `[PROJ-03]`. End-user projects are `blackbox`.

`top_module`
: The Verilog/SystemVerilog top module name; must be a valid HDL identifier `[PROJ-15]`.

`parameters`
: Map of HDL parameter name → value. Parsed from the module by `fslab init`. Used to size ports and to feed bridge parameters via `{ ref: NAME }` (see [`bridges:`](#bridges-optional) below).

`sources`
: List of RTL source paths. Required and non-empty for `blackbox` designs `[PROJ-14]`. Relative paths resolve against `project_dir`; each file must exist or parsing fails.

`blackbox_ports`
: Map of port name → `"<direction> <width>"`, required for `blackbox` designs with at least one entry `[PROJ-07]`. Direction is `in` or `out`. The width token is one of:

  - a **designation**: `clock`, `reset`, or `enable`;
  - a literal width: `logic`, `reg`, or a vector like `logic[31:0]` / `reg[7:0]`;
  - a parameterised range whose bounds are integer literals or names that exist in `design.parameters` `[PROJ-09]`.

:::{warning}
`fslab init` parses the clock and reset as ordinary `in logic`. You **must** edit them to the designations `in clock` and `in reset` — a blackbox design must contain exactly those two designated ports or parsing fails. Golden Gate needs them to decouple target time from host time. The optional `in enable` designation marks a clock-enable port. See {doc}`/concepts/target-rtl-requirements`.
:::

### `host:` — required

Selects the metasim backend and configures the generated C++ driver.

```yaml
host:
  emulator: "verilator"
  driver_name: "UartPrintDriver"
  cxx_standard: 20
  cxx_flags: "-O3 -Wall"
  sources:
    - "src/main/cc/UartPrintDriver.cc"
  includes:
    - "src/main/cc/include"
  libs:
    - "pthread"
```

`emulator`
: Metasim backend: `verilator`, `vcs`, or `xcelium` `[PROJ-04]`. Verilator ships in the container; VCS and Xcelium need their own licensed toolchains and the `VCS_HOME` / `XCELIUM_HOME` environment variables.

`driver_name`
: Base name of the generated driver. `fslab generate` writes `<driver_name>.cc` at the path you list under `sources` — you do not author it. The built binary name is derived from this (see {doc}`sim`).

`cxx_standard`
: C++ standard (e.g. `17`, `20`, `23`). Default `17`.

`cxx_flags`
: Extra compiler flags as a single string. Default empty.

`sources`, `includes`, `libs`
: Additional C++ sources, include directories, and linker libraries for the driver build. All default to empty lists.

### `target:` — required (basic fields)

The basic target fields below are written by `fslab init`. The full `target.build` and `target.run` sub-blocks are documented in {doc}`build` and {doc}`sim-fpga`; metasimulation does not read them.

```yaml
target:
  platform:     "f2"
  clock_period: "1.0"
  fpga_sim:     "xsim"
```

`platform`
: Registry platform id `[PROJ-11]`. `f2` is the only platform with a complete FPGA build/run pipeline today; the others (Alveo, VCU118, …) support driver compilation and metasim only. See {doc}`/developer/bridges/registry-yaml`.

`clock_period`
: Target clock period hint.

`fpga_sim`
: FPGA-level simulator id; must exist in the registry `[PROJ-16]`. `xsim` (Xilinx XSIM) is the shipped value.

(bridges-optional)=
### `bridges:` — optional

Connects your design's ports to host-modelled bridges (UART, FASED memory, BlockDev). `fslab init` does not generate this block — you add it. Each entry maps **bridge port → design port**.

```yaml
bridges:
  - type: "uart"
    name: "serial_0"
    port_map:
      txd: "uart_txd"
      rxd: "uart_rxd"
    params:
      baud_rate: { ref: BAUD }
      freq_mhz: 100
```

`type`
: Bridge id from the registry (`uart`, `fased`, `iceblk`) `[PROJ-12]`.

`name`
: Unique instance name; must match `^[a-zA-Z_][a-zA-Z0-9_]*$` `[PROJ-06]` and be unique within the project `[PROJ-10]`.

`port_map`
: Map of bridge-port name → blackbox-port name. Each value must be a declared `blackbox_ports` entry, and the bridge-port key must appear in the bridge's input or output list consistent with the design port's direction `[PROJ-13]`.

`params`
: Bridge parameters. A value is either a literal (`freq_mhz: 100`) or a reference to a design parameter (`baud_rate: { ref: BAUD }`), which keeps the bridge in sync with the RTL. Each bridge declares its required parameters in the registry.

The exhaustive per-bridge port and parameter lists live in {doc}`/developer/bridge-reference/index` ({doc}`/developer/bridge-reference/uart`, {doc}`/developer/bridge-reference/fased`, {doc}`/developer/bridge-reference/blockdev`). For the concept, see {doc}`/concepts/bridges-overview`.

### `advanced:` — optional

Registry overrides, toolchain paths, and generation parameters. Most projects leave this commented out.

`default_registry`
: Path to the framework `registry.yaml`.

`custom_registries`
: Additional registry files merged on top (last definition wins on id conflicts). Each entry is a path, optionally with a `plugin:` validator.

`firesim_root`, `firesim_lab_root`, `platforms_root`
: Toolchain root path overrides.

`gen_dir`
: Output directory for generated files. Default `generated-src`.

`gen_file_basename`
: Base filename for the generated Golden Gate top. Default `FireSim-generated`.

:::{warning}
A `custom_registries` entry may name a `plugin:` Python file, which executes arbitrary code at parse time. Only enable plugins you wrote or fully trust.
:::

## Related

- {doc}`/quickstart/metasim` — the worked edits to this `fslab.yaml`.
- {doc}`/concepts/target-rtl-requirements` — what your RTL must satisfy.
- {doc}`generate` — the next step.
- {doc}`build`, {doc}`sim-fpga` — the `target.build` / `target.run` field references.
