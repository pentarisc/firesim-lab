# Target RTL Requirements

Golden Gate and the FPGA flow impose a concrete set of preconditions on the RTL you can submit as a target. This page is a checklist of the rules most likely to bite a firesim-lab user, with the *why* spelled out for each so you can reason about edge cases instead of memorising the list.

firesim-lab uses Golden Gate as shipped, so every rule below comes from upstream FireSim. The authoritative reference is FireSim's [Generating Different Targets](https://docs.fires.im/en/latest/Advanced-Usage/Generating-Different-Targets.html) page; consult it when this page is silent on a question.

:::{tip}
If you landed here from a `fslab generate` or Golden Gate elaboration error, scan the section headings first — the failing rule is almost always listed below.
:::

## Clocking and reset

- **Single-clock targets are the easy path.** The default scaffold handles a single target clock automatically; no extra declarations are needed.
- **Multi-clock targets must use a recognised clock-generator module.** Golden Gate identifies clock domains at elaboration time so it can decouple each from host time independently. Enable the `multi-clock` feature in your project (declared in `lib/registry.yaml`) to get the supporting scaffold — see {doc}`bridges-overview` for where features sit alongside bridges.
- **Async clock crossings must be explicit and use a recognised pattern.** Implicit CDCs that happen to work in software simulation are not guaranteed to work after Golden Gate's transformation. Async crossings should be wrapped in an async-FIFO or other primitive the framework can identify, not left as ad-hoc combinational paths between domains.
- **Reset must be synchronous and propagate deterministically.** Target reset is a target-time signal, distinct from host reset. Async resets break the cycle-accuracy guarantee at the reset transition because the deassertion edge is not aligned with a target clock edge.

## Combinational structure

- **No combinational loops anywhere — and especially not across bridge boundaries.** A combinational path across a bridge would require the host to resolve a value before producing the very cycle in which that value is being decided, which is incoherent with the latency-insensitive protocol that decouples target time from host time. Combinational loops *inside* the target are simply illegal for FPGA synthesis.
- **No latches.** Every branch of every combinational block must be assigned. Latches cannot be reasoned about cycle-accurately and will not synthesise cleanly to FPGA primitives.

## Bridge interfaces

- **Bridge target-facing ports are decoupled (ready/valid).** The host must be free to stall the target while it computes the next response. Your RTL cannot assume combinational availability of bridge data; drive bridge ports through proper handshakes, with no path that depends on a bridge response being ready in the same cycle as its request.
- **Memory bridges expect AXI4.** FASED is built on AXI4 today. If your design speaks a different memory protocol (AHB, AXI-Lite, a custom request/response interface), you need either an in-target adapter to AXI4 or a custom memory bridge that speaks your protocol. See {doc}`bridges-overview` and {doc}`/developer/bridge-reference/index`.

## Blackbox / synthesis hygiene

- **No tristate or `inout` on the FPGA side.** FPGAs cannot synthesise true tristate internally. Model bidirectional buses with explicit direction signals at the bridge boundary, or route them through a bridge protocol that already abstracts direction.
- **`initial` blocks are not honoured for synthesis-side state.[^init-blocks]** Reset is the only deterministic source of initial state in a FAME-1 target. If your block-level Verilator setup relied on `initial` to bring up registers or memories, move that initialisation into the reset path before bringing the design to FireSim.
- **No `$display`, `$finish`, `$random`, or other simulation-only constructs in synthesizable paths.** These are simulator primitives, not synthesizable signals; Golden Gate has no way to map them onto an FPGA. For run-time messages from the target, enable the `printf-synthesis` feature (so Chisel `printf()` calls bridge through to the host) or route text out through a UART bridge.
- **Hierarchical and cross-module references are not supported.** All inter-module communication must go through declared ports. The framework cannot transform a target that reaches across module boundaries through hierarchical paths.
- **DPI calls must go through bridges, not direct.** The C++ driver runs on the host side; the target has no path to it except via a bridge. Anything you would have done with a direct DPI call should be implemented as (or routed through) a bridge.

## Determinism

- **Anything non-deterministic must be seeded.** The whole point of cycle-accurate simulation is reproducibility: same RTL plus same inputs plus same seeds equals same trace. Random initial register state, unseeded `$random`, or memory contents that depend on the synthesis flow all break this guarantee. If your design needs randomness, route it through a deterministic, seedable source.

## Where to confirm and where next

This page is the curated subset; for the authoritative, complete reference always consult the upstream FireSim [Generating Different Targets](https://docs.fires.im/en/latest/Advanced-Usage/Generating-Different-Targets.html) page.

Once your RTL is structured to obey these rules, the next stop is the end-to-end walkthrough at {doc}`/quickstart/index`.

[^init-blocks]: The exact treatment of `initial` blocks in metasim mode (Verilator/VCS) versus FPGA mode under Golden Gate needs further verification — this rule is stated in the FPGA-synthesis form, which is the safe default. Refine this footnote once the behaviour is confirmed against the framework.
