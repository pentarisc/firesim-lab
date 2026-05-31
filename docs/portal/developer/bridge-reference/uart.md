# UART Bridge

Connects the target's UART TX/RX pins to the simulation host. The host driver
writes received bytes to stdout, a PTY, or a file, and feeds host input back
into the target. This is the simplest bridge and the framework's canonical
walkthrough example — see {doc}`/developer/bridges/concepts` for the mechanism.

## Identity

| Field | Value |
|---|---|
| `id` | `uart` |
| `origin` | `fslab` |
| `cpp_type` | `uart_t` |
| C++ sources | `bridges/uart.cc`, `bridges/uart.h` |
| Target interface | `firechip.bridgeinterfaces.UARTPortIO` / `UARTBridgeTargetIO` |
| Target stub | `firechip.bridgestubs.uart.UARTBridge` |
| Host model | `firechip.goldengateimplementations.UARTBridgeModule` |

## Ports

Directions are from the target's point of view. Both pins are one bit wide.

| `port_map` key | Direction | Width | Meaning |
|---|---|---|---|
| `txd` | output | 1 | Serial transmit — target drives bytes out |
| `rxd` | input | 1 | Serial receive — bridge drives bytes in |

Your Verilog top module must expose a working UART (a real TX/RX implementation
at the configured baud rate); the bridge only transports the serial line, it
does not implement the UART protocol on the target side.

## Parameters

Both are required (`required_params: [freq_mhz, baud_rate]`).

| `params` key | Type | Meaning |
|---|---|---|
| `freq_mhz` | int (MHz) | Target clock frequency, used with `baud_rate` to derive the bridge's bit-period divider |
| `baud_rate` | int (baud) | Serial baud rate, e.g. `115200` |

The bridge computes the divider as `freq_mhz * 1_000_000 / baud_rate` and passes
it to the host model as `UARTKey(div)`.

## Runtime plusargs

These are understood by the C++ driver at run time (they are not declared in the
registry's `runtime_plusargs`, which is empty for UART):

| Plusarg | Effect |
|---|---|
| `+uart-in<N>=<file>` | Read UART *N*'s input from `<file>` instead of the default |
| `+uart-out<N>=<file>` | Write UART *N*'s output to `<file>` instead of the default |

Default behaviour when no plusargs are given:

- **UART 0** attaches to **stdin/stdout** (`"UART0 is here (stdin/stdout)."`).
  A `SIGINT` handler keeps `Ctrl-C` from killing the simulation.
- **UART *N* > 0** attaches to a **PTY**, symlinked as `uartpty<N>`, and mirrors
  output to a `uartlog<N>` file. Attach with `sudo screen uartpty<N>`.

If both `+uart-in<N>` and `+uart-out<N>` are supplied, that UART uses the named
files instead.

## Driver hooks

- **MMIO struct** `UARTBRIDGEMODULE_struct`: `out_bits`, `out_valid`,
  `out_ready`, `in_bits`, `in_valid`, `in_ready`.
- **`uart_t::tick()`** reads the TX FIFO head (`out_bits`/`out_valid`) and emits
  the byte; pulls a host input byte and pushes it (`in_bits`/`in_valid`) when the
  RX FIFO has room. Per-byte deserialisation/serialisation is done on the FPGA in
  `UARTBridgeModule`.
- Bridge type tag: `uart_t::KIND`; instances are collected with
  `registry.get_bridges<uart_t>()`.

## `fslab.yaml` example

```yaml
bridges:
  - type: "uart"
    name: "serial_0"
    port_map:
      txd: "uart_tx"      # a 1-bit output pin on your top module
      rxd: "uart_rx"      # a 1-bit input pin on your top module
    params:
      freq_mhz: 100
      baud_rate: 115200
```

## See also

- {doc}`/developer/bridges/concepts` — token flow and MMIO model.
- {doc}`/developer/bridges/registry-yaml` — meaning of each registry field.
