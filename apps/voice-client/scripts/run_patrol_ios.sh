#!/usr/bin/env bash
# Boot an iOS simulator, grant permissions, run the Patrol e2e suite, and
# summarise results. Mirrors `e2e/run_e2e_ui.py` but uses Patrol (XCUITest
# + Flutter integration_test) so the test logic runs as Dart code on a
# real Flutter engine instead of pixel-sampled coordinates.
#
# Prereqs:
#   - xcrun simctl (Xcode 15+)
#   - dart pub global activate patrol_cli 3.11.0
#   - iPhone simulator available
#   - openvox-agent worker running (see /tmp/livekit-worker.log)
#
# Usage:
#   scripts/run_patrol_ios.sh                  # default iPhone 17
#   scripts/run_patrol_ios.sh --device <UDID>   # specific simulator

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

UDID="${UDID:-31386DB9-7585-4AED-AC57-7CEEE70DD76B}"  # iPhone 17
BUNDLE_ID="com.livekit.example.VoiceAssistant-flutter"

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --device) UDID="$2"; shift 2 ;;
    --device=*) UDID="${1#*=}"; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

export PATH="$PATH:$HOME/.pub-cache/bin"

log() { printf "\033[1;36m[run_patrol_ios]\033[0m %s\n" "$*"; }
fail() { printf "\033[1;31m[run_patrol_ios]\033[0m %s\n" "$*" >&2; exit 1; }

# 1. Boot simulator
log "checking simulator $UDID"
if ! xcrun simctl list devices booted | grep -q "$UDID"; then
  log "booting simulator $UDID"
  xcrun simctl boot "$UDID" || log "boot returned non-zero (already booted)"
fi

# 2. Grant microphone + camera permission via simctl BEFORE first launch
#    (avoids the LiveKit -4010 race that idb pipeline had to self-heal).
log "granting mic + camera permissions"
xcrun simctl privacy booted grant microphone "$BUNDLE_ID" || true
xcrun simctl privacy booted grant camera "$BUNDLE_ID" || true

# 2b. Create explicit AgentDispatch for 'openz-room'. See Android script
# for rationale. The server's auto-dispatch via roomConfig.agents is
# unreliable for the Flutter LiveKit client; we issue
# CreateAgentDispatch via Twirp to guarantee the openz agent is in the
# room before the test connects.
OPENVOX_PY="/Users/pz/workspace/openvox/.venv/bin/python"
if [[ -x "$OPENVOX_PY" ]]; then
  log "creating agent dispatch (openz-room / openz)"
  (cd /Users/pz/workspace/openvox && "$OPENVOX_PY" - >/dev/null 2>&1 <<'PYEOF' || true
import asyncio
from livekit.api import LiveKitAPI, CreateAgentDispatchRequest

async def main():
    api = LiveKitAPI(
        'https://livekit.openz.top:7443', 'openz',
        '35b58a62c4a6f5a188c8537999e0524dbb0b697085fc3660bf9564d5dc083ce6',
    )
    req = CreateAgentDispatchRequest(agent_name='openz', room='openz-room', metadata='')
    try:
        await api.agent_dispatch.create_dispatch(req)
    except Exception as e:
        import sys
        print(f'create_dispatch err: {e}', file=sys.stderr)
    await api.aclose()

asyncio.run(main())
PYEOF
  )
fi

# 3. Optional: launch verify_flutter_app.py as sidecar (Twirp room listener)
if [[ -n "${ENABLE_VERIFY_LISTENER:-1}" ]]; then
  log "starting verify_flutter_app.py sidecar (duration=180s)"
  (
    python3 e2e/verify_flutter_app.py --duration 180 \
      > e2e/logs/verify-patrol-ios.log 2>&1 &
    echo $! > /tmp/vox-verify-listener.pid
  )
  cleanup() {
    if [[ -f /tmp/vox-verify-listener.pid ]]; then
      kill "$(cat /tmp/vox-verify-listener.pid)" 2>/dev/null || true
      rm -f /tmp/vox-verify-listener.pid
    fi
  }
  trap cleanup EXIT
fi

# 4. flutter pub get (idempotent; ensures patrol pods link cleanly)
log "flutter pub get"
flutter pub get

# 5. Run flutter test integration_test
# NOTE: We use `flutter test integration_test/...` instead of
# `patrol test` because Patrol's iOS RunnerUITests harness has a
# discovery gap that prevents the Dart side from running. The plain
# `flutter test integration_test` runs the Dart tests on the same
# simulator under integration_test's own binding. See
# e2e/PATROL_GUIDE.md §4.1 for the Patrol-specific issue and the
# rationale.
log "running flutter test integration_test on iOS simulator $UDID"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOG_FILE="e2e/logs/patrol-ios-${TIMESTAMP}.log"
mkdir -p e2e/logs
set +e
flutter test \
  integration_test/vox_e2e_test.dart \
  -d "$UDID" \
  2>&1 | tee "$LOG_FILE"
EXIT=$?
set -e

# 6. Parse VOX_E2E_SUMMARY from log
SUMMARY_FILE="e2e/logs/summary-ios-${TIMESTAMP}.json"
if grep -q "=== VOX_E2E_SUMMARY ===" "$LOG_FILE"; then
  log "extracting PHASE_RESULT lines"
  python3 - "$LOG_FILE" "$SUMMARY_FILE" <<'PYEOF'
import sys, json, re
log_path, out_path = sys.argv[1], sys.argv[2]
text = open(log_path).read()
results = []
for m in re.finditer(r"PHASE_RESULT (PASS|FAIL) (\S+)(?:\s*::\s*(.*))?", text):
    passed, name, detail = m.group(1) == 'PASS', m.group(2), m.group(3) or ''
    results.append({"name": name, "passed": passed, "detail": detail})
total, passed = len(results), sum(1 for r in results if r['passed'])
summary = {
    "platform": "ios",
    "total": total,
    "passed": passed,
    "failed": total - passed,
    "all_passed": total == passed,
    "phases": results,
}
with open(out_path, 'w') as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)
print(f"summary saved to {out_path}")
print(f"RESULT passed={passed} failed={total-passed}")
PYEOF
fi

if [[ $EXIT -eq 0 ]]; then
  log "✅ patrol test passed (exit=0)"
else
  log "❌ patrol test failed (exit=$EXIT) — see $LOG_FILE"
fi
exit $EXIT