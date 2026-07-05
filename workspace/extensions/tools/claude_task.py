"""claude_task 四件套 — create / list / status / continue。

设计原则（语音场景）:
- 不暴露任何 UUID / 短码给用户或小语。**用户记不住 ID，记不住短码**。
- 用户用**任务编号**（"第一个"、"第二个"）或**关键词**（"竞品 X 调研"）指代。
- 内部用 UUID 跟踪，但所有工具接口只用编号 + 关键词。

Args 约定:
- task_ref: 任务编号 ("1", "2", "3"...) 或关键词（"竞品 X"）。
  编号 = claude_task_list 返回的列表顺序（最新在前）。
  关键词 = 在 prompt 里做子串匹配。
- 没有歧义时直接匹配；多个匹配时返回候选列表让小语反问。
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from pathlib import Path

from livekit.agents import function_tool

logger = logging.getLogger("volcengine-agent")


def _workspace_root() -> Path:
    """运行时 workspace 根 = main.py 所在目录的 workspace/。"""
    env_root = os.environ.get("AGENT_WORKSPACE_ROOT")
    if env_root:
        return Path(env_root)
    cwd_workspace = Path.cwd() / "workspace"
    if cwd_workspace.is_dir():
        return cwd_workspace
    return Path(__file__).resolve().parent.parent.parent / "workspace"


def _resolve_task_ref(workspace: Path, task_ref: str, scope: str = "runner") -> tuple[str | None, str]:
    """把 task_ref（编号或关键词）解析成完整 UUID。

    Returns:
        (task_uuid, error_msg). 成功 → error_msg 为空；失败 → task_uuid 为 None。
    """
    from claude_task_runner import list_tasks

    task_ref = task_ref.strip()
    if not task_ref:
        return None, "task 标识不能为空"

    include_history = scope in ("all", "history")
    project_cwd = str(Path.cwd()) if include_history else None
    records = list_tasks(
        workspace,
        include_history=include_history,
        project_cwd=project_cwd,
    )
    if not records:
        return None, "当前没有任何任务"

    # 路径 1: 纯数字 → 编号（list 的顺序，最新在前）
    if task_ref.isdigit():
        idx = int(task_ref) - 1
        if 0 <= idx < len(records):
            return records[idx].id, ""
        return None, (
            f"任务编号 {task_ref} 超出范围（当前共 {len(records)} 个任务，编号 1-{len(records)}）"
        )

    # 路径 2: 关键词匹配 prompt 子串
    matches = [r for r in records if task_ref.lower() in r.prompt.lower()]

    if len(matches) == 1:
        return matches[0].id, ""
    if len(matches) == 0:
        # 给点提示：列出最近的几个任务让小语反问
        recent = "\n".join(
            f"  {i+1}. {r.prompt[:40]}" for i, r in enumerate(records[:5])
        )
        return None, f"没找到包含 {task_ref!r} 的任务。最近的任务：\n{recent}"
    # 多个匹配 → 让小语反问
    opts = "\n".join(
        f"  {i+1}. {r.prompt[:60]}" for i, r in enumerate(matches)
    )
    return None, f"{task_ref!r} 匹配到 {len(matches)} 个任务，请用编号指明：\n{opts}"


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@function_tool()
async def claude_task_create(prompt: str, cwd: str = "") -> str:
    """启动一个后台 Claude Code 调研任务。

    适用于：调研、深度分析、多步操作、跨多小时工作。
    任务在后台独立进程运行，完成后会自动生成口语版总结。
    用户后续问"怎么样了" → claude_task_status。

    Args:
        prompt: 完整的调研/任务描述。
        cwd: 调研执行目录（绝对路径）。空 = 默认 worker cwd（仓库根）。
              例: "/Users/pz/workspace/my-app" —— 让 Claude 在该项目下调研。
              续接时会自动沿用首次指定的 cwd。

    Returns:
        口语化确认: "调研任务已开起，方向是《XXX》"
        "[ERROR] ..." —— CLI 缺失 / prompt 空 / 并发上限 / 目录不存在
    """
    from claude_task_runner import start_task

    if not prompt or not prompt.strip():
        return "[ERROR] 调研内容不能为空"

    workspace = _workspace_root()
    rec, err = start_task(workspace, prompt.strip(), cwd=cwd.strip() or "")
    if err:
        logger.warning("[claude_task] CREATE_FAILED prompt=%r err=%r", prompt[:80], err)
        return f"[ERROR] {err}"
    logger.info(
        "[claude_task] CREATED task_id=%s prompt=%r cwd=%s dir=%s",
        rec.id, prompt[:80], rec.cwd, workspace / ".agent-tasks" / rec.id,
    )
    cwd_note = f"（在 {rec.cwd} 下调研）" if rec.cwd else ""
    return f"调研任务已开起，方向是《{prompt.strip()[:30]}》{cwd_note}。跑完了我告诉你。"


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@function_tool()
async def claude_task_list(scope: str = "all", limit: int = 10) -> str:
    """列出最近的会话（按时间倒序，最新在前），给用户口语化描述。

    数据源：合并 (1) runner 管理的任务（带状态/summary）+ (2) ~/.claude/history.jsonl 真实历史。
    **按 sessionId 分组**：history.jsonl 同 sessionId 多条 entry 只算一条（取最新）。
    默认返回最近 10 条；想看更多传 limit=N。

    Args:
        scope: "all"（默认, runner + history） | "runner"（只看 runner） | "history"（只看历史）。
        limit: 返回条数上限，默认 10。0 或负数 = 全部。

    Returns:
        口语化列表，每行一条：
        "第 N 个：<状态>——<prompt 前 40 字>"
        没有任务时返回 "当前没有调研任务"。
    """
    from claude_task_runner import list_tasks

    workspace = _workspace_root()
    include_history = scope in ("all", "history")
    project_cwd = str(Path.cwd()) if include_history else None
    records = list_tasks(
        workspace,
        include_history=include_history,
        project_cwd=project_cwd,
        limit=limit if limit > 0 else None,
    )
    logger.info(
        "[claude_task] LIST scope=%s limit=%s count=%d",
        scope, limit, len(records),
    )

    if not records:
        return "当前没有调研任务"

    status_text = {
        "running": "正在跑",
        "summarizing": "在总结",
        "ready": "已完成",
        "failed": "失败了",
        "created": "刚启动",
        "history": "历史会话",
    }

    lines = []
    for i, rec in enumerate(records, 1):
        s = status_text.get(rec.status, rec.status)
        prompt_short = rec.prompt[:40].replace("\n", " ")
        if len(rec.prompt) > 40:
            prompt_short += "..."
        lines.append(f"第 {i} 个：{s}——{prompt_short}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@function_tool()
async def claude_task_status(task_ref: str) -> str:
    """查询任务状态。

    Args:
        task_ref: 任务编号（"1" / "2"...，最新在前）或 prompt 关键词（"竞品 X"）。

    Returns:
        ready → 口语版总结（≤100 字，直接念给用户）
        running / summarizing → "还在跑" 或 "马上就好"
        failed → 错误摘要
        多任务匹配 → 候选列表（让小语反问）
    """
    from claude_task_runner import get_task_status, load_task

    workspace = _workspace_root()
    task_id, err = _resolve_task_ref(workspace, task_ref)
    if err:
        return f"[ERROR] {err}"

    status, body = get_task_status(workspace, task_id)
    rec = load_task(workspace, task_id)
    logger.info(
        "[claude_task] STATUS task_ref=%r task_id=%s status=%s body_len=%d",
        task_ref, task_id, status, len(body),
    )

    if status in ("running", "summarizing"):
        return "还在跑，再等一下"
    if status == "not_found":
        return f"[ERROR] task {task_ref!r} not found"
    # ready / failed —— 直接把 body 给小语，它会念给用户
    if rec:
        return f"《{rec.prompt[:30]}》结果：{body}"
    return body


# ---------------------------------------------------------------------------
# continue
# ---------------------------------------------------------------------------


@function_tool()
async def claude_task_continue(task_ref: str, prompt: str) -> str:
    """在已有任务上追加指令（语义上等价于 Claude Code 的 --resume）。

    Args:
        task_ref: 任务编号（"1" / "2"...）或 prompt 关键词。
        prompt: 追加的指令。

    Returns:
        "已在《XXX》任务上追加指令，跑完了告诉你"
        "[ERROR] ..." —— 任务不存在 / 状态非 ready / 多个匹配
    """
    from claude_task_runner import continue_task, load_task

    if not prompt or not prompt.strip():
        return "[ERROR] 追加内容不能为空"

    workspace = _workspace_root()
    task_id, err = _resolve_task_ref(workspace, task_ref)
    if err:
        return f"[ERROR] {err}"

    rec, err = continue_task(workspace, task_id, prompt.strip())
    if err:
        logger.warning("[claude_task] CONTINUE_FAILED task_ref=%r err=%r", task_ref, err)
        return f"[ERROR] {err}"

    new_rec = load_task(workspace, task_id)
    logger.info(
        "[claude_task] CONTINUED task_ref=%r task_id=%s prompt=%r",
        task_ref, task_id, prompt[:80],
    )
    return f"已在《{new_rec.prompt[:30] if new_rec else '该任务'}》任务上追加指令，跑完了告诉你。"


def register() -> list:
    """返回本文件提供的 tool 列表。"""
    return [claude_task_create, claude_task_list, claude_task_status, claude_task_continue]