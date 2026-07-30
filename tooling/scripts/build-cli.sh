#!/usr/bin/env bash
# build-cli.sh — Build both OpenVox CLI artifacts in dist/.
#
# Output:
#   apps/agentd/dist/                 compiled TypeScript
#   apps/voice-agent/dist/            Python wheel + sdist
#
# Usage:
#   ./tooling/scripts/build-cli.sh             # build both
#   ./tooling/scripts/build-cli.sh agentd      # only agentd (TypeScript)
#   ./tooling/scripts/build-cli.sh openvox     # only openvox (Python wheel)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=lib/log.sh
. "$SCRIPT_DIR/lib/log.sh"
# shellcheck source=lib/versions.sh
. "$SCRIPT_DIR/lib/versions.sh"

TARGET="${1:-all}"

build_agentd() {
  step "agentd (TypeScript)"
  if ! have node; then die "node not found in PATH"; fi
  if ! have pnpm; then warn "pnpm not found, falling back to npm"; fi

  cd "$REPO_ROOT/apps/agentd"

  if have pnpm; then
    pnpm install --frozen-lockfile
    pnpm build
  else
    npm ci
    npm run build
  fi

  shopt -s nullglob
  local entries=(dist/index.js)
  shopt -u nullglob
  [[ ${#entries[@]} -gt 0 ]] || die "agentd build did not produce dist/index.js"
  info "agentd $(app_version agentd) → dist/index.js ($(du -h dist/index.js | awk '{print $1}'))"
}

build_openvox() {
  step "openvox (Python wheel + sdist)"
  if ! have python3 && ! have python; then die "python not found in PATH"; fi
  local py
  if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    py="$REPO_ROOT/.venv/bin/python"
  elif have python3; then
    py="python3"
  else
    py="python"
  fi

  "$py" -m pip install --quiet --upgrade build 2>/dev/null \
    || die "failed to install 'build' (pip)"

  cd "$REPO_ROOT/apps/voice-agent"
  rm -rf dist
  "$py" -m build

  shopt -s nullglob
  local wheels=(dist/*.whl)
  shopt -u nullglob
  [[ ${#wheels[@]} -gt 0 ]] || die "openvox build did not produce a wheel"
  info "openvox $(app_version openvox) → $(ls dist/*.whl dist/*.tar.gz 2>/dev/null | tr '\n' ' ')"
}

case "$TARGET" in
  agentd) build_agentd ;;
  openvox) build_openvox ;;
  all|"") build_agentd; build_openvox ;;
  *) die "unknown target: $TARGET (use agentd | openvox | all)";;
esac

step "done"
