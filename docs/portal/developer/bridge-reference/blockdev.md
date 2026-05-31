# BlockDevice Bridge

Provides the target with access to a virtual disk served from a file on the
simulation host. The target speaks a simple sector-based request/response
protocol over four decoupled channels; the host driver backs it with a disk
image. Registry `id` is `iceblk` (after the Chipyard IceBlk device).

```{note}
This page is the bridge **spec sheet**. The bridge is only the disk *backend* —
to actually use it you must add a block-device **controller to your DUT RTL** and
a **device driver to the target OS**. For that end-to-end integration guide
(including a SystemVerilog controller skeleton), see
{doc}`/developer/bridges/blockdevice-integration`.
```

## Identity

| Field | Value |
|---|---|
| `id` | `iceblk` |
| `origin` | `fslab` |
| `cpp_type` | `blockdev_t` |
| C++ sources | `bridges/blockdev.cc`, `bridges/blockdev.h` |
| Target interface | `firechip.bridgeinterfaces.BlockDeviceIO` (+ `BlockDeviceConfig`) |
| Target stub | `firechip.bridgestubs.iceblk.BlockDevBridge` |
| Host model | `firechip.goldengateimplementations.BlockDevBridgeModule` |

## Fixed geometry

These are constants in the model and driver, not parameters:

| Constant | Value |
|---|---|
| Sector size | 512 bytes |
| Data beat width | 64 bits |
| Beats per sector | 64 (`512 × 8 / 64`) |
| Max request length | 16 sectors (`MAX_REQ_LEN`) |

## Ports

Directions are from the target's point of view. The interface is the flattened
form of a `BlockDeviceIO` bundle (`req`, `data`, `resp`, `info`). Tag fields are
`tag_bits` wide; data fields are 64 bits; offset/len/info fields are 32 bits.

**Request channel** (target issues read/write requests)

| `port_map` key | Direction | Width | Meaning |
|---|---|---|---|
| `bdev_req_valid` | output | 1 | Request valid |
| `bdev_req_ready` | input | 1 | Request accepted |
| `bdev_req_bits_write` | output | 1 | 1 = write, 0 = read |
| `bdev_req_bits_offset` | output | 32 | Starting sector offset |
| `bdev_req_bits_len` | output | 32 | Length in sectors |
| `bdev_req_bits_tag` | output | `tag_bits` | Request tag |

**Write-data channel** (target streams write payload)

| `port_map` key | Direction | Width | Meaning |
|---|---|---|---|
| `bdev_data_valid` | output | 1 | Write-data valid |
| `bdev_data_ready` | input | 1 | Write-data accepted |
| `bdev_data_bits_data` | output | 64 | One beat of write data |
| `bdev_data_bits_tag` | output | `tag_bits` | Tag of the owning request |

**Response channel** (bridge returns read data / write acks)

| `port_map` key | Direction | Width | Meaning |
|---|---|---|---|
| `bdev_resp_valid` | input | 1 | Response valid |
| `bdev_resp_ready` | output | 1 | Response accepted |
| `bdev_resp_bits_data` | input | 64 | One beat of read data |
| `bdev_resp_bits_tag` | input | `tag_bits` | Tag of the owning request |

**Info channel** (bridge advertises device geometry)

| `port_map` key | Direction | Width | Meaning |
|---|---|---|---|
| `bdev_info_nsectors` | input | 32 | Number of sectors on the device |
| `bdev_info_max_req_len` | input | 32 | Maximum request length in sectors |

```{note}
When `n_trackers <= 1` the bridge has no meaningful tag space; the wiring
template ties all three tag ports to `0`, so the `*_tag` mappings are present
but unused. Supply more than one tracker to enable in-flight tagging.
```

## Parameters

Both are required (`required_params: [tag_bits, n_trackers]`).

| `params` key | Type | Meaning |
|---|---|---|
| `n_trackers` | int | Number of outstanding-request trackers; backs `BlockDeviceConfig(nTrackers=…)` |
| `tag_bits` | int | Width of the tag ports; should match `log2Up(n_trackers)` |

## Runtime plusargs

Understood by the C++ driver (not declared in the registry `runtime_plusargs`).
Every flag is suffixed with the device number *N* (e.g. `+blkdev0=disk.img`):

| Plusarg | Effect |
|---|---|
| `+blkdev<N>=<image>` | Disk image file backing device *N* (opened read/write, `r+`) |
| `+blkdev-in-mem<N>=<nsectors>` | Back the device from a RAM file of `<nsectors>` sectors instead of a disk image (testing) |
| `+blkdev-rlatency<N>=<cycles>` | Read latency in cycles (default 4096; must be ≤ the model's latency-register limit, `2^latency_bits − 1`) |
| `+blkdev-wlatency<N>=<cycles>` | Write latency in cycles (default 4096; same limit) |
| `+blkdev-log<N>=<file>` | Write a per-device transaction log |

## Driver hooks

- **MMIO struct** `BLOCKDEVBRIDGEMODULE_struct`: request/data/response/write-ack
  register addresses plus timing controls (`read_latency`, `write_latency`),
  device geometry (`bdev_nsectors`, `bdev_max_req_len`), and stall flags.
- **`blockdev_t::init()`** opens the disk image and reports geometry to the model.
- **`blockdev_t::tick()`** drains target requests, performs the corresponding
  `fread`/`fwrite` against the image (one 64-bit beat at a time, `SECTOR_BEATS`
  per sector), and feeds read responses / write acks back. No target time is
  modelled in the driver — the host model stalls the simulation until the driver
  has serviced a scheduled transaction.
- The host model (`BlockDevBridgeModule`) applies a `DynamicLatencyPipe` timing
  model; the read/write latency values (default 4096 cycles, overridable via the
  `+blkdev-rlatency`/`+blkdev-wlatency` plusargs) are pushed to the model by the
  driver's `init()`.
- Bridge type tag: `blockdev_t::KIND`; collected with
  `registry.get_bridges<blockdev_t>()`.

## `fslab.yaml` example

```yaml
bridges:
  - type: "iceblk"
    name: "disk_0"
    port_map:
      bdev_req_valid:        "d0_bdev_req_valid"
      bdev_req_ready:        "d0_bdev_req_ready"
      bdev_req_bits_write:   "d0_bdev_req_bits_write"
      bdev_req_bits_offset:  "d0_bdev_req_bits_offset"
      bdev_req_bits_len:     "d0_bdev_req_bits_len"
      bdev_req_bits_tag:     "d0_bdev_req_bits_tag"
      bdev_data_valid:       "d0_bdev_data_valid"
      bdev_data_ready:       "d0_bdev_data_ready"
      bdev_data_bits_data:   "d0_bdev_data_bits_data"
      bdev_data_bits_tag:    "d0_bdev_data_bits_tag"
      bdev_resp_valid:       "d0_bdev_resp_valid"
      bdev_resp_ready:       "d0_bdev_resp_ready"
      bdev_resp_bits_data:   "d0_bdev_resp_bits_data"
      bdev_resp_bits_tag:    "d0_bdev_resp_bits_tag"
      bdev_info_nsectors:    "d0_bdev_info_nsectors"
      bdev_info_max_req_len: "d0_bdev_info_max_req_len"
    params:
      tag_bits: 1
      n_trackers: 1
```

Run the simulation with a disk image, e.g. `+blkdev=disk.img`.

## See also

- {doc}`/developer/bridges/concepts` — token flow, MMIO, and DMA (`pull`/`push`).
- {doc}`/developer/bridges/registry-yaml` — meaning of each registry field.
