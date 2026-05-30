# Quickstart: Metasimulation

This page continues from {doc}`index` — you have scaffolded `uart-print-test`, copied `AXIUARTPrinter.v` into `user_rtl/`, and `sample.hex` into `payloads/`. Here you turn that into a `fslab.yaml`, generate the framework code, and run a cycle-accurate software simulation. No AWS, no FPGA — everything runs in the container.

## 1. Initialise the project

`fslab init` parses your top module and writes a starting `fslab.yaml` with the design's ports already populated:

```bash
fslab init -t AXIUARTPrinter -f AXIUARTPrinter.v
```

- `-t / --top-module` is the Verilog module name.
- `-f / --top-module-file` is the source file. A bare filename is resolved under `user_rtl/`, so `AXIUARTPrinter.v` is enough.
- `-p / --platform` defaults to `f2`; you do not need it for metasim.

The parser reads the module's parameters and ports and writes a `design:` block. The full set of `fslab init` behaviours is documented in {doc}`/commands/init`; here we focus on what you edit next.

## 2. Mark the clock and reset

Open `fslab.yaml`. The `design:` block looks like this — every port comes through as `in logic` / `out logic[...]`, including the clock and reset:

```yaml
design:
  type: "blackbox"
  top_module: "AXIUARTPrinter"
  parameters:
    ADDR_W: 32
    DATA_W: 64
    ID_W: 4
    USER_W: 1
    CLK_HZ: 100000000
    BAUD: 115200
  sources:
    - "/target/uart-print-test/user_rtl/AXIUARTPrinter.v"
  blackbox_ports:
    clk: "in clock"
    rst: "in reset"
    m_axi_awid: "out logic[3:0]"
    m_axi_awaddr: "out logic[31:0]"
    m_axi_awlen: "out logic[7:0]"
    m_axi_awsize: "out logic[2:0]"
    m_axi_awburst: "out logic[1:0]"
    m_axi_awlock: "out logic"
    m_axi_awcache: "out logic[3:0]"
    m_axi_awprot: "out logic[2:0]"
    m_axi_awqos: "out logic[3:0]"
    m_axi_awuser: "out logic[0:0]"
    m_axi_awregion: "out logic[3:0]"
    m_axi_awvalid: "out logic"
    m_axi_awready: "in logic"
    m_axi_wdata: "out logic[63:0]"
    m_axi_wstrb: "out logic[7:0]"
    m_axi_wlast: "out logic"
    m_axi_wuser: "out logic[0:0]"
    m_axi_wvalid: "out logic"
    m_axi_wready: "in logic"
    m_axi_bid: "in logic[3:0]"
    m_axi_bresp: "in logic[1:0]"
    m_axi_buser: "in logic[0:0]"
    m_axi_bvalid: "in logic"
    m_axi_bready: "out logic"
    m_axi_arid: "out reg[3:0]"
    m_axi_araddr: "out reg[31:0]"
    m_axi_arlen: "out reg[7:0]"
    m_axi_arsize: "out reg[2:0]"
    m_axi_arburst: "out reg[1:0]"
    m_axi_arlock: "out logic"
    m_axi_arcache: "out logic[3:0]"
    m_axi_arprot: "out logic[2:0]"
    m_axi_arqos: "out logic[3:0]"
    m_axi_aruser: "out logic[0:0]"
    m_axi_arregion: "out logic[3:0]"
    m_axi_arvalid: "out reg"
    m_axi_arready: "in logic"
    m_axi_rid: "in logic[3:0]"
    m_axi_rdata: "in logic[63:0]"
    m_axi_rresp: "in logic[1:0]"
    m_axi_rlast: "in logic"
    m_axi_ruser: "in logic[0:0]"
    m_axi_rvalid: "in logic"
    m_axi_rready: "out reg"
    uart_txd: "out logic"
    uart_rxd: "in logic"
```

