# =============================================================================
#  common_base.mk
#  Must be included before any other common bridge fragment.
#  Sets COMMON_BASE_DIR, COMMON_CC_DIR, COMMON_GG_SCALA_DIR.
# =============================================================================

# Absolute path to targets/common/ regardless of where make is invoked from
COMMON_BASE_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
COMMON_CC_DIR   := $(COMMON_BASE_DIR)/src/main/cc
COMMON_GG_SCALA := $(COMMON_BASE_DIR)/src/main/goldengateimplementations/scala

# Accumulator variables — appended to by each bridge fragment below.
# Each target's config.mk passes these to TARGET_COPY_TO_MIDAS_SCALA_DIRS.
COMMON_DRIVER_CC      :=
COMMON_CXX_FLAGS      := -I$(COMMON_CC_DIR)/bridges
COMMON_GG_SCALA_DIRS  := $(COMMON_GG_SCALA)