"""web_search tool — 使用 DuckDuckGo 搜索互联网 (ddgs 纯 Python 实现)。"""
from __future__ import annotations

import asyncio
import logging

from livekit.agents import function_tool

logger = logging.getLogger("volcengine-agent")

_TIME_MAP = {"d": "d", "w": "w", "m": "m", "y": "y"}


def _run_search(query: str, num: int = 10, time_range: str | None = None) -> list[dict[str, str]]:
    """Execute search via ddgs (pure Python, no subprocess)."""
    from ddgs import DDGS

    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=num, timelimit=time_range or None))

    return [
        {"title": r["title"], "url": r["href"], "abstract": r["body"]}
        for r in results
    ]


@function_tool()
async def web_search(query: str, num: int = 10, time_range: str = "") -> str:
    """使用 DuckDuckGo 搜索互联网，返回结构化结果。

    底层调用 ddgs (纯 Python DuckDuckGo 搜索库)，结果格式化为人类可读的文本返回。

    Args:
        query: 搜索关键词。
        num: 返回结果数量，范围 1-10，默认 10。
        time_range: 时间过滤，空字符串表示不限时间。
            "d" = 最近一天，"w" = 最近一周，"m" = 最近一月，"y" = 最近一年。

    Returns:
        格式化的搜索结果文本，包含标题、URL 和摘要。
    """
    num = max(1, min(num, 10))
    time_range = time_range.strip().lower()
    if time_range and time_range not in _TIME_MAP:
        time_range = ""

    try:
        results = await asyncio.to_thread(
            _run_search, query, num=num, time_range=time_range or None
        )
    except Exception as e:
        logger.error(f"web_search failed: {e}")
        return f"搜索失败: {e}"

    if not results:
        return f'未找到关于 "{query}" 的搜索结果。'

    lines = [f'🔍 搜索 "{query}" 共 {len(results)} 条结果:\n']
    for i, r in enumerate(results, 1):
        lines.append(f"## {i}. {r['title']}")
        lines.append(f"   URL: {r['url']}")
        lines.append(f"   {r['abstract']}")
        lines.append("")
    return "\n".join(lines)


def register() -> list:
    """返回本文件提供的 tool 列表。"""
    return [web_search]
