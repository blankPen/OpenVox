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
  if command -v agentd >/dev/null; then
    info "  → $(command -v agentd)"
  else
    warn "agentd not on PATH (you may need to relogin)"
  fi
}

install_openvox() {
  step "openvox (Python)"

  # Pick the Python interpreter to install openvox against. Priority:
  #   1. The interpreter driving the current shell (VIRTUAL_ENV).
  #   2. The project-local .venv, so a plain `./install-cli.sh openvox`
  #      still works for a freshly cloned repo.
  #   3. A system python3 / python reachable on PATH.
  #   4. Otherwise die with a hint to install / activate one.
  local py=""
  if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
    py="${VIRTUAL_ENV}/bin/python"
  elif [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    py="$REPO_ROOT/.venv/bin/python"
  elif have python3; then
    py="python3"
  elif have python; then
    py="python"
  else
    die "python not found (set \$PATH, create $REPO_ROOT/.venv, or activate a venv)"
  fi

  cd "$REPO_ROOT/apps/voice-agent"

  if have pipx; then
    pipx install --force "."
  else
    # Use an array so each arg is its own word (avoids quoting traps).
    local -a pip_cmd=("$py" -m pip)
    local -a install_args=()
    # --user is meaningful only outside an active venv / system python.
    # Declare-and-assign separately so a failing python invocation cannot
    # silently leave in_venv empty and bypass the guard below.
    local in_venv
    if ! in_venv=$("$py" -c "import sys; print(int(sys.prefix != sys.base_prefix))" 2>/dev/null); then
      die "python sanity check failed via: $py"
    fi
    if [[ "$in_venv" -eq 0 ]] && "${pip_cmd[@]}" install --user --help >/dev/null 2>&1; then
      install_args=(--user)
    fi
    "${pip_cmd[@]}" install --force-reinstall "${install_args[@]+"${install_args[@]}"}" .
  fi

  info "openvox installed via $py"
  # Resolve the installed console script. We try (in order): shutil.which on
  # PATH (handles pipx / --user sites), then a few well-known locations
  # inside the current interpreter's environment.
  "$py" - <<'PYEOF' 2>/dev/null || info "  \u2192 (path resolution failed; run \`which openvox\`)"
import os, shutil, sys
from pathlib import Path
candidates = []
which = shutil.which("openvox")
if which:
    candidates.append(which)
# pipx
for p in (Path.home() / ".local" / "bin" / "openvox",):
    if p.exists():
        candidates.append(str(p))
# active / project venv
for env_var in ("VIRTUAL_ENV",):
    base = os.environ.get(env_var)
    if base:
        for sub in ("bin/openvox", "Scripts/openvox.exe"):
            p = Path(base) / sub
            if p.exists():
                candidates.append(str(p))
# next to the interpreter we used to install
interp_dir = Path(sys.executable).parent
for name in ("openvox", "openvox.exe"):
    p = interp_dir / name
    if p.exists():
        candidates.append(str(p))
if candidates:
    on_path = shutil.which("openvox") is not None
    suffix = "" if on_path else " (not on PATH \u2014 rehash your shell)"
    print(f"  \u2192 {candidates[0]}{suffix}")
else:
    print("  \u2192 openvox install succeeded but console script not found")
PYEOF
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
