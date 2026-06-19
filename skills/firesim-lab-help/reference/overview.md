# The firesim-lab flow — first-run overview

Show this verbatim when the user wants the overview (the `help` keyword in the
other skills re-shows it too).

> **What's about to happen — the firesim-lab flow**
> 1. **Setup (one-time):** check Docker + firesim-lab are installed and your
>    workspace is initialized. Free.
> 2. **Metasim — prove your design in software:** scaffold a project, add your
>    Verilog/SV + a test payload, pick bridges (UART, FASED memory, BlockDev),
>    and run a fast *desktop* simulation. No FPGA, no cost. We confirm it behaves
>    correctly here **first**.
> 3. **AWS setup (only if you want real FPGA):** help you log in (SSO) and check
>    your AWS roles/quota. Nothing here costs money by itself.
> 4. **F2 — run on a real FPGA:** build a bitstream and run it on an AWS F2
>    instance. This **costs money and takes time**, so we always confirm before
>    spending and shut the cloud machines down for you when done.

## The three skills

| Skill | Runs | Owns |
|---|---|---|
| `firesim-lab-help` | on demand (this) | the overview + map; changes nothing |
| `firesim-lab-setup` | once per host/account | host prereqs, workspace init, opt-in AWS provisioning + SSO, opt-in notifications |
| `firesim-lab-sim` | every iteration | scaffold → configure → build → metasim → **gate** → F2 build/run |

The hard rule: **F2 (real FPGA, costs money) is only offered after a design
passes the metasim gate** — a software simulation that produces the output you
defined as "success".
