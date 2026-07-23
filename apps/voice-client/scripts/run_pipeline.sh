#!/usr/bin/env bash
# Vox Flutter e2e pipeline — orchestrates Python sidecars + Patrol run.
#
# This is the single entrypoint that exercises everything the old
# run_e2e_ui.py + verify_flutter_app.py + parallel_audio_subscriber.py
# stack did, but using Patrol as the primary UI driver and keeping the
# Python scripts as cross-validation sidecars.
#
# Usage:
#   scripts/run_pipeline.sh --platform ios
#   scripts/run_pipeline.sh --platform android
#   scripts/run_pipeline.sh --platform ios --with-audio-subscriber

set -euo pipefail

cd "$(dirname "$0")/.."

PLATFORM="${PLATFORM:-ios}"
ROOM_NAME="${ROOM_NAME:-openz-room-patrol-$(date +%s)}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform) PLATFORM="$2"; shift 2 ;;
    --platform=*) PLATFORM="${1#*=}"; shift ;;
    --room) ROOM_NAME="$2"; shift 2 ;;
    --room=*) ROOM_NAME="${1#*=}"; shift ;;
    --with-audio-subscriber) WITH_AUDIO_SUBSCRIBER=1; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

log() { printf "\033[1;35m[pipeline]\033[0m %s\n" "$*"; }

log "Vox Flutter e2e pipeline"
log "  platform = $PLATFORM"
log "  room     = $ROOM_NAME"

# 1. Run LiveKit pipeline test (token sign + Twirp auth + 2-client
#    publish/subscribe) so we know the server side is healthy before
#    dragging Flutter into it.
log "running e2e/e2e_test.py (LiveKit pipeline sanity)"
python3 e2e/e2e_test.py --server wss://livekit.openz.top:7443

# 2. Spawn verify_flutter_app.py — Twirp room participant listener that
#    will prove the Flutter client actually joins.
log "spawning verify_flutter_app.py for $ROOM_NAME"
python3 e2e/verify_flutter_app.py --duration 240 --room "$ROOM_NAME" \
  > e2e/logs/verify-pipeline-$(date +%s).log 2>&1 &
VERIFY_PID=$!

# 3. Optionally spawn parallel audio subscriber
SUB_PID=""
if [[ -n "${WITH_AUDIO_SUBSCRIBER:-}" ]]; then
  AUDIO_OUT="e2e/audio/reply-pipeline-$(date +%s).wav"
  mkdir -p e2e/audio
  log "spawning parallel_audio_subscriber → $AUDIO_OUT"
  E2E_ROOM_NAME="$ROOM_NAME" \
  E2E_AUDIO_OUT="$AUDIO_OUT" \
  E2E_RECORD_DURATION_SEC=20 \
    python3 e2e/parallel_audio_subscriber.py \
    > e2e/logs/subscriber-pipeline-$(date +%s).log 2>&1 &
  SUB_PID=$!
fi

cleanup() {
  log "cleaning up background processes"
  kill "$VERIFY_PID" 2>/dev/null || true
  if [[ -n "$SUB_PID" ]]; then
    kill "$SUB_PID" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
}
trap cleanup EXIT

# 4. Run the actual Patrol test
log "dispatching Patrol test for $PLATFORM"
if [[ "$PLATFORM" == "ios" ]]; then
  bash scripts/run_patrol_ios.sh
else
  bash scripts/run_patrol_android.sh
fi
EXIT=$?

# 5. Post-mortem: cross-check verify_flutter_app.py log + (if any) audio WAV
log "cross-checking sidecar evidence"
if [[ -f e2e/logs/verify-pipeline-*.log ]]; then
  log "verify_flutter_app.py saw these participants:"
  grep -E "JOIN|LEAVE" e2e/logs/verify-pipeline-*.log | tail -20 || true
fi
if [[ -n "$SUB_PID" ]]; then
  sleep 4  # let subscriber write WAV
  LATEST_WAV=$(ls -t e2e/audio/reply-pipeline-*.wav 2>/dev/null | head -1)
  if [[ -n "$LATEST_WAV" ]]; then
    log "audio WAV at $LATEST_WAV"
    python3 - "$LATEST_WAV" <<'PYEOF'
import sys, wave, struct
path = sys.argv[1]
with wave.open(path, 'rb') as wf:
    sr = wf.getframerate()
    frames = wf.readframes(wf.getnframes())
amp = max(abs(s) for s in struct.unpack(f'<{len(frames)//2}h', frames)) if frames else 0
dur = len(frames) / (sr * 2)
print(f"  duration={dur:.2f}s max_amp={amp}")
PYEOF
  fi
fi

exit $EXIT