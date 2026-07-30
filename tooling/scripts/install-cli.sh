#!/usr/bin/env bash
# install-cli.sh — Install both OpenVox CLI tools globally on this machine.
#
# What it does:
#   1. Builds both CLIs via build-cli.sh (skipped if --no-build).
#   2. agentd: `npm install -g <pack-tarball>` (works for both npm and pnpm).
#   3. openvox (Python): pipx if available, else `pip install --user`,
#      else `pip install` (uses the active python).
#
# Usage:
#   ./tooling/scripts/install-cli.sh             # build + install both
#   ./tooling/scripts/install-cli.sh --no-build  # skip build (use existing dist/)
#   ./tooling/scripts/install-cli.sh agentd      # only agentd
#   ./tooling/scripts/install-cli.sh openvox     # only openvox
#
# Notes:
#   - Re-running is safe: existing installs are upgraded in place.
#   - The agentd tarball is created in /tmp and removed after install.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=lib/log.sh
. "$SCRIPT_DIR/lib/log.sh"

DO_BUILD=1
TARGET="all"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-build) DO_BUILD=0; shift ;;
    agentd|openvox|all) TARGET="$1"; shift ;;
    *) die "unknown argument: $1";;
  esac
done

install_agentd() {
  step "agentd"
  if ! have node; then die "node not found in PATH"; fi

  # Always install via npm install -g <tgz> — it works regardless of whether
  # the active package manager is npm or pnpm, and it never complains about
  # PATH. Both npm and pnpm share the same global bin dir, so the resulting
  # CLI is reachable from either ecosystem.
  : # placeholder for future pnpm-specific handling

  local pack_tgz tarball_name
  cd "$REPO_ROOT/apps/agentd"
  tarball_name="$(npm pack 2>/dev/null | tail -1)"
  [[ -n "$tarball_name" ]] || die "npm pack produced no tarball"
  pack_tgz="$REPO_ROOT/apps/agentd/$tarball_name"

  if ! have npm; then die "npm not found in PATH"; fi
  npm install -g "$pack_tgz" >/dev/null

  rm -f "$pack_tgz"
  info "agentd $(node -e "console.log(require('$REPO_ROOT/apps/agentd/package.json').version)") installed"
  command -v agentd >/dev/null && info "  → $(command -v agentd)" || warn "agentd not on PATH (you may need to relogin)"
}

install_openvox() {
  step "openvox (Python)"
  local py pip_cmd install_args=()
  if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    py="$REPO_ROOT/.venv/bin/python"
  elif have python3; then
    py="python3"
  elif have python; then
    py="python"
  else
    die "python not found in PATH"
  fi

  cd "$REPO_ROOT/apps/voice-agent"

  if have pipx; then
    pipx install --force "."
  else
    # Use an array so each arg is its own word (avoids quoting traps).
    local -a pip_cmd=("$py" -m pip)
    local -a install_args=()
    # --user is meaningful only outside an active venv / system python.
    local in_venv=$("$py" -c "import sys; print(int(sys.prefix != sys.base_prefix))")
    if [[ "$in_venv" -eq 0 ]] && "${pip_cmd[@]}" install --user --help >/dev/null 2>&1; then
      install_args=(--user)
    fi
    "${pip_cmd[@]}" install --force-reinstall "${install_args[@]+"${install_args[@]}"}" .
  fi

  info "openvox installed via $py"
  # The console script lives in the venv/Scripts/bin — print the resolved path.
  if [[ -x "$REPO_ROOT/.venv/bin/openvox" ]]; then
    info "  → $REPO_ROOT/.venv/bin/openvox"
  else
    "$py" -c "import shutil; print('  →', shutil.which('openvox') or '(not on PATH)')"
  fi
}

if [[ "$DO_BUILD" -eq 1 ]]; then
  case "$TARGET" in
    agentd) "$SCRIPT_DIR/build-cli.sh" agentd ;;
    openvox) "$SCRIPT_DIR/build-cli.sh" openvox ;;
    all|"") "$SCRIPT_DIR/build-cli.sh" all ;;
  esac
fi

case "$TARGET" in
  agentd) install_agentd ;;
  openvox) install_openvox ;;
  all|"") install_agentd; install_openvox ;;
  *) die "unknown target: $TARGET" ;;
esac

step "done"
info "verify with:  agentd --check && openvox --help"
