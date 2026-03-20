#!/usr/bin/env bash
# =============================================================================
#  build-examples.sh — firesim-lab example builder
#
#  Called during `docker build` to:
#    1. Elaborate each example (Chisel → FIRRTL → GoldenGate Verilog)
#    2. Compile each example with Verilator (warms ccache)
#
#  Must be run with FIRESIM_ROOT and FIRESIM_LAB_ROOT set.
#  Outputs land in <example>/generated-src/ inside the image layer.
#
#  Exit behaviour:
#    Any failed example prints a warning but does NOT abort the image build
#    (set -e is intentionally absent).  A broken example should not prevent
#    the image from being usable for other targets.
# =============================================================================

set -uo pipefail

FIRESIM_ROOT="${FIRESIM_ROOT:-/firesim}"
FIRESIM_LAB_ROOT="${FIRESIM_LAB_ROOT:-/firesim-lab}"
EXAMPLES_DIR="${FIRESIM_LAB_ROOT}/examples"
SBT_OPTS_BUILD="-Xmx6g -Xss8m -Dsbt.supershell=false"

# Colours
_bold()  { printf '\033[1m%s\033[0m' "$*"; }
_green() { printf '\033[32m%s\033[0m' "$*"; }
_yellow(){ printf '\033[33m%s\033[0m' "$*"; }
_red()   { printf '\033[31m%s\033[0m' "$*"; }
_cyan()  { printf '\033[36m%s\033[0m' "$*"; }

echo ""
echo "  $(_bold "firesim-lab: building examples")"
echo "  FIRESIM_ROOT     = ${FIRESIM_ROOT}"
echo "  FIRESIM_LAB_ROOT = ${FIRESIM_LAB_ROOT}"
echo "  EXAMPLES_DIR     = ${EXAMPLES_DIR}"
echo ""

PASS=()
FAIL=()

build_example() {
  local name="$1"
  local dir="${EXAMPLES_DIR}/${name}"
  local generated="${dir}/generated-src"

  echo "──────────────────────────────────────────────────────────────"
  echo "  $(_cyan "Example:") $(_bold "$name")"
  echo "──────────────────────────────────────────────────────────────"

  if [[ ! -d "$dir" ]]; then
    echo "  $(_yellow "SKIP") directory not found: $dir"
    return
  fi

  mkdir -p "$generated"

  # Each example's Makefile already has FIRESIM_ROOT, FIRESIM_LAB_ROOT and
  # GENERATED_DIR wired correctly.  We override SBT_COMMAND here so it
  # points at the example's own build.sbt (the ProjectRef root).
  local make_cmd="make -C ${dir} \
    GENERATED_DIR=${generated} \
    FIRESIM_ROOT=${FIRESIM_ROOT} \
    FIRESIM_LAB_ROOT=${FIRESIM_LAB_ROOT}"

  export SBT_COMMAND="sbt --rootdir ${dir} ${SBT_OPTS_BUILD}"

  # ── Step 1: elaborate ─────────────────────────────────────────────────────
  echo "  $(_cyan "→ elaborating ${name}...")"
  if $make_cmd elaborate; then
    echo "  $(_green "✓ elaborate OK")"
  else
    echo "  $(_red "✗ elaborate FAILED for ${name} — skipping verilator step")"
    FAIL+=("${name}:elaborate")
    return
  fi

  # ── Step 2: verilator compile (warms ccache) ──────────────────────────────
  echo "  $(_cyan "→ compiling with Verilator (warming ccache)...")"
  if $make_cmd verilator; then
    echo "  $(_green "✓ verilator OK")"
    PASS+=("$name")
  else
    echo "  $(_red "✗ verilator FAILED for ${name}")"
    FAIL+=("${name}:verilator")
  fi

  echo ""
}

# ── Build each example ────────────────────────────────────────────────────────
build_example "chisel-baremetal"
build_example "verilog-blackbox"

# ── Print ccache statistics ───────────────────────────────────────────────────
if command -v ccache &>/dev/null; then
  echo ""
  echo "  $(_bold "ccache statistics after example builds:")"
  ccache --show-stats | grep -E "cache hit|cache miss|files in cache|cache size" \
    | sed 's/^/    /'
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "──────────────────────────────────────────────────────────────"
echo "  $(_bold "Build summary")"
echo "──────────────────────────────────────────────────────────────"
for name in "${PASS[@]:-}"; do
  [[ -n "$name" ]] && echo "  $(_green "✓") $name"
done
for name in "${FAIL[@]:-}"; do
  [[ -n "$name" ]] && echo "  $(_red "✗") $name"
done
echo ""

# Report failures but exit 0 so docker build continues
if [[ ${#FAIL[@]} -gt 0 ]]; then
  echo "  $(_yellow "WARNING:") ${#FAIL[@]} example(s) failed."
  echo "  The image is still usable; failed examples may need inspection."
else
  echo "  $(_green "All examples built successfully.")"
  echo "  Verilator ccache is warm — first user compilation will be fast."
fi
echo ""
