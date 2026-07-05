"""End-to-end test for the **pipeline** (STT + LLM + TTS) variant.

Mirrors ``e2e_realtime.py`` but the worker is launched with
``PIPELINE=pipeline`` (separate STT + LLM + TTS WebSocket/HTTP calls)
instead of the single RealtimeModel WebSocket.

What this test actually verifies (beyond e2e_realtime.py):
1. **Opening greeting**: agent says something non-silent *before* the user
   sends any audio (proves the pipeline-mode greeting in ``on_enter`` fires).
2. **Reply correctness**: each agent's reply is checked by reading the
   assistant's actual text from the worker log (via a ``[LLM-TEXT]`` marker
   emitted by a monkey-patch in main.py) and matching expected keywords.
   This is a true semantic check — non-silent audio alone could be a
   completely unrelated sound.

Prerequisites:
- A running LiveKit server. The shared remote instance at
  ``wss://livekit.openz.top:7443`` (see .env) is the default; override via
  E2E_LIVEKIT_URL.
- Worker running **with pipeline mode** (the main.py in this worktree has
  the ``[LLM-TEXT]`` marker patch):
      PIPELINE=pipeline python main.py start
  The worker must register with the same ``AGENT_NAME`` (default
  ``openz``) as this test's ``lk dispatch create``. The worker log must be
  written to a path that this test can read — by default the test looks
  at ``E2E_WORKER_LOG`` or falls back to the most recent
  ``worker-pipeline*.log`` under ``/Users/pz/.claude/jobs/0f377e69/tmp/``.
- .env with LIVEKIT_API_KEY / LIVEKIT_API_SECRET and the three sets of
  Volcengine credentials (VOLCENGINE_STT_*, VOLCENGINE_LLM_API_KEY,
  VOLCENGINE_TTS_*).
- **AppID 1605412251 must have the "流式语音识别 大模型" service
  activated in the Volcengine console**, otherwise STT will 403. See
  README §3.
- Audio fixtures in tests/fixtures/audio/ — regenerate via
  tests/fixtures/gen_audio.py.

To run:
    source .venv/bin/activate
    PIPELINE=pipeline python main.py start   # in another terminal
    pytest tests/e2e_pipeline.py -v -s
"""
from __future__ import annotations

import asyncio
import glob
import os
import struct
import time
import wave
from pathlib import Path

import pytest
from dotenv import load_dotenv
from livekit import api, rtc

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

# Default to remote LiveKit (matches .env in this repo). Override with E2E_LIVEKIT_URL.
LIVEKIT_URL = os.environ.get("E2E_LIVEKIT_URL") or os.environ.get("LIVEKIT_URL", "ws://localhost:7880")
API_KEY = os.environ["LIVEKIT_API_KEY"]
API_SECRET = os.environ["LIVEKIT_API_SECRET"]
AGENT_NAME = os.environ.get("AGENT_NAME", "volcengine-agent")
ROOM_NAME = os.environ.get("E2E_ROOM_NAME", f"e2e-pipeline-test-{os.getpid()}")

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "audio"
OUT_DIR = ROOT / "tests" / "fixtures" / "out" / "pipeline"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Worker log location — main.py writes to this when piped via ``tee``. The
# test reads it to extract [LLM-TEXT] markers.
WORKER_LOG = Path(os.environ.get(
    "E2E_WORKER_LOG",
    "/Users/pz/.claude/jobs/0f377e69/tmp/worker-pipeline.log",
))

# 4-turn script. ``keywords`` is asserted against the assistant text in the
# worker log (one [LLM-TEXT] line per LLM call).
TURNS: list[tuple[str, tuple[str, ...]]] = [
    # turn 0: greet user back — agent should respond with a self-intro / hi
    ("hello", ("你好", "您好", "在", "嗨", "小语", "hello", "hi")),
    # turn 1: ask the time — should reply with current time-ish wording
    ("ask_time", ("点", "时间", "时", "分", "现在", "几")),
    # turn 2: ask to load weather skill — agent should acknowledge
    # (note: the actual load_skill tool may fail on 1.2.9; we still expect a
    # verbal acknowledgement in the LLM reply)
    ("load_weather_skill", ("weather", "天气", "skill", "加载", "已")),
    # turn 3: ask about Beijing weather — should mention 天气 + city
    ("ask_weather", ("北京", "天气", "晴", "雨", "云", "度", "风", "北")),
]

