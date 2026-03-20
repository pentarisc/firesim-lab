#!/usr/bin/env bash
# =============================================================================
# install.sh — firesim-lab bootstrap installer
#
# This is the ONLY script that is curl-piped.  It:
# 1. Picks an install directory (~/.firesim-lab or cwd)
# 2. Downloads run.sh, docker-compose.yaml, and Dockerfile from the repo
# 3. Makes run.sh executable
# 4. Prints instructions — then stops (no Docker interaction here)
#
# Usage:
# curl -sSL https://raw.githubusercontent.com/pentarisc/firesim-lab/main/install.sh | bash
#
# Install specific version (tag/branch):
# curl -sSL .../install.sh | VERSION=v1.2.0 bash
#
# Or:
# curl -sSL .../install.sh | bash -s -- v1.2.0
#
# Install into custom directory:
# curl -sSL .../install.sh | INSTALL_DIR=/opt/firesim-lab bash
# =============================================================================

set -euo pipefail

# ── Version handling ──────────────────────────────────────────────────────────
# Priority: CLI arg > ENV var > default (main)

if [ $# -ge 1 ]; then
VERSION="$1"
else
VERSION="${VERSION:-main}"
fi

REPO_BASE="https://raw.githubusercontent.com/pentarisc/firesim-lab"
REPO_RAW="${REPO_BASE}/${VERSION}"

# ── Install location ──────────────────────────────────────────────────────────

INSTALL_DIR="${INSTALL_DIR:-${HOME}/.firesim-lab}"

# Files to download from the repo

FILES=(
"docker/run.sh"
"docker/docker-compose.yaml"
"docker/Dockerfile"
)

# ── Colour helpers ────────────────────────────────────────────────────────────

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

# ── Validate version exists (check run.sh) ────────────────────────────────────

if ! curl -fsSL "${REPO_RAW}/docker/run.sh" -o /dev/null; then
echo "$(_red "Error:") Version '$VERSION' not found."
echo "  Check available tags/branches at:"
echo "  ${REPO_BASE}"
exit 1
fi

# ── Banner ────────────────────────────────────────────────────────────────────

echo ""
echo "  $(_bold "firesim-lab installer")"
echo "  ─────────────────────────────────"
echo ""
echo "  $(_cyan "→ Installing version:") $VERSION"
echo "  $(_cyan "→ Install directory:") $INSTALL_DIR"
echo ""

# ── Create install directory ──────────────────────────────────────────────────

mkdir -p "$INSTALL_DIR"

# ── Download files ────────────────────────────────────────────────────────────

for file in "${FILES[@]}"; do
url="${REPO_RAW}/${file}"
dest="${INSTALL_DIR}/${file}"

echo "  $(_cyan "→ Downloading") $file ..."
mkdir -p "$(dirname "$dest")"

download "$url" "$dest"

echo "     $(_green "✓") $dest"
done

# Make run.sh executable

chmod +x "${INSTALL_DIR}/docker/run.sh"

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "  $(_green "✓  Installation complete.")"
echo ""
echo "  $(_bold "Next step — run the launcher:")"
echo ""
echo "    $(_cyan "cd ${INSTALL_DIR}/docker")"
echo "    $(_cyan "./run.sh")"
echo ""
echo "  The launcher will prompt you for your target project directory"
echo "  and then start the firesim-lab Docker container."
echo ""
echo "  $(_bold "To re-run the launcher any time:")"
echo "    ${INSTALL_DIR}/docker/run.sh"
echo ""
