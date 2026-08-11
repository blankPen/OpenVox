#!/usr/bin/env bash
# install.sh — Bootstrap an openvox development environment on macOS or Linux.
#
# What it does (each step is skipped via --no-* flag):
#   1. Verify platform is macOS or Linux (Windows users: scripts/install.ps1).
#   2. Detect python3.10+ on PATH (override with --python <bin>).
#   3. Create .venv at repo root (idempotent: reuse if present).
#   4. Install openvox_worker + livekit-plugins-volcengine editable with --no-deps.
#   5. flutter pub get for apps/voice-client (skipped if flutter missing or --no-flutter).
#   6. pnpm/npm install + build for apps/agentd (skipped if node missing or --no-node).
#   7. docker compose up -d for infra/ (skipped if docker missing or --no-livekit).
#
# Usage:
#   ./scripts/install.sh                 # full bootstrap
#   ./scripts/install.sh --no-flutter    # skip Flutter / voice-client deps
#   ./scripts/install.sh --no-node       # skip agentd Node deps
#   ./scripts/install.sh --no-livekit    # skip LiveKit Server container
#   ./scripts/install.sh --python /opt/homebrew/bin/python3.11
#
# Notes:
#   - Idempotent: re-running picks up new code without wiping state.
#   - Does NOT modify ~/.openvox or ~/.agentd (those are written by `openvox init`).
#   - Does NOT build release artifacts (use tooling/scripts/build-*.sh for that).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---- Colors (auto-disabled when stdout is not a TTY) ----
if [[ -t 1 ]]; then
  C_RED=$'\033[0;31m'; C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[1;33m'; C_BLUE=$'\033[0;34m'; C_RESET=$'\033[0m'
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_RESET=""
fi

log()  { printf "%s[install]%s %s\n" "$C_BLUE" "$C_RESET" "$*"; }
ok()   { printf "%s[ ok  ]%s %s\n" "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf "%s[warn ]%s %s\n" "$C_YELLOW" "$C_RESET" "$*" >&2; }
err()  { printf "%s[err  ]%s %s\n" "$C_RED"   "$C_RESET" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ---- Defaults / flags ----
WITH_LIVEKIT=1
WITH_FLUTTER=1
WITH_NODE=1
PYTHON_BIN=""

usage() {
  cat <<'EOF'
Usage: ./scripts/install.sh [options]

Bootstrap an openvox development environment on macOS or Linux.

Options:
  --no-livekit          skip docker compose up -d for LiveKit Server
  --no-flutter          skip flutter pub get for apps/voice-client
  --no-node             skip pnpm/npm install + build for apps/agentd
  --python <bin>        use a specific python binary (default: detect python3.10+ on PATH)
  -h, --help            show this help

After install:
  openvox init                  # write ~/.openvox/config.json
  openvox start --yes           # start the selected backend + LiveKit worker
  cd apps/voice-client && flutter run   # optional: start the Flutter UI
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-livekit) WITH_LIVEKIT=0; shift ;;
    --no-flutter) WITH_FLUTTER=0; shift ;;
    --no-node)    WITH_NODE=0;    shift ;;
    --python)     PYTHON_BIN="$2"; shift 2 ;;
    -h|--help)    usage; exit 0 ;;
    *) die "unknown argument: $1 (use --help)" ;;
  esac
done

# Sanity: are we at the repo root?
if [[ ! -d "$REPO_ROOT/apps/voice-agent" ]] || [[ ! -d "$REPO_ROOT/tooling" ]]; then
  die "scripts/install.sh must be run from inside the openvox repo (missing apps/voice-agent or tooling/)"
fi

# ---- Step 1: platform ----
log "Step 1/5 · Detecting platform"
UNAME_S="$(uname -s)"
case "$UNAME_S" in
  Darwin|Linux) ok "platform=$UNAME_S" ;;
  *) die "this script targets macOS / Linux; on Windows use scripts\\install.ps1 (PowerShell)" ;;
esac

