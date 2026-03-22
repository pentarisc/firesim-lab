// =============================================================================
//  ChiselBaremetalTop.scala — chisel-baremetal example
//
//  Demonstrates a pure-Chisel FireSim target using FASEDBridge and UARTBridge.
//
//  Behaviour:
//    1. Issues a single AXI4 burst read of 8 bytes from address 0x0
//    2. Serialises the returned bytes over UART (8N1, 115200 baud)
//    3. Parks in DONE state — simulation can be ended via +timeout or Ctrl-C
//
//  Pre-load memory at address 0 with your payload via +memloadhex or ELF.
//  The default FASED model fills uninitialised memory with 0xDEADBEEF…
//  so you should see "ﾭ￾" bytes without a payload — useful for verifying
//  that the AXI4 → FASED → UART pipeline is alive.
// =============================================================================

package chiselbaremetal

import chisel3._
import chisel3.util._
import freechips.rocketchip.amba.axi4._
import freechips.rocketchip.config.Parameters
import freechips.rocketchip.diplomacy._
import midas.models.{FASEDTargetKey, AXI4EdgeSummary}
import firesim.bridges.{FASEDBridge, CompleteConfig}
import firechip.bridgestubs.UARTBridge
import firechip.bridgeinterfaces.UARTPortIO

// ---------------------------------------------------------------------------
// UART 8N1 transmitter (pure Chisel)
// ---------------------------------------------------------------------------
class Uart8n1Tx(clkHz: Int = 100_000_000, baud: Int = 115_200) extends Module {
  val io = IO(new Bundle {
    val txByte  = Input(UInt(8.W))
    val start   = Input(Bool())
    val done    = Output(Bool())
    val tx      = Output(Bool())
  })

  val CLKS_PER_BIT = (clkHz / baud).U(32.W)

  val sIdle :: sStart :: sData :: sStop :: Nil = Enum(4)
  val state = RegInit(sIdle)
  val cnt   = RegInit(0.U(32.W))
  val bitN  = RegInit(0.U(3.W))
  val shift = RegInit(0.U(8.W))

  io.tx   := true.B
  io.done := false.B

  switch(state) {
    is(sIdle) {
      io.tx := true.B
      when(io.start) {
        shift := io.txByte
        cnt   := 0.U
        state := sStart
      }
    }
    is(sStart) {
      io.tx := false.B
      when(cnt === CLKS_PER_BIT - 1.U) { cnt := 0.U; bitN := 0.U; state := sData }
      .otherwise { cnt := cnt + 1.U }
    }
    is(sData) {
      io.tx := shift(bitN)
      when(cnt === CLKS_PER_BIT - 1.U) {
        cnt := 0.U
        when(bitN === 7.U) { state := sStop }
        .otherwise         { bitN := bitN + 1.U }
      } .otherwise { cnt := cnt + 1.U }
    }
    is(sStop) {
      io.tx := true.B
      when(cnt === CLKS_PER_BIT - 1.U) { io.done := true.B; cnt := 0.U; state := sIdle }
      .otherwise { cnt := cnt + 1.U }
    }
  }
}

// ---------------------------------------------------------------------------
// Top-level target module
// ---------------------------------------------------------------------------
class ChiselBaremetalTop(implicit p: Parameters) extends Module {

  // AXI4 parameters — must match Configs.scala
  val AXI_AW   = 32
  val AXI_DW   = 64
  val AXI_IW   = 4
  val NBYTES   = AXI_DW / 8   // 8 bytes per beat

  // ── AXI4 bundle wired to FASEDBridge ─────────────────────────────────────
  val axi4Params = AXI4BundleParameters(
    addrBits = AXI_AW, dataBits = AXI_DW, idBits = AXI_IW)
  val mem = Wire(new AXI4Bundle(axi4Params))

  // Unused optional fields
  mem.aw.bits.user := DontCare
  mem.ar.bits.user := DontCare
  mem.w.bits.user  := DontCare

