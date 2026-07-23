#!/usr/bin/env bash
# dev-up.sh — 一键起 LiveKit + agent worker
# 不起 Flutter client（需要 GUI 终端）

set -euo pipefail

# 起 LiveKit
cd "$(dirname "$0")/../../infra"
docker compose up -d

# 等 livekit 健康
echo "等待 LiveKit 就绪 ..."
for i in {1..30}; do
  if curl -sf http://localhost:7880 >/dev/null 2>&1; then
    echo "LiveKit up."
    break
  fi
  sleep 1
done

# 起 agent worker
cd "$(dirname "$0")/../../apps/voice-agent"
if [ -d .venv ]; then
  source .venv/bin/activate
fi
exec python main.py start
