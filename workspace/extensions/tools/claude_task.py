"""claude_task 三件套 — create / status / continue。

Spec: docs/superpowers/specs/2026-07-05-search-and-claudecode-bridge-design.md §3.2-3.4
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from livekit.agents import function_tool

logger = logging.getLogger("volcengine-agent")


def _workspace_root() -> Path:
    """运行时 workspace 根 = main.py 所在目录的 workspace/。"""
    # main.py 把 workspace/ 加到 sys.path，所以 _workspace_root 也走同一个基准
    # 但这里是工具模块，可能在别的进程 import。要 robust：用环境变量或 cwd 推断
    env_root = os.environ.get("AGENT_WORKSPACE_ROOT")
    if env_root:
        return Path(env_root)
    # 兜底：cwd/workspace
    cwd_workspace = Path.cwd() / "workspace"
    if cwd_workspace.is_dir():
        return cwd_workspace
    # 再兜底：脚本位置推导
    return Path(__file__).resolve().parent.parent.parent / "workspace"


@function_tool()
async def claude_task_create(prompt: str) -> str:
    """启动一个后台 Claude Code 调研任务。

    适用于：调研、深度分析、多步操作、跨多小时工作。
    任务在后台独立进程运行，完成后会自动生成口语版总结。
    用户后续问"怎么样了" → claude_task_status(task_id)。

    Args:
        prompt: 完整的调研/任务描述。

    Returns:
        "task_id=<8位短码> status=running" —— 立即返回
        "[ERROR] claude CLI 未安装" —— 若 CLI 缺失
    """
    from claude_task_runner import start_task

    if not prompt or not prompt.strip():
        return "[ERROR] prompt 不能为空"

    workspace = _workspace_root()
    rec, err = start_task(workspace, prompt.strip())
    if err:
        logger.warning("[claude_task] CREATE_FAILED prompt=%r err=%r", prompt[:80], err)
        return f"[ERROR] {err}"
    logger.info(
        "[claude_task] CREATED task_id=%s prompt=%r dir=%s",
        rec.id, prompt[:80], workspace / ".agent-tasks" / rec.id,
    )
    return f"task_id={rec.id} status=running"


@function_tool()
async def claude_task_status(task_id: str) -> str:
    """查询后台任务状态。

    Args:
        task_id: claude_task_create 返回的 8 位短码。

    Returns:
        - "status=running" —— 还在跑
        - "status=summarizing" —— 跑完了但总结还在生成
        - "status=ready\n<summary.md 全文>" —— 已完成
        - "status=failed\n<summary.md 前 500 字>" —— 失败
        - "[ERROR] task <task_id> not found" —— 短码无效
    """
    from claude_task_runner import get_task_status

    if not task_id or not task_id.strip():
        return "[ERROR] task_id 不能为空"

    workspace = _workspace_root()
    status, body = get_task_status(workspace, task_id.strip())
    logger.info("[claude_task] STATUS task_id=%s status=%s body_len=%d", task_id, status, len(body))

    if status == "not_found":
        return f"[ERROR] task {task_id!r} not found"
    if status in ("running", "summarizing"):
        return f"status={status}"
    # ready / failed
    return f"status={status}\n{body}"


@function_tool()
async def claude_task_continue(task_id: str, prompt: str) -> str:
    """在已有任务上追加指令（语义上等价于 Claude Code 的 --resume）。

    Args:
        task_id: 已存在的任务短码；任务必须在 ready 状态。
        prompt: 追加的指令。

    Returns:
        "task_id=<task_id> status=running continue_seq=N" —— 立即返回
        "[ERROR] task not found" / "[ERROR] task not in ready state, current=<status>" —— 错误
    """
    from claude_task_runner import continue_task, load_task

    if not task_id or not task_id.strip():
        return "[ERROR] task_id 不能为空"
    if not prompt or not prompt.strip():
        return "[ERROR] prompt 不能为空"

    workspace = _workspace_root()
    rec, err = continue_task(workspace, task_id.strip(), prompt.strip())
    if err:
        logger.warning("[claude_task] CONTINUE_FAILED task_id=%s err=%r", task_id, err)
        return f"[ERROR] {err}"
    new_rec = load_task(workspace, task_id.strip())
    seq = new_rec.archive_seq if new_rec else 0
    logger.info(
        "[claude_task] CONTINUED task_id=%s seq=%d prompt=%r",
        task_id, seq, prompt[:80],
    )
    return f"task_id={task_id} status=running continue_seq={seq}"


def register() -> list:
    """返回本文件提供的 tool 列表。"""
    return [claude_task_create, claude_task_status, claude_task_continue]