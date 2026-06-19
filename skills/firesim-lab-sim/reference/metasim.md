# Sim reference — metasim (steps 1–6)

The skill's core job here is to **be the AI standing in for the human at the
intended post-`init` configuration step**. `fslab init` parses RTL verbatim
(pyslang) and does not infer intent — these gotchas are intended config, not bugs.
Every one is load-bearing.

## Command sequence

All `fslab` commands go through `fslab_in_dir <proj> 'fslab …'`.

```bash
fslab new <proj>                          # scaffold under /target
#   copy user RTL -> /target/<proj>/user_rtl/<top>.v
#   copy payload  -> /target/<proj>/payloads/<file>     (NOTE: "payloads", plural — #3)
fslab init -t <TopModule> -f <top>.v      # parse ports/params -> fslab.yaml
#   EDIT fslab.yaml (patch, not author): host:, clk/reset/enable, bridges,
#   port_map, ref:, mem_base  (see below)
fslab generate                            # render Chisel shim, CMake, driver
fslab build metasim                       # via the build-runner sub-agent
fslab sim --args '+loadmem=/target/<proj>/payloads/<file> +max-cycles=<N>'
```

Each step is hash-aware/idempotent. After a completed build, prefer
`fslab sim --skip-rtl --skip-driver` whenever the binary is already current
(faster). (The old implicit-recompile bug, #10, is fixed; the flags are still the
fast path.)

## Post-`init` configuration (INFER + SHOW; user vetoes)

Patch `fslab.yaml` — never author from scratch. Required edits:

1. **Top-level `host:` block is MANDATORY (#11).** `init` emits it **commented
   out**, but `FSLabConfig.host` is required → `generate` aborts with
   `host / Field required`. **This is the top-level `host`, NOT
   `target.build.host`** (that block is present and unrelated — a real trap; do not
   mis-blame it). Author at minimum:
   ```yaml
   host:
     emulator: "verilator"
     driver_name: "<name>"
     sources:
       - "src/main/cc/<driver_name>.cc"   # #12 — see below
   ```
2. **`host.sources` must list the generated driver (#12).** `USER_CC` comes from
   `config.host.sources`; the generated `src/main/cc/<driver_name>.cc` defines
   `create_simulation()` and is **not** auto-added — an empty `host.sources` →
   Verilator link fails with `undefined reference to create_simulation`.
3. **clk / reset / enable designation (#2).** `init` emits ports verbatim (e.g.
   `clk: "in logic"`). The Chisel shim picks `clock_port`/`reset_port` by matching
   the literal strings **`"in clock"` / `"in reset"`** — rewrite the clock, reset
   (and enable) entries in `blackbox_ports` to those values or the DUT clock/reset
   stay unconnected and nothing toggles.

## Bridges & port_map

- **Port check (step 3):** compare the DUT's parsed ports against the bridge's
  `input_ports`/`output_ports` in `lib/registry.yaml`. A missing **required** port
  is a HARD STOP (`needs_decision`) — it needs a user RTL change, not a config fix.
- **`port_map` direction (#6):** keys are *bridge* port names, values are *DUT
  blackbox* port names. FASED: `m_*` keys = DUT **master outputs** (aw/w/ar
  valid+payload, b/r ready); `s_*` keys = DUT **slave inputs** (aw/w ready, b/r
  valid+payload). Full key list per bridge in `lib/registry.yaml`; a worked example
  is in the `fslab.yaml.j2` template comments. Only ASK for overrides when bridge↔DUT
  names don't auto-align.

## Params with `ref:` (avoid duplication, #7)

Source bridge params from `design.parameters` instead of hardcoding:
`addr_bits: { ref: ADDR_W }`. Validated mappings: `ADDR_W↔addr_bits`,
`DATA_W↔data_bits`, `ID_W↔id_bits`, `USER_W↔user_bits`, `BAUD↔baud_rate`. Params
with no 1:1 design param stay literal: `mem_base`, `mem_size`, `memory_region_name`,
`freq_mhz`. Literal params must match the bridge stub's **Scala type**:

- **UART `freq_mhz` must be an integer literal (#13).** `UARTBridge.apply` types
  `freqMHz: Int` — `freq_mhz: 100.0` renders `UARTBridge(..., 100.0, ...)` and sbt
  fails `type mismatch; found Double required Int`. Write `freq_mhz: 100`.

## FASED `mem_base` / `mem_size` (#5, #14)

- `+loadmem` writes the payload at **offset 0** of the model. `mem_base` must
  contain the addresses the DUT actually drives — a mismatched base (e.g.
  `0x80000000` when the DUT reads `0x0`) puts the read outside the modeled region →
  no response → the sim **hangs**. Sanity-check this rather than accepting a
  mismatch.
- **Encoding: bare hex-digit strings, no `0x`, not decimal.** The template emits
  `BigInt("{{ value }}", 16)`, so the value is parsed base-16. `mem_base: 0x40000000`
  (YAML → decimal `1073741824`) becomes `BigInt("1073741824", 16)` → a
  non-power-of-two mask → Golden Gate fails
  `AXI4SlaveParameters: minAlignment must be >= maxTransfer`. Write
  `mem_base: "0"`, `mem_size: "40000000"` (= 0x0 / 0x40000000).

## Success criterion & cycle budget (step 6)

- **UART output is on stdout, interleaved (#8)** with FireSim's banner
  (`FireSim fingerprint: 0x…`) — no `uartlog` in metasim. Parse accordingly.
- **`+max-cycles` math (#9):** at 115200 baud / 100 MHz each byte ≈ 8680 cycles
  (≈11 bytes per 100k cycles). Size from payload bytes × baud, or warn about
  truncation.
- Criterion shapes to offer: **expected-output match** (substring/regex; strongest),
  **clean-exit + non-empty UART**, **manual confirm** (show output, user decides).

## The project stamp (you own it)

Write `<project>/.fslab/skill-state.json` (atomic `*.tmp`→rename):

```json
{
  "schema_version": 1,
  "fslab_version": "0.8.0",
  "skill_version": "0.8.0",
  "created_at": "...",
  "updated_at": "...",
  "design": {
    "project_name": "uart-print-test",
    "top_module": "AXIUARTPrinter",
    "rtl_paths": ["user_rtl/AXIUARTPrinter.v"],
    "bridges": ["fased", "uart"]
  },
  "metasim": {
    "passed": true,
    "config_hash": "<sha256 copied from .fslab/state.json at pass time>",
    "criterion": { "type": "expected_output", "value": "Hello fr" },
    "evidence": { "matched": true, "captured_excerpt": "Hello frfom FiReim!…", "max_cycles": 100000 },
    "passed_at": "..."
  },
  "f2": { "last_build_id": null, "last_run_id": null, "agfi": null }
}
```

**Gate rule:** F2 unlocks iff `metasim.passed === true` AND `metasim.config_hash`
equals the current `config_hash` in `.fslab/state.json`. Editing RTL or
`fslab.yaml` changes that hash and re-opens the gate — never trust stale evidence.

## Validated reference design

`fslab new uart-print-test`; DUT
`examples/axi-uart/AXIUARTPrinter.v` (AXI4 read-master streaming FASED bytes over
UART 8N1); payload `examples/axi-uart/sample.hex`; expected UART
`Hello frfom FiReim! Hell` + FASED fill. Walkthrough:
`examples/axi-uart/README.md`. Validate the skill against this before any F2
wiring.
