#!/usr/bin/env python3
"""Listen on a LiveKit room for ~30s and report every participant that joins.

Used as a sanity-check tool while exercising the Flutter Voice Assistant:
start this script before tapping "开始语音通话" in the app, and the script
will print any participant that joins/leaves the room from the server's
point of view. Useful for proving the Flutter client actually reaches the
WebRTC handshake.

Usage:
  python3 e2e/verify_flutter_app.py [--room openz-room] [--duration 30]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time

import jwt

from livekit import api

# ---------------------------------------------------------------------------
# Credentials (matches lib/livekit_config.dart)
# ---------------------------------------------------------------------------
API_KEY = "openz"
API_SECRET = "35b58a62c4a6f5a188c8537999e0524dbb0b697085fc3660bf9564d5dc083ce6"
DEFAULT_HTTP_URL = "https://livekit.openz.top:7443"
DEFAULT_ROOM = "openz-room"
DEFAULT_DURATION = 30  # seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("verify")


def make_token(identity: str = "verify-listener", room: str = DEFAULT_ROOM) -> str:
    """Build an admin-scoped JWT so we can read all participants."""
    now = int(time.time())
    payload = {
        "iss": API_KEY,
        "sub": identity,
        "iat": now,
        "exp": now + 600,
        "video": {
            "room": room,
            "roomJoin": True,
            "roomAdmin": True,
            "roomList": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
    }
    return jwt.encode(payload, API_SECRET, algorithm="HS256")


async def poll_room(
    http_url: str,
    room_name: str,
    duration: int,
) -> None:
    """Poll RoomService for participants every second, print diffs."""
    lkapi = api.LiveKitAPI(http_url, api_key=API_KEY, api_secret=API_SECRET)
    seen: dict[str, dict] = {}
    stop_at = time.monotonic() + duration

    log.info("listening on room=%s for %ds", room_name, duration)
    log.info("(launch the Flutter app and tap 开始语音通话 now)")
    try:
        while time.monotonic() < stop_at:
            try:
                resp = await lkapi.room.list_participants(
                    api.ListParticipantsRequest(room=room_name)
                )
                current = {
                    p.identity: {
                        "sid": p.sid,
                        "state": str(p.state),
                        "joined_at": p.joined_at,
                        "tracks": len(p.tracks),
                    }
                    for p in resp.participants
                }
            except Exception as e:
                log.warning("list_participants failed: %s", e)
                await asyncio.sleep(1.0)
                continue

            # Detect joins and leaves.
            for ident, info in current.items():
                if ident not in seen:
                    kind = "agent" if ident.startswith("agent-") else (
                        "flutter" if ident.startswith("participant-") else "user"
                    )
                    log.info(
                        "  + JOIN  identity=%s kind=%s sid=%s tracks=%d",
                        ident, kind, info["sid"], info["tracks"],
                    )
            for ident in seen.keys() - current.keys():
                log.info("  - LEAVE identity=%s", ident)

            seen = current
            if current:
                log.info(
                    "  active=%d identities=%s",
                    len(current), sorted(current.keys()),
                )
            await asyncio.sleep(1.0)
    finally:
        await lkapi.aclose()
        log.info("listener stopped, final participant count = %d", len(seen))


def install_sigint_handler(loop: asyncio.AbstractEventLoop, duration: int) -> None:
    """Allow Ctrl-C to abort early without raising a stack trace."""
    def _handler() -> None:
        log.warning("Ctrl-C received, exiting early")
        for task in asyncio.all_tasks(loop=loop):
            task.cancel()
    signal.signal(signal.SIGINT, _handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=DEFAULT_HTTP_URL,
                        help=f"LiveKit HTTP base URL (default: {DEFAULT_HTTP_URL})")
    parser.add_argument("--room", default=DEFAULT_ROOM,
                        help=f"room name to monitor (default: {DEFAULT_ROOM})")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION,
                        help=f"listen duration in seconds (default: {DEFAULT_DURATION})")
    args = parser.parse_args()

    token_preview = make_token(identity=f"verify-{os.getpid()}", room=args.room)
    log.info("=== verify_flutter_app.py ===")
    log.info("server=%s room=%s duration=%ds token_preview=%s...",
             args.server, args.room, args.duration, token_preview[:24])

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    install_sigint_handler(loop, args.duration)
    try:
        loop.run_until_complete(poll_room(args.server, args.room, args.duration))
    except KeyboardInterrupt:
        log.warning("interrupted")
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())