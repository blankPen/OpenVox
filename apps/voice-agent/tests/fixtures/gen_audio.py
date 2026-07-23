"""Generate TTS fixture audio for e2e tests.

Run once (and on demand when fixtures need refresh):
    source .venv/bin/activate
    python tests/fixtures/gen_audio.py

Output: tests/fixtures/audio/<name>.wav  (16kHz mono PCM, per volcengine TTS default)
"""
from __future__ import annotations

import asyncio
import os
import sys
import wave
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent.parent
load_dotenv(ROOT / ".env")

# Add workspace/ to sys.path so agent_* modules are importable
sys.path.insert(0, str(ROOT / "workspace"))

from livekit.plugins import volcengine  # noqa: E402

FIXTURES = [
    ("hello", "你好小语"),
    ("ask_time", "现在几点了"),
    ("load_weather_skill", "加载 weather skill"),
    ("ask_weather", "北京今天天气怎么样"),
    # fs tools coverage (e2e_pipeline.py 第二轮 fs 测试用)
    ("e2e_fs_read", "请读一下 ws_test/read.txt 的内容"),
    ("e2e_fs_write", "请在 ws_test 创建一个 write.txt 内容为 hello from fs e2e"),
    ("e2e_fs_glob", "请列出 ws_test 目录下的 txt 文件"),
    ("e2e_fs_bash", "请用 ls 查看 ws_test 下有什么文件"),
]

OUT_DIR = ROOT / "tests" / "fixtures" / "audio"


async def synth_one(tts, text: str, out_path: Path) -> None:
    """Synthesize one phrase to a WAV file by collecting all audio frames from a stream."""
    stream = tts.stream()
    chunks: list[bytes] = []

    async def reader() -> None:
        async for ev in stream:
            # ev is a SynthesizedAudio event with frame data
            if hasattr(ev, "frame") and ev.frame is not None:
                chunks.append(bytes(ev.frame.data))

    reader_task = asyncio.create_task(reader())
    stream.push_text(text)
    stream.end_input()
    await reader_task
    await stream.aclose()

    pcm = b"".join(chunks)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(tts.sample_rate)
        wf.writeframes(pcm)
    print(f"  wrote {out_path.name} ({len(pcm)} bytes, {len(pcm) / (tts.sample_rate * 2):.2f}s)")


async def main() -> None:
    import aiohttp
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    async with aiohttp.ClientSession() as session:
        tts = volcengine.TTS(
            app_id=os.environ["VOLCENGINE_TTS_APP_ID"],
            access_token=os.environ["VOLCENGINE_TTS_ACCESS_TOKEN"],
            http_session=session,
        )
        for name, text in FIXTURES:
            out_path = OUT_DIR / f"{name}.wav"
            if out_path.exists() and "--force" not in sys.argv:
                print(f"  skip {out_path.name} (exists, use --force to regen)")
                continue
            print(f"  synth: {text!r} → {out_path.name}")
            await synth_one(tts, text, out_path)
    print(f"TTS sample_rate={tts.sample_rate}, voice={tts._opts.voice}")


if __name__ == "__main__":
    asyncio.run(main())
