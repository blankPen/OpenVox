"""read_file — 读文本文件，支持行范围、敏感路径告警、超大文件截断。

Spec: docs/superpowers/specs/2026-07-05-agent-filesystem-tools-design.md §3.3 / §5.2
"""
from __future__ import annotations

import logging
from pathlib import Path

from livekit.agents import function_tool

from workspace.extensions.tools.fs._sensitive import is_sensitive as _is_sensitive

logger = logging.getLogger("volcengine-agent")

_MAX_BYTES = 1_000_000  # 1MB
_MAX_LINES = 2000


@function_tool()
async def read_file(path: str, start_line: int = 0, end_line: int = 0) -> str:
    """读取文本文件的内容。

    Args:
        path: 绝对路径或相对 worker cwd 的路径。
        start_line: 从第几行开始（0-indexed；0 表示从开头）。默认 0。
        end_line: 到第几行结束（exclusive；0 表示读到末尾）。默认 0。

    Returns:
        文件内容字符串，或 "[ERROR] ..." 开头的错误描述。
    """
    try:
        p = Path(path).expanduser().resolve()
        if _is_sensitive(str(p)):
            logger.warning("[fs] SENSITIVE_PATH read_file(path=%r)", path)
        if not p.exists():
            return f"[ERROR] 路径 {p} 不存在"
        if p.is_dir():
            return f"[ERROR] {p} 是目录，请用 glob_files"
        if not p.is_file():
            return f"[ERROR] {p} 不是普通文件"
        size = p.stat().st_size
        if size > _MAX_BYTES:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            truncated = "\n".join(lines[:_MAX_LINES])
            logger.info(
                "[fs] read_file(path=%r, TRUNCATED) size=%d → %d lines",
                path, size, min(_MAX_LINES, len(lines)),
            )
            return truncated + f"\n\n[TRUNCATED] 文件共 {len(lines)} 行，已截断至前 {_MAX_LINES} 行"
        content = p.read_text(encoding="utf-8")
        if start_line > 0 or end_line > 0:
            lines = content.splitlines()
            s = start_line
            e = end_line if end_line > 0 else len(lines)
            content = "\n".join(lines[s:e])
        logger.info(
            "[fs] read_file(path=%r, start=%d, end=%d) → %dc",
            path, start_line, end_line, len(content),
        )
        return content
    except Exception as e:
        logger.warning("[fs] read_file ERROR path=%r err=%r", path, e)
        return f"[ERROR] {e}"


def register() -> list:
    """返回本文件提供的 tool 列表。"""
    return [read_file]