  // ── State machine ─────────────────────────────────────────────────────────
  val sIdle    :: sAR :: sR :: sTxLoad :: sTxWait :: sDone :: Nil = Enum(6)
  val state    = RegInit(sIdle)
  val dataBuf  = RegInit(0.U(AXI_DW.W))
  val byteIdx  = RegInit(0.U(4.W))

  // AXI4 AR channel registers
  val arValid  = RegInit(false.B)
  val rReady   = RegInit(false.B)

  // Default tie-offs for write channels (read-only master)
  mem.aw.valid        := false.B
  mem.aw.bits         := 0.U.asTypeOf(mem.aw.bits)
  mem.w.valid         := false.B
  mem.w.bits          := 0.U.asTypeOf(mem.w.bits)
  mem.b.ready         := true.B    // absorb any spurious B beats

  // AR channel
  mem.ar.valid        := arValid
  mem.ar.bits.id      := 0.U
  mem.ar.bits.addr    := 0.U        // read from address 0
  mem.ar.bits.len     := 0.U        // single beat
  mem.ar.bits.size    := 3.U        // log2(8 bytes)
  mem.ar.bits.burst   := 1.U        // INCR
  mem.ar.bits.lock    := 0.U
  mem.ar.bits.cache   := 0.U
  mem.ar.bits.prot    := 0.U
  mem.ar.bits.qos     := 0.U

  // R channel
  mem.r.ready         := rReady

  // ── UART TX ───────────────────────────────────────────────────────────────
  val uartTx  = Module(new Uart8n1Tx)
  val txStart = WireDefault(false.B)
  val txByte  = WireDefault(0.U(8.W))

  uartTx.io.start  := txStart
  uartTx.io.txByte := txByte

  // ── State transitions ─────────────────────────────────────────────────────
  switch(state) {
    is(sIdle) {
      arValid := true.B
      state   := sAR
    }
    is(sAR) {
      when(mem.ar.valid && mem.ar.ready) {
        arValid := false.B
        rReady  := true.B
        state   := sR
      }
    }
    is(sR) {
      when(mem.r.valid && mem.r.ready) {
        dataBuf := mem.r.bits.data
        rReady  := false.B
        byteIdx := 0.U
        state   := sTxLoad
      }
    }
    is(sTxLoad) {
      when(byteIdx < NBYTES.U) {
        // Little-endian: byte 0 → bits [7:0]
        txByte  := dataBuf(byteIdx * 8.U + 7.U, byteIdx * 8.U)
        txStart := true.B
        state   := sTxWait
      } .otherwise {
        state := sDone
      }
    }
    is(sTxWait) {
      when(uartTx.io.done) {
        byteIdx := byteIdx + 1.U
        state   := sTxLoad
      }
    }
    is(sDone) { /* park */ }
  }

  // ── FASEDBridge ───────────────────────────────────────────────────────────
  FASEDBridge(clock, mem, reset.asBool,
    CompleteConfig(
      p(FASEDTargetKey),
      Some(AXI4EdgeSummary(
        AXI4MasterParameters(
          name      = "ChiselBaremetal-mem",
          id        = IdRange(0, 1 << AXI_IW),
          aligned   = true,
          maxFlight = Some(1)
        ),
        AXI4SlaveParameters(
          address       = Seq(AddressSet(0x00000000L, 0xFFFFFFFFL)),
          regionType    = RegionType.UNCACHED,
          executable    = true,
          supportsRead  = TransferSizes(1, NBYTES),
          supportsWrite = TransferSizes(1, NBYTES)
        )
      ))
    )
  )

  // ── UARTBridge ────────────────────────────────────────────────────────────
  val uart = Wire(new UARTPortIO)
  uart.txd := uartTx.io.tx
  // rxd unused in this example — UART bridge drives it idle-high
  UARTBridge(clock, uart, reset.asBool, UARTBridgeParams())
}
