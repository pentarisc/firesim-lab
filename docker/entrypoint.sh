#!/bin/bash
# =============================================================================
#  entrypoint.sh — runtime UID/GID remapping for the firesim-lab container
#
#  This script runs as root.  It performs lightweight setup and then drops
#  privileges via gosu before executing the user-supplied command.
#  It never touches /opt/firesim*, /opt/firesim-venv, or the named-volume
#  cache directories with chown — group permissions and the setgid bit handle
#  write access without any per-start file-system scans.
#
#  UID/GID detection
#  ─────────────────
#  HOST_UID and HOST_GID are detected from the ownership of the /target
#  bind mount rather than read from environment variables.  This is more
#  reliable: /target is always owned by the host user who launched the
#  container, so the values are always correct and never missing.
#  No coordination between the run script and the compose environment block
#  is required.
#
#  Environment variables (from docker-compose / Dockerfile):
#    CACHE_GID  — GID of the firesim-lab-cache group baked into the image
#                 (default: 2543; matches the ARG CACHE_GID in the Dockerfile)
#
#  Steps performed:
#    1. Detect HOST_UID and HOST_GID from /target ownership.
#    2. Append an /etc/group entry for HOST_GID (if absent).
#    3. Append an /etc/passwd entry for HOST_UID (if absent) with the
#       pseudo-username 'firesim-lab-user'.  Home directory is set to
#       /home/firesim-lab so getpwuid() and the HOME env var agree —
#       keeping SBT, pip, and ccache pointed at the pre-warmed cache paths.
#       Direct file append is used instead of useradd to avoid shadow-utils
#       side effects (/etc/shadow, /etc/gshadow) that can produce inconsistent
#       state in containers and cause whoami/id to fail.
#    4. Append the pseudo-username to the firesim-lab-cache member list in
#       /etc/group so gosu's initgroups() call grants write access to all
#       2775/setgid cache directories.
#    5. Set umask 002 — inherited by the exec'd process, so new files created
#       in group-writable directories are 664/775 by default.
#    6. exec gosu HOST_UID <command>.  gosu calls initgroups() which reads
#       /etc/group and picks up the firesim-lab-cache membership added above.
# =============================================================================
set -euo pipefail

CACHE_GID="${CACHE_GID:-2543}"
CACHE_HOME="/home/firesim-lab"
# Pseudo-username shown by whoami, id, ls -l, etc. for any host UID that is
# not already present in /etc/passwd (i.e. any UID other than 1000 which is
# the build-time firesim-lab user).
PSEUDO_USER="firesim-lab-user"

# ---------------------------------------------------------------------------
# Step 1 — Detect HOST_UID and HOST_GID from /target ownership.
# /target is the bind mount of the user's workspace directory on the host.
# Its ownership reflects the host user's UID/GID exactly, with no need for
# environment variable plumbing through docker-compose.
# ---------------------------------------------------------------------------
if [[ ! -d /target ]]; then
    echo "[entrypoint] ERROR: /target is not mounted." >&2
    echo "[entrypoint] Ensure HOST_WORKSPACE_DIR is set in .firesim-lab.env" >&2
    echo "[entrypoint] and the bind mount is configured in docker-compose.yaml." >&2
    exit 1
fi

HOST_UID=$(stat -c '%u' /target)
HOST_GID=$(stat -c '%g' /target)

# If /target is owned by root (unusual but possible), warn and proceed —
# the container will run as root which is not ideal but functional.
if [[ "${HOST_UID}" -eq 0 ]]; then
    echo "[entrypoint] WARNING: /target is owned by root (UID 0)." >&2
    echo "[entrypoint] Running as root. For proper UID mapping, ensure" >&2
    echo "[entrypoint] HOST_WORKSPACE_DIR is owned by your host user." >&2
    exec "$@"
fi

# ---------------------------------------------------------------------------
# Step 2 — /etc/group entry for HOST_GID
# Append directly to /etc/group rather than using groupadd to avoid any
# interaction with /etc/gshadow or other shadow-utils side effects.
# ---------------------------------------------------------------------------
if ! getent group "${HOST_GID}" > /dev/null 2>&1; then
    echo "${PSEUDO_USER}:x:${HOST_GID}:" >> /etc/group
fi

# ---------------------------------------------------------------------------
# Step 3 — /etc/passwd entry for HOST_UID
# Append directly to /etc/passwd rather than using useradd.  In a container
# there is no authentication, so a plain passwd entry is all that is needed
# for whoami, id, ls, and getpwuid() to resolve the numeric UID to a name.
# Home directory is set to the fixed cache home so that getpwuid() and the
# HOME env var agree regardless of which UID is running.
# ---------------------------------------------------------------------------
if ! getent passwd "${HOST_UID}" > /dev/null 2>&1; then
    echo "${PSEUDO_USER}:x:${HOST_UID}:${HOST_GID}:FireSim Lab User:${CACHE_HOME}:/bin/bash" >> /etc/passwd
fi

# ---------------------------------------------------------------------------
# Step 4 — Add the host user to firesim-lab-cache (GID CACHE_GID)
# Modify the firesim-lab-cache line in /etc/group directly — no usermod
# needed.  sed appends the pseudo-username to the member list of the group
# line, which gosu's initgroups() call will then pick up.
# ---------------------------------------------------------------------------
if ! grep -q "^firesim-lab-cache:.*${PSEUDO_USER}" /etc/group 2>/dev/null; then
    sed -i "/^firesim-lab-cache:/s/$/,${PSEUDO_USER}/" /etc/group
fi

# ---------------------------------------------------------------------------
# Step 5 — umask 002
# Inherited across exec(), so the child process creates group-writable files
# (664 regular, 775 directories) inside the setgid cache directories.
# ---------------------------------------------------------------------------
umask 002

# ---------------------------------------------------------------------------
# Step 6 — Drop privileges and exec the requested command.
# gosu calls initgroups(), which reads /etc/group and sets all supplementary
# groups including firesim-lab-cache added above.
# ---------------------------------------------------------------------------
if [[ $# -eq 0 ]]; then
    echo "[entrypoint] ERROR: No command specified." >&2
    echo "[entrypoint] Usage: entrypoint.sh <command> [args...]" >&2
    echo "[entrypoint] Example: entrypoint.sh /bin/bash" >&2
    exit 1
fi

echo "[entrypoint] Running as UID=${HOST_UID} GID=${HOST_GID} cache-group=${CACHE_GID}" >&2
exec gosu "${HOST_UID}" "$@"