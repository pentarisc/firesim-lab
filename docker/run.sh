#!/usr/bin/env bash
# =============================================================================
#  run.sh — firesim-lab container launcher
#
#  Run this script on your local desktop to start the firesim-lab simulation
#  environment.  It will:
#    1. Collect configuration from you interactively (or from env vars)
#    2. Validate and create required directories
#    3. Write a .env file consumed by docker-compose
#    4. Download docker-compose.yaml from the project repo (if not local)
#    5. Start the container with all volumes mounted
#
#  Usage:
#    ./run.sh                     # fully interactive
#    ./run.sh --help              # show usage
#
#  Non-interactive (CI / scripted):
#    HOST_TARGET_DIR=/home/user/my-baremetal ./run.sh
#
#  Re-enter a running container:
#    docker exec -it firesim-lab bash
# =============================================================================

set -euo pipefail

# ── Pipe-detection guard ──────────────────────────────────────────────────────
# run.sh uses interactive `read` prompts and must be run from a local file.
# If stdin is not a terminal (i.e. being piped) the prompts receive EOF and
# silently fail.  Direct users to install.sh instead.
#
# Exception: --help, --down, --pull, --clean-cache do not prompt and are
# safe to run non-interactively (e.g. from CI or a wrapper script).
_first_arg="${1:-}"
_non_interactive_safe=0
case "$_first_arg" in
  -h|--help|--down|--pull|--clean-cache) _non_interactive_safe=1 ;;
esac

if [[ ! -t 0 && $_non_interactive_safe -eq 0 ]]; then
  cat >&2 <<PIPE_ERR

  ERROR: run.sh cannot be piped directly.

  run.sh uses interactive prompts and must be run from a local file,
  not piped from curl.  The correct entry point is install.sh:

    curl -sSL https://raw.githubusercontent.com/pentarisc/firesim-lab/main/install.sh | bash

  install.sh downloads run.sh to ~/.firesim-lab/ and tells you what to do next.

PIPE_ERR
  exit 1
fi

# ── Configuration ─────────────────────────────────────────────────────────────

REPO_RAW="https://raw.githubusercontent.com/pentarisc/firesim-lab/main"

# URL of the docker-compose.yaml — downloaded if not already local.
COMPOSE_URL="${REPO_RAW}/docker-compose.yaml"

# Default image name — override via FIRESIM_IMAGE env var or interactive prompt
DEFAULT_IMAGE="firesim-lab:latest"

# Default container name
DEFAULT_CONTAINER="firesim-lab"

# Default Verilator thread count (set to number of physical cores for best perf)
DEFAULT_VERILATOR_THREADS=$(nproc 2>/dev/null || echo 4)

# Default memory limit for the container
DEFAULT_MEMORY_LIMIT="16g"
DEFAULT_MEMORY_RESERVE="8g"

# ── Colour helpers ────────────────────────────────────────────────────────────
_bold()  { printf '\033[1m%s\033[0m' "$*"; }
_green() { printf '\033[32m%s\033[0m' "$*"; }
_cyan()  { printf '\033[36m%s\033[0m' "$*"; }
_yellow(){ printf '\033[33m%s\033[0m' "$*"; }
_red()   { printf '\033[31m%s\033[0m' "$*"; }

# ── Help ──────────────────────────────────────────────────────────────────────
usage() {
  cat <<USAGE

  $(_bold "firesim-lab run.sh")

  Starts the firesim-lab Docker container with all project volumes mounted.

  $(_bold "Usage:")
    ./run.sh [OPTIONS]

  $(_bold "Options:")
    -h, --help          Show this message and exit
    --down              Stop and remove the running container, then exit
    --pull              Pull the latest image before starting
    --clean-cache       Remove all named SBT/ccache volumes (forces re-download)

  $(_bold "Non-interactive mode — set these environment variables before running:")
    HOST_TARGET_DIR     Absolute path to your target project on the host
    FIRESIM_IMAGE       Docker image name:tag            [default: $DEFAULT_IMAGE]
    CONTAINER_NAME      Name to give the container       [default: $DEFAULT_CONTAINER]
    VERILATOR_THREADS   Verilator parallel compile jobs  [default: $DEFAULT_VERILATOR_THREADS]
    CONTAINER_MEMORY_LIMIT    Hard memory limit          [default: $DEFAULT_MEMORY_LIMIT]
    CONTAINER_MEMORY_RESERVE  Soft memory reservation    [default: $DEFAULT_MEMORY_RESERVE]

  $(_bold "Examples:")
    # Interactive (will prompt for all inputs)
    ./run.sh

    # Non-interactive
    HOST_TARGET_DIR=/home/alice/my-baremetal ./run.sh

    # Stop the container
    ./run.sh --down

    # Pull a fresh image and restart
    ./run.sh --pull

USAGE
}

# ── Parse arguments ───────────────────────────────────────────────────────────
DO_DOWN=0
DO_PULL=0
DO_CLEAN_CACHE=0

for arg in "$@"; do
  case "$arg" in
    -h|--help)        usage; exit 0 ;;
    --down)           DO_DOWN=1 ;;
    --pull)           DO_PULL=1 ;;
    --clean-cache)    DO_CLEAN_CACHE=1 ;;
    *)
      echo "$(_red "Unknown option:") $arg"
      usage
      exit 1
      ;;
  esac
done

# ── Detect docker compose command ─────────────────────────────────────────────
if docker compose version &>/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose &>/dev/null; then
  COMPOSE="docker-compose"
else
  echo "$(_red "Error:") Neither 'docker compose' nor 'docker-compose' found."
  echo "Install Docker Desktop (macOS/Windows) or docker-compose-plugin (Linux)."
  exit 1
fi

# ── Locate docker-compose.yaml ────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yaml"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "$(_cyan "→ docker-compose.yaml not found locally, downloading from:")"
  echo "  $COMPOSE_URL"
  if command -v curl &>/dev/null; then
    curl -fsSL "$COMPOSE_URL" -o "$COMPOSE_FILE"
  elif command -v wget &>/dev/null; then
    wget -qO "$COMPOSE_FILE" "$COMPOSE_URL"
  else
    echo "$(_red "Error:") curl or wget is required to download docker-compose.yaml"
    exit 1
  fi
  echo "$(_green "✓") docker-compose.yaml downloaded"
fi

# ── Handle --clean-cache ──────────────────────────────────────────────────────
if [[ $DO_CLEAN_CACHE -eq 1 ]]; then
  echo "$(_yellow "⚠  Removing all named SBT and ccache volumes...")"
  docker volume rm \
    firesim-lab-sbt-ivy \
    firesim-lab-sbt-coursier \
    firesim-lab-sbt-boot \
    firesim-lab-sbt-global \
    firesim-lab-verilator-ccache \
    2>/dev/null || true
  echo "$(_green "✓") Cache volumes removed. They will be re-seeded from the image on next start."
  exit 0
fi

# ── Handle --down ─────────────────────────────────────────────────────────────
if [[ $DO_DOWN -eq 1 ]]; then
  echo "$(_cyan "→ Stopping firesim-lab container...")"
  # Source .env so compose resolves variables cleanly
  [[ -f "$SCRIPT_DIR/.env" ]] && set -a && source "$SCRIPT_DIR/.env" && set +a
  $COMPOSE -f "$COMPOSE_FILE" down
  echo "$(_green "✓") Container stopped."
  exit 0
fi

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo "  $(_bold "firesim-lab — simulation environment launcher")"
echo "  ───────────────────────────────────────────────"
echo ""

# ── Collect configuration ─────────────────────────────────────────────────────
# Each value: use env var if already set, otherwise prompt interactively.

# Target project directory
if [[ -z "${HOST_TARGET_DIR:-}" ]]; then
  read -e -p "  $(_bold "Target project directory") (absolute path on host): " HOST_TARGET_DIR
fi
HOST_TARGET_DIR="${HOST_TARGET_DIR/#\~/$HOME}"   # expand ~ if entered manually
HOST_TARGET_DIR="$(realpath -m "$HOST_TARGET_DIR")"

# Docker image
if [[ -z "${FIRESIM_IMAGE:-}" ]]; then
  read -e -p "  $(_bold "Docker image name:tag") [$DEFAULT_IMAGE]: " FIRESIM_IMAGE
fi
FIRESIM_IMAGE="${FIRESIM_IMAGE:-$DEFAULT_IMAGE}"

# Container name
if [[ -z "${CONTAINER_NAME:-}" ]]; then
  read -e -p "  $(_bold "Container name") [$DEFAULT_CONTAINER]: " CONTAINER_NAME
fi
CONTAINER_NAME="${CONTAINER_NAME:-$DEFAULT_CONTAINER}"

# Verilator threads
if [[ -z "${VERILATOR_THREADS:-}" ]]; then
  read -e -p "  $(_bold "Verilator parallel jobs") [$DEFAULT_VERILATOR_THREADS]: " VERILATOR_THREADS
fi
VERILATOR_THREADS="${VERILATOR_THREADS:-$DEFAULT_VERILATOR_THREADS}"

# Memory limits
CONTAINER_MEMORY_LIMIT="${CONTAINER_MEMORY_LIMIT:-$DEFAULT_MEMORY_LIMIT}"
CONTAINER_MEMORY_RESERVE="${CONTAINER_MEMORY_RESERVE:-$DEFAULT_MEMORY_RESERVE}"

echo ""

# ── Validate and create directories ───────────────────────────────────────────
echo "$(_cyan "→ Validating directories...")"

errors=0

# Target project directory — must exist OR we offer to create it
if [[ ! -d "$HOST_TARGET_DIR" ]]; then
  echo "  $(_yellow "⚠  Target directory does not exist:") $HOST_TARGET_DIR"
  read -p "     Create it? [Y/n]: " yn
  yn="${yn:-Y}"
  if [[ "$yn" =~ ^[Yy]$ ]]; then
    mkdir -p "$HOST_TARGET_DIR"
    echo "  $(_green "✓") Created: $HOST_TARGET_DIR"
  else
    echo "  $(_red "✗") Target directory is required. Aborting."
    errors=1
  fi
else
  echo "  $(_green "✓") Target directory:  $HOST_TARGET_DIR"
fi

# Create generated-src/ inside the target dir so the bind mount path exists
# before Docker tries to resolve it.  (Docker will create it anyway, but
# pre-creating it avoids a root-owned directory surprise.)
GENERATED_DIR="$HOST_TARGET_DIR/generated-src"
if [[ ! -d "$GENERATED_DIR" ]]; then
  mkdir -p "$GENERATED_DIR"
  echo "  $(_green "✓") Created: $GENERATED_DIR  (build outputs will land here)"
fi

[[ $errors -ne 0 ]] && exit 1

# ── Validate Docker image exists (locally or remotely) ────────────────────────
echo ""
echo "$(_cyan "→ Checking Docker image: $FIRESIM_IMAGE...")"
if ! docker image inspect "$FIRESIM_IMAGE" &>/dev/null; then
  echo "  $(_yellow "⚠  Image not found locally.")"
  if [[ $DO_PULL -eq 1 ]]; then
    echo "  Pulling $FIRESIM_IMAGE..."
    docker pull "$FIRESIM_IMAGE"
  else
    read -p "     Pull it now? [Y/n]: " yn
    yn="${yn:-Y}"
    if [[ "$yn" =~ ^[Yy]$ ]]; then
      docker pull "$FIRESIM_IMAGE"
    else
      echo "  $(_red "✗") Image not available. Build the image first or re-run with --pull."
      exit 1
    fi
  fi
else
  echo "  $(_green "✓") Image found: $FIRESIM_IMAGE"
  if [[ $DO_PULL -eq 1 ]]; then
    echo "  $(_cyan "→ Pulling latest...")"
    docker pull "$FIRESIM_IMAGE"
  fi
fi

# ── Host user identity (for container UID/GID mapping) ───────
HOST_UID=$(id -u)
HOST_GID=$(id -g)

# ── Write .env file ───────────────────────────────────────────────────────────
ENV_FILE="$SCRIPT_DIR/.env"
echo ""
echo "$(_cyan "→ Writing $ENV_FILE...")"

cat > "$ENV_FILE" <<ENV
# ============================================================
# .env — firesim-lab docker-compose variables
# Generated by run.sh on $(date "+%Y-%m-%d %H:%M")
# ============================================================

# ── Docker image and container ────────────────────────────────
FIRESIM_IMAGE=${FIRESIM_IMAGE}
CONTAINER_NAME=${CONTAINER_NAME}

# ── Host paths (bind-mounted into the container) ─────────────
# Your target project directory → /target inside the container
HOST_TARGET_DIR=${HOST_TARGET_DIR}

# ── Host user mapping (avoids permission issues) ─────────────
HOST_UID=${HOST_UID}
HOST_GID=${HOST_GID}

# ── Performance tuning ────────────────────────────────────────
VERILATOR_THREADS=${VERILATOR_THREADS}
CONTAINER_MEMORY_LIMIT=${CONTAINER_MEMORY_LIMIT}
CONTAINER_MEMORY_RESERVE=${CONTAINER_MEMORY_RESERVE}
ENV

echo "  $(_green "✓") .env written"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "  $(_bold "Configuration summary")"
echo "  ─────────────────────────────────────────────────────────"
echo "  Image            : $(_cyan "$FIRESIM_IMAGE")"
echo "  Container name   : $CONTAINER_NAME"
echo "  Target project   : $HOST_TARGET_DIR"
echo "                     → mounted at /target inside container"
echo "  Generated-src    : $GENERATED_DIR"
echo "                     → persists between container restarts"
echo "  Verilator jobs   : $VERILATOR_THREADS"
echo "  Memory limit     : $CONTAINER_MEMORY_LIMIT (reserve: $CONTAINER_MEMORY_RESERVE)"
echo "  SBT caches       : named Docker volumes (persist across restarts)"
echo "  ─────────────────────────────────────────────────────────"
echo ""

# ── Start container ───────────────────────────────────────────────────────────
echo "$(_cyan "→ Starting container...")"
echo ""

$COMPOSE -f "$COMPOSE_FILE" up -d

echo ""
echo "  $(_green "✓  Container '$CONTAINER_NAME' is running.")"
echo ""
echo "  $(_bold "To open a shell inside the container:")"
echo "    docker exec -it $CONTAINER_NAME bash"
echo ""
echo "  $(_bold "Once inside, run your target:")"
echo "    source env.sh"
echo "    make elaborate"
echo "    make verilator"
echo "    make run"
echo ""
echo "  $(_bold "To stop the container:")"
echo "    ./run.sh --down"
echo ""
