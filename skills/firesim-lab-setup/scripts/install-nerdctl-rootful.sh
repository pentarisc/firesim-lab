#!/usr/bin/env bash
# install-nerdctl-rootful.sh — Setup S1, nerdctl path (Linux only).
#
# Downloads the matching-architecture "nerdctl-full" release (bundles
# containerd, buildkit, CNI plugins, and nerdctl itself) and enables the
# containerd + buildkit systemd services. This gives ROOTFUL nerdctl — the
# only mode firesim-lab supports today (see docs/prompts/skill-requirements.md
# §3.1). Unlike Podman, nerdctl's rootful mode requires the invoking process
# to actually be UID 0; there is no non-root socket-group equivalent. This
# script deliberately does NOT configure passwordless sudo — that is a
# security-posture decision the user should make themselves, not something a
# skill silently sets up. After this script, run firesim-lab with `sudo`.
#
# This is a HOST-side script — it runs before any firesim-lab container
# exists, so it does NOT source detect-context.sh / use fslab_exec like the
# AWS provisioning scripts do.
#
# Idempotent: safe to re-run. Requires sudo (installs to /usr/local, enables
# systemd units) — the caller should confirm with the user before running,
# per this skill's per-step-confirm rule.
#
# Usage:  install-nerdctl-rootful.sh

set -euo pipefail

_bold()  { printf '\033[1m%s\033[0m' "$*"; }
_green() { printf '\033[32m%s\033[0m' "$*"; }
_cyan()  { printf '\033[36m%s\033[0m' "$*"; }
_red()   { printf '\033[31m%s\033[0m' "$*"; }

case "$(uname -s)" in
  Linux) ;;
  *) echo "$(_red "Error:") this script only supports Linux (nerdctl-full's bundled"
     echo "containerd/buildkit systemd units are Linux-specific)."
     exit 1 ;;
esac

case "$(uname -m)" in
  x86_64)  ARCH=amd64 ;;
  aarch64) ARCH=arm64 ;;
  *) echo "$(_red "Error:") unsupported architecture $(uname -m)."; exit 1 ;;
esac

echo "$(_cyan "→ Looking up the latest nerdctl release...")"
LATEST="$(curl -fsSL https://api.github.com/repos/containerd/nerdctl/releases/latest \
  | grep -oP '"tag_name":\s*"\K[^"]+')"
if [[ -z "$LATEST" ]]; then
  echo "$(_red "Error:") could not determine the latest nerdctl release (network / GitHub API issue)."
  exit 1
fi
VER="${LATEST#v}"
URL="https://github.com/containerd/nerdctl/releases/download/${LATEST}/nerdctl-full-${VER}-linux-${ARCH}.tar.gz"

echo "$(_cyan "→ Downloading $LATEST ($ARCH)...")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL -o "$TMP/nerdctl-full.tar.gz" "$URL"

echo "$(_cyan "→ Installing to /usr/local...")"
sudo tar Cxzf /usr/local "$TMP/nerdctl-full.tar.gz"
echo "  $(_green "✓") Installed: $(nerdctl --version)"

echo "$(_cyan "→ Enabling containerd + buildkit...")"
sudo systemctl enable --now containerd
sudo systemctl enable --now buildkit 2>/dev/null || true

echo ""
echo "  $(_bold "nerdctl installed (rootful, via containerd).")"
echo ""
echo "  nerdctl's rootful mode requires the invoking process to be UID 0."
echo "  Run firesim-lab with sudo:"
echo ""
echo "    $(_cyan "sudo firesim-lab")"
echo ""
echo "  (Passwordless sudo is a security-posture change this script does not"
echo "  make for you — set it up yourself via visudo if you want it.)"
echo ""
