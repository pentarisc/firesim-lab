#!/usr/bin/env bash
# fslab-lab EC2 build-host bootstrap (F2 / aws-fpga-firesim-f2)
#
# Run by Ec2LaunchBuildHostProvider after a fresh platform-HDK upload.
# Purpose: verify the HDK can be sourced and Vivado is reachable, so a
# fundamentally broken environment surfaces before the long bitstream
# build starts. Side-effect-free apart from stdout/stderr.
#
# Invocation (Python side):
#   host.put(local_bootstrap_path, "/tmp/firesim-lab-bootstrap.sh")
#   host.run("bash /tmp/firesim-lab-bootstrap.sh <remote_platform_path>")
#
# Exit codes:
#   0  all checks passed
#   1  HDK setup script missing or failed to source
#   2  Vivado not on PATH after sourcing the SDK
#
# The script is intentionally idempotent — running it on an already-
# configured instance is a no-op apart from the printed report.
#
# Note on sourcing: hdk_setup.sh / sdk_setup.sh parse their own positional
# parameters (looking for -d/-s/-h). Sourcing them directly from this
# script would leak our $1 (the platform dir) into their argv and trigger
# "Invalid option: <path>". We therefore source them inside `bash -c`,
# which starts a fresh shell with an empty $@. We also `cd` into the
# platform dir first, mirroring the firesim setup pattern — the AWS FPGA
# scripts can be sensitive to $PWD.

set -u

PLATFORM_DIR="${1:-}"
if [[ -z "${PLATFORM_DIR}" ]]; then
  echo "[bootstrap] ERROR: usage: $0 <remote_platform_path>" >&2
  exit 1
fi

echo "[bootstrap] platform dir: ${PLATFORM_DIR}"
echo "[bootstrap] $(uname -srm)  uptime: $(uptime -p 2>/dev/null || echo n/a)"

# --- HDK setup ---------------------------------------------------------
HDK_SETUP="${PLATFORM_DIR}/hdk_setup.sh"
if [[ ! -f "${HDK_SETUP}" ]]; then
  echo "[bootstrap] ERROR: hdk_setup.sh not found at ${HDK_SETUP}" >&2
  exit 1
fi

echo "[bootstrap] sourcing ${HDK_SETUP}"
if ! ( cd "${PLATFORM_DIR}" && bash -c "source ./hdk_setup.sh" ) \
        >/tmp/firesim-lab-bootstrap-hdk.log 2>&1; then
  echo "[bootstrap] ERROR: hdk_setup.sh failed; see /tmp/firesim-lab-bootstrap-hdk.log" >&2
  tail -n 40 /tmp/firesim-lab-bootstrap-hdk.log >&2 || true
  exit 1
fi

# --- SDK setup (optional but customary on AWS FPGA Dev AMIs) -----------
SDK_SETUP="${PLATFORM_DIR}/sdk_setup.sh"
if [[ -f "${SDK_SETUP}" ]]; then
  echo "[bootstrap] sourcing ${SDK_SETUP}"
  if ! ( cd "${PLATFORM_DIR}" && bash -c "source ./sdk_setup.sh" ) \
          >/tmp/firesim-lab-bootstrap-sdk.log 2>&1; then
    echo "[bootstrap] WARN: sdk_setup.sh failed; build may still work if SDK is preinstalled" >&2
    tail -n 20 /tmp/firesim-lab-bootstrap-sdk.log >&2 || true
  fi
else
  echo "[bootstrap] note: ${SDK_SETUP} absent (typical for HDK-only AMIs)"
fi

# --- Vivado presence ---------------------------------------------------
# The vivado check has to run inside the same `bash -c` that sources the
# HDK setup, because env from `bash -c` does not leak back out.
echo "[bootstrap] checking vivado on PATH (after sourcing in subshell)"
VIVADO_VERSION="$(
  cd "${PLATFORM_DIR}" && bash -c '
    source ./hdk_setup.sh >/dev/null 2>&1
    command -v vivado >/dev/null 2>&1 && vivado -version 2>/dev/null | head -n 1
  '
)"
if [[ -z "${VIVADO_VERSION}" ]]; then
  echo "[bootstrap] ERROR: vivado not found after sourcing hdk_setup.sh" >&2
  exit 2
fi
echo "[bootstrap] ${VIVADO_VERSION}"

# --- Disk space probe (advisory) ---------------------------------------
DF_LINE="$(df -BG --output=avail "${PLATFORM_DIR}" 2>/dev/null | tail -n 1 | tr -d ' G')"
if [[ -n "${DF_LINE}" && "${DF_LINE}" -lt 30 ]]; then
  echo "[bootstrap] WARN: only ${DF_LINE}G free on ${PLATFORM_DIR}; build may run out of space" >&2
fi

echo "[bootstrap] OK"
exit 0
