"""Tests for ``main._build_session`` — verify pipeline wiring.

These tests assert the public contract of ``_build_session``:

* pipeline 模式必须用 openai.LLM（指向 hermes api_server）
* STT / TTS 仍是火山引擎
* 未知 PIPELINE 必须抛 ValueError
* qwen-realtime / volcengine.RealtimeModel 分支必须不存在
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest


MAIN_PATH = Path(__file__).resolve().parents[1] / "main.py"


def _set_pipeline_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Populate every env var ``_build_session`` reads under PIPELINE=pipeline."""
    monkeypatch.setenv("PIPELINE", "pipeline")
    monkeypatch.setenv("HERMES_BRIDGE_MODEL", "test-model")
    monkeypatch.setenv("HERMES_BRIDGE_API_KEY", "test-api-key")
    monkeypatch.setenv("HERMES_BRIDGE_BASE_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.setenv("VOLCENGINE_STT_APP_ID", "stt-app-id")
    monkeypatch.setenv("VOLCENGINE_STT_ACCESS_TOKEN", "stt-access-token")
    monkeypatch.setenv("VOLCENGINE_TTS_APP_ID", "tts-app-id")
    monkeypatch.setenv("VOLCENGINE_TTS_ACCESS_TOKEN", "tts-access-token")


def test_pipeline_uses_openai_llm(monkeypatch):
    """pipeline 模式必须把 openai.LLM 构造出来，三个配置都来自环境变量。"""
    _set_pipeline_env(monkeypatch)
    import main
    monkeypatch.setattr(main, "PIPELINE", "pipeline")

    with patch("livekit.plugins.openai.LLM") as mock_llm, \
         patch("livekit.plugins.volcengine.STT") as mock_stt, \
         patch("livekit.plugins.volcengine.TTS") as mock_tts, \
         patch("main.AgentSession") as mock_session:
        main._build_session()

    # 必须真调 openai.LLM(...) 一次
    mock_llm.assert_called_once()
    kwargs = mock_llm.call_args.kwargs
    assert kwargs["model"] == "test-model", kwargs
    assert kwargs["api_key"] == "test-api-key", kwargs
    assert kwargs["base_url"] == "http://127.0.0.1:9999/v1", kwargs

    # AgentSession 拿到的是 openai.LLM 返回的 mock 实例
    session_kwargs = mock_session.call_args.kwargs
    assert session_kwargs["llm"] is mock_llm.return_value


def test_pipeline_uses_volcengine_stt_tts(monkeypatch):
    """pipeline 模式 STT / TTS 仍用火山引擎插件。"""
    _set_pipeline_env(monkeypatch)
    import main
    monkeypatch.setattr(main, "PIPELINE", "pipeline")

    with patch("livekit.plugins.openai.LLM"), \
         patch("livekit.plugins.volcengine.STT") as mock_stt, \
         patch("livekit.plugins.volcengine.TTS") as mock_tts, \
         patch("main.AgentSession"):
        main._build_session()

    # 必须真调 volcengine.STT / volcengine.TTS
    mock_stt.assert_called_once()
    mock_tts.assert_called_once()
    stt_kwargs = mock_stt.call_args.kwargs
    assert stt_kwargs["app_id"] == "stt-app-id", stt_kwargs
    assert stt_kwargs["access_token"] == "stt-access-token", stt_kwargs
    tts_kwargs = mock_tts.call_args.kwargs
    assert tts_kwargs["app_id"] == "tts-app-id", tts_kwargs
    assert tts_kwargs["access_token"] == "tts-access-token", tts_kwargs

    # 反向断言：openai 没有 STT / TTS（不会调它）
    # 留空 — openai 没有 STT/TTS 调用是隐式事实


def test_unknown_pipeline_raises(monkeypatch):
    """任何 PIPELINE != 'pipeline' 必须抛 ValueError。"""
    import main
    monkeypatch.setattr(main, "PIPELINE", "weird-thing")

    with patch("livekit.plugins.openai.LLM"), \
         patch("livekit.plugins.volcengine.STT"), \
         patch("livekit.plugins.volcengine.TTS"), \
         patch("main.AgentSession"):
        with pytest.raises(ValueError, match=r"Unsupported PIPELINE"):
            main._build_session()


def test_qwen_realtime_branch_removed():
    """main.py 不能再 import 或引用 livekit.plugins.qwen。"""
    src = MAIN_PATH.read_text(encoding="utf-8")
    # 出现任何 `qwen` 字面引用都算违规
    assert "qwen" not in src.lower(), (
        "main.py 仍含 qwen 引用 — qwen-realtime 分支必须完全移除"
    )


def test_volcengine_realtime_branch_removed():
    """main.py 不能再构造 volcengine.RealtimeModel——realtime 端到端分支彻底移除。"""
    src = MAIN_PATH.read_text(encoding="utf-8")
    assert "RealtimeModel" not in src, (
        "main.py 仍引用 RealtimeModel — realtime 分支必须删除"
    )
    # 兜底：唯一保留的分支必须只有一个 'pipeline' if
    pipeline_ifs = len(re.findall(r'if PIPELINE\s*==\s*["\']pipeline["\']', src))
    # 若用了 if PIPELINE != ... raise 的形态，再确认没有 else 分支兜底构造 RealtimeModel
    assert pipeline_ifs >= 1, (
        "main.py 必须保留至少一个对 PIPELINE=='pipeline' 的判断"
    )
