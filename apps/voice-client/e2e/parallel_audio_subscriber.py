#!/usr/bin/env python3
"""Parallel audio subscriber — joins the agent room as a second participant
and records the agent's audio track to a WAV file.

Used by `run_e2e_ui.py` to verify the agent's audio actually reaches a
client (the iOS simulator) — we read the WAV amplitude/duration after
each phase and assert non-silent.

Env vars (all required except DURATION):
  E2E_ROOM_NAME            room to join (e.g. openz-room-mrhdcume-3mkp6)
  E2E_AUDIO_OUT            output wav path
  E2E_RECORD_DURATION_SEC  default 5
  E2E_LIVEKIT_URL          default wss://livekit.openz.top:7443
  E2E_API_KEY              default openz
  E2E_API_SECRET           default <dev secret>
"""
from __future__ import annotations
import asyncio
import os
import sys
import time

from livekit import api, rtc


URL = os.environ.get("E2E_LIVEKIT_URL", "wss://livekit.openz.top:7443")
KEY = os.environ.get("E2E_API_KEY", "openz")
SECRET = os.environ.get(
    "E2E_API_SECRET",
    "35b58a62c4a6f5a188c8537999e0524dbb0b697085fc3660bf9564d5dc083ce6",
)
ROOM = os.environ["E2E_ROOM_NAME"]
OUT = os.environ["E2E_AUDIO_OUT"]
DURATION = float(os.environ.get("E2E_RECORD_DURATION_SEC", "5"))


async def gen_token(identity: str, room: str) -> str:
    token = (
        api.AccessToken(KEY, SECRET)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
    )
    return token.to_jwt()


async def main() -> int:
    identity = f"subscriber-{os.getpid()}-{int(time.time())}"
    token = await gen_token(identity, ROOM)
    room = rtc.Room()

    chunks: list[bytes] = []
    got_audio = asyncio.Event()
    sample_rate_holder = {"sr": 16000}

    @room.on("track_subscribed")
    def on_track(track, publication, participant):
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        print(
            f"[subscriber] audio subscribed from {participant.identity}",
            flush=True,
        )

        async def reader():
            async for ev in rtc.AudioStream(
                track, sample_rate=16000, num_channels=1
            ):
                if ev.frame is not None:
                    chunks.append(bytes(ev.frame.data))
                    sample_rate_holder["sr"] = ev.frame.sample_rate
                    got_audio.set()

        asyncio.create_task(reader())

    try:
        await room.connect(URL, token)
        print(f"[subscriber] connected to {ROOM} as {identity}", flush=True)
        await asyncio.sleep(DURATION)
        print(f"[subscriber] recorded {len(chunks)} chunks", flush=True)
    finally:
        await room.disconnect()

    if not chunks:
        print("[subscriber] no audio captured", file=sys.stderr, flush=True)
        # 仍写空 wav，避免测试断言找不到文件
        import wave
        with wave.open(OUT, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
        return 1

    import wave
    pcm = b"".join(chunks)
    sr = sample_rate_holder["sr"]
    with wave.open(OUT, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm)
    print(
        f"[subscriber] wrote {OUT} ({len(pcm)} bytes, sr={sr}, "
        f"dur={len(pcm)/(sr*2):.2f}s)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))