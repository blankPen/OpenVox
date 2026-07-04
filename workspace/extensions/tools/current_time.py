"""current_time tool — 告诉用户现在的日期时间。"""
from __future__ import annotations

from datetime import datetime

from livekit.agents import function_tool


@function_tool()
async def current_time() -> str:
    """获取当前的日期和时间。

    Returns:
        形如 "现在是 2026-07-04 14:32:10" 的字符串。
    """
    return datetime.now().strftime("现在是 %Y-%m-%d %H:%M:%S")


def register() -> list:
    """返回本文件提供的 tool 列表。"""
    return [current_time]
