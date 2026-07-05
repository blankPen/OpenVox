"""Tests for workspace/extensions/tools/web_search.py."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def web_search_tools():
    """Import and register the web_search tool."""
    from extensions.tools.web_search import web_search, register
    return register()


def _fake_ddgs(results: list[dict], side_effect: Exception | None = None):
    """Build a mock DDGS context manager that returns the given results."""
    mock_ddgs = MagicMock()

    if side_effect:
        mock_ddgs.__enter__ = MagicMock(side_effect=side_effect)
    else:
        mock_inst = MagicMock()
        mock_inst.text.return_value = results
        mock_ddgs.__enter__.return_value = mock_inst

    mock_ddgs.__exit__.return_value = None
    return mock_ddgs


# -- 注册 & 元数据 -----------------------------------------------------------


def test_register_returns_list_with_one_tool(web_search_tools):
    """register() 应该返回包含 web_search 的列表。"""
    assert isinstance(web_search_tools, list)
    assert len(web_search_tools) == 1


def test_tool_has_livekit_info(web_search_tools):
    """tool 应该有 __livekit_tool_info 属性。"""
    tool = web_search_tools[0]
    assert hasattr(tool, "__livekit_tool_info")
    info = tool.__livekit_tool_info
    assert info.name == "web_search"
    assert "DuckDuckGo" in info.description or "ddgs" in info.description


def test_tool_is_callable(web_search_tools):
    """tool 必须是可调用的。"""
    tool = web_search_tools[0]
    assert callable(tool)


# -- 正常搜索 ----------------------------------------------------------------


def test_web_search_returns_formatted_results(web_search_tools):
    """正常搜索返回格式化的结果字符串。"""
    web_search = web_search_tools[0]
    fake_results = [
        {"title": "Title A", "href": "https://a.com", "body": "Abstract A"},
        {"title": "Title B", "href": "https://b.com", "body": "Abstract B"},
    ]

    async def _run():
        mock = _fake_ddgs(fake_results)
        with patch("ddgs.DDGS", return_value=mock):
            return await web_search("test query")

    result = asyncio.run(_run())
    assert "test query" in result
    assert "Title A" in result
    assert "https://a.com" in result
    assert "Abstract A" in result
    assert "Title B" in result
    assert "https://b.com" in result
    assert "Abstract B" in result
    assert "2 条结果" in result


def test_web_search_respects_num_parameter(web_search_tools):
    """num 参数应传递给 ddgs.text 的 max_results。"""
    web_search = web_search_tools[0]

    async def _run():
        mock = _fake_ddgs([])
        with patch("ddgs.DDGS", return_value=mock):
            await web_search("q", num=7)
        return mock.__enter__.return_value.text.call_args

    call_args = asyncio.run(_run())
    _, kwargs = call_args
    assert kwargs["max_results"] == 7


def test_web_search_clamps_num_to_range(web_search_tools):
    """num 超出范围时应 clamp 到 1-10。"""
    web_search = web_search_tools[0]

    async def _run(num):
        mock = _fake_ddgs([])
        with patch("ddgs.DDGS", return_value=mock):
            await web_search("q", num=num)
        return mock.__enter__.return_value.text.call_args

    call_args = asyncio.run(_run(50))
    _, kwargs = call_args
    assert kwargs["max_results"] == 10

    call_args2 = asyncio.run(_run(0))
    _, kwargs2 = call_args2
    assert kwargs2["max_results"] == 1


def test_web_search_default_num_is_ten(web_search_tools):
    """未传 num 时默认 max_results=10。"""
    web_search = web_search_tools[0]

    async def _run():
        mock = _fake_ddgs([])
        with patch("ddgs.DDGS", return_value=mock):
            await web_search("q")
        return mock.__enter__.return_value.text.call_args

    call_args = asyncio.run(_run())
    _, kwargs = call_args
    assert kwargs["max_results"] == 10


# -- time_range --------------------------------------------------------------


def test_web_search_passes_time_range(web_search_tools):
    """传入 time_range="w" 时 ddgs.text 应收到 timelimit="w"。"""
    web_search = web_search_tools[0]

    async def _run():
        mock = _fake_ddgs([])
        with patch("ddgs.DDGS", return_value=mock):
            await web_search("q", time_range="w")
        return mock.__enter__.return_value.text.call_args

    call_args = asyncio.run(_run())
    _, kwargs = call_args
    assert kwargs["timelimit"] == "w"


@pytest.mark.parametrize("tr_value", ["d", "w", "m", "y"])
def test_web_search_accepts_all_valid_time_ranges(web_search_tools, tr_value):
    """d/w/m/y 四个合法 time_range 都应通过。"""
    web_search = web_search_tools[0]

    async def _run():
        mock = _fake_ddgs([])
        with patch("ddgs.DDGS", return_value=mock):
            await web_search("q", time_range=tr_value)
        return mock.__enter__.return_value.text.call_args

    call_args = asyncio.run(_run())
    _, kwargs = call_args
    assert kwargs["timelimit"] == tr_value


def test_web_search_ignores_invalid_time_range(web_search_tools):
    """非法的 time_range 应被忽略，timelimit=None。"""
    web_search = web_search_tools[0]

    async def _run():
        mock = _fake_ddgs([])
        with patch("ddgs.DDGS", return_value=mock):
            await web_search("q", time_range="x")
        return mock.__enter__.return_value.text.call_args

    call_args = asyncio.run(_run())
    _, kwargs = call_args
    assert kwargs["timelimit"] is None


def test_web_search_empty_time_range_defaults_none(web_search_tools):
    """空字符串 time_range 默认 timelimit=None。"""
    web_search = web_search_tools[0]

    async def _run():
        mock = _fake_ddgs([])
        with patch("ddgs.DDGS", return_value=mock):
            await web_search("q")
        return mock.__enter__.return_value.text.call_args

    call_args = asyncio.run(_run())
    _, kwargs = call_args
    assert kwargs["timelimit"] is None


# -- 异常路径 ----------------------------------------------------------------


def test_web_search_handles_error(web_search_tools):
    """底层抛异常时应返回错误字符串而非抛出。"""
    web_search = web_search_tools[0]

    async def _run():
        mock = _fake_ddgs([], side_effect=RuntimeError("rate limited"))
        with patch("ddgs.DDGS", return_value=mock):
            return await web_search("test")

    result = asyncio.run(_run())
    assert "搜索失败" in result
    assert "rate limited" in result


def test_web_search_handles_empty_results(web_search_tools):
    """空结果列表应返回提示信息。"""
    web_search = web_search_tools[0]

    async def _run():
        mock = _fake_ddgs([])
        with patch("ddgs.DDGS", return_value=mock):
            return await web_search("noresults")

    result = asyncio.run(_run())
    assert "未找到" in result
    assert "noresults" in result


def test_web_search_handles_missing_keys_in_result(web_search_tools):
    """ddgs 返回缺少 key 的条目时不应崩溃。"""
    web_search = web_search_tools[0]
    fake_results = [{"href": "https://x.com", "title": "", "body": ""}]

    async def _run():
        mock = _fake_ddgs(fake_results)
        with patch("ddgs.DDGS", return_value=mock):
            return await web_search("q")

    result = asyncio.run(_run())
    assert "https://x.com" in result
    assert "1 条结果" in result
