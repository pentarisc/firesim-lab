#!/usr/bin/env bash
# detect-context.sh — the ONE container-runtime seam for the firesim-lab skills.
#
# This is the single place the literal runtime name ("docker") may appear
# (spec §3.1, seam 1). Every skill/reference invokes the container exclusively
# through fslab_exec() / fslab_in_dir() below — never by inlining `docker exec …`.
# When multi-runtime support lands, only CONTAINER_RUNTIME resolution changes here.
#
# Source this file, then call:
#   fslab_detect_context                # sets FSLAB_CONTEXT + RUNTIME + FSLAB_CONTAINER
#   fslab_exec '<shell command>'        # run a command in the firesim-lab env
#   fslab_in_dir <proj> '<fslab cmd>'   # cd /target/<proj> && run an fslab command
#
# Usage note for callers: prefer fslab_in_dir for project-scoped fslab calls.

# ---- runtime resolution (the only place a runtime name is hardcoded) ----------
# Read CONTAINER_RUNTIME from the workspace .firesim-lab.env if present, else
# default to docker. The day the launcher writes CONTAINER_RUNTIME, this honors
# it with no other change (spec §3.1, seam 2).
#
# NOTE: the env file is resolved relative to CWD by default. Callers must invoke
# the skills from the workspace root, or export FSLAB_ENV_FILE=<abs path>, so the
# runtime and version cross-check read the right file. (Today's docker default is
# harmless if it isn't found; the version cross-check is the part that matters.)
fslab_resolve_runtime() {
  local env_file="${1:-.firesim-lab.env}"
  local rt=""
  if [ -f "$env_file" ]; then
    rt="$(grep -E '^[[:space:]]*CONTAINER_RUNTIME=' "$env_file" 2>/dev/null \
          | tail -n1 | cut -d= -f2- | tr -d '"' | xargs 2>/dev/null)"
  fi
  RUNTIME="${rt:-docker}"
  export RUNTIME
}

# ---- context detection --------------------------------------------------------
# In-container  => `fslab` is on PATH; call it directly (skip the runtime layer).
# Host          => drive the container via the runtime + firesim-lab-shell (gosu
#                  drops to the host UID with the cache group; never bare exec,
#                  which runs as root and breaks SBT/ccache writes — spec §13 #1).
fslab_detect_context() {
  fslab_resolve_runtime "${FSLAB_ENV_FILE:-.firesim-lab.env}"
  if command -v fslab >/dev/null 2>&1; then
    FSLAB_CONTEXT="in_container"
    FSLAB_CONTAINER=""
  else
    FSLAB_CONTEXT="host"
    fslab_discover_container
  fi
  export FSLAB_CONTEXT FSLAB_CONTAINER
}

# Compose names the container firesim-lab-firesim-lab-<workspace>; match on the
# stable "firesim-lab" prefix. If multiple match, prefer one whose mount maps the
# current workspace; otherwise take the first and let the caller confirm.
fslab_discover_container() {
  FSLAB_CONTAINER="$("$RUNTIME" ps --filter name=firesim-lab --format '{{.Names}}' 2>/dev/null | head -n1)"
  export FSLAB_CONTAINER
}

# ---- the single exec helper ---------------------------------------------------
# fslab_exec '<command run inside the firesim-lab environment>'
fslab_exec() {
  local cmd="$1"
  if [ "$FSLAB_CONTEXT" = "in_container" ]; then
    bash -lc "$cmd"
  else
    if [ -z "$FSLAB_CONTAINER" ]; then
      echo "firesim-lab: no running container found (start it with 'firesim-lab')." >&2
      return 127
    fi
    "$RUNTIME" exec "$FSLAB_CONTAINER" firesim-lab-shell bash -lc "$cmd"
  fi
}

# fslab_in_dir <project> '<fslab subcommand and args>'
fslab_in_dir() {
  local proj="$1"; shift
  fslab_exec "cd /target/${proj} && $*"
}