# ---- Step 2: python ----
log "Step 2/5 · Detecting Python"
if [[ -z "$PYTHON_BIN" ]]; then
  for cand in python3.12 python3.11 python3.10 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
      PYTHON_BIN="$(command -v "$cand")"
      break
    fi
  done
fi
[[ -n "$PYTHON_BIN" ]] || die "python not found in PATH (install Python 3.10+ or pass --python /path/to/python3)"
PY_VER="$("$PYTHON_BIN" -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "${PY_VER%%.*}" -lt 3 ]] || { [[ "${PY_VER%%.*}" -eq 3 ]] && [[ "${PY_VER#*.}" -lt 10 ]]; }; then
  die "python $PY_VER found, but openvox needs 3.10+ (use --python <path> or install 3.10+)"
fi
ok "python=$PYTHON_BIN ($PY_VER)"

# ---- Step 3: venv ----
log "Step 3/5 · Creating .venv (idempotent)"
VENV_DIR="$REPO_ROOT/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  ok "created $VENV_DIR"
else
  ok "$VENV_DIR already exists (reusing)"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --quiet --upgrade pip wheel setuptools
ok "pip upgraded"

# ---- Step 4: openvox_worker + volcengine plugin ----
log "Step 4/5 · Installing openvox_worker + Volcengine plugin (editable, --no-deps)"
pushd "$REPO_ROOT/apps/voice-agent" >/dev/null
python -m pip install --quiet -e . --no-deps
python -m pip install --quiet -e ./plugins/livekit-plugins-volcengine --no-deps
popd >/dev/null
ok "openvox installed (editable) at $VENV_DIR"

# ---- Step 5: extras (flutter + agentd + livekit, all optional) ----
log "Step 5/5 · Extras (flutter, agentd, LiveKit)"

if [[ "$WITH_FLUTTER" -eq 1 ]] && [[ -d "$REPO_ROOT/apps/voice-client" ]]; then
  if command -v flutter >/dev/null 2>&1; then
    pushd "$REPO_ROOT/apps/voice-client" >/dev/null
    if [[ ! -f .env && -f .env.example ]]; then
      cp .env.example .env
      warn "created .env from .env.example — edit it before flutter run"
    fi
    flutter --no-version-check pub get
    popd >/dev/null
    ok "flutter deps installed"
  else
    warn "flutter not found in PATH — skipping voice-client deps"
  fi
else
  log "  · flutter skipped (--no-flutter or voice-client missing)"
fi

if [[ "$WITH_NODE" -eq 1 ]] && [[ -d "$REPO_ROOT/apps/agentd" ]]; then
  if command -v node >/dev/null 2>&1; then
    pushd "$REPO_ROOT/apps/agentd" >/dev/null
    if command -v pnpm >/dev/null 2>&1; then
      CI=true pnpm install --frozen-lockfile
      pnpm build
    else
      warn "pnpm not found, falling back to npm"
      npm ci 2>/dev/null || npm install
      npm run build
    fi
    popd >/dev/null
    ok "agentd built"
  else
    warn "node not found in PATH — skipping agentd build"
  fi
else
  log "  · agentd skipped (--no-node or agentd missing)"
fi

if [[ "$WITH_LIVEKIT" -eq 1 ]] && [[ -f "$REPO_ROOT/infra/docker-compose.yml" ]]; then
  if command -v docker >/dev/null 2>&1; then
    pushd "$REPO_ROOT/infra" >/dev/null
    docker compose up -d
    popd >/dev/null
    ok "LiveKit container(s) up (run 'cd infra && docker compose ps' to verify)"
  else
    warn "docker not found — skipping LiveKit. Install Docker Desktop, then 'cd infra && docker compose up -d'"
  fi
else
  log "  · LiveKit skipped (--no-livekit or infra/docker-compose.yml missing)"
fi

ok "install complete"
cat <<'NEXT'

Next steps:
  openvox init                    # write ~/.openvox/config.json (choose LLM backend)
  openvox start --yes             # start the selected backend + LiveKit worker
  cd apps/voice-client && flutter run   # optional: start the Flutter UI

See USAGE.md for the full command matrix and troubleshooting.
NEXT
