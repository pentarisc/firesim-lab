// See LICENSE for license details

package firechip.bridgestubs.iceblk

import chisel3._
import chisel3.util._

import org.chipsalliance.cde.config.Parameters

import firesim.lib.bridgeutils._

import firechip.bridgeinterfaces._

class BlockDevBridge(bdParams: BlockDeviceConfig) extends BlackBox
    with Bridge[HostPortIO[BlockDevBridgeTargetIO]] {
  val moduleName = "firechip.goldengateimplementations.BlockDevBridgeModule"
  val io = IO(new BlockDevBridgeTargetIO(bdParams))
  val bridgeIO = HostPort(io)
  val constructorArg = Some(bdParams)
  generateAnnotations()
}

object BlockDevBridge  {
  def apply(clock: Clock, blkdevIO: firechip.bridgeinterfaces.BlockDeviceIO, reset: Bool): BlockDevBridge = {
    val ep = Module(new BlockDevBridge(BlockDeviceConfig(blkdevIO.bdParams.nTrackers)))
    // TODO: Check following IOs are same size/names/etc
    ep.io.bdev <> blkdevIO
    ep.io.clock := clock
    ep.io.reset := reset
    ep
  }
}
