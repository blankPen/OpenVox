"""bash — 子进程执行 shell 命令。

变聪明的几个能力（v0.2，相对 v0.1）：
1. **输出截断** — 单条命令输出 > 50KB 自动截断，附 [TRUNCATED N/M bytes]
2. **错误诊断 hint** — 常见 exit code (127=未找到, 126=无权限) 给小语提示
3. **stdout/stderr 分离** — 改用 PIPE 两者，stderr 以 [STDERR] 前缀拼接
4. **环境隔离** — 只透传 PATH 和 HOME
5. **timeout 精准** — kill 后 wait，不留僵尸进程
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from livekit.agents import function_tool

logger = logging.getLogger("volcengine-agent")

_MIN_TIMEOUT = 1
_MAX_TIMEOUT = 300
_DEFAULT_TIMEOUT = 30
_MAX_OUTPUT_BYTES = 50_000  # 50KB 上限，避免一次输出几 MB 把 chat ctx 撑爆

# 常见 exit code 诊断提示。来源: bash 标准 / sysexits.h
_EXIT_CODE_HINTS = {
    1: "通用错误（命令执行失败，但具体原因看上面输出）",
    2: "命令语法错误 / 参数错（misuse of shell builtins）",
    126: "命令找到但无法执行（缺执行权限或不是可执行文件）",
    127: "命令未找到（检查拼写、PATH、是否需要 sudo / source）",
    130: "Ctrl+C 终止",
    137: "OOM / SIGKILL（被 kill -9）",
    139: "段错误（segfault）",
    143: "SIGTERM 终止",
}


def _safe_env() -> dict[str, str]:
    """只透传 PATH 和 HOME，最小权限环境。"""
    env: dict[str, str] = {}
    for key in ("PATH", "HOME"):
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    return env


def _truncate_output(stdout: bytes, stderr: bytes) -> tuple[str, bool]:
    """拼接 stdout + stderr 后截断到 _MAX_OUTPUT_BYTES。

    返回 (formatted_text, was_truncated)。
    """
    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    parts = []
    if out:
        parts.append(out)
    if err:
        # stderr 用 [STDERR] 前缀标识，方便小语区分
        parts.append(f"[STDERR]\n{err}")
    combined = "\n".join(parts)
    if len(combined) <= _MAX_OUTPUT_BYTES:
        return combined, False
    head = int(_MAX_OUTPUT_BYTES * 0.8)
    tail = _MAX_OUTPUT_BYTES - head
    truncated = (
        combined[:head]
        + f"\n\n[... TRUNCATED ...\n"
        + combined[-tail:]
    )
    return truncated, True


@function_tool()
async def bash(cmd: str, cwd: str = "", timeout: int = _DEFAULT_TIMEOUT) -> str:
    """执行 shell 命令。

    适用场景：
    - 任何 shell 命令（管道 / && / || / 重定向都支持）
    - 默认 cwd 是 worker 当前目录；可显式传 cwd 切换
    - 默认 timeout 30s，最长 300s

    Args:
        cmd: shell 命令字符串。
        cwd: 工作目录（绝对路径）。空表示用 worker cwd。
        timeout: 超时秒数，范围 [1, 300]，默认 30。

    Returns:
        格式: "[EXIT <code>] <输出>"
        - 成功 → "[EXIT 0] <输出>"
        - 失败 → "[EXIT <code>] <输出>\n[hint] <错误码诊断>"
        - 超时 → "[TIMEOUT] <timeout>s 内未完成，已 kill"
        - 参数错 → "[ERROR] ..."
        - 输出 > 50KB 自动截断, 附 [... TRUNCATED ...]
    """
    if not cmd or not cmd.strip():
        return "[ERROR] cmd 不能为空"
    if not (isinstance(timeout, int) and _MIN_TIMEOUT <= timeout <= _MAX_TIMEOUT):
        return f"[ERROR] timeout 必须在 {_MIN_TIMEOUT}-{_MAX_TIMEOUT} 之间，收到 {timeout!r}"
    try:
        work_dir = Path(cwd).expanduser().resolve() if cwd else Path.cwd()
        if not work_dir.exists() or not work_dir.is_dir():
            return f"[ERROR] cwd {work_dir} 不存在或不是目录"
        logger.warning(
            "[fs] BASH_OP cmd=%r cwd=%r timeout=%ds",
            cmd[:200], str(work_dir), timeout,
        )
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(work_dir),
            env=_safe_env(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            return f"[TIMEOUT] {timeout}s 内未完成，已 kill"

        exit_code = proc.returncode if proc.returncode is not None else -1
        output, truncated = _truncate_output(stdout, stderr)
        if truncated:
            output += f"\n\n[TRUNCATED 总输出 > {_MAX_OUTPUT_BYTES} 字节]"
        result = f"[EXIT {exit_code}] {output}"
        # exit != 0 加诊断 hint
        if exit_code != 0 and exit_code in _EXIT_CODE_HINTS:
            result += f"\n[hint] exit={exit_code}: {_EXIT_CODE_HINTS[exit_code]}"
        elif exit_code != 0:
            result += f"\n[hint] exit={exit_code}（非 0 退出）"
        return result
    except Exception as e:
        logger.warning("[fs] bash ERROR cmd=%r err=%r", cmd[:200], e)
        return f"[ERROR] {type(e).__name__}: {e}"


@function_tool()
async def which(cmd: str) -> str:
    """检查命令是否存在 + 返回绝对路径。

    用于诊断 "command not found" 类问题。

    Args:
        cmd: 命令名（如 "git"、"python3"）。

    Returns:
        "<abs_path>" —— 命令存在
        "[NOT FOUND]" —— 不存在（PATH 里搜不到）
        "[ERROR] ..." —— 参数错
    """
    if not cmd or not cmd.strip():
        return "[ERROR] cmd 不能为空"
    path = shutil.which(cmd.strip())
    if path is None:
        return f"[NOT FOUND] {cmd!r} 不在 PATH 里"
    return path


def register() -> list:
    return [bash, which]