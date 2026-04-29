#!/usr/bin/env bash
# =============================================================================
# install.sh — firesim-lab bootstrap installer
#
# This is the ONLY script that is curl-piped.  It:
# 1. Picks an install directory (~/.firesim-lab or custom)
# 2. Downloads firesim-lab, docker-compose.yaml, and Dockerfile from the repo
# 3. Makes firesim-lab executable
# 4. Adds the install directory to PATH (via ~/.local/bin symlink or shell rc)
# 5. Prints instructions — then stops (no Docker interaction here)
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/pentarisc/firesim-lab/main/docker/install.sh | bash
#
# Install specific version (tag/branch):
#   curl -sSL .../install.sh | VERSION=v1.2.0 bash
#
# Or:
#   curl -sSL .../install.sh | bash -s -- v1.2.0
#
# Install into custom directory:
#   curl -sSL .../install.sh | INSTALL_DIR=/opt/firesim-lab bash
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

# The main launcher script and the files it needs alongside it
LAUNCHER_SCRIPT="docker/firesim-lab"

FILES=(
  "docker/firesim-lab"
  "docker/docker-compose.yaml"
  "docker/Dockerfile"
  "docker/entrypoint.sh"
  "docker/firesim-lab-shell"
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

# ── Validate version exists ───────────────────────────────────────────────────
if ! curl -fsSL --head "${REPO_RAW}/${LAUNCHER_SCRIPT}" -o /dev/null 2>/dev/null; then
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
echo "  $(_cyan "→ Version  :") $VERSION"
echo "  $(_cyan "→ Installing to:") $INSTALL_DIR"
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

# ── Make launcher executable ──────────────────────────────────────────────────
LAUNCHER_PATH="${INSTALL_DIR}/${LAUNCHER_SCRIPT}"
chmod +x "$LAUNCHER_PATH"
echo ""
echo "  $(_green "✓") Made executable: $LAUNCHER_PATH"

# ── Add to PATH ───────────────────────────────────────────────────────────────
# Strategy (in order of preference):
#   1. ~/.local/bin  — standard XDG user bin dir, already in PATH on most distros
#   2. ~/bin         — legacy user bin dir, common on older systems
#   3. Append to shell rc as a last resort with a PATH export line
#
# In all cases we create a symlink rather than copying, so updates to the
# install dir are reflected immediately without re-running install.sh.

_symlink_into_dir() {
  local bin_dir="$1"
  mkdir -p "$bin_dir"
  ln -sf "$LAUNCHER_PATH" "$bin_dir/firesim-lab"
  echo "  $(_green "✓") Symlinked: $bin_dir/firesim-lab → $LAUNCHER_PATH"
}

_path_contains() {
  # Returns 0 if the given directory is already on PATH
  [[ ":$PATH:" == *":$1:"* ]]
}

_detect_shell_rc() {
  # Return the most likely rc file for the current shell
  local shell_name
  shell_name="$(basename "${SHELL:-bash}")"
  case "$shell_name" in
    zsh)   echo "${ZDOTDIR:-$HOME}/.zshrc" ;;
    fish)  echo "$HOME/.config/fish/config.fish" ;;
    *)     echo "$HOME/.bashrc" ;;
  esac
}

_append_path_to_rc() {
  local bin_dir="$1"
  local rc_file
  rc_file="$(_detect_shell_rc)"

  local export_line='export PATH="'"$bin_dir"':$PATH"'

  # Don't add it twice
  if grep -qF "$bin_dir" "$rc_file" 2>/dev/null; then
    echo "  $(_yellow "⚠  PATH entry already present in $rc_file — skipping.")"
    return
  fi

  printf '\n# firesim-lab — added by install.sh\n%s\n' "$export_line" >> "$rc_file"
  echo "  $(_green "✓") Added PATH entry to $rc_file"
  echo "  $(_yellow "⚠  Restart your shell or run:") source $rc_file"
}

echo ""
echo "  $(_cyan "→ Adding firesim-lab to PATH...")"

PATH_SETUP_DONE=0

# Try ~/.local/bin first
LOCAL_BIN="$HOME/.local/bin"
if _path_contains "$LOCAL_BIN" || [[ -d "$LOCAL_BIN" ]]; then
  _symlink_into_dir "$LOCAL_BIN"
  PATH_SETUP_DONE=1
fi

# Fall back to ~/bin
if [[ $PATH_SETUP_DONE -eq 0 ]]; then
  USER_BIN="$HOME/bin"
  if _path_contains "$USER_BIN" || [[ -d "$USER_BIN" ]]; then
    _symlink_into_dir "$USER_BIN"
    PATH_SETUP_DONE=1
  fi
fi

# Last resort: create ~/.local/bin and add it to the shell rc
if [[ $PATH_SETUP_DONE -eq 0 ]]; then
  _symlink_into_dir "$LOCAL_BIN"
  _append_path_to_rc "$LOCAL_BIN"
  PATH_SETUP_DONE=1
fi

# ── Verify the symlink will resolve after PATH is set ────────────────────────
# (We can't use `command -v` reliably since PATH may not yet include the new dir
#  in this shell session, so just confirm the symlink target exists.)
if [[ ! -x "$LAUNCHER_PATH" ]]; then
  echo "  $(_red "✗ Launcher not executable:") $LAUNCHER_PATH"
  exit 1
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "  $(_green "✓  Installation complete.")"
echo ""
echo "  $(_bold "Usage — run from any workspace directory:")"
echo ""
echo "    $(_cyan "cd ~/my-workspace")"
echo "    $(_cyan "firesim-lab")"
echo ""
echo "  On first run in a new directory, firesim-lab will prompt you for"
echo "  your project name and settings, then start the Docker container"
echo "  and drop you straight into a shell."
echo ""
echo "  $(_bold "If 'firesim-lab' is not found yet, reload your shell first:")"
echo "    $(_cyan "source $(_detect_shell_rc)")"
echo "  $(_bold "or open a new terminal — then run:") $(_cyan "firesim-lab --help")"
echo ""
