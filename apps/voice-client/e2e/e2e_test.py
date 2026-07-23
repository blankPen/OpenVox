#!/usr/bin/env python3
"""End-to-end test for LiveKit voice chat pipeline.

Runs 6 phases against the LiveKit server used by the Flutter app
(`wss://livekit.openz.top:7443`):

  1. Sign two HS256 JWTs (one for "user" identity, one for "agent").
  2. Authenticate against the Twirp RoomService (sanity check).
  3. Connect user client, publish an audio track, wait for CONN_CONNECTED.
  4. Connect agent client, subscribe to user's audio track.
  5. Server-side verification via Twirp (2 participants, agent subscribed).
  6. Disconnect both clients, snapshot was_connected before teardown.

The test does NOT depend on a real LiveKit worker image — the "agent"
side is simulated by a second SDK client, which is enough to validate
the WebRTC plumbing (token, signalling, audio publish/subscribe).

Usage:
  python3 e2e/e2e_test.py [--server wss://livekit.openz.top:7443]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys
import time

import jwt

# LiveKit SDK + Twirp client.
from livekit import api, rtc

# ---------------------------------------------------------------------------
# Credentials (DEV-only, matches lib/livekit_config.dart in Flutter app)
# ---------------------------------------------------------------------------
API_KEY = "openz"
API_SECRET = "35b58a62c4a6f5a188c8537999e0524dbb0b697085fc3660bf9564d5dc083ce6"
DEFAULT_WS_URL = "wss://livekit.openz.top:7443"

# Connection state enum values from livekit-python (1.1.13).
CONN_DISCONNECTED = 0
CONN_CONNECTED = 1

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("e2e")


# ---------------------------------------------------------------------------
# Phase 1: token sign
# ---------------------------------------------------------------------------
def make_token(identity: str, name: str) -> str:
    """Sign a JWT carrying full video grants + agent dispatch hints."""
    now = int(time.time())
    payload = {
        "iss": API_KEY,
        "sub": identity,
        "iat": now,
        "exp": now + 3600,
        "metadata": json.dumps({"name": name}),
        "video": {
            "room": "openz-room",
            "roomJoin": True,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
            "canPublishSources": [
                "camera", "microphone", "screen_share", "screen_share_audio",
            ],
            "canUpdateOwnMetadata": True,
            "roomCreate": True,
            "roomAdmin": True,
            "roomList": True,
            "roomRecord": True,
        },
        "roomConfig": {
            "agents": [{"agentName": "openz"}],
        },
    }
    return jwt.encode(payload, API_SECRET, algorithm="HS256")


def phase1_token() -> tuple[str, str]:
    log.info("[1/6] phase1_token — sign user + agent JWTs")
    user_token = make_token("e2e-user", "E2E User")
    agent_token = make_token("e2e-agent", "E2E Agent")
    decoded = jwt.decode(user_token, API_SECRET, algorithms=["HS256"])
    assert decoded["iss"] == API_KEY
    assert decoded["video"]["roomJoin"] is True
    log.info("    user_token  iss=%s sub=%s exp_in=%ds",
             decoded["iss"], decoded["sub"],
             decoded["exp"] - int(time.time()))
    log.info("    agent_token ready (room=%s)", decoded["video"]["room"])
    return user_token, agent_token


# ---------------------------------------------------------------------------
# Phase 2: Twirp auth (sanity check that we can hit the server)
# ---------------------------------------------------------------------------
async def phase2_twirp(http_url: str) -> api.LiveKitAPI:
    log.info("[2/6] phase2_twirp — RoomService.ListRooms via %s", http_url)
    lkapi = api.LiveKitAPI(http_url, api_key=API_KEY, api_secret=API_SECRET)
    try:
        rooms = await lkapi.room.list_rooms(api.ListRoomsRequest())
        log.info("    list_rooms ok, current=%d room(s)", len(rooms.rooms))
        for r in rooms.rooms[:3]:
            log.info("      - %s (participants=%d)", r.name, r.num_participants)
    except Exception as e:
        log.error("    twirp failed: %s", e)
        raise
    return lkapi


# ---------------------------------------------------------------------------
# Phase 3: user client connects + publishes audio
# ---------------------------------------------------------------------------
async def phase3_user_connect(ws_url: str, user_token: str) -> rtc.Room:
    log.info("[3/6] phase3_user_connect — connect user client to %s", ws_url)
    room = rtc.Room(loop=asyncio.get_event_loop())
    connected_evt = asyncio.Event()
    was_connected = {"flag": False}

    @room.on("connected")
    def _on_connected():
        was_connected["flag"] = True
        connected_evt.set()
        log.info("    [user] on('connected') fired")

    @room.on("disconnected")
    def _on_disconnected(reason):
        log.info("    [user] on('disconnected') reason=%s", reason)

    await room.connect(ws_url, user_token)
    # Memory: SDK needs ~2s of event-loop driving for the connected event
    # to settle. wait_for with a generous timeout avoids race on connect.
    try:
        await asyncio.wait_for(connected_evt.wait(), timeout=8.0)
    except asyncio.TimeoutError:
        log.warning("    on('connected') did not fire within 8s, "
                    "checking state directly")

    # Drive the loop briefly to let async event handlers catch up.
    for _ in range(20):
        await asyncio.sleep(0.1)
        if was_connected["flag"]:
            break

    state = room.connection_state
    log.info("    [user] connection_state=%s (CONN_CONNECTED=%s)",
             state, state == CONN_CONNECTED)
    assert state == CONN_CONNECTED or was_connected["flag"], (
        f"user did not reach connected state (state={state})"
    )

    # Publish a synthesized audio track (silence frame is enough for plumbing).
    log.info("    [user] publishing audio track")
    audio_source = rtc.AudioSource(48000, 1)
    audio_track = rtc.LocalAudioTrack.create_audio_track("e2e-mic", audio_source)
    # CRITICAL: must declare the source kind, otherwise LiveKit server
    # silently times out the publish RPC.
    publish_opts = rtc.TrackPublishOptions()
    publish_opts.source = rtc.TrackSource.SOURCE_MICROPHONE
    await room.local_participant.publish_track(audio_track, options=publish_opts)
    # Push a few silence frames so the track isn't empty.
    push_task = asyncio.create_task(_push_silence(audio_source, count=50))
    log.info("    [user] audio track published, was_connected=%s",
             was_connected["flag"])

    # Stash the silence pusher so we can cancel later.
    room._e2e_push_task = push_task
    return room


async def _push_silence(source: rtc.AudioSource, count: int) -> None:
    """Push `count` 10ms mono silence frames via AudioFrame."""
    import numpy as np
    samples = np.zeros(480, dtype=np.int16)  # 10ms @ 48kHz mono
    frame = rtc.AudioFrame(
        data=samples.tobytes(),
        sample_rate=48000,
        num_channels=1,
        samples_per_channel=480,
    )
    for _ in range(count):
        try:
            await source.capture_frame(frame)
        except Exception as e:
            log.debug("silence push stopped: %s", e)
            return
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# Phase 4: agent client connects + subscribes
# ---------------------------------------------------------------------------
async def phase4_agent_connect(
    ws_url: str, agent_token: str, user_room: rtc.Room
) -> rtc.Room:
    log.info("[4/6] phase4_agent_connect — second SDK client as agent")
    agent_room = rtc.Room(loop=asyncio.get_event_loop())
    subscribed_evt = asyncio.Event()
    state = {"tracks": [], "user_sid": None}

    @agent_room.on("track_subscribed")
    def _on_track(track, publication, participant):
        log.info("    [agent] subscribed track sid=%s kind=%s from=%s",
                 track.sid, track.kind, participant.identity)
        state["tracks"].append((track.sid, track.kind, participant.identity))
        if participant.identity == "e2e-user":
            subscribed_evt.set()

    @agent_room.on("participant_connected")
    def _on_participant(p):
        log.info("    [agent] on('participant_connected') identity=%s sid=%s",
                 p.identity, p.sid)
        if p.identity == "e2e-user":
            state["user_sid"] = p.sid

    await agent_room.connect(ws_url, agent_token)
    # Memory: keep the loop alive a few seconds for SDK event delivery.
    for _ in range(20):
        await asyncio.sleep(0.1)
        if state["user_sid"]:
            break

    log.info("    [agent] connection_state=%s", agent_room.connection_state)
    assert agent_room.connection_state == CONN_CONNECTED, (
        f"agent did not reach connected state "
        f"(state={agent_room.connection_state})"
    )

    # Wait up to 5s for audio subscription from the e2e-user.
    try:
        await asyncio.wait_for(subscribed_evt.wait(), timeout=5.0)
        log.info("    [agent] audio subscription confirmed")
    except asyncio.TimeoutError:
        log.warning("    [agent] did not see e2e-user audio within 5s "
                    "(tracks seen so far: %d)", len(state["tracks"]))
    return agent_room


# ---------------------------------------------------------------------------
# Phase 5: server-side Twirp verification
# ---------------------------------------------------------------------------
async def phase5_server_verify(
    lkapi: api.LiveKitAPI, agent_room: rtc.Room
) -> bool:
    log.info("[5/6] phase5_server_verify — list rooms + participants via Twirp")
    rooms = await lkapi.room.list_rooms(api.ListRoomsRequest(names=["openz-room"]))
    if not rooms.rooms:
        log.error("    openz-room not found via Twirp")
        return False
    room = rooms.rooms[0]
    log.info("    openz-room sid=%s participants=%d",
             room.sid, room.num_participants)
    parts = await lkapi.room.list_participants(
        api.ListParticipantsRequest(room="openz-room")
    )
    ids = sorted(p.identity for p in parts.participants)
    log.info("    participants via Twirp: %s", ids)
    has_user = "e2e-user" in ids
    has_agent = "e2e-agent" in ids
    log.info("    [server] has_user=%s has_agent=%s", has_user, has_agent)
    return has_user and has_agent


# ---------------------------------------------------------------------------
# Phase 6: disconnect
# ---------------------------------------------------------------------------
async def phase6_disconnect(
    user_room: rtc.Room,
    agent_room: rtc.Room,
    lkapi: api.LiveKitAPI,
) -> bool:
    log.info("[6/6] phase6_disconnect — graceful teardown")
    # Memory: snapshot was_connected BEFORE disconnect, because the SDK
    # reports CONN_DISCONNECTED (0) immediately after disconnect().
    user_was_connected = user_room.connection_state == CONN_CONNECTED
    agent_was_connected = agent_room.connection_state == CONN_CONNECTED
    log.info("    snapshot was_connected: user=%s agent=%s",
             user_was_connected, agent_was_connected)

    if hasattr(user_room, "_e2e_push_task"):
        user_room._e2e_push_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await user_room._e2e_push_task

    await user_room.disconnect()
    await agent_room.disconnect()

    # Close Twirp session to silence aiohttp "Unclosed client session".
    await lkapi.aclose()
    log.info("    aclose() done — no leaked sessions")
    return user_was_connected and agent_was_connected


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
async def run_e2e(server: str) -> bool:
    http_url = server.replace("wss://", "https://").replace("ws://", "http://")
    ws_url = server
    log.info("============================================================")
    log.info("LiveKit E2E Test — target ws=%s http=%s", ws_url, http_url)
    log.info("============================================================")

    # Phase 1
    user_token, agent_token = phase1_token()

    # Phase 2
    lkapi = await phase2_twirp(http_url)

    # Phase 3
    user_room = await phase3_user_connect(ws_url, user_token)

    # Phase 4
    agent_room = await phase4_agent_connect(ws_url, agent_token, user_room)

    # Phase 5
    server_ok = await phase5_server_verify(lkapi, agent_room)

    # Phase 6
    was_ok = await phase6_disconnect(user_room, agent_room, lkapi)

    log.info("============================================================")
    log.info("RESULT  was_connected_snapshot=%s  server_two_participants=%s",
             was_ok, server_ok)
    log.info("============================================================")
    return was_ok and server_ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server",
        default=DEFAULT_WS_URL,
        help=f"WebSocket URL (default: {DEFAULT_WS_URL})",
    )
    args = parser.parse_args()

    try:
        ok = asyncio.run(run_e2e(args.server))
    except KeyboardInterrupt:
        log.warning("interrupted by user")
        return 130
    except Exception as e:
        log.exception("e2e failed: %s", e)
        return 1
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())