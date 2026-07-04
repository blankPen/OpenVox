"""write_file — 原子写文本文件，支持 overwrite / append 两种 mode。

Spec: docs/superpowers/specs/2026-07-05-agent-filesystem-tools-design.md §3.3 / §5.2 / §5.5
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from livekit.agents import function_tool

from workspace.extensions.tools.fs._sensitive import is_sensitive

logger = logging.getLogger("volcengine-agent")


@function_tool()
async def write_file(path: str, content: str, mode: str = "overwrite") -> str:
    """写文本文件到指定路径。

    Args:
        path: 绝对路径或相对 worker cwd 的路径。
        content: 要写入的文本内容（必须 UTF-8）。
        mode: "overwrite"（默认，覆盖）或 "append"（追加到末尾）。

    Returns:
        "[OK] <路径>" 成功，或 "[ERROR] ..." 开头的错误描述。
    """
    if mode not in ("overwrite", "append"):
        return f"[ERROR] mode 必须是 overwrite 或 append，收到 {mode!r}"
    try:
        try:
            content.encode("utf-8")
        except UnicodeEncodeError as e:
            return f"[ERROR] 内容不是合法 UTF-8: {e}"

        p = Path(path).expanduser().resolve()
        if is_sensitive(str(p)):
            logger.warning("[fs] SENSITIVE_PATH write_file(path=%r)", path)
        parent = p.parent
        parent.mkdir(parents=True, exist_ok=True)

        if mode == "append":
            with open(p, "a", encoding="utf-8") as f:
                f.write(content)
            logger.warning(
                "[fs] WRITE_OP write_file(path=%r, mode='append', size=%dc)",
                path, len(content),
            )
            return f"[OK] append {p} (+{len(content)}c)"

        fd, tmp_path = tempfile.mkstemp(dir=str(parent), prefix=".tmp_", suffix=".tmp")
        try:
            os.write(fd, content.encode("utf-8"))
            os.close(fd)
            os.replace(tmp_path, str(p))
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        logger.warning(
            "[fs] WRITE_OP write_file(path=%r, mode='overwrite', size=%dc)",
            path, len(content),
        )
        return f"[OK] overwrite {p} ({len(content)}c)"
    except Exception as e:
        logger.warning("[fs] write_file ERROR path=%r err=%r", path, e)
        return f"[ERROR] {e}"


def register() -> list:
    return [write_file]