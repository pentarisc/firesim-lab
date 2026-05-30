# Pre-installation Setup

Before you install firesim-lab and build your first design, get two things ready: the **host machine** you will run on, and — only if you intend to use FPGA-accelerated simulation — your **AWS account**. This section covers both. When it is done, head to {doc}`/installation/index` to install firesim-lab and start the container.

The host side is short by design: firesim-lab ships its whole toolchain in a Docker image, so the host needs little more than Docker. The AWS side is only relevant for the FPGA path (`fslab build fpga` / `fslab sim fpga`) and can be deferred until your design already works in desktop metasimulation.

Work through the section in this order:

1. {doc}`host-prerequisites` — the checklist for the host machine: Docker, curl, supported platforms, and recommended hardware. **Everyone needs this.**
2. {doc}`aws/index` — prepare an AWS account, a login identity, and the IAM roles firesim-lab attaches to its EC2 build and run hosts. **FPGA path only** — skip it if you are only running metasimulations.

```{toctree}
:maxdepth: 2

host-prerequisites
aws/index
```
