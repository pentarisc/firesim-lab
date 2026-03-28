# =============================================================================
#  uart.mk — opt-in UART bridge fragment
#  Include this in your target's driver.mk to get UART bridge C++ support.
#
#  Usage in driver.mk:
#    include $(COMMON_MAKEFRAG)/uart.mk
# =============================================================================

# Guard: only include once
ifndef COMMON_UART_MK
COMMON_UART_MK := 1

COMMON_DRIVER_CC  += $(COMMON_CC_DIR)/bridges/uart.cc
COMMON_CXX_FLAGS  += -DENABLE_UART_BRIDGE

endif