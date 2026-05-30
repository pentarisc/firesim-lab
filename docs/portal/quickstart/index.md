# Quickstart: AXIUARTPrinter End-to-End

This quickstart takes a small Verilog design from a blank workspace to a running cycle-accurate simulation, and then — optionally — to a real AWS F2 FPGA. You write no Chisel: you supply Verilog, describe it in one `fslab.yaml`, and the framework generates everything else.

The walkthrough uses the **AXIUARTPrinter** example that ships with firesim-lab. It is a tiny AXI4 master that reads words from memory one beat at a time and serialises each byte over a UART at 115200 baud. Two bridges connect it to the host:

- a **FASED memory timing model** backs the AXI4 read port, so the design reads from host-loaded DRAM contents;
- a **UART bridge** turns the design's serial output into bytes on your terminal.

Pre-load a file at address `0x0`, run the simulation, and the bytes come back out over UART. That is the whole demo — and it exercises the entire pipeline: blackbox wrapping, bridge wiring, Golden Gate elaboration, and the host driver.

## Before you start

You need a working firesim-lab install and a container shell:

- {doc}`/installation/index` — install the launcher and pull the image.
- {doc}`/installation/first-container-start` — what the first `firesim-lab` run does.

Everything below runs **inside the container**, where the `fslab` CLI lives. Your host workspace is bind-mounted at `/target`; every project you scaffold lives there. Start the container from your workspace directory:

```bash
cd ~/firesim-workspaces/my-workspace
firesim-lab
```

The FPGA half of this quickstart additionally needs an AWS account configured for F2 — see {doc}`/setup/aws/index`. The metasimulation half needs nothing beyond the container.

If the concepts here are unfamiliar — what a bridge is, why memory and UART are *modelled* on the host rather than wired directly — read {doc}`/concepts/bridges-overview` and {doc}`/concepts/target-vs-host` first. This page assumes you just want to see it run.

## Scaffold the project

From inside the container, create a project. `fslab new` builds the directory skeleton, including the `user_rtl/` and `payloads/` folders you are about to fill:

```bash
fslab new uart-print-test
cd uart-print-test
```

Copy the example design into `user_rtl/` and the sample payload into `payloads/`. The example files live in the firesim-lab repository under `examples/axi-uart/`:

- `AXIUARTPrinter.v` → `user_rtl/AXIUARTPrinter.v`
- `sample.hex` → `payloads/sample.hex`

```bash
# from wherever you have the firesim-lab examples checked out
cp examples/axi-uart/AXIUARTPrinter.v  user_rtl/
cp examples/axi-uart/sample.hex        payloads/
```

These `cp` commands run **inside the container** — the `examples/` directory is baked into the image, not your workspace. For your *own* designs, put the RTL in your host workspace folder instead: it is bind-mounted at `/target`, so files you add on the host appear in the container automatically, with no copy step. `docker cp` is only needed for source you keep outside the mounted workspace. See {doc}`/installation/mountpoints` for the full mount map.

`sample.hex` is a few little-endian 64-bit words of ASCII. The design reads them back word by word and prints their bytes — so whatever you put here is what you will see on the UART.

:::{note}
Payloads are not version-controlled by default (the scaffold's `.gitignore` excludes `payloads/*`). They are uploaded per-run, not baked into the build. Reproducibility comes from an optional `payloads/SHA256SUMS` manifest, which *is* committed if you create it.
:::

## Where to next

- {doc}`metasim` — initialise the project, fill in `fslab.yaml`, and run a cycle-accurate software simulation. **Start here.**
- {doc}`fpga` — take the same project to a real AWS F2 FPGA. Continues from a working metasim project and assumes AWS is set up.

```{toctree}
:maxdepth: 1
:hidden:

metasim
fpga
```
