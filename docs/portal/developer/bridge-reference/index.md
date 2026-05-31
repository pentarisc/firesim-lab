# Bridge Reference

Per-bridge specification sheets for the bridges that ship with firesim-lab:
exact ports, parameters, registry entry, runtime plusargs, and driver hooks.
This is the **reference** half of the bridge documentation. For the conceptual
model and the procedure for adding a new bridge, see
{doc}`/developer/bridges/index`.

Each page is a spec sheet, not a tutorial — it tells you precisely what to put
in `port_map` and `params` when you select the bridge in `fslab.yaml`, and what
the host driver does at run time. Directions in the port tables are given **from
the target's point of view**: an *output* is a signal your RTL drives toward the
bridge; an *input* is a signal the bridge drives back into your RTL.

## The bridges at a glance

| Bridge | `id` | Origin | C++ type | Required params | Purpose |
|---|---|---|---|---|---|
| {doc}`UART <uart>` | `uart` | `fslab` | `uart_t` | `freq_mhz`, `baud_rate` | Serial TX/RX to terminal, PTY, or file |
| {doc}`BlockDevice <blockdev>` | `iceblk` | `fslab` | `blockdev_t` | `tag_bits`, `n_trackers` | Virtual disk served from a host file |
| {doc}`FASED <fased>` | `fased` | `firesim` | `FASEDMemoryTimingModel` | `addr_bits`, `data_bits`, `id_bits`, `user_bits`, `memory_region_name`, `mem_base`, `mem_size` | AXI4 DRAM timing model |

```{toctree}
:maxdepth: 1

uart
blockdev
fased
```
