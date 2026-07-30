"""Regression: livekit openai.LLM + 火山引擎 Hermes 网关的 None-choices usage 块。

背景：
  livekit-plugins-openai 的 inference/llm.py:432 写的是
      for choice in chunk.choices:
  火山引擎 Hermes 网关在 stream_options.include_usage=True 时发的 usage-only
  chunk 形如 {"choices": null, "usage": {...}}，触发 TypeError。

main.py 在模块顶部装了一个 monkey-patch，把 None-choices 的块在到达 livekit
之前过滤掉。本测试覆盖两层：
1. 单元：_FilterNoneChoices 自身能正确丢 None-choices 块
2. 集成：构造 main.openai.LLM，mock openai SDK 让它 yield None-choices chunk，
   调 chat() 必须不抛 TypeError 且有效 chunk 透传。

main._cfg 用 fake Config 注入，避免依赖 ~/.openvox/config.json。
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import (
    Choice as ChunkChoice,
    ChoiceDelta,
)


# ───────── helpers ─────────


class _FakeStream:
    """async iterable，可指定要 yield 的 chunk 列表 + aclose。"""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._idx = 0
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._chunks):
            raise StopAsyncIteration
        c = self._chunks[self._idx]
        self._idx += 1
        return c

    async def aclose(self):
        self.closed = True


def _make_valid_chunk(content: str = "hello", finish_reason=None) -> ChatCompletionChunk:
    """构造一个 choices=[Choice(delta=ChoiceDelta(content=...))] 的真实 openai 类型 chunk。"""
    return ChatCompletionChunk(
        id="test",
        object="chat.completion.chunk",
        created=0,
        model="test",
        choices=[
            ChunkChoice(
                index=0,
                delta=ChoiceDelta(content=content, role="assistant"),
                finish_reason=finish_reason,
            )
        ],
    )


def _make_none_choices_chunk() -> ChatCompletionChunk:
    """Hermes 发的那种 usage-only 块：choices=null，但 usage 有数据。

    用 model_construct 绕过 pydantic 校验——SDK 在生产里靠 streaming response
    parser 容忍这个 None；正常 __init__ 校验不允许 choices=None。
    """
    return ChatCompletionChunk.model_construct(
        id="test",
        object="chat.completion.chunk",
        created=0,
        model="test",
        choices=None,
        usage=None,
    )


# ───────── 单元测试 ─────────


def test_filter_drops_none_choices_chunks():
    """_FilterNoneChoices 必须跳过 choices=None 的块，保留其他。"""
    from openvox_worker.main import _FilterNoneChoices

    good1 = _make_valid_chunk("first")
    bad = _make_none_choices_chunk()
    good2 = _make_valid_chunk("third")

    filtered = _FilterNoneChoices(_FakeStream([good1, bad, good2]))

    async def collect():
        return [c async for c in filtered]

    chunks = asyncio.run(collect())
    assert chunks == [good1, good2], f"expected [good1, good2], got {chunks}"


def test_filter_aclose_propagates():
    """_FilterNoneChoices.aclose 必须调到 inner 的 aclose。"""
    from openvox_worker.main import _FilterNoneChoices

    inner = _FakeStream([])
    filtered = _FilterNoneChoices(inner)
    asyncio.run(filtered.aclose())
    assert inner.closed is True


def test_filter_is_patched_on_openai_sdk():
    """导入 main 必须把 openai.AsyncCompletions.create 替换成 _safe_create。"""
    # 重新加载保证拿到最新 patch
    import sys
    for mod in list(sys.modules):
        if mod == "openvox_worker.main" or mod.startswith("openai"):
            del sys.modules[mod]
    import openvox_worker.main as main  # noqa: F401
    from openai.resources.chat.completions import AsyncCompletions
    assert AsyncCompletions.create is main._safe_create, (
        "patch not applied: AsyncCompletions.create is still original"
    )


# ───────── 集成测试 ─────────


def _make_fake_config() -> "Config":
    from openvox_worker.config import Config
    return Config({
        "volcengine": {
            "stt": {"app_id": "stt-id", "access_token": "stt-token"},
            "tts": {"app_id": "tts-id", "access_token": "tts-token"},
        },
        "livekit": {"agent_name": "test-agent"},
        "hermes": {
            "api_base": "http://127.0.0.1:9999/v1",
            "api_key": "test-key",
            "model": "test-model",
        },
    })


@pytest.fixture
def fake_config(monkeypatch):
    """注入 fake Config 到 main，绕开 ~/.openvox/config.json。"""
    import openvox_worker.main as main
    monkeypatch.setattr(main, "_cfg", _make_fake_config())
    return main._cfg


def test_llm_chat_survives_none_choices_chunk(fake_config, monkeypatch):
    """mock openai SDK 让它 yield None-choices chunk → livekit chat() 必须不抛。

    测试构造：
    - 真实 import main（触发 patch 把 _AsyncCompletions.create 换成 _safe_create）
    - patch `main._orig_create` 让它返回包含一个 None-choices 块的 fake 流
    - 调 main._build_session() 拿到真 openai.LLM
    - 调 llm.chat()，期望 _safe_create → _orig_create（被 mock） → 流过滤 →
      至少一个有效 ChatChunk 透传
    """
    import openvox_worker.main as main

    bad_chunk = _make_none_choices_chunk()
    good_chunk = _make_valid_chunk("你好", finish_reason="stop")
    fake_inner_stream = _FakeStream([bad_chunk, good_chunk])

    # patch main._orig_create 而不是 patch 整个 AsyncClient — 保留 patch 链路
    async def fake_orig_create(self, **kwargs):
        return fake_inner_stream

    monkeypatch.setattr(main, "_orig_create", fake_orig_create)

    from livekit.agents import llm as livekit_llm

    async def drive():
        session = main._build_session()
        llm_inst = session.llm
        ctx = livekit_llm.ChatContext()
        ctx.add_message(role="user", content="hello")
        stream = llm_inst.chat(chat_ctx=ctx)
        return [c async for c in stream]

    chunks = asyncio.run(drive())

    # 至少一个有效 chunk 到达；None-choices 块被吞
    valid = [c for c in chunks if c.delta and c.delta.content]
    assert len(valid) >= 1, f"expected at least 1 valid chunk, got {chunks}"
    assert "".join(c.delta.content for c in valid) == "你好"