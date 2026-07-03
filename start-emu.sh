#!/bin/bash
# Android emulator client → node-ip=10.0.2.2 (QEMU host alias)
set -e
docker rm -f livekit-local >/dev/null 2>&1 || true
docker run -d --name livekit-local --restart unless-stopped \
  -p 7880:7880/tcp -p 7881:7881/tcp -p 7882:7882/udp \
  -v "$(dirname "$0")/livekit.yaml":/etc/livekit.yaml:ro \
  livekit/livekit-server:latest \
  --config /etc/livekit.yaml --bind=0.0.0.0 --node-ip=10.0.2.2
echo "Started for Android emulator (node-ip=10.0.2.2)"