#!/usr/bin/env bash
# dev-down.sh — 停掉 LiveKit + agent worker

set -euo pipefail

cd "$(dirname "$0")/../../infra"
docker compose down

pkill -f "main.py start" || true
echo "Done."