The one edit you must make: change the clock and reset port definitions from the parsed `in logic` to the **designations** `in clock` and `in reset`. In the block above they are already shown corrected:

```yaml
    clk: "in clock"
    rst: "in reset"
```

Besides `clock` and `reset`, the framework recognises a third port designation: `enable`. Marking a port `in enable` tells the framework the port is a clock-enable rather than ordinary logic, which is what gated or enable-driven designs need. Unlike `clock` and `reset`, it is optional — AXIUARTPrinter is free-running and has no enable port, so you do not add one here. See {doc}`/concepts/target-rtl-requirements` for when an enable designation applies.

Golden Gate needs to know which port is the target clock and which is target reset so it can decouple target time from host time. Parsing fails if no port is marked `in clock` and `in reset`.

The remaining ports keep their parsed widths. Note `m_axi_awuser: "out logic[0:0]"` — `USER_W` is 1, so the AXI4 user channel is a single bit; it is still a vector, not a scalar. The rules a target's RTL must satisfy for the framework to accept it are collected in {doc}`/concepts/target-rtl-requirements`.

## 3. Add the host driver

The `host:` block tells the framework which simulator to build and what to call the generated driver. Add it to `fslab.yaml`:

```yaml
host:
  emulator: "verilator"
  driver_name: "UartPrintDriver"
  cxx_standard: 20
  cxx_flags: "-O3 -Wall "
  sources:
    - "src/main/cc/UartPrintDriver.cc"
  libs:
    - "pthread"
```

You do **not** write `UartPrintDriver.cc` — `fslab generate` produces it from a template, named after `driver_name`, at the path you list in `sources`. `emulator` selects the metasim backend (`verilator`, `vcs`, or `xcelium`); Verilator ships in the container and needs no extra setup.

## 4. Wire up the bridges

The `bridges:` block connects your design's ports to bridge ports. Each entry names a bridge `type` from the registry, gives the instance a `name`, and maps **bridge port → design port** in `port_map`. Add both bridges:

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

  - type: "fased"
    name: "dram_0"
    port_map:
      s_aw_ready: "m_axi_awready"
      s_w_ready: "m_axi_wready"
      s_b_valid: "m_axi_bvalid"
      s_b_id: "m_axi_bid"
      s_b_resp: "m_axi_bresp"
      s_b_user: "m_axi_buser"
      s_ar_ready: "m_axi_arready"
      s_r_valid: "m_axi_rvalid"
      s_r_id: "m_axi_rid"
      s_r_data: "m_axi_rdata"
      s_r_resp: "m_axi_rresp"
      s_r_last: "m_axi_rlast"
      s_r_user: "m_axi_ruser"
      m_aw_valid: "m_axi_awvalid"
      m_aw_id: "m_axi_awid"
      m_aw_addr: "m_axi_awaddr"
      m_aw_len: "m_axi_awlen"
      m_aw_size: "m_axi_awsize"
      m_aw_burst: "m_axi_awburst"
      m_aw_lock: "m_axi_awlock"
      m_aw_cache: "m_axi_awcache"
      m_aw_prot: "m_axi_awprot"
      m_aw_qos: "m_axi_awqos"
      m_aw_user: "m_axi_awuser"
      m_aw_region: "m_axi_awregion"
      m_w_valid: "m_axi_wvalid"
      m_w_data: "m_axi_wdata"
      m_w_strb: "m_axi_wstrb"
      m_w_last: "m_axi_wlast"
      m_w_user: "m_axi_wuser"
      m_b_ready: "m_axi_bready"
      m_ar_valid: "m_axi_arvalid"
      m_ar_id: "m_axi_arid"
      m_ar_addr: "m_axi_araddr"
      m_ar_len: "m_axi_arlen"
      m_ar_size: "m_axi_arsize"
      m_ar_burst: "m_axi_arburst"
      m_ar_lock: "m_axi_arlock"
      m_ar_cache: "m_axi_arcache"
      m_ar_prot: "m_axi_arprot"
      m_ar_qos: "m_axi_arqos"
      m_ar_user: "m_axi_aruser"
      m_ar_region: "m_axi_arregion"
      m_r_ready: "m_axi_rready"
    params:
      addr_bits: { ref: ADDR_W }
      data_bits: { ref: DATA_W }
      id_bits: { ref: ID_W }
      user_bits: { ref: USER_W }
      memory_region_name: DefaultMemoryRegion
      mem_base: 0
      mem_size: 40000000
