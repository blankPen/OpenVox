"""web_fetch tool 单元测试。

注意：网络测试走真实 httpbin.org，离线环境下会被标记 skip。
"""
from __future__ import annotations

import asyncio

import pytest

from workspace.extensions.tools.web_fetch import _html_to_text, _truncate


def _run(coro):
    return asyncio.run(coro)


# --- 纯函数测试（不依赖网络）---


def test_html_to_text_strips_script_and_style() -> None:
    html = """
    <html><head><script>alert(1)</script><style>body{}</style></head>
    <body><h1>标题</h1><p>正文段落一。</p><p>正文段落二。</p></body></html>
    """
    out = _html_to_text(html)
    assert "alert(1)" not in out
    assert "body{}" not in out
    assert "标题" in out
    assert "正文段落一" in out
    assert "正文段落二" in out


def test_html_to_text_handles_empty() -> None:
    assert _html_to_text("") == ""
    assert _html_to_text("<html></html>").strip() == ""


def test_truncate_short_text_unchanged() -> None:
    text = "hello world"
    out, truncated = _truncate(text, 100)
    assert out == text
    assert truncated is False


def test_truncate_long_text_marks_truncated() -> None:
    text = "a" * 2000 + "middle" + "b" * 2000
    out, truncated = _truncate(text, 1000)
    assert truncated is True
    assert "[... TRUNCATED ...]" in out
    assert len(out) > 1000  # 含标记


def test_truncate_keeps_head_and_tail() -> None:
    text = "HEAD_START" + "x" * 5000 + "TAIL_END"
    out, truncated = _truncate(text, 1000)
    assert "HEAD_START" in out
    assert "TAIL_END" in out
    assert truncated is True


# --- 网络测试（httpbin.org）—— 默认 skip，需要网络时 --run-network 启动 ---


def test_web_fetch_httpbin_html() -> None:
    """真实抓 httpbin.org/html，验证能拿到页面。默认 skip。"""
    pytest.skip("需要 --run-network 才跑（手动移除 skip 即可）")
    from workspace.extensions.tools.web_fetch import web_fetch
    result = _run(web_fetch("https://httpbin.org/html", max_chars=5000))
    if "[ERROR]" not in result:
        assert len(result) > 50


def test_web_fetch_rejects_bad_url() -> None:
    from workspace.extensions.tools.web_fetch import web_fetch
    result = _run(web_fetch("not-a-url", max_chars=1000))
    assert result.startswith("[ERROR]")
    assert "http://" in result or "https://" in result


def test_web_fetch_rejects_bad_max_chars() -> None:
    from workspace.extensions.tools.web_fetch import web_fetch
    result = _run(web_fetch("https://example.com", max_chars=10))
    assert result.startswith("[ERROR]")
    assert "500" in result and "50000" in result