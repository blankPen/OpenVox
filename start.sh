#!/bin/zsh
# 启动本地 LiveKit Agent 开发栈：先确保 server 在跑，再起 worker。
# 用法：./start.sh

set -e

# 1. LiveKit Server（已在跑则跳过；跑 brew 的那个也行）
if ! docker ps --filter name=livekit-local --format '{{.Names}}' | grep -q livekit-local; then
  echo "[start.sh] launching livekit-local (docker) ..."
  docker run -d \
    --name livekit-local \
    --restart unless-stopped \
    -p 7880:7880 -p 7881:7881 -p 7882:7882/udp \
    livekit/livekit-server:latest \
    --dev --bind=0.0.0.0 --node-ip=127.0.0.1
  sleep 2
fi

# 2. 端口冲突保护（之前 worker 残留）
if lsof -nP -iTCP:8081 -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "[start.sh] killing old worker on 8081 ..."
  lsof -nP -iTCP:8081 -sTCP:LISTEN -t | xargs kill -9
  sleep 1
fi

# 3. 起 worker
echo "[start.sh] starting agent worker ..."
cd "$(dirname "$0")"
source .venv/bin/activate
exec python main.py start
