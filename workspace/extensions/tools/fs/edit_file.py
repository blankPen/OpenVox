"""edit_file — 基于 old_string/new_string 的精确文本替换。

Spec: docs/superpowers/specs/2026-07-05-agent-filesystem-tools-design.md §3.3 / §5.2
"""
from __future__ import annotations

import logging
from pathlib import Path

from livekit.agents import function_tool

from workspace.extensions.tools.fs._sensitive import is_sensitive

logger = logging.getLogger("volcengine-agent")


@function_tool()
async def edit_file(
    path: str, old_string: str, new_string: str, replace_all: bool = False
) -> str:
    """基于字符串字面量替换文件内容。

    Args:
        path: 目标文件路径。
        old_string: 要查找的字符串（字面量，大小写敏感）。
        new_string: 替换为的字符串。
        replace_all: True 表示替换所有出现；False（默认）要求 old_string 仅出现 1 次。

    Returns:
        "[OK] ..." 成功，或 "[ERROR] ..." 错误描述。
    """
    try:
        p = Path(path).expanduser().resolve()
        if is_sensitive(str(p)):
            logger.warning("[fs] SENSITIVE_PATH edit_file(path=%r)", path)
        if not p.exists():
            return f"[ERROR] 路径 {p} 不存在"
        if not p.is_file():
            return f"[ERROR] {p} 不是普通文件"
        content = p.read_text(encoding="utf-8")
        occurrences = content.count(old_string)
        if occurrences == 0:
            return f"[ERROR] 在 {p} 中找不到 {old_string!r}"
        if occurrences > 1 and not replace_all:
            return (
                f"[ERROR] {old_string!r} 出现 {occurrences} 次，"
                "请加更多上下文或设 replace_all=true"
            )
        if new_string == old_string:
            logger.warning(
                "[fs] EDIT_OP edit_file(path=%r, NO_CHANGE) old_len=%d, new_len=%d",
                path, len(old_string), len(new_string),
            )
            return "[OK] 内容未变化"
        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)
        p.write_text(new_content, encoding="utf-8")
        logger.warning(
            "[fs] EDIT_OP edit_file(path=%r, old_len=%d, new_len=%d, replace_all=%s, occurrences=%d)",
            path, len(old_string), len(new_string), replace_all, occurrences,
        )
        return f"[OK] edited {p} ({occurrences} replacement{'s' if replace_all and occurrences > 1 else ''})"
    except Exception as e:
        logger.warning("[fs] edit_file ERROR path=%r err=%r", path, e)
        return f"[ERROR] {e}"


def register() -> list:
    return [edit_file]