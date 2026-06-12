# FireSim-Lab Documentation

`firesim-lab` lets you simulate Verilog/SystemVerilog designs with cycle-accurate, FPGA-accelerated performance — without writing a line of Chisel or Scala. It builds on UCB-BAR's FireSim, wraps your blackbox in a generated Chisel shim, and exposes a single `fslab` CLI for the full project lifecycle: scaffold, build, simulate on a desktop, and run on AWS F2.

## Where to start

- **New to FireSim or cycle-accurate simulation?** Begin with {doc}`/concepts/index` for the mental model, then {doc}`/setup/index` and {doc}`/installation/index`.
- **Have a Verilog design and want to see it run?** Jump to {doc}`/quickstart/index` — a copy-paste walkthrough using the AXIUARTPrinter example.
- **Looking up an `fslab` command?** See {doc}`/commands/index`.
- **Extending firesim-lab itself** — adding a bridge, changing the container, hacking on the CLI? Start with {doc}`/developer/index`.

For the full project context — what firesim-lab is, how it relates to upstream FireSim/Chipyard, and current limitations — see {doc}`/introduction/index`.

```{toctree}
:maxdepth: 2
:caption: Getting Started

introduction/index
concepts/index
setup/index
installation/index
quickstart/index
```

```{toctree}
:maxdepth: 2
:caption: Using fslab

commands/index
examples/index
troubleshooting/index
```

```{toctree}
:maxdepth: 2
:caption: Developer Documentation

developer/index
```

```{toctree}
:maxdepth: 1
:caption: Reference

help-and-support
contributing
changelog
appendix/glossary
```
