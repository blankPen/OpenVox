"""End-to-end test: fake user joins a LiveKit room, exchanges multiple turns of
audio with the agent, and verifies each agent response is non-silent.

Prerequisites:
- A running LiveKit server. Default: ws://localhost:7880 (local docker).
  Override via E2E_LIVEKIT_URL or LIVEKIT_URL env var. The vendored realtime
  plugin's WebRTC handshake through Cloudflare tunnel takes >10s and triggers
  worker process restarts mid-test, so local server is recommended.
- Worker running: `LIVEKIT_URL=ws://localhost:7880 python main.py start` in
  another terminal (use the SAME URL as the test).
- .env with LIVEKIT_API_KEY / LIVEKIT_API_SECRET / VOLCENGINE_REALTIME_*.
- Audio fixtures in tests/fixtures/audio/ — regenerate via tests/fixtures/gen_audio.py.

The test runs 4 turns: hello / ask_time / load_weather_skill / ask_weather.
Each turn sends a TTS-synthesized fixture, waits for non-silent response audio,
asserts non-zero amplitude, and saves the response to tests/fixtures/out/.

To run:
    source .venv/bin/activate
    pytest tests/e2e_realtime.py -v -s
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

# Default to local LiveKit (faster, no cloudflared latency). Override with E2E_LIVEKIT_URL.
LIVEKIT_URL = os.environ.get("E2E_LIVEKIT_URL") or os.environ.get("LIVEKIT_URL", "ws://localhost:7880")
API_KEY = os.environ["LIVEKIT_API_KEY"]
API_SECRET = os.environ["LIVEKIT_API_SECRET"]
AGENT_NAME = os.environ.get("AGENT_NAME", "openvox")
ROOM_NAME = os.environ.get("E2E_ROOM_NAME", f"e2e-realtime-test-{os.getpid()}")

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "audio"
OUT_DIR = ROOT / "tests" / "fixtures" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Multi-turn conversation: each turn is (fixture_name, expected_keywords_any_of)
# v0.1 stable: 4 turns with 1.5s pause between turns. Use local LiveKit
# (ws://localhost:7880) to avoid cloudflared-tunnel-induced worker restart.
TURNS: list[tuple[str, tuple[str, ...]]] = [
    ("hello", ("你好", "您好", "在", "嗨", "小语", "hello", "hi")),
    ("ask_time", ("点", "时间", "时", "分", "现在")),
    ("load_weather_skill", ("weather", "天气", "skill", "加载", "已")),
    ("ask_weather", ("北京", "天气", "晴", "雨", "云", "度", "风", "北")),
]

pytestmark = pytest.mark.e2e

SAMPLE_RATE = 48000  # LiveKit AudioStream default
PER_TURN_TIMEOUT = 30.0  # max wait for an agent response


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

async def _gen_token(identity: str, room: str) -> str:
    token = api.AccessToken(API_KEY, API_SECRET) \
        .with_identity(identity) \
        .with_name(identity) \
        .with_grants(api.VideoGrants(
            room_join=True,
            room=room,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
        ))
    return token.to_jwt()


async def _dispatch_agent() -> None:
    async with api.LiveKitAPI(LIVEKIT_URL, API_KEY, API_SECRET) as lk:
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME, room=ROOM_NAME,
            )
        )


def _wav_to_pcm(path: Path) -> tuple[bytes, int, int]:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        pcm = wf.readframes(wf.getnframes())
    return pcm, sr, ch


async def _publish_wav(audio_source: rtc.AudioSource, wav_path: Path) -> None:
    """Stream PCM frames from a WAV file into the audio source, 20ms per frame."""
    pcm, sr, ch = _wav_to_pcm(wav_path)
    assert sr == 16000, f"fixture must be 16kHz, got {sr}"
    assert ch == 1, f"fixture must be mono, got {ch}"

    frame_samples = 320  # 20ms at 16kHz
    bytes_per_sample = 2
    samples_total = len(pcm) // bytes_per_sample
    for i in range(0, samples_total, frame_samples):
        chunk = pcm[i*bytes_per_sample : (i+frame_samples)*bytes_per_sample]
        if len(chunk) < frame_samples * bytes_per_sample:
            chunk = chunk + b"\x00" * (frame_samples * bytes_per_sample - len(chunk))
        frame = rtc.AudioFrame(chunk, sr, ch, frame_samples)
        await audio_source.capture_frame(frame)
        await asyncio.sleep(0.02)


def _save_wav(pcm: bytes, path: Path) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)


def _is_silent(pcm: bytes, threshold: int = 200) -> bool:
    if not pcm:
        return True
    samples = struct.unpack(f"<{len(pcm)//2}h", pcm)
    return max(abs(s) for s in samples) < threshold


def _max_amplitude(pcm: bytes) -> int:
    if not pcm:
        return 0
    samples = struct.unpack(f"<{len(pcm)//2}h", pcm)
    return max(abs(s) for s in samples)


# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------

def test_realtime_multi_turn_conversation():
    """e2e multi-turn: open greeting + N turns of send-audio + verify-response.

    Uses an AudioStream that's set up IMMEDIATELY when the agent's track is
    subscribed, and stays open for the whole test, so we don't miss audio
    frames that arrive between "subscribed" and "ready to record".
    """
    print(f"\n[e2e] room={ROOM_NAME!r} agent={AGENT_NAME!r}")
    asyncio.run(_run_test())


async def _run_test() -> None:
    # 1. Dispatch agent
    print("[e2e] dispatching agent...")
    await _dispatch_agent()

    # 2. Connect as fake participant (unique identity per run to avoid stale state)
    fake_identity = f"fake_alice_{os.getpid()}_{int(asyncio.get_event_loop().time())}"
    print(f"[e2e] connecting as {fake_identity}...")
    token = await _gen_token(fake_identity, ROOM_NAME)
    room = rtc.Room()

    # 3. Subscribe to agent audio IMMEDIATELY when track_subscribed fires.
    #    Set up the AudioStream right away (don't wait) and collect frames in
    #    a background task. Per-turn "did the agent respond" check inspects
    #    frame chunks since the previous turn's end.
    chunks_lock = asyncio.Lock()
    all_chunks: list[bytes] = []
    stream_ready = asyncio.Event()
    agent_audio_stream: rtc.AudioStream | None = None

    async def reader() -> None:
        assert agent_audio_stream is not None
        async for ev in agent_audio_stream:
            if ev.frame is not None:
                async with chunks_lock:
                    all_chunks.append(bytes(ev.frame.data))

    @room.on("track_subscribed")
    def on_track(track, publication, participant):
        nonlocal agent_audio_stream
        if participant.identity == fake_identity:
            return
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        print(f"[e2e] subscribed to audio from {participant.identity}")
        if agent_audio_stream is None:
            agent_audio_stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1)
            asyncio.create_task(reader())
            stream_ready.set()

    await room.connect(LIVEKIT_URL, token)
    print("[e2e] connected to room")

    # 4. Publish mic track (must be after connect)
    audio_source = rtc.AudioSource(16000, 1)
    mic_track = rtc.LocalAudioTrack.create_audio_track("mic", audio_source)
    publish_opts = rtc.TrackPublishOptions()
    publish_opts.source = rtc.TrackSource.SOURCE_MICROPHONE
    await room.local_participant.publish_track(mic_track, publish_opts)
    print("[e2e] mic track published")

    # 4. Wait for agent to join
    print("[e2e] waiting for agent to join...")
    deadline = asyncio.get_event_loop().time() + 20
    while not room.remote_participants:
        if asyncio.get_event_loop().time() > deadline:
            await room.disconnect()
            pytest.fail("agent did not join within 20s")
        await asyncio.sleep(0.2)
    print(f"[e2e] agent joined: {list(p.identity for p in room.remote_participants.values())}")

    # 5. Wait for agent's audio track to be subscribed (so our stream is set up)
    print("[e2e] waiting for agent's audio track subscription...")
    try:
        await asyncio.wait_for(stream_ready.wait(), timeout=20.0)
    except asyncio.TimeoutError:
        await room.disconnect()
        pytest.fail("agent did not publish audio within 20s")
    print("[e2e] agent audio stream active")

    # 6. Multi-turn: send each fixture, wait for non-silent response, verify
    for turn_idx, (fixture_name, keywords) in enumerate(TURNS):
        fixture = FIXTURE_DIR / f"{fixture_name}.wav"
        if not fixture.exists():
            pytest.skip(f"fixture missing: {fixture} — run tests/fixtures/gen_audio.py")

        out_wav = OUT_DIR / f"turn{turn_idx}_{fixture_name}.wav"
        print(f"\n[e2e] === Turn {turn_idx}: sending {fixture_name} ===")

        # Mark the chunk count BEFORE we send, so we only inspect frames
        # generated in response to this turn.
        async with chunks_lock:
            start_count = len(all_chunks)

        # Send the audio
        await _publish_wav(audio_source, fixture)
        print(f"[e2e] turn {turn_idx}: audio sent ({fixture.stat().st_size} bytes)")

        # Wait for new non-silent chunks to appear (up to PER_TURN_TIMEOUT)
        responded = await _wait_for_response(
            all_chunks, chunks_lock, start_count, PER_TURN_TIMEOUT,
        )
        if not responded:
            await room.disconnect()
            pytest.fail(f"turn {turn_idx} ({fixture_name}): no non-silent response within {PER_TURN_TIMEOUT}s")

        # Save the response audio
        async with chunks_lock:
            turn_pcm = b"".join(all_chunks[start_count:])
        _save_wav(turn_pcm, out_wav)
        duration = len(turn_pcm) / (SAMPLE_RATE * 2)
        max_amp = _max_amplitude(turn_pcm)
        print(f"[e2e] turn {turn_idx}: recorded {len(turn_pcm)} bytes, "
              f"{duration:.2f}s, max_amp={max_amp} → {out_wav.name}")
        assert duration > 0.3, f"turn {turn_idx} response too short: {duration:.2f}s"
        assert max_amp >= 200, f"turn {turn_idx} response silent: max_amp={max_amp}"

        # Pause between turns to let agent's TTS drain fully before next input
        await asyncio.sleep(1.5)

    await room.disconnect()
    if agent_audio_stream is not None:
        await agent_audio_stream.aclose()
    print(f"\n[e2e] PASS: all {len(TURNS)} turns received non-silent audio responses")


async def _wait_for_response(
    all_chunks: list[bytes],
    chunks_lock: asyncio.Lock,
    start_count: int,
    timeout: float,
) -> bool:
    """Poll until new non-silent chunks appear (or timeout)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with chunks_lock:
            new_chunks = all_chunks[start_count:]
        if new_chunks:
            new_pcm = b"".join(new_chunks)
            if not _is_silent(new_pcm, threshold=200):
                return True
        await asyncio.sleep(0.3)
    return False
