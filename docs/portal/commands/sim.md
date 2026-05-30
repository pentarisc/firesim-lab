# fslab sim

Run a cycle-accurate **software** simulation (metasimulation). `fslab sim` builds the project if needed, then runs the simulator binary — so in day-to-day work this is often the only command you type.

For running on real FPGA hardware, see {doc}`sim-fpga`.

## Synopsis

```bash
fslab sim [metasim] [options]
```

With no subcommand, `fslab sim` runs **metasim**. `fslab sim metasim` is the explicit form.

## Options

| Option | Default | Description |
|---|---|---|
| `-a`, `--args <str>` | — | Extra arguments forwarded verbatim to the simulation binary. Quote as one string: `--args '+loadmem=... +max-cycles=100000'`. |
| `--skip-rtl` | off | Skip the sbt/Java RTL build steps when (re)building. |
| `--skip-driver` | off | Skip the C++ driver build when (re)building. |
| `--force-gen` | off | Force {doc}`generate` even if the config hash is unchanged. |
| `-c`, `--config <path>` | `fslab.yaml` | Path to the project YAML. |

## What it does

1. **Ensure compiled.** Runs the implicit build chain (which itself runs {doc}`generate`). Because every step is hash-aware, this is a near no-op when nothing has changed.
2. **Locate the binary.** Finds the compiled simulator under `build/` (the name is derived from `host.emulator` and `host.driver_name` — e.g. Verilator produces `V<driver_name>`).
3. **Run it**, appending anything you pass via `--args`, and streams its output to your terminal.

If the binary cannot be found, `fslab sim` reports the paths it searched and tells you to run {doc}`build` first.

## Passing plusargs

Simulator inputs are passed through `--args` as a single quoted string. Common ones for the AXIUARTPrinter example:

- `+loadmem=<path>` — pre-load the FASED memory model from a hex file. Use the in-container `/target/...` path.
- `+max-cycles=<n>` — bound the run (designs that loop forever need a cycle cap).

```bash
fslab sim metasim --args '+loadmem=/target/uart-print-test/payloads/sample.hex +max-cycles=100000'
```

The set of available plusargs depends on the bridges you wired in; each bridge documents its runtime plusargs in {doc}`/developer/bridge-reference/index`.

## Other simulation subcommands

- `fslab sim fpgasim` — reserved for FPGA-level (XSIM) simulation; **not yet implemented**. Build the FPGA-level driver with `fslab build fpgasim`.
- `fslab sim fpga` — run on real F2 hardware. Documented separately in {doc}`sim-fpga`.

## Related

- {doc}`/quickstart/metasim` — the worked end-to-end metasim run.
- {doc}`/concepts/cycle-accurate-simulation` — what "cycle-accurate" means here.
- {doc}`build` — what `sim` calls under the hood.
- {doc}`sim-fpga` — the hardware-accelerated counterpart.
