# Walkthrough: AXIUARTPrinter via the Skill

This mirrors the manual {doc}`/quickstart/index` — same AXIUARTPrinter design, same result — but driven by the **Claude Code skill** instead of typed `fslab` commands. You describe what you want in plain language; the skill runs the commands, fills in `fslab.yaml`, fixes trivial lint, and confirms the output. Compare it side by side with {doc}`/quickstart/metasim` to see exactly what the skill is automating for you.

## Before you start

- The plugin installed — see {doc}`/installation/skill-plugin`.
- A working firesim-lab toolchain (Docker + the `firesim-lab` launcher + the image). If you don't have it yet, the skill can detect the gaps and offer to install — just start with setup below.
- The example files: `examples/axi-uart/AXIUARTPrinter.v` and `examples/axi-uart/sample.hex` (baked into the image, also in the repo).

Open Claude Code in your workspace directory. Everything below is conversation — the exact wording doesn't matter, the intent does.

## 1. One-time setup

> **You:** Run firesim-lab-setup.

`firesim-lab-setup` checks that the container runtime is up, the `firesim-lab` launcher is installed, and the image is pulled — offering to remediate each gap one confirmed step at a time. It initialises your workspace, detects and pins the installed `fslab` version, and asks whether you plan to use AWS F2 (say **no** for now — metasim needs no AWS) and whether you want task notifications.

You only do this once per host/account. When it's done it tells you to run the simulation skill.

## 2. Scaffold, place files, configure

> **You:** Run firesim-lab-sim. I want to simulate AXIUARTPrinter from examples/axi-uart, using a UART bridge and a FASED memory.

`firesim-lab-sim` takes it from here. It will, asking only where it must:

- **Ask** for the top module and RTL path (it may propose `AXIUARTPrinter.v` if you have it open), and a project name (e.g. `uart-print-test`).
- Run `fslab new`, copy the RTL into `user_rtl/` and `sample.hex` into `payloads/`, then `fslab init` to parse the real ports.
- **Propose, for you to veto:** marking `clk`/`rst` as `in clock` / `in reset`, the `ref:` parameter mappings (`ADDR_W → addr_bits`, `BAUD → baud_rate`, …), and `mem_base`/`mem_size`.
- **Ask** for any port-map overrides — for AXIUARTPrinter the FASED AXI4 surface maps cleanly, so there's usually nothing to override.
- Author the required top-level `host:` block (emulator + driver) and add the generated driver to `host.sources` — the easy-to-miss steps that otherwise make `fslab generate` or the link fail.

It shows you the `fslab.yaml` it produced before building, so you can confirm or adjust.

## 3. Build, with the compile-fix loop

> **You:** Looks good, build it.

The skill runs `fslab generate` and then the build through the **build-runner** sub-agent, so the verbose sbt / Golden Gate / Verilator output stays out of your conversation. You get back one distilled verdict:

- **Success** → it moves on.
- **A Verilator width-lint warning** (the FireSim Makefrag makes these fatal) → it may apply a minimal, mechanical sized-literal fix and **show you the diff** — never a logic change.
- **A real logic / elaboration error** → it **reports** it with the diagnosis (or the raw log excerpt if it can't root-cause it) and **waits for you** to fix the RTL. It never edits your design logic.

## 4. Define success and run the metasim

> **You:** Success is the UART printing "Hello fr".

The skill asks what counts as a pass — an expected-output substring (strongest), a clean exit with non-empty UART, or a manual confirm. It sizes `+max-cycles` from the payload, runs `fslab sim`, and evaluates the captured UART (which is interleaved with the FireSim banner on stdout). On success it records the pass — with the matched output as evidence — and tells you the gate is open.

That's the whole metasim demo: host file → FASED model → AXI4 read → design → UART bridge → your terminal, with no Chisel written.

## 5. (Optional) Take it to AWS F2

The F2 path is offered **only now**, because the gate passed.

> **You:** Now run it on a real FPGA.

If you didn't provision AWS in setup, the skill points you back to `firesim-lab-setup` first. Otherwise it:

- Runs a **verify-only** readiness probe (roles, key pair, quota, AMI, SSO session) and reports any gaps — without changing anything.
- Drives the **device-code SSO login**: it shows you the verification URL and user code immediately, you approve in a browser, and it polls to completion.
- **Asks** the F2 questionnaire (profile/region, host models, slot, publish mode) and a **spend acknowledgement**, then patches `target.build`/`target.run`.
- Runs `fslab build fpga` (hard spend-confirm) and hands off to the **build-monitor** sub-agent, which watches the build, pulls the artifacts, and **terminates the build EC2** when done.
- Patches in the AGFI, runs `fslab sim fpga --detach`, and hands off to the **run-monitor**, which pulls the output and **stops the F2 host**.

Cleanup is automatic and cost-safe, and logs are always pulled back first — even on a failed build. If you enabled notifications, you get a ping when each long step finishes.

## What you just did

The same end-to-end pipeline as the manual quickstart — unmodified Verilog, host-modelled DRAM and UART, then a real FPGA — but the skill handled the scaffolding, the easy-to-miss `fslab.yaml` configuration, the build loop, the success check, and the AWS lifecycle. To see what each generated command does under the hood, read the manual {doc}`/quickstart/metasim` and {doc}`/quickstart/fpga`.
