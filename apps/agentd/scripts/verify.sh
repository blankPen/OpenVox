#!/usr/bin/env bash
# scripts/verify.sh — one-shot acceptance for the agentd daemon.
#
# Usage:
#   ./scripts/verify.sh                 # full run (assume daemon is running)
#   AGENTD_AUTO_START=1 ./scripts/verify.sh   # also start the daemon if needed
#
# Exit code is non-zero on any failed step.

set -euo pipefail

PORT="${AGENTD_PORT:-8787}"
HOST="${AGENTD_HOST:-127.0.0.1}"
BASE="http://${HOST}:${PORT}"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
red()  { printf "\033[31m%s\033[0m\n" "$*"; }
grn()  { printf "\033[32m%s\033[0m\n" "$*"; }
ylw()  { printf "\033[33m%s\033[0m\n" "$*"; }

PASSED=()
FAILED=()

step() {
  local name="$1"; shift
  bold "▶ $name"
  if "$@"; then
    grn "  ✓ $name"
    PASSED+=("$name")
  else
    red "  ✗ $name"
    FAILED+=("$name")
  fi
}

# 1. pnpm install & build
step "pnpm install (skipped if node_modules present)" bash -c "test -d node_modules"
step "pnpm build" bash -c "pnpm build >/dev/null 2>&1"

# 2. spawn daemon if requested
PID_FILE="/tmp/agentd-verify.pid"
if [[ "${AGENTD_AUTO_START:-0}" == "1" ]]; then
  if curl -fsS "$BASE/health" >/dev/null 2>&1; then
    ylw "  daemon already running on $BASE"
  else
    ylw "  spawning pnpm dev in background..."
    ( pnpm dev > /tmp/agentd-verify.log 2>&1 & echo $! > "$PID_FILE" )
    for i in 1 2 3 4 5 6 7 8 9 10; do
      sleep 1
      if curl -fsS "$BASE/health" >/dev/null 2>&1; then break; fi
    done
  fi
  # Make sure to clean up when this script exits.
  trap '[[ -s "$PID_FILE" ]] && kill "$(cat "$PID_FILE")" 2>/dev/null || true' EXIT
fi

# 3. probes
step "GET /health returns 200" bash -c "curl -fsS $BASE/health | grep -q '\"status\":\"ok\"'"
step "GET /v1/models returns ≥1 provider" bash -c "curl -fsS $BASE/v1/models | grep -q '\"id\":\"agentd/'"
step "POST /v1/chat/completions stream returns PONG" bash -c "
  curl -fsS -N -X POST $BASE/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{\"model\":\"agentd/claude\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the single word: PONG\"}],\"stream\":true}' \
    --max-time 90 2>&1 | grep -q 'PONG'
"
step "GET /v1/sessions returns JSON list" bash -c "curl -fsS $BASE/v1/sessions | grep -q '\"object\":\"list\"'"

# 4. pnpm test
step "pnpm test (5 files / 38 tests)" bash -c "pnpm test >/dev/null 2>&1"

# Final summary
bold "=== verify.sh summary ==="
printf "passed: %s\n" "${#PASSED[@]}"
printf "failed: %s\n" "${#FAILED[@]}"
if (( ${#FAILED[@]} > 0 )); then
  printf "failures:\n"
  for f in "${FAILED[@]}"; do printf "  - %s\n" "$f"; done
  exit 1
fi
grn "all checks passed"
