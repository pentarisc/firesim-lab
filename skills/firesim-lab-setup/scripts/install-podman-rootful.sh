#!/usr/bin/env bash
# install-podman-rootful.sh — Setup S1, Podman path (Linux only).
#
# Installs Podman + podman-compose and configures ROOTFUL access without
# requiring `sudo` on every firesim-lab invocation: enables the system-wide
# podman.socket, creates a `podman` group whose members can reach it, and
# exports CONTAINER_HOST so the plain `podman` CLI talks to the rootful
# backend instead of defaulting to a per-user rootless one (which firesim-lab
# does not yet support — see docs/prompts/skill-requirements.md §3.1).
#
# This is a HOST-side script — it runs before any firesim-lab container
# exists, so it does NOT source detect-context.sh / use fslab_exec like the
# AWS provisioning scripts do.
#
# Idempotent: safe to re-run. Requires sudo (package install, systemd units,
# group creation) — the caller should confirm with the user before running,
# per this skill's per-step-confirm rule.
#
# IMPORTANT: group membership (`usermod -aG podman`) only takes effect in a
# NEW login session. This script cannot force that — it prints the required
# next step and exits; re-run `firesim-lab --status` (or this skill's S1
# probe) after logging back in to confirm rootful access.
#
# Usage:  install-podman-rootful.sh

set -euo pipefail

_bold()  { printf '\033[1m%s\033[0m' "$*"; }
_green() { printf '\033[32m%s\033[0m' "$*"; }
_cyan()  { printf '\033[36m%s\033[0m' "$*"; }
_red()   { printf '\033[31m%s\033[0m' "$*"; }

if ! command -v apt-get &>/dev/null; then
  echo "$(_red "Error:") this script only automates the Debian/Ubuntu (apt) path."
  echo "On Fedora/RHEL: sudo dnf install podman podman-compose"
  echo "On Arch:        sudo pacman -S podman podman-compose"
  echo "Then re-run the rootful-socket steps below by hand (see"
  echo "docs/portal/setup/host-prerequisites.md)."
  exit 1
fi

echo "$(_cyan "→ Installing podman + podman-compose (apt)...")"
sudo apt-get update -qq
sudo apt-get install -y podman podman-compose
echo "  $(_green "✓") Installed: $(podman --version)"

echo "$(_cyan "→ Enabling the rootful podman.socket...")"
sudo systemctl enable --now podman.socket

echo "$(_cyan "→ Creating the 'podman' group and adding $USER to it...")"
sudo groupadd -f podman
sudo usermod -aG podman "$USER"

echo "$(_cyan "→ Configuring the socket to be group-accessible...")"
sudo mkdir -p /etc/systemd/system/podman.socket.d
printf '[Socket]\nSocketGroup=podman\nSocketMode=0660\n' \
  | sudo tee /etc/systemd/system/podman.socket.d/override.conf > /dev/null
sudo systemctl daemon-reload
sudo systemctl restart podman.socket

RC_LINE='export CONTAINER_HOST=unix:///run/podman/podman.sock'
RC_FILE="${HOME}/.bashrc"
if ! grep -qF "$RC_LINE" "$RC_FILE" 2>/dev/null; then
  echo "$(_cyan "→ Adding CONTAINER_HOST to $RC_FILE...")"
  printf '%s\n' "$RC_LINE" >> "$RC_FILE"
else
  echo "  $(_green "✓") CONTAINER_HOST already set in $RC_FILE"
fi

echo ""
echo "  $(_bold "Podman installed and configured for rootful access.")"
echo ""
echo "  $(_red "Action required:") log out and back in (so the new 'podman'"
echo "  group membership takes effect), then verify with:"
echo ""
echo "    $(_cyan "podman info --format '{{.Host.Security.Rootless}}'")   # expect: false"
echo ""
