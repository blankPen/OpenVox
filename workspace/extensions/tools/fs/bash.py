"""bash — 子进程执行 shell 命令，带 timeout + 环境隔离。

Spec: docs/superpowers/specs/2026-07-05-agent-filesystem-tools-design.md §3.3 / §5.4
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from livekit.agents import function_tool

logger = logging.getLogger("volcengine-agent")

_MIN_TIMEOUT = 1
_MAX_TIMEOUT = 300
_DEFAULT_TIMEOUT = 30


def _safe_env() -> dict[str, str]:
    """只透传 PATH 和 HOME。"""
    env: dict[str, str] = {}
    for key in ("PATH", "HOME"):
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    return env


@function_tool()
async def bash(cmd: str, cwd: str = "", timeout: int = _DEFAULT_TIMEOUT) -> str:
    """执行 shell 命令。

    Args:
        cmd: shell 命令字符串（支持管道 / && / || / 重定向）。
        cwd: 工作目录（绝对路径或相对 worker cwd）。空表示用 worker cwd。
        timeout: 超时秒数。范围 [1, 300]，默认 30。

    Returns:
        "[EXIT N] <stdout+stderr>" 成功（含退出码），
        "[TIMEOUT] ..." 超时，或 "[ERROR] ..." 参数错误。
    """
    if not (isinstance(timeout, int) and _MIN_TIMEOUT <= timeout <= _MAX_TIMEOUT):
        return f"[ERROR] timeout 必须在 {_MIN_TIMEOUT}-{_MAX_TIMEOUT} 之间，收到 {timeout!r}"
    try:
        work_dir = Path(cwd).expanduser().resolve() if cwd else Path.cwd()
        if not work_dir.exists() or not work_dir.is_dir():
            return f"[ERROR] cwd {work_dir} 不存在或不是目录"
        logger.warning(
            "[fs] BASH_OP bash(cmd=%r, cwd=%r, timeout=%ds)",
            cmd[:200], str(work_dir), timeout,
        )
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(work_dir),
            env=_safe_env(),
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            return f"[TIMEOUT] {timeout}s 内未完成，已 kill"
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        return f"[EXIT {proc.returncode}] {output}"
    except Exception as e:
        logger.warning("[fs] bash ERROR cmd=%r err=%r", cmd[:200], e)
        return f"[ERROR] {e}"


def register() -> list:
    return [bash]