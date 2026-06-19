#!/usr/bin/env bash
# scrape-sso-code.sh — recurring SSO device-code login helper (spec §9.4).
#
# The container is HEADLESS, so login ALWAYS uses `--use-device-code` (a plain
# `aws sso login` would try to open a browser that isn't there).
#
# Split into short, non-blocking actions so the skill can surface the code
# immediately and control the poll cadence itself — a single long-blocking call
# would (a) not stream stdout until it exits, hiding the code, and (b) risk being
# killed by the Bash-tool timeout mid-poll. The skill calls --launch once, shows
# the code, then loops --poll on its own short sleeps.
#
# Actions:
#   --launch        (default) background the device-code login, scrape + print the
#                   verification URL and user code, then EXIT fast.
#   --poll          one completion check; SSO_STATUS=logged_in (exit 0) or
#                   not_logged_in (exit 1). Call in a loop after --launch.
#   --verify-only   one session check (for step 7 / already-logged-in mode):
#                   logged_in (exit 0) or not_logged_in (exit 1).
#
# Usage:  scrape-sso-code.sh <profile> [--launch|--poll|--verify-only] [--timeout <sec>]
#   --timeout bounds only the --launch URL/code scrape wait (default 60).
#
# Sources the shared detect-context.sh (the single container-runtime seam),
# resolved via CLAUDE_PLUGIN_ROOT with a relative fallback for local/dev runs.

set -uo pipefail

PROFILE="${1:?usage: scrape-sso-code.sh <profile> [--launch|--poll|--verify-only] [--timeout <sec>]}"; shift || true
ACTION="launch"
SCRAPE_TIMEOUT=60
while [ $# -gt 0 ]; do
  case "$1" in
    --launch)      ACTION="launch" ;;
    --poll)        ACTION="poll" ;;
    --verify-only) ACTION="verify" ;;
    --timeout)     shift; SCRAPE_TIMEOUT="${1:-60}" ;;
  esac
  shift || true
done

if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "$CLAUDE_PLUGIN_ROOT/skills/firesim-lab-setup/scripts/detect-context.sh" ]; then
  SHARED="$CLAUDE_PLUGIN_ROOT/skills/firesim-lab-setup/scripts/detect-context.sh"
else
  SHARED="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../firesim-lab-setup/scripts" && pwd)/detect-context.sh"
fi
# shellcheck source=../../firesim-lab-setup/scripts/detect-context.sh
source "$SHARED"
fslab_detect_context

is_logged_in() { fslab_exec "aws sts get-caller-identity --profile $PROFILE >/dev/null 2>&1"; }

case "$ACTION" in
  poll|verify)
    if is_logged_in; then echo "SSO_STATUS=logged_in"; exit 0
    else echo "SSO_STATUS=not_logged_in"; exit 1; fi
    ;;
esac

# ---- launch ------------------------------------------------------------------
if is_logged_in; then
  echo "SSO_STATUS=already_valid"
  exit 0
fi

# Background the headless device-code login inside the container; capture output
# to a log the container keeps writing to after this exec returns.
LOG="/tmp/fslab-sso-${PROFILE}.log"
fslab_exec "rm -f $LOG; nohup aws sso login --use-device-code --profile $PROFILE > $LOG 2>&1 & echo started" >/dev/null

# Scrape the verification URL + user code as soon as they appear.
URL=""; CODE=""; OUT=""
deadline=$(( $(date +%s) + SCRAPE_TIMEOUT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  OUT="$(fslab_exec "cat $LOG 2>/dev/null")"
  # Grab the whole URL up to whitespace — the device URL carries a #fragment
  # (e.g. https://…/start/#/device) that a restricted char class would truncate.
  URL="$(printf '%s\n' "$OUT"  | grep -oE 'https://[^[:space:]]+' | head -n1)"
  CODE="$(printf '%s\n' "$OUT" | grep -oE '[A-Z0-9]{4}-[A-Z0-9]{4}' | head -n1)"
  if [ -n "$URL" ] && [ -n "$CODE" ]; then break; fi
  sleep 2
done

if [ -z "$URL" ] || [ -z "$CODE" ]; then
  echo "SSO_STATUS=scrape_failed"
  echo "SSO_LOG_TAIL<<EOF"; printf '%s\n' "$OUT" | tail -n 20; echo "EOF"
  exit 2
fi

# Surface immediately; the skill relays these and then loops --poll to completion.
echo "SSO_VERIFICATION_URL=$URL"
echo "SSO_USER_CODE=$CODE"
echo "SSO_STATUS=awaiting_approval"
exit 0
