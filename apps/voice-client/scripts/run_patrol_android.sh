#!/usr/bin/env bash
# Boot an Android emulator, grant permissions, run the Patrol e2e suite.
#
# Prereqs:
#   - Android SDK installed (adb + emulator)
#   - AVD available (default name: vox_test_avd)
#   - dart pub global activate patrol_cli 3.11.0
#   - openvox-agent worker running (see /tmp/livekit-worker.log)
#
# Usage:
#   scripts/run_patrol_android.sh
#   scripts/run_patrol_android.sh --avd <name>
#   scripts/run_patrol_android.sh --device <serial>

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

AVD_NAME="${AVD_NAME:-vox_test_avd}"
DEVICE_SERIAL=""
APP_PKG="com.livekit.example.VoiceAssistantFlutter"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --avd) AVD_NAME="$2"; shift 2 ;;
    --avd=*) AVD_NAME="${1#*=}"; shift ;;
    --device) DEVICE_SERIAL="$2"; shift 2 ;;
    --device=*) DEVICE_SERIAL="${1#*=}"; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

export PATH="$PATH:$HOME/.pub-cache/bin"

log() { printf "\033[1;36m[run_patrol_android]\033[0m %s\n" "$*"; }

# 1. Ensure a device is connected
if [[ -z "$DEVICE_SERIAL" ]]; then
  log "booting AVD $AVD_NAME"
  (emulator -avd "$AVD_NAME" -no-snapshot -no-window > /tmp/vox-avd.log 2>&1 &)
  # Wait for boot complete.
  log "waiting for emulator to finish booting"
  for i in $(seq 1 60); do
    if adb shell getprop sys.boot_completed 2>/dev/null | grep -q 1; then
      log "emulator booted after ${i}s"
      break
    fi
    sleep 2
  done
  DEVICE_SERIAL=$(adb devices | awk 'NR>1 && $2=="device" {print $1; exit}')
fi

if [[ -z "$DEVICE_SERIAL" ]]; then
  echo "No Android device available" >&2
  exit 2
fi

log "using device $DEVICE_SERIAL"

# 2. Grant permissions (RECORD_AUDIO + CAMERA)
log "granting permissions"
adb -s "$DEVICE_SERIAL" shell pm grant "$APP_PKG" android.permission.RECORD_AUDIO || true
adb -s "$DEVICE_SERIAL" shell pm grant "$APP_PKG" android.permission.CAMERA || true

# 2b. Create explicit AgentDispatch for 'openz-room' (LiveKit server's
# auto-dispatch via roomConfig.agents is unreliable for the Flutter
# Android client; issuing CreateAgentDispatch via Twirp guarantees the
# openz agent is in the room before the test connects). The dispatch
# is idempotent — the server returns OK on duplicates.
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

# 3. Optional verify_flutter_app.py sidecar
if [[ -n "${ENABLE_VERIFY_LISTENER:-1}" ]]; then
  log "starting verify_flutter_app.py sidecar"
  (
    python3 e2e/verify_flutter_app.py --duration 180 \
      > e2e/logs/verify-patrol-android.log 2>&1 &
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

# 4. flutter pub get
log "flutter pub get"
flutter pub get

# 5. Run flutter test integration_test
# See scripts/run_patrol_ios.sh for the rationale (Patrol iOS harness
# has a discovery gap; we use plain integration_test instead).
log "running flutter test integration_test on Android emulator $DEVICE_SERIAL"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOG_FILE="e2e/logs/patrol-android-${TIMESTAMP}.log"
mkdir -p e2e/logs

# Bypass global ~/.gradle/init.d/mirrors.gradle (aliyun mirror) which
# conflicts with Gradle 8.10's "prefer settings repositories" mode and
# breaks Flutter's plugin-loader resolution. Seed a clean GRADLE_USER_HOME
# once and cache it for subsequent runs.
GRADLE_HOME="${GRADLE_USER_HOME:-/tmp/empty-gradle}"
mkdir -p "$GRADLE_HOME/init.d"
if [[ ! -d "$GRADLE_HOME/caches" ]]; then
  log "seeding clean GRADLE_USER_HOME at $GRADLE_HOME (first run only)"
  for d in caches wrapper jars; do
    [[ -d "$HOME/.gradle/$d" ]] && cp -R "$HOME/.gradle/$d" "$GRADLE_HOME/" 2>/dev/null || true
  done
  [[ -f "$HOME/.gradle/gradle.properties" ]] && cp "$HOME/.gradle/gradle.properties" "$GRADLE_HOME/" 2>/dev/null || true
fi
export GRADLE_USER_HOME="$GRADLE_HOME"

set +e
flutter test \
  integration_test/vox_e2e_test.dart \
  -d "$DEVICE_SERIAL" \
  --dart-define=VOX_PRECONNECT_AUDIO=false \
  --dart-define=VOX_E2E_ROOM_NAME=openz-room \
  --dart-define=VOX_E2E_ROOM_SUFFIX= \
  2>&1 | tee "$LOG_FILE"
EXIT=$?
set -e

# 6. Parse summary
SUMMARY_FILE="e2e/logs/summary-android-${TIMESTAMP}.json"
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
    "platform": "android",
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