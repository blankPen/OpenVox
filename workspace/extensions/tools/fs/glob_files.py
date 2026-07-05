"""glob_files — 按 glob 模式列文件。

Spec: docs/superpowers/specs/2026-07-05-agent-filesystem-tools-design.md §3.3 / §5.2
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from livekit.agents import function_tool

logger = logging.getLogger("volcengine-agent")


@function_tool()
async def glob_files(pattern: str, path: str = ".") -> str:
    """按 glob 模式列文件。

    Args:
        pattern: 标准 glob 模式，支持 `*` `**` `?` `[...]`。
        path: 搜索起点（绝对路径或相对 cwd）。默认 "."。

    Returns:
        JSON 字符串数组（相对 path 的相对路径），或 "[ERROR] ..."。
    """
    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"[ERROR] path {p} 不存在"
        if not p.is_dir():
            return f"[ERROR] path {p} 不是目录"
        matches = sorted(str(m.relative_to(p)) for m in p.glob(pattern))
        logger.info(
            "[fs] glob_files(pattern=%r, path=%r) → %d matches",
            pattern, str(p), len(matches),
        )
        return json.dumps(matches, ensure_ascii=False)
    except Exception as e:
        logger.warning("[fs] glob_files ERROR pattern=%r err=%r", pattern, e)
        return f"[ERROR] {e}"


def register() -> list:
    return [glob_files]