```

A few things to understand about this block:

- **Direction matters.** A bridge port key must appear in the bridge's input or output port list *consistent with the direction of the design port it maps to*. The bridge's `s_*` ports are inputs (signals the design drives back, like `arready`/`rvalid`); the `m_*` ports are outputs the design produces (like `arvalid`/`araddr`). The full port list for each bridge lives in {doc}`/developer/bridge-reference/index` (see {doc}`/developer/bridge-reference/fased` and {doc}`/developer/bridge-reference/uart`).
- **The UART map is trivial** — two pins, `txd`/`rxd`, straight onto the design's `uart_txd`/`uart_rxd`.
- **FASED needs the full AXI4 surface.** Because the model implements the complete AXI4 protocol, every channel signal — including the `lock`/`cache`/`prot`/`qos`/`user`/`region` qualifiers — must be mapped. AXIUARTPrinter exposes all of them (tying the unused ones off internally), which is why the map is exhaustive.
- **`{ ref: NAME }` pulls a value from `design.parameters`.** Writing `addr_bits: { ref: ADDR_W }` keeps the bridge's address width locked to the RTL parameter, so changing `ADDR_W` in one place stays consistent. Plain literals work too (`freq_mhz: 100`).
- **`mem_base: 0` matches the design**, which reads starting at address `0x0`. `mem_base`/`mem_size` are hex. `mem_size: 40000000` is a comfortably large region for the demo.

## 5. The target block

`fslab init` already wrote a `target:` block (it defaults to `platform: "f2"`). Metasimulation does not use it — it is consumed by the FPGA flow. Leave it as generated for now; you return to it in {doc}`fpga`.

## 6. Generate and inspect (optional)

To see what the framework produces before simulating, run:

```bash
fslab generate
```

This renders the Chisel shim (`Top.scala`, `DUT.scala`, `Config.scala`), the `UartPrintDriver.cc` driver, `build.sbt`, and `CMakeLists.txt` into the project. You never edit these by hand — they are regenerated from `fslab.yaml`. Details are in {doc}`/commands/generate`.

To compile the Verilator simulator without running it:

```bash
fslab build metasim
```

Both steps are optional: `fslab sim` runs them for you when the config has changed.

## 7. Run the simulation

```bash
fslab sim metasim --args '+loadmem=/target/uart-print-test/payloads/sample.hex +max-cycles=100000'
```

This implicitly generates and builds (if needed), then runs the simulator with two plusargs:

- `+loadmem=<path>` pre-loads the FASED memory from your hex file. Use the in-container `/target/...` path.
- `+max-cycles=<n>` bounds the run. AXIUARTPrinter loops forever — it keeps reading the next word and printing it — so a cycle cap is how you stop it.

You will see the bytes from `sample.hex` printed back over the UART as the simulation advances. That round trip — host file → FASED model → AXI4 read → design → UART bridge → your terminal — is the whole pipeline working end to end.

For the full `fslab sim` reference (flags, debug builds, waveform dumps), see {doc}`/commands/sim`.

## What you just proved

You ran a real cycle-accurate simulation of unmodified Verilog, with host-modelled DRAM and UART, without writing a line of Chisel. The same `fslab.yaml` is the input to the FPGA flow: continue to {doc}`fpga` to run it on AWS F2, or browse {doc}`/commands/index` for everything `fslab` can do.
