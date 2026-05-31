# FASED Memory Timing Model

Attaches a target AXI4 memory port to **FASED**, FireSim's DRAM timing model, so
memory accesses are returned with realistic latency/bandwidth instead of as an
ideal single-cycle memory. Unlike the UART and BlockDevice bridges, FASED's
C++ driver is built into `firesim-lib` upstream, so its registry entry is
`origin: firesim` and carries no fslab-owned C++ sources.

## Identity

| Field | Value |
|---|---|
| `id` | `fased` |
| `origin` | `firesim` |
| `cpp_type` | `FASEDMemoryTimingModel` |
| C++ sources | `bridges/fased_memory_timing_model.cc` / `.h` (built into `firesim-lib`) |
| Wiring | `firesim.lib.bridges.FASEDBridge` + `CompleteConfig` (NASTI/AXI4 edge) |

## Ports

FASED exposes a full **AXI4** (NASTI) memory interface. The target is the
*master*: it drives the `m_*` signals out and receives the `s_*` signals back.
All five AXI4 channels are present. Widths below use the parameters from the next
section (`A` = `addr_bits`, `D` = `data_bits`, `I` = `id_bits`, `U` =
`user_bits`).

**Write address (AW)** and **read address (AR)** — identical shape

| `port_map` key (AW / AR) | Direction | Width |
|---|---|---|
| `m_aw_valid` / `m_ar_valid` | output | 1 |
| `m_aw_id` / `m_ar_id` | output | `I` |
| `m_aw_addr` / `m_ar_addr` | output | `A` |
| `m_aw_len` / `m_ar_len` | output | 8 |
| `m_aw_size` / `m_ar_size` | output | 3 |
| `m_aw_burst` / `m_ar_burst` | output | 2 |
| `m_aw_lock` / `m_ar_lock` | output | 1 |
| `m_aw_cache` / `m_ar_cache` | output | 4 |
| `m_aw_prot` / `m_ar_prot` | output | 3 |
| `m_aw_qos` / `m_ar_qos` | output | 4 |
| `m_aw_user` / `m_ar_user` | output | `U` |
| `m_aw_region` / `m_ar_region` | output | 4 |
| `s_aw_ready` / `s_ar_ready` | input | 1 |

**Write data (W)**

| `port_map` key | Direction | Width |
|---|---|---|
| `m_w_valid` | output | 1 |
| `m_w_data` | output | `D` |
| `m_w_strb` | output | `D / 8` |
| `m_w_last` | output | 1 |
| `m_w_user` | output | `U` |
| `s_w_ready` | input | 1 |

**Write response (B)**

| `port_map` key | Direction | Width |
|---|---|---|
| `s_b_valid` | input | 1 |
| `s_b_id` | input | `I` |
| `s_b_resp` | input | 2 |
| `s_b_user` | input | `U` |
| `m_b_ready` | output | 1 |

**Read data (R)**

| `port_map` key | Direction | Width |
|---|---|---|
| `s_r_valid` | input | 1 |
| `s_r_id` | input | `I` |
| `s_r_data` | input | `D` |
| `s_r_resp` | input | 2 |
| `s_r_last` | input | 1 |
| `s_r_user` | input | `U` |
| `m_r_ready` | output | 1 |

```{note}
The wiring template ties `user` and `region` fields to `DontCare` / `0` on the
model side — FASED only uses AXI4 (not AXI3), and the `user` channels are not
modelled. You still map every port so the generated blackbox matches your RTL.
```

## Parameters

All required
(`required_params: [addr_bits, data_bits, id_bits, user_bits, memory_region_name, mem_base, mem_size]`).

| `params` key | Type | Meaning |
|---|---|---|
| `addr_bits` | int | AXI address width (`A`) |
| `data_bits` | int | AXI data width (`D`); write-strobe width is `D/8` |
| `id_bits` | int | AXI ID width (`I`); the model allows `1 << id_bits` masters |
| `user_bits` | int | AXI user width (`U`) |
| `memory_region_name` | string | Name of the FASED memory region (passed to `CompleteConfig`) |
| `mem_base` | hex string | Base address of the modelled region (parsed as `BigInt(.., 16)`) |
| `mem_size` | hex string | Size of the modelled region (parsed as `BigInt(.., 16)`) |

```{note}
`mem_base` and `mem_size` are written as hexadecimal **strings** (e.g.
`"80000000"`), because the wiring template parses them with `BigInt(value, 16)`
to build the `AddressSet`.
```

## Runtime plusargs

Declared in the registry `runtime_plusargs` and handled by the FASED driver:

| Plusarg | Effect |
|---|---|
| `+mm-unified-latency=<cycles>` | Simple unified read/write latency model |
| `+dramsim` | Enable the DRAMSim2 timing model |
| `+fased-init-depth=<n>` | Initial DRAM row-buffer fill depth |

## Driver hooks

- `cpp_type` is `FASEDMemoryTimingModel`; the driver ships with `firesim-lib`, so
  there is no fslab-owned `.cc`/`.h` to write — the registry entry only wires the
  Golden Gate model and documents the plusargs.
- The wiring template builds `NastiParameters(dataBits, addrBits, idBits)`, an
  `AXI4` master/slave edge over `AddressSet(mem_base, mem_size - 1)`, and
  instantiates `FASEDBridge(clock, nasti, reset, CompleteConfig(...))`.

## `fslab.yaml` example

```yaml
bridges:
  - type: "fased"
    name: "mainmem"
    port_map:
      # Map every m_* / s_* port to the matching pin on your top module.
      # (abbreviated — see the registry entry for the full port list)
      m_aw_valid: "m_aw_valid"
      s_aw_ready: "s_aw_ready"
      m_w_valid:  "m_w_valid"
      m_w_data:   "m_w_data"
      m_r_ready:  "m_r_ready"
      s_r_valid:  "s_r_valid"
      s_r_data:   "s_r_data"
      # ... all remaining AW/W/B/AR/R ports ...
    params:
      addr_bits: 32
      data_bits: 64
      id_bits: 4
      user_bits: 1
      memory_region_name: "DefaultMemoryRegion"
      mem_base: "0"
      mem_size: "80000000"
```

```{tip}
The commented `fased` block in a freshly generated `fslab.yaml` lists every AXI4
port with the correct keys — copy it and rename the values to your RTL pins
rather than typing the full list by hand.
```

## See also

- {doc}`/developer/bridges/concepts` — how a bridge attaches to the target.
- {doc}`/developer/bridges/registry-yaml` — the `origin: firesim` distinction.
