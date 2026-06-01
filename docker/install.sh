#!/usr/bin/env bash
# =============================================================================
# install.sh — firesim-lab bootstrap installer
#
# This is the ONLY script that is curl-piped.  It:
# 1. Picks an install directory (~/.firesim-lab or custom)
# 2. Downloads firesim-lab, docker-compose.yaml, and Dockerfile from the repo
# 3. Makes firesim-lab executable
# 4. Creates a self-contained .aws directory under the install dir.  This is
#    bind-mounted into the container as ~/.aws so the user can run
#    `aws configure` / `aws sso login` inside the container without needing
#    aws-cli on the host and without colliding with any host-side ~/.aws.
# 5. Creates a self-contained .ssh directory under the install dir.  This is
#    bind-mounted into the container as ~/.ssh so SSH-aware tools (ssh, scp,
#    git, rsync, ...) discover keys at the conventional location without
#    colliding with any host-side ~/.ssh.
# 6. Adds the install directory to PATH (via ~/.local/bin symlink or shell rc)
# 7. Prints instructions — then stops (no Docker interaction here)
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

# ── Image tag + contract version resolution ───────────────────────────────────
# The install ref maps to a Docker image tag and a firesim-lab contract version:
#   v0.7.0  → image 0.7.0, version 0.7.0   (release: strip the leading 'v')
#   main    → image latest, version main   (moving dev image)
#   <other> → image <ref>,  version <ref>  (branch / sha)
# Git tags are 'vX.Y.Z'; Docker tags and PEP 440 versions are 'X.Y.Z' (no 'v').
# The leading 'v' is stripped exactly once, here at the boundary.
IMAGE_REPO="pentarisc/firesim-lab"
case "$VERSION" in
  v[0-9]*) IMAGE_TAG="${VERSION#v}"; FIRESIM_LAB_VERSION="${VERSION#v}" ;;
  main)    IMAGE_TAG="latest";       FIRESIM_LAB_VERSION="main" ;;
  *)       IMAGE_TAG="$VERSION";      FIRESIM_LAB_VERSION="$VERSION" ;;
esac
FIRESIM_IMAGE="${IMAGE_REPO}:${IMAGE_TAG}"

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

# ── Optional digest pinning ───────────────────────────────────────────────────
# If this version's GitHub Release published a versions.json asset, prefer its
# immutable image digest (pentarisc/firesim-lab@sha256:...) over the mutable tag
# — Docker tags can be overwritten, digests cannot.  The asset is produced by
# the release CI after the image is built and pushed, so it exists only for
# released tags; for branch/commit installs (e.g. main) there is no Release and
# the fetch 404s, leaving the tag in place (no-op).
#
# Schema (flat JSON, single object):
#   { "version": "0.7.0",
#     "image":   "pentarisc/firesim-lab@sha256:<digest>",
#     "firesim_commit": "<shortsha>" }
_json_get() {
  # Extract a flat top-level string value for key $1 from JSON file $2.
  sed -n 's/.*"'"$1"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$2" | head -n1
}
VERSIONS_FILE="${INSTALL_DIR}/versions.json"
VERSIONS_URL="https://github.com/pentarisc/firesim-lab/releases/download/${VERSION}/versions.json"
if curl -fsSL "$VERSIONS_URL" -o "$VERSIONS_FILE" 2>/dev/null; then
  _digest_image="$(_json_get image "$VERSIONS_FILE")"
  if [[ -n "$_digest_image" ]]; then
    FIRESIM_IMAGE="$_digest_image"
    echo "  $(_green "✓") Pinned to image digest from the release manifest"
  fi
else
  rm -f "$VERSIONS_FILE" 2>/dev/null || true
fi

# ── Write installed manifest ──────────────────────────────────────────────────
# Records what this installation was set up for.  The firesim-lab launcher reads
# this to pin new workspaces to the installed image/version and to detect (and
# refuse) version skew in existing workspaces.
MANIFEST_FILE="${INSTALL_DIR}/.firesim-lab-installed"
cat > "$MANIFEST_FILE" <<MANIFEST
# firesim-lab installed manifest — written by install.sh on $(date "+%Y-%m-%d %H:%M")
# Consumed by the firesim-lab launcher.  Do not edit by hand; re-run install.sh.
FIRESIM_LAB_VERSION=${FIRESIM_LAB_VERSION}
FIRESIM_LAB_REF=${VERSION}
FIRESIM_IMAGE=${FIRESIM_IMAGE}
MANIFEST
echo "  $(_green "✓") Recorded installed version: ${FIRESIM_LAB_VERSION}  (${FIRESIM_IMAGE})"

# ── Create self-contained AWS config directory ───────────────────────────────
# Bind-mounted into the container as ~/.aws (HOME is fixed to /home/firesim-lab
# inside the container, so this path is the same for any host UID).  Keeping
# AWS credentials under the install dir avoids any collision with a host-side
# ~/.aws and means the user does not need aws-cli installed on the host.
#
# Mode 700 matches the standard ~/.aws layout; AWS CLI warns about looser
# permissions on credentials files.  The directory is created empty on first
# install and left untouched on subsequent runs so existing credentials are
# never clobbered.
AWS_DIR="${INSTALL_DIR}/.aws"
if [[ ! -d "$AWS_DIR" ]]; then
  mkdir -p "$AWS_DIR"
  chmod 700 "$AWS_DIR"
  echo "  $(_green "✓") Created AWS config dir: $AWS_DIR (mode 700)"
else
  echo "  $(_green "✓") AWS config dir already present: $AWS_DIR"
fi

# ── Create self-contained SSH key directory ──────────────────────────────────
# Bind-mounted into the container as ~/.ssh so SSH-aware tools (ssh, scp,
# git, rsync, ansible, ec2-instance-connect, ...) discover keys at the
# conventional location.  Kept separate from .aws because SSH keys and AWS
# credentials have different lifecycles, and because OpenSSH refuses to use
# keys whose directory or file permissions are too loose — easier to enforce
# on a dedicated directory than a subdirectory of something else.
#
# Mode 700 satisfies OpenSSH's directory permission requirement.  Individual
# private keys placed here must be chmod 600.  Created empty on first install
# and left untouched on subsequent runs so existing keys are never clobbered.
SSH_DIR="${INSTALL_DIR}/.ssh"
if [[ ! -d "$SSH_DIR" ]]; then
  mkdir -p "$SSH_DIR"
  chmod 700 "$SSH_DIR"
  echo "  $(_green "✓") Created SSH key dir: $SSH_DIR (mode 700)"
else
  echo "  $(_green "✓") SSH key dir already present: $SSH_DIR"
fi

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
echo "  $(_bold "AWS credentials:")"
echo "    Inside the container, run $(_cyan "aws configure sso --use-device-code") or $(_cyan "aws sso login --use-device-code --profile <profile>")."
echo "    Credentials are persisted to $AWS_DIR on the host."
echo "    No aws-cli install is needed on the host."
echo ""
echo "  $(_bold "SSH keys:")"
echo "    Place private keys (e.g. AWS .pem files) in $SSH_DIR on the host."
echo "    They appear as ~/.ssh inside the container."
echo "    Set permissions with $(_cyan "chmod 600 $SSH_DIR/<keyfile>") so OpenSSH accepts them."
echo ""
echo "  $(_bold "If 'firesim-lab' is not found yet, reload your shell first:")"
echo "    $(_cyan "source $(_detect_shell_rc)")"
echo "  $(_bold "or open a new terminal — then run:") $(_cyan "firesim-lab --help")"
echo ""
