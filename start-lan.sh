#!/bin/bash
# Mac browser / same-LAN phone → node-ip=Mac LAN IP (en1)
set -e
LAN_IP=$(ipconfig getifaddr en1 2>/dev/null || ipconfig getifaddr en0 2>/dev/null)
if [ -z "$LAN_IP" ]; then
  echo "Could not detect LAN IP"; exit 1
fi
docker rm -f livekit-local >/dev/null 2>&1 || true
docker run -d --name livekit-local --restart unless-stopped \
  -p 7880:7880/tcp -p 7881:7881/tcp -p 7882:7882/udp \
  -v "$(dirname "$0")/livekit.yaml":/etc/livekit.yaml:ro \
  livekit/livekit-server:latest \
  --config /etc/livekit.yaml --bind=0.0.0.0 --node-ip="$LAN_IP"
echo "Started for LAN clients (node-ip=$LAN_IP)"
echo "On the client, set ws://$LAN_IP:7880"