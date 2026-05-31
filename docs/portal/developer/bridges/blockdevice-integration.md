# Using the BlockDevice Bridge

The BlockDevice bridge is different from a "transport" bridge like UART. The
name says what it is *for*: it lets an operating system running on your simulated
target use a **block device** — a disk — whose storage is actually a file on the
simulation host. Making that work is a three-layer job, and only the middle
layer is the bridge itself. This page covers the two layers you also have to
provide: the **controller RTL inside your DUT** and the **device driver in the
target software**. For the bridge's port/parameter spec, see
{doc}`/developer/bridge-reference/blockdev`; for the bridge mechanism in general,
see {doc}`concepts`.

## The three layers

```text
   TARGET (simulated SoC)                         HOST (simulation)
 ┌───────────────────────────────────────┐
 │  OS / software on the target CPU       │   ← layer 3: device driver (e.g. Linux iceblk.c)
 │     │ MMIO regs + IRQ                   │
 │     ▼                                   │
 │  Block-device controller (your SV RTL) │   ← layer 1: controller in your DUT
 │   ├─ CPU-facing MMIO register port      │
 │   ├─ DMA master to target memory        │
 │   └─ front-end: req / data / resp / info│
 │        │  (BlockDeviceIO channels)      │
 │  ┌─────┴─────────────┐                  │   tokens   ┌──────────────────────┐
 │  │ BlockDevBridge     │ ===============▶ │ ========▶ │ BlockDevBridgeModule  │
 │  │ (BlackBox stub)    │ ◀=============== │ ◀======= │  (FPGA model)         │   ← layer 2: the bridge
 │  └────────────────────┘                  │           └──────────┬───────────┘
 └───────────────────────────────────────┘                        │ MMIO
                                                          ┌────────┴───────────┐
                                                          │ blockdev_t (C++)    │
                                                          │  serves +blkdev0=…  │
                                                          └────────┬───────────┘
                                                                   │
                                                            disk image file
```

1. **Controller RTL (in your DUT)** — a hardware block-device controller. It
   presents a memory-mapped register interface and an interrupt to the CPU, owns
   a DMA master into target memory, and speaks the bridge's front-end protocol
   (the `req`/`data`/`resp`/`info` channels) on its other side. You write this in
   SystemVerilog (or instantiate an existing controller).
2. **The bridge** — `BlockDevBridge` (target stub) + `BlockDevBridgeModule` (FPGA
   model) + `blockdev_t` (host C++ driver). This is what firesim-lab provides and
   what you select in `fslab.yaml`. It transports the front-end channels to the
   host and serves them from a disk-image file. You do not write any of this; you
   only wire the controller's front-end ports to it via `port_map`.
3. **Target software driver** — a driver in the OS running on the simulated CPU
   that programs the controller's registers and handles its interrupt. For Linux
   this is the IceBlk kernel module.

The key mental model: **the bridge is the disk backend, not the disk
controller.** Read data flows *disk-image file → `blockdev_t` → bridge → front-end
`resp` → controller → DMA → target memory*. A write flows the other way. The DMA
between the controller and target memory is **not** part of the bridge — it is a
separate master port on your controller into the SoC's own memory system.

```{note}
Why a controller *and* a software driver? Because the bridge only moves the
front-end channels. Something in the target has to turn "the OS wants to read
sector 42 into this buffer" into front-end requests plus a DMA into that buffer,
and something in the OS has to issue and complete those operations. Those are the
controller and the driver, respectively.
```

## Reference implementations

The bridge interface (`BlockDeviceIO`) carried by firesim-lab matches the
**testchipip IceBlk** controller. Use these upstream sources as the authoritative
implementations to port from or instantiate:

- **Controller RTL (Chisel):**
  [`testchipip/src/main/scala/iceblk/BlockDevice.scala`](https://github.com/ucb-bar/testchipip/blob/bfe7aa36fc570ee17e3f461c1ed48525684b95ff/src/main/scala/iceblk/BlockDevice.scala)
- **Linux kernel driver (C):**
  [`firesim/iceblk-driver/iceblk.c`](https://github.com/firesim/iceblk-driver/blob/89227faf4a92c4ba3f528905fca1222750d21b56/iceblk.c)

The SystemVerilog below is a translation/skeleton derived from the testchipip
controller; the Chisel source remains authoritative for exact behaviour, and the
driver source is authoritative for exact register offsets.

## The controller's two interfaces

The controller exposes two distinct interfaces (three if you count DMA):

### CPU-facing MMIO register map

The OS driver programs the controller through a small set of memory-mapped
registers and receives completions via an interrupt. The offsets below are the
IceBlk Linux driver's `ICEBLK_*` macros and match the testchipip controller's
`regmap`; treat the linked driver source as authoritative. Note the registers in
the `0x11`–`0x14` cluster are single-byte (`ioread8`).

| Register (`ICEBLK_*`) | Offset | Software access | Purpose |
|---|---|---|---|
| `ADDR` | `0x00` | write (64-bit) | DMA target address in system memory for the next request |
| `OFFSET` | `0x08` | write (32-bit) | Starting sector offset on the device |
| `LEN` | `0x0C` | write (32-bit) | Transfer length in sectors |
| `WRITE` | `0x10` | write (8-bit) | Direction: 1 = write to disk, 0 = read from disk |
| `REQUEST` | `0x11` | read (8-bit) | Allocate a tracker, launch the staged request, returns its tag |
| `NREQUEST` | `0x12` | read (8-bit) | Number of free request slots (back-pressure to the driver) |
| `COMPLETE` | `0x13` | read (8-bit) | Tag of a completed request (reading dequeues one completion) |
| `NCOMPLETE` | `0x14` | read (8-bit) | Number of completed requests waiting to be reaped |
| `NSECTORS` | `0x18` | read | Total sectors on the device (from the front-end `info` channel) |
| `MAX_REQUEST_LENGTH` | `0x1C` | read | Maximum request length in sectors |

A typical submission is: write `ADDR`, `OFFSET`, `LEN`, `WRITE`, then **read**
`REQUEST` — that read allocates a tracker, latches the staged values into it,
launches the request, and returns the assigned tag. Completion is
interrupt-driven: the ISR reads `NCOMPLETE`, then reads `COMPLETE` once per
completion to recover each finished tag and end the corresponding block-layer
request.

### Bridge-facing front-end (`BlockDeviceIO`)

The other side of the controller is the front-end the bridge transports. These
are exactly the ports you list under `port_map` for the `iceblk` bridge — see
{doc}`/developer/bridge-reference/blockdev` for the full table. Summarised:

- **`req`** (controller → bridge): a request — `write`, `offset`, `len`, `tag`.
- **`data`** (controller → bridge): write payload beats — `data` (64-bit), `tag`.
- **`resp`** (bridge → controller): read payload beats — `data` (64-bit), `tag`.
- **`info`** (bridge → controller): device geometry — `nsectors`, `max_req_len`.

Each sector is 512 bytes = 64 beats of 64 bits (`dataBeats`). Tags are
`log2Up(n_trackers)` bits and identify in-flight requests.

### DMA master (to target memory)

The controller also has a memory master (TileLink in testchipip; AXI4 works
equally well for an SV controller) that it uses to move payload between the DMA
`ADDR` and the front-end. This master connects to your SoC's own memory system —
not to the bridge.

## Request lifecycle

For a **read** (`WRITE = 0`):

1. The OS driver stages `ADDR`/`OFFSET`/`LEN`/`WRITE`, then reads `REQUEST` to
   allocate a tag and launch the request.
2. The controller issues a front-end `req` (`write=0`) and, as `resp` beats
   arrive from the bridge (sourced from the host disk image), DMA-writes them to
   memory starting at `ADDR` — `LEN × 64` beats total.
3. When the last beat lands, the controller marks the tag complete and raises its
   interrupt.
4. The ISR reads `NCOMPLETE`/`COMPLETE` and ends the block request.

For a **write** (`WRITE = 1`) the controller instead DMA-*reads* `LEN × 64` beats
from memory at `ADDR` and streams them out on the front-end `data` channel; the
bridge writes them into the disk image and the controller completes the tag.

## SystemVerilog controller

Below is a SystemVerilog rendering of the controller. It comes in two parts: an
**accurate module declaration** (the port contract is exactly knowable), and an
**illustrative behavioural skeleton**.

:::{warning}
The behavioural skeleton is **illustrative and unverified**. It abstracts the
CPU register bus and the DMA master behind simple handshakes, elides multi-tag
tracking, error handling, and back-pressure corner cases, and has not been
compiled or simulated. Treat it as a structural starting point — the testchipip
[`BlockDevice.scala`](https://github.com/ucb-bar/testchipip/blob/bfe7aa36fc570ee17e3f461c1ed48525684b95ff/src/main/scala/iceblk/BlockDevice.scala)
is the authoritative implementation. Do not ship it as-is.
:::

### Module declaration (port contract)

The bridge-facing ports are named to match the `iceblk` registry keys, so the
`port_map` in `fslab.yaml` is one-to-one. The CPU bus and DMA master are shown as
simple abstract interfaces; replace them with your SoC's real bus (AXI4-lite for
the register port, AXI4/TileLink for the DMA master).

```systemverilog
module blockdev_controller #(
    parameter int TAG_BITS  = 1,   // = $clog2(n_trackers); match the bridge param
    parameter int ADDR_BITS = 64,  // system memory address width (DMA master)
    parameter int REG_AW    = 12   // CPU register-port address width
) (
    input  logic                 clk,
    input  logic                 reset,

    // ── CPU-facing MMIO register port (abstract; use AXI4-lite in a real SoC) ──
    input  logic                 reg_wr_en,    // write strobe
    input  logic [REG_AW-1:0]    reg_wr_addr,  // register offset (see ICEBLK_* map)
    input  logic [63:0]          reg_wr_data,
    input  logic                 reg_rd_en,    // read strobe
    input  logic [REG_AW-1:0]    reg_rd_addr,
    output logic [63:0]          reg_rd_data,
    output logic                 irq,          // completion interrupt

    // ── DMA master into target memory (abstract; use AXI4/TileLink in a real SoC) ──
    output logic                 dma_req_valid,
    input  logic                 dma_req_ready,
    output logic                 dma_req_write,         // 1 = mem write (disk read), 0 = mem read
    output logic [ADDR_BITS-1:0] dma_req_addr,
    output logic [63:0]          dma_wdata,             // beat to write to memory
    input  logic [63:0]          dma_rdata,             // beat read from memory
    input  logic                 dma_resp_valid,

    // ── Bridge front-end: REQUEST channel (controller → bridge) ──
    output logic                 bdev_req_valid,
    input  logic                 bdev_req_ready,
    output logic                 bdev_req_bits_write,
    output logic [31:0]          bdev_req_bits_offset,
    output logic [31:0]          bdev_req_bits_len,
    output logic [TAG_BITS-1:0]  bdev_req_bits_tag,

    // ── Bridge front-end: WRITE-DATA channel (controller → bridge) ──
    output logic                 bdev_data_valid,
    input  logic                 bdev_data_ready,
    output logic [63:0]          bdev_data_bits_data,
    output logic [TAG_BITS-1:0]  bdev_data_bits_tag,

    // ── Bridge front-end: RESPONSE channel (bridge → controller) ──
    input  logic                 bdev_resp_valid,
    output logic                 bdev_resp_ready,
    input  logic [63:0]          bdev_resp_bits_data,
    input  logic [TAG_BITS-1:0]  bdev_resp_bits_tag,

    // ── Bridge front-end: INFO channel (bridge → controller) ──
    input  logic [31:0]          bdev_info_nsectors,
    input  logic [31:0]          bdev_info_max_req_len
);
```

### Behavioural skeleton

```systemverilog
    // ---- geometry ----------------------------------------------------------
    localparam int DATA_BEATS = 64;  // 512-byte sector / 64-bit beat

    // ---- register offsets (match the IceBlk Linux driver ICEBLK_* macros) --
    localparam logic [REG_AW-1:0] REG_ADDR      = 'h00;  // write, 64-bit
    localparam logic [REG_AW-1:0] REG_OFFSET    = 'h08;  // write, 32-bit
    localparam logic [REG_AW-1:0] REG_LEN       = 'h0C;  // write, 32-bit
    localparam logic [REG_AW-1:0] REG_WRITE     = 'h10;  // write, 8-bit
    localparam logic [REG_AW-1:0] REG_REQUEST   = 'h11;  // read: allocate tag + submit
    localparam logic [REG_AW-1:0] REG_NREQUEST  = 'h12;  // read: free slots
    localparam logic [REG_AW-1:0] REG_COMPLETE  = 'h13;  // read: completed tag (dequeues)
    localparam logic [REG_AW-1:0] REG_NCOMPLETE = 'h14;  // read: pending completions
    localparam logic [REG_AW-1:0] REG_NSECTORS  = 'h18;  // read
    localparam logic [REG_AW-1:0] REG_MAXLEN    = 'h1C;  // read

    // ---- staged request registers (written by the driver) ------------------
    logic [ADDR_BITS-1:0] r_addr;
    logic [31:0]          r_offset, r_len;
    logic                 r_write;
    logic [TAG_BITS-1:0]  r_tag;

    // ---- completion bookkeeping (single outstanding request shown) ---------
    logic                 complete_valid;
    logic [TAG_BITS-1:0]  complete_tag;
    assign irq = complete_valid;

    // ---- register writes ---------------------------------------------------
    // The driver stages the request with writes, then *reads* REG_REQUEST to
    // allocate a tag and launch it (see `submit` below).
    always_ff @(posedge clk) begin
        if (reset) begin
            r_addr <= '0; r_offset <= '0; r_len <= '0; r_write <= '0;
        end else if (reg_wr_en) begin
            case (reg_wr_addr)
                REG_ADDR:   r_addr   <= reg_wr_data[ADDR_BITS-1:0];
                REG_OFFSET: r_offset <= reg_wr_data[31:0];
                REG_LEN:    r_len    <= reg_wr_data[31:0];
                REG_WRITE:  r_write  <= reg_wr_data[0];
                default: ;
            endcase
        end
    end

    // ---- register reads ----------------------------------------------------
    logic busy;  // a request is in flight
    // Single-tracker model: reading REQUEST while idle allocates tag 0 + submits.
    wire submit       = reg_rd_en && (reg_rd_addr == REG_REQUEST) && !busy;
    // Reading COMPLETE dequeues a completion.
    wire complete_pop = reg_rd_en && (reg_rd_addr == REG_COMPLETE);
    always_comb begin
        reg_rd_data = '0;
        case (reg_rd_addr)
            REG_REQUEST:   reg_rd_data = '0;                       // allocated tag (0, single tracker)
            REG_NREQUEST:  reg_rd_data = {63'b0, ~busy};           // 1 free slot when idle
            REG_NCOMPLETE: reg_rd_data = {63'b0, complete_valid};
            REG_COMPLETE:  reg_rd_data = {{(64-TAG_BITS){1'b0}}, complete_tag};
            REG_NSECTORS:  reg_rd_data = {32'b0, bdev_info_nsectors};
            REG_MAXLEN:    reg_rd_data = {32'b0, bdev_info_max_req_len};
            default: ;
        endcase
    end

    // ---- main FSM ----------------------------------------------------------
    typedef enum logic [2:0] {S_IDLE, S_REQ, S_READ, S_WRITE, S_DONE} state_t;
    state_t state;
    logic [31:0] beats_left;

    always_ff @(posedge clk) begin
        if (reset) begin
            state <= S_IDLE; busy <= 1'b0; beats_left <= '0;
            complete_valid <= 1'b0; complete_tag <= '0;
            bdev_req_valid <= 1'b0; bdev_data_valid <= 1'b0; bdev_resp_ready <= 1'b0;
            dma_req_valid <= 1'b0;
        end else begin
            if (complete_pop) complete_valid <= 1'b0;

            case (state)
            // Latch a submitted request and issue it on the front-end.
            S_IDLE: if (submit) begin
                busy           <= 1'b1;
                r_tag          <= '0;          // single tracker
                beats_left     <= r_len * DATA_BEATS;
                bdev_req_valid       <= 1'b1;
                bdev_req_bits_write  <= r_write;
                bdev_req_bits_offset <= r_offset;
                bdev_req_bits_len    <= r_len;
                bdev_req_bits_tag    <= '0;
                state <= S_REQ;
            end

            S_REQ: if (bdev_req_ready) begin
                bdev_req_valid <= 1'b0;
                state <= r_write ? S_WRITE : S_READ;
                bdev_resp_ready <= ~r_write;  // accept read beats
            end

            // READ: pull beats from the bridge, DMA each into memory at r_addr.
            // (Abstracted: assumes dma accepts one beat per resp beat.)
            S_READ: if (bdev_resp_valid && dma_req_ready) begin
                dma_req_valid <= 1'b1;
                dma_req_write <= 1'b1;             // writing into memory
                dma_req_addr  <= r_addr;
                dma_wdata     <= bdev_resp_bits_data;
                r_addr        <= r_addr + 8;
                beats_left    <= beats_left - 1;
                if (beats_left == 1) begin bdev_resp_ready <= 1'b0; state <= S_DONE; end
            end else dma_req_valid <= 1'b0;

            // WRITE: DMA-read beats from memory, stream them out on `data`.
            // (Abstracted: dma_rdata assumed valid when dma_resp_valid.)
            S_WRITE: begin
                dma_req_valid <= (beats_left != 0) && !bdev_data_valid;
                dma_req_write <= 1'b0;             // reading from memory
                dma_req_addr  <= r_addr;
                if (dma_resp_valid) begin
                    bdev_data_valid     <= 1'b1;
                    bdev_data_bits_data <= dma_rdata;
                    bdev_data_bits_tag  <= r_tag;
                end
                if (bdev_data_valid && bdev_data_ready) begin
                    bdev_data_valid <= 1'b0;
                    r_addr     <= r_addr + 8;
                    beats_left <= beats_left - 1;
                    if (beats_left == 1) state <= S_DONE;
                end
            end

            S_DONE: begin
                dma_req_valid  <= 1'b0;
                complete_valid <= 1'b1;
                complete_tag   <= r_tag;
                busy           <= 1'b0;
                state          <= S_IDLE;
            end
            default: state <= S_IDLE;
            endcase
        end
    end
endmodule
```

What the skeleton deliberately leaves out, and what a production controller (per
testchipip) handles: multiple concurrent tags/trackers, proper DMA burst
handshaking and ordering, partial-beat and unaligned transfers, completion FIFO
depth and back-pressure via `NREQUEST`/`NCOMPLETE`, and reset/error semantics.

## Wiring it into firesim-lab

1. **Add the controller to your DUT.** Instantiate `blockdev_controller` (or your
   own equivalent) in your top module, connect its CPU register port and DMA
   master to your SoC's buses, and expose the `bdev_*` front-end ports on the top
   module.
2. **Select the bridge** in `fslab.yaml` and map the `bdev_*` keys to your top
   module's pins — see the example in {doc}`/developer/bridge-reference/blockdev`.
3. **Build and run.** Provide a disk image at run time with `+blkdev0=<image>`
   (see the reference page for the full plusarg list).
4. **Add the software driver.** Build the OS image for the target with the IceBlk
   driver (or your own) so the OS sees the device; the driver programs the
   registers from layer 1 above and reaps completions on the interrupt.

## See also

- {doc}`/developer/bridge-reference/blockdev` — the bridge spec sheet (ports,
  params, plusargs, host driver hooks).
- {doc}`concepts` — the general bridge model (tokens, MMIO, the target/host split).
- {doc}`adding-new-bridges` — how to add a brand-new bridge of your own.
- testchipip [`BlockDevice.scala`](https://github.com/ucb-bar/testchipip/blob/bfe7aa36fc570ee17e3f461c1ed48525684b95ff/src/main/scala/iceblk/BlockDevice.scala)
  and the firesim [`iceblk.c`](https://github.com/firesim/iceblk-driver/blob/89227faf4a92c4ba3f528905fca1222750d21b56/iceblk.c)
  Linux driver — the authoritative controller RTL and software driver.
