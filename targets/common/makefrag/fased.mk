# =============================================================================
#  fased.mk — memo fragment for FASED memory model
#
#  FASED's C++ driver (fased.cc) is compiled automatically by firesim-lib;
#  you do NOT add it to DRIVER_CC.  This fragment exists only to:
#    1. Document that FASED is in use for this target
#    2. Add the FASED GoldenGate BridgeModule Scala to the symlink hook
#       (in case you have a custom FASED variant in common)
#    3. Provide a place for FASED-specific plusargs documentation
#
#  For the standard FASED model from firesim-lib, just instantiate
#  FASEDBridge in your Chisel top — nothing else is needed here.
# =============================================================================

ifndef COMMON_FASED_MK
COMMON_FASED_MK := 1

# Uncomment if you add a custom FASED variant under common/goldengateimplementations:
# COMMON_GG_SCALA_DIRS += $(COMMON_BASE_DIR)/src/main/goldengateimplementations/fased

# Runtime plusargs (pass via ARGS= on the make command line):
#   +dramsim          → enable DRAMSim2 timing model
#   +fased-init-depth → set initial DRAM row buffer fill depth
#   +mm-unified-latency=<n> → simple unified latency model

endif