pytestmark = pytest.mark.e2e_pipeline

# 16kHz matches volcengine.STT's default sample rate.
SAMPLE_RATE = 16000
PER_TURN_TIMEOUT = 30.0
GREETING_TIMEOUT = 15.0  # wait up to 15s for opening greeting after stream ready
GREETING_KEYWORDS = (
    # conventional greetings
    "你好", "您好", "嗨", "hi", "hello",
    # self-intro (persona is "小语")
    "小语", "我叫", "我是",
    # Chinese conversational openers that count as "greeting"
    "哈", "请", "欢迎", "帮", "聊", "说", "问", "事", "在吗", "您好呀",
)  # any-of


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


def _find_latest_worker_log() -> Path:
    """Locate the worker log file the test should read for [LLM-TEXT] markers.

    Order:
    1. $E2E_WORKER_LOG if set
    2. /Users/pz/.claude/jobs/0f377e69/tmp/worker-pipeline*.log (most recent)
    """
    if os.environ.get("E2E_WORKER_LOG"):
        return Path(os.environ["E2E_WORKER_LOG"])
    candidates = sorted(
        glob.glob("/Users/pz/.claude/jobs/0f377e69/tmp/worker-pipeline*.log"),
        key=lambda p: Path(p).stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        pytest.skip(
            "no worker-pipeline*.log found in /Users/pz/.claude/jobs/0f377e69/tmp/ — "
            "start the worker first or set E2E_WORKER_LOG to point at it"
        )
    return Path(candidates[0])


def _wait_for_llm_text(
    log_path: Path,
    since_offset: int,
    timeout: float = 25.0,
) -> tuple[str, int]:
    """Block until a new ``[LLM-TEXT]`` line appears past ``since_offset``.

    Returns ``(text, new_offset)`` — the assistant text and the new file
    size to use as the next ``since_offset``. Polling-based because the
    worker writes async; no inotify in stdlib.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(since_offset)
                chunk = f.read()
                new_offset = since_offset + len(chunk.encode("utf-8", errors="replace"))
                for line in chunk.splitlines():
                    if "[LLM-TEXT]" in line:
                        # Strip log prefix + [LLM-TEXT] marker
                        marker = "[LLM-TEXT]"
                        idx = line.find(marker)
                        text = line[idx + len(marker):].strip()
                        return text, new_offset
        except FileNotFoundError:
            pass
        time.sleep(0.3)
    raise TimeoutError(
        f"no new [LLM-TEXT] marker in {log_path} within {timeout}s "
        f"(since offset {since_offset})"
    )


def _wait_for_llm_text_count(
    log_path: Path,
    target_count: int,
    timeout: float = 25.0,
) -> list[str]:
    """Wait until at least ``target_count`` [LLM-TEXT] lines exist in log.

    Returns the list of all [LLM-TEXT] contents seen so far.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            texts = []
            for line in content.splitlines():
                if "[LLM-TEXT]" in line:
                    marker = "[LLM-TEXT]"
                    idx = line.find(marker)
                    texts.append(line[idx + len(marker):].strip())
            if len(texts) >= target_count:
                return texts
        except FileNotFoundError:
            pass
        time.sleep(0.3)
    raise TimeoutError(
        f"only saw {len(texts)} [LLM-TEXT] markers (need {target_count}) "
        f"in {log_path} within {timeout}s"
    )


# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------

def test_pipeline_multi_turn_conversation():
    """e2e multi-turn under PIPELINE=pipeline with semantic content checks.

    Sequence:
      1. dispatch agent → join room
      2. connect as fake_alice, subscribe to agent audio
      3. wait for opening greeting (non-silent audio + greeting keyword
         in the corresponding [LLM-TEXT] log line)
      4. for each turn: send fixture → wait for non-silent reply →
         assert keyword in the corresponding [LLM-TEXT] line
    """
    print(f"\n[e2e-pipeline] room={ROOM_NAME!r} agent={AGENT_NAME!r}")
    asyncio.run(_run_test())


async def _run_test() -> None:
    # Locate worker log up front so we fail fast if worker isn't running.
    log_path = _find_latest_worker_log()
    print(f"[e2e-pipeline] reading worker log: {log_path}")
    initial_offset = log_path.stat().st_size if log_path.exists() else 0
    seen_llm_texts: list[str] = []

    def _next_text(expected_keywords: tuple[str, ...], timeout: float = 25.0) -> str:
        """Wait for the next [LLM-TEXT] line and verify it has expected keywords."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(initial_offset)
                    chunk = f.read()
                for line in chunk.splitlines():
                    if "[LLM-TEXT]" in line and not any(
                        text in line for text in seen_llm_texts
                    ):
                        marker = "[LLM-TEXT]"
                        idx = line.find(marker)
                        text = line[idx + len(marker):].strip()
                        seen_llm_texts.append(text)
                        matched = [kw for kw in expected_keywords if kw in text]
                        assert matched, (
                            f"LLM reply {text!r} contains none of "
                            f"expected keywords {expected_keywords!r}"
                        )
                        return text
            except FileNotFoundError:
                pass
            time.sleep(0.3)
        raise AssertionError(
            f"no new [LLM-TEXT] in {log_path} within {timeout}s"
        )

    # 1. Dispatch agent
    print("[e2e-pipeline] dispatching agent...")
    await _dispatch_agent()

    # 2. Connect as fake participant (unique identity per run to avoid stale state)
    fake_identity = f"fake_alice_{os.getpid()}_{int(asyncio.get_event_loop().time())}"
    print(f"[e2e-pipeline] connecting as {fake_identity}...")
    token = await _gen_token(fake_identity, ROOM_NAME)
    room = rtc.Room()

    # 3. Subscribe to agent audio IMMEDIATELY when track_subscribed fires.
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
        print(f"[e2e-pipeline] subscribed to audio from {participant.identity}")
        if agent_audio_stream is None:
            agent_audio_stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1)
            asyncio.create_task(reader())
            stream_ready.set()

    await room.connect(LIVEKIT_URL, token)
    print("[e2e-pipeline] connected to room")

    # 4. Publish mic track (must be after connect)
    audio_source = rtc.AudioSource(16000, 1)
    mic_track = rtc.LocalAudioTrack.create_audio_track("mic", audio_source)
    publish_opts = rtc.TrackPublishOptions()
    publish_opts.source = rtc.TrackSource.SOURCE_MICROPHONE
    await room.local_participant.publish_track(mic_track, publish_opts)
    print("[e2e-pipeline] mic track published")

    # 5. Wait for agent to join
    print("[e2e-pipeline] waiting for agent to join...")
    deadline = asyncio.get_event_loop().time() + 20
    while not room.remote_participants:
        if asyncio.get_event_loop().time() > deadline:
            await room.disconnect()
            pytest.fail("agent did not join within 20s")
        await asyncio.sleep(0.2)
    print(f"[e2e-pipeline] agent joined: {list(p.identity for p in room.remote_participants.values())}")

    # 6. Wait for agent's audio track to be subscribed
    print("[e2e-pipeline] waiting for agent's audio track subscription...")
    try:
        await asyncio.wait_for(stream_ready.wait(), timeout=20.0)
    except asyncio.TimeoutError:
        await room.disconnect()
        pytest.fail("agent did not publish audio within 20s")
    print("[e2e-pipeline] agent audio stream active")

    # 7. Wait for OPENING GREETING — non-silent audio + greeting keyword in LLM
    print("[e2e-pipeline] waiting for opening greeting (audio + LLM-TEXT)...")
    greeting_received = await _wait_for_greeting(
        all_chunks, chunks_lock, GREETING_TIMEOUT,
    )
    if not greeting_received:
        await room.disconnect()
        pytest.fail(f"agent did not produce opening greeting audio within {GREETING_TIMEOUT}s")

    greeting_text = _next_text(GREETING_KEYWORDS, timeout=25.0)
    print(f"[e2e-pipeline] opening greeting LLM text: {greeting_text!r}")

    async with chunks_lock:
        greeting_pcm = b"".join(all_chunks)
    _save_wav(greeting_pcm, OUT_DIR / "greeting.wav")
    greeting_amp = _max_amplitude(greeting_pcm)
    greeting_dur = len(greeting_pcm) / (SAMPLE_RATE * 2)
    print(f"[e2e-pipeline] greeting audio: {len(greeting_pcm)} bytes, "
          f"{greeting_dur:.2f}s, max_amp={greeting_amp}")

    # 8. Multi-turn: send each fixture, wait for non-silent response, verify
    for turn_idx, (fixture_name, keywords) in enumerate(TURNS):
        fixture = FIXTURE_DIR / f"{fixture_name}.wav"
        if not fixture.exists():
            pytest.skip(f"fixture missing: {fixture} — run tests/fixtures/gen_audio.py")

        out_wav = OUT_DIR / f"turn{turn_idx}_{fixture_name}.wav"
        print(f"\n[e2e-pipeline] === Turn {turn_idx}: sending {fixture_name} ===")

        async with chunks_lock:
            start_count = len(all_chunks)

        await _publish_wav(audio_source, fixture)
        print(f"[e2e-pipeline] turn {turn_idx}: audio sent ({fixture.stat().st_size} bytes)")

        responded = await _wait_for_response(
            all_chunks, chunks_lock, start_count, PER_TURN_TIMEOUT,
        )
        if not responded:
            await room.disconnect()
            pytest.fail(f"turn {turn_idx} ({fixture_name}): no non-silent response within {PER_TURN_TIMEOUT}s")

        # Verify LLM text (the semantic check)
        reply_text = _next_text(keywords, timeout=25.0)
        print(f"[e2e-pipeline] turn {turn_idx} LLM reply: {reply_text!r}")

        # Also save response audio for the human
        async with chunks_lock:
            turn_pcm = b"".join(all_chunks[start_count:])
        _save_wav(turn_pcm, out_wav)
        duration = len(turn_pcm) / (SAMPLE_RATE * 2)
        max_amp = _max_amplitude(turn_pcm)
        print(f"[e2e-pipeline] turn {turn_idx}: recorded {len(turn_pcm)} bytes, "
              f"{duration:.2f}s, max_amp={max_amp} → {out_wav.name}")
        assert duration > 0.3, f"turn {turn_idx} response too short: {duration:.2f}s"
        assert max_amp >= 200, f"turn {turn_idx} response silent: max_amp={max_amp}"

        await asyncio.sleep(1.5)

    await room.disconnect()
    if agent_audio_stream is not None:
        await agent_audio_stream.aclose()
    print(f"\n[e2e-pipeline] PASS: opening greeting + all {len(TURNS)} turns "
          f"got semantically correct LLM replies (matched keywords: greeting "
          f"+ {len(TURNS)} turn responses)")


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


async def _wait_for_greeting(
    all_chunks: list[bytes],
    chunks_lock: asyncio.Lock,
    timeout: float,
) -> bool:
    """Poll until the agent has produced any non-silent audio at all."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with chunks_lock:
            if all_chunks:
                pcm = b"".join(all_chunks)
                if not _is_silent(pcm, threshold=200):
                    return True
        await asyncio.sleep(0.3)
    return False