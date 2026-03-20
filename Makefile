# =============================================================================
#  firesim-lab/Makefile
#
#  Usage:
#    make TARGET=my-baremetal verilator
#    make TARGET=my-baremetal run
#    make TARGET=my-baremetal run-debug
#    make TARGET=my-baremetal elaborate
#    make TARGET=my-baremetal clean
#
#  Add new targets by adding a block in the "Target registry" section below.
# =============================================================================

# ---------------------------------------------------------------------------
#  Paths
# ---------------------------------------------------------------------------
FIRESIM_LAB_ROOT  := $(shell pwd)
FIRESIM_ROOT      ?= /firesim
FIRESIM_SIM       := $(FIRESIM_ROOT)/sim
GENERATED_BASE    := $(FIRESIM_LAB_ROOT)/generated-src

# ---------------------------------------------------------------------------
#  Target registry
#  Each entry sets: DESIGN, TARGET_CONFIG, PLATFORM_CONFIG, SBT_PROJECT,
#  GENERATOR_PACKAGE, and MAKEFRAG_DIR for the selected TARGET=
# ---------------------------------------------------------------------------

ifeq ($(TARGET),my-baremetal)
  DESIGN           := MyTargetTop
  TARGET_CONFIG    := customtarget.MyTargetConfig
  PLATFORM_CONFIG  := customtarget.MyVerilatorConfig
  SBT_PROJECT      := myBaremetal
  GENERATOR_PACKAGE := customtarget
  MAKEFRAG_DIR     := $(FIRESIM_LAB_ROOT)/targets/my-baremetal/makefrag

# ── Add new targets below ────────────────────────────────────────────────────
# else ifeq ($(TARGET),my-second-target)
#   DESIGN           := SecondTargetTop
#   TARGET_CONFIG    := secondtarget.SecondTargetConfig
#   PLATFORM_CONFIG  := secondtarget.SecondVerilatorConfig
#   SBT_PROJECT      := mySecondTarget
#   GENERATOR_PACKAGE := secondtarget
#   MAKEFRAG_DIR     := $(FIRESIM_LAB_ROOT)/targets/my-second-target/makefrag

else
  $(error "Unknown TARGET='$(TARGET)'. Set TARGET=<name> from the registry above.")
endif

# ---------------------------------------------------------------------------
#  Generated output directory for this specific design
#  Redirected onto the mounted volume, not into the firesim image.
# ---------------------------------------------------------------------------
GENERATED_DIR := $(GENERATED_BASE)/$(TARGET)

# ---------------------------------------------------------------------------
#  SBT override: launch sbt with OUR build root, not firesim/sim/
#  This is what makes Option A work — firesim's Makefile calls $(SBT) but
#  we intercept it to point at our build.sbt instead.
# ---------------------------------------------------------------------------
export SBT_COMMAND := sbt \
  "--rootdir $(FIRESIM_LAB_ROOT)" \
  -Dsbt.supershell=false \
  -Xmx4g -Xss8m

# ---------------------------------------------------------------------------
#  FireSim make passthrough
#  All targets not listed here fall through to firesim/sim/Makefile directly.
# ---------------------------------------------------------------------------
FIRESIM_MAKE := $(MAKE) -C $(FIRESIM_SIM) \
  TARGET_PROJECT_MAKEFRAG=$(MAKEFRAG_DIR) \
  DESIGN=$(DESIGN) \
  TARGET_CONFIG=$(TARGET_CONFIG) \
  PLATFORM_CONFIG=$(PLATFORM_CONFIG) \
  SBT_PROJECT=$(SBT_PROJECT) \
  GENERATOR_PACKAGE=$(GENERATOR_PACKAGE) \
  GENERATED_DIR=$(GENERATED_DIR)

.PHONY: elaborate verilator vcs run run-debug run-vcs run-vcs-debug clean help

elaborate:
	$(FIRESIM_MAKE) elaborate

verilator:
	$(FIRESIM_MAKE) verilator

vcs:
	$(FIRESIM_MAKE) vcs

run:
	$(FIRESIM_MAKE) run-verilator

run-debug:
	$(FIRESIM_MAKE) run-verilator-debug

run-vcs:
	$(FIRESIM_MAKE) run-vcs

run-vcs-debug:
	$(FIRESIM_MAKE) run-vcs-debug

clean:
	$(FIRESIM_MAKE) clean
	rm -rf $(GENERATED_DIR)

help:
	@echo "Usage: make TARGET=<name> <goal>"
	@echo ""
	@echo "Registered targets:"
	@echo "  my-baremetal"
	@echo ""
	@echo "Goals: elaborate | verilator | vcs | run | run-debug | run-vcs | clean"