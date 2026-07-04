"""Phase 0 最小 read_file — 仅 path 参数，无 start/end_line、无敏感路径检查。

完整版本在 Task 4 补全。Spec: docs/superpowers/specs/2026-07-05-agent-filesystem-tools-design.md §3.3
"""
from __future__ import annotations

import logging
from pathlib import Path

from livekit.agents import function_tool

logger = logging.getLogger("volcengine-agent")


@function_tool()
async def read_file(path: str) -> str:
    """读取文本文件的内容（Phase 0 最小版本）。

    Args:
        path: 绝对路径或相对 worker cwd 的路径。

    Returns:
        文件内容字符串，或 "[ERROR] ..." 开头的错误描述。
    """
    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"[ERROR] 路径 {p} 不存在"
        if p.is_dir():
            return f"[ERROR] {p} 是目录，请用 glob_files"
        content = p.read_text(encoding="utf-8")
        logger.info("[fs] read_file(path=%r) → %dc", path, len(content))
        return content
    except Exception as e:
        logger.warning("[fs] read_file ERROR path=%r err=%r", path, e)
        return f"[ERROR] {e}"


def register() -> list:
    """返回本文件提供的 tool 列表。"""
    return [read_file]