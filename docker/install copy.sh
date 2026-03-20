#!/usr/bin/env bash
# =============================================================================
#  install.sh — firesim-lab bootstrap installer
#
#  This is the ONLY script that is curl-piped.  It:
#    1. Picks an install directory  (~/.firesim-lab or cwd)
#    2. Downloads run.sh and docker-compose.yaml from the project repo
#    3. Makes run.sh executable
#    4. Prints instructions — then stops.  No Docker interaction here.
#
#  The user then runs ./run.sh interactively.  This mirrors how Homebrew,
#  rustup, and nvm work: the curl-piped script is a thin installer only.
#
#  Usage (what users run):
#    curl -sSL https://github.com/pentarisc/firesim-lab/main/install.sh | bash
#
#  To install into a specific directory:
#    curl -sSL .../install.sh | INSTALL_DIR=/opt/firesim-lab bash
# =============================================================================

set -euo pipefail

# ── Configurable via environment ──────────────────────────────────────────────
VERSION="${VERSION:-main}"
REPO_RAW="https://github.com/pentarisc/firesim-lab/${VERSION}"
INSTALL_DIR="${INSTALL_DIR:-${HOME}/.firesim-lab}"

# Files to download from the repo
FILES=(
  "run.sh"
  "docker/docker-compose.yaml"
  "docker/Dockerfile"
)

# ── Colour helpers (safe — fall back silently if terminal has no colours) ─────
_bold()   { printf '\033[1m%s\033[0m'  "$*"; }
_green()  { printf '\033[32m%s\033[0m' "$*"; }
_cyan()   { printf '\033[36m%s\033[0m' "$*"; }
_yellow() { printf '\033[33m%s\033[0m' "$*"; }
_red()    { printf '\033[31m%s\033[0m' "$*"; }

# ── Dependency check ──────────────────────────────────────────────────────────
need() {
  command -v "$1" &>/dev/null || {
    echo "$(_red "Error:") '$1' is required but not installed."
    exit 1
  }
}
need curl
need docker

# ── Download helper ───────────────────────────────────────────────────────────
download() {
  local url="$1"
  local dest="$2"
  if ! curl -fsSL "$url" -o "$dest"; then
    echo "$(_red "Error:") Failed to download $url"
    exit 1
  fi
}

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo "  $(_bold "firesim-lab installer")"
echo "  ─────────────────────────────────"
echo ""

# ── Create install directory ──────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"
echo "  $(_cyan "→ Install directory:") $INSTALL_DIR"
echo ""

# ── Download files ────────────────────────────────────────────────────────────
for file in "${FILES[@]}"; do
  url="${REPO_RAW}/${file}"
  dest="${INSTALL_DIR}/${file}"
  echo "  $(_cyan "→ Downloading") $file ..."
  download "$url" "$dest"
  echo "     $(_green "✓") $dest"
done

# Make run.sh executable
chmod +x "${INSTALL_DIR}/run.sh"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "  $(_green "✓  Installation complete.")"
echo ""
echo "  $(_bold "Next step — run the launcher:")"
echo ""
echo "    $(_cyan "cd ${INSTALL_DIR}")"
echo "    $(_cyan "./run.sh")"
echo ""
echo "  The launcher will prompt you for your target project directory"
echo "  and then start the firesim-lab Docker container."
echo ""
echo "  $(_bold "To re-run the launcher any time:")"
echo "    ${INSTALL_DIR}/run.sh"
echo ""
