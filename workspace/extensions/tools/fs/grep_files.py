"""grep_files — 按 regex 搜文件内容，支持 include glob + max_results 截断。

Spec: docs/superpowers/specs/2026-07-05-agent-filesystem-tools-design.md §3.3 / §5.2
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from livekit.agents import function_tool

logger = logging.getLogger("volcengine-agent")


@function_tool()
async def grep_files(
    pattern: str, path: str = ".", include: str = "", max_results: int = 100
) -> str:
    """按 regex 搜索文件内容。

    Args:
        pattern: 正则表达式字符串。
        path: 搜索起点目录（绝对路径或相对 cwd）。默认 "."。
        include: 文件 glob 过滤（如 "*.py"），空表示匹配所有。默认 ""。
        max_results: 最多返回多少匹配。默认 100。

    Returns:
        JSON 字符串数组，每元素格式 "relative_path:lineno:content"，
        或 "[ERROR] ..."。
    """
    try:
        try:
            compiled = re.compile(pattern)
        except re.error as e:
            return f"[ERROR] pattern 不是合法 regex: {e}"
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"[ERROR] path {p} 不存在"
        if not p.is_dir():
            return f"[ERROR] path {p} 不是目录"
        results: list[str] = []
        for file_path in p.rglob("*"):
            if not file_path.is_file():
                continue
            if include and not file_path.match(include):
                continue
            try:
                for lineno, line in enumerate(
                    file_path.read_text(encoding="utf-8", errors="replace").splitlines(),
                    start=1,
                ):
                    if compiled.search(line):
                        rel = str(file_path.relative_to(p))
                        results.append(f"{rel}:{lineno}:{line}")
                        if len(results) >= max_results:
                            break
            except (OSError, UnicodeDecodeError):
                continue
            if len(results) >= max_results:
                break
        logger.info(
            "[fs] grep_files(pattern=%r, path=%r, include=%r, max=%d) → %d matches",
            pattern, str(p), include, max_results, len(results),
        )
        return json.dumps(results, ensure_ascii=False)
    except Exception as e:
        logger.warning("[fs] grep_files ERROR pattern=%r err=%r", pattern, e)
        return f"[ERROR] {e}"


def register() -> list:
    return [grep_files]