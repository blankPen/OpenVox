"""End-to-end test: fake user joins a LiveKit room, verifies the agent's
opening greeting audio flows back via the full e2e pipe.

Prerequisites (all in .env):
- LIVEKIT_URL (e.g. wss://your-livekit-server:7443)
- LIVEKIT_API_KEY / LIVEKIT_API_SECRET
- VOLCENGINE_REALTIME_APP_ID / VOLCENGINE_REALTIME_ACCESS_TOKEN

The worker must already be running (`python main.py start` in another terminal).
"""
from __future__ import annotations

import asyncio
import os
import struct
import wave
from pathlib import Path

import pytest
from dotenv import load_dotenv
from livekit import api, rtc

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

LIVEKIT_URL = os.environ["LIVEKIT_URL"]
API_KEY = os.environ["LIVEKIT_API_KEY"]
API_SECRET = os.environ["LIVEKIT_API_SECRET"]
AGENT_NAME = os.environ.get("AGENT_NAME", "volcengine-agent")
ROOM_NAME = os.environ.get("E2E_ROOM_NAME", f"e2e-realtime-test-{os.getpid()}")

OUT_DIR = ROOT / "tests" / "fixtures" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

pytestmark = pytest.mark.e2e


async def _gen_token(identity: str, room: str) -> str:
    token = api.AccessToken(API_KEY, API_SECRET) \
        .with_identity(identity) \
        .with_name(identity) \
        .with_grants(api.VideoGrants(
            room_join=True,
            room=room,
            can_publish=True,
            can_subscribe=True,
        ))
    return token.to_jwt()


async def _dispatch_agent() -> None:
    async with api.LiveKitAPI(LIVEKIT_URL, API_KEY, API_SECRET) as lk:
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME, room=ROOM_NAME,
            )
        )


async def _record_track(track: rtc.Track, duration_s: float, out_path: Path) -> tuple[bytes, int]:
    """Subscribe to a remote audio track, record for duration_s, save to WAV."""
    SAMPLE_RATE = 48000
    audio_stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1)
    chunks: list[bytes] = []

    async def reader() -> None:
        async for ev in audio_stream:
            if ev.frame is not None:
                chunks.append(bytes(ev.frame.data))

    reader_task = asyncio.create_task(reader())
    await asyncio.sleep(duration_s)
    await audio_stream.aclose()
    try:
        await asyncio.wait_for(reader_task, timeout=2.0)
    except asyncio.TimeoutError:
        reader_task.cancel()

    pcm = b"".join(chunks)
    if pcm:
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm)
    return pcm, SAMPLE_RATE


def _is_silent(pcm: bytes, threshold: int = 200) -> bool:
    if not pcm:
        return True
    samples = struct.unpack(f"<{len(pcm)//2}h", pcm)
    return max(abs(s) for s in samples) < threshold


def test_realtime_opening_greeting():
    """e2e: join room → wait for agent → agent plays opening greeting → we receive non-silent audio.

    Exercises: LiveKit dispatch + WebRTC + Volcengine RealtimeModel WS + TTS + room audio publish.
    Doesn't send any audio (avoids duplex-capture flakiness on short responses).

    KNOWN ISSUE: Flaky on the 2nd+ run against the same worker process. The worker
    calls `closing agent session due to participant disconnect` after the first test,
    and the next dispatch's audio output chain doesn't fully reset, so our AudioStream
    gets all-zero frames. The 1st run after `python main.py start` is reliable.
    To make this CI-grade we'd need to either (a) restart worker between tests,
    (b) investigate livekit-agents session lifecycle for the leak, or (c) wait
    longer for the worker to fully reset (~10-15s).
    """
    out_wav = OUT_DIR / "agent_opening.wav"

    print(f"\n[e2e] room={ROOM_NAME!r} agent={AGENT_NAME!r}")
    asyncio.run(_run_test(out_wav))


async def _run_test(out_wav: Path) -> None:
    # 1. Dispatch agent
    print("[e2e] dispatching agent...")
    await _dispatch_agent()

    # 2. Connect as fake participant (listener only, no mic publish)
    print("[e2e] connecting as fake_alice...")
    token = await _gen_token("fake_alice", ROOM_NAME)
    room = rtc.Room()

    agent_audio_future: asyncio.Future = asyncio.get_event_loop().create_future()

    @room.on("track_subscribed")
    def on_track(track, publication, participant):
        if participant.identity == "fake_alice":
            return
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            print(f"[e2e] subscribed to audio from {participant.identity}")
            if not agent_audio_future.done():
                agent_audio_future.set_result(track)

    await room.connect(LIVEKIT_URL, token)
    print("[e2e] connected to room")

    # 3. Wait for agent to join
    print("[e2e] waiting for agent to join...")
    deadline = asyncio.get_event_loop().time() + 20
    while not room.remote_participants:
        if asyncio.get_event_loop().time() > deadline:
            await room.disconnect()
            pytest.fail("agent did not join within 20s")
        await asyncio.sleep(0.2)
    print(f"[e2e] agent joined: {list(p.identity for p in room.remote_participants.values())}")

    # 4. Wait for agent to publish audio (opening greeting triggered by RealtimeModel hello_request)
    print("[e2e] waiting for agent's audio track (up to 20s)...")
    try:
        agent_track = await asyncio.wait_for(agent_audio_future, timeout=20.0)
    except asyncio.TimeoutError:
        await room.disconnect()
        pytest.fail("agent did not publish audio within 20s")

    # 5. Wait for opening TTS to complete (greeting is ~1-3s of TTS)
    print("[e2e] waiting 3s for opening greeting to complete...")
    await asyncio.sleep(3.0)

    # 6. Record 6 seconds of audio
    print(f"[e2e] recording 6s → {out_wav.name}")
    pcm, sample_rate = await _record_track(agent_track, 6.0, out_wav)
    print(f"[e2e] recorded {len(pcm)} bytes at {sample_rate}Hz")

    await room.disconnect()

    # 7. Verify
    assert len(pcm) > 0, "no audio frames received"
    duration = len(pcm) / (sample_rate * 2)
    print(f"[e2e] recording duration: {duration:.2f}s")
    assert not _is_silent(pcm, threshold=200), "agent audio was silent (max abs < 200)"
    print(f"[e2e] PASS: received {duration:.2f}s of non-silent audio from agent")
