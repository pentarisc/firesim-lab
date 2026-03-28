# =============================================================================
#  blockdev.mk — opt-in BlockDevice bridge fragment
# =============================================================================

ifndef COMMON_BLOCKDEV_MK
COMMON_BLOCKDEV_MK := 1

COMMON_DRIVER_CC  += $(COMMON_CC_DIR)/bridges/blockdev.cc
COMMON_CXX_FLAGS  += -DENABLE_BLOCKDEV_BRIDGE

# BlockDevice needs a backing image path at runtime — pass via ARGS=
# e.g.  make TARGET=my-target run ARGS="+blkdev-in-mem=1"

endif