"""Claude Code 后台任务运行器 + Summarizer。

Spec: docs/superpowers/specs/2026-07-05-search-and-claudecode-bridge-design.md §4

数据落点: <workspace_root>/.agent-tasks/<task_id>/
  task.json     — {id, prompt, status, started_at, finished_at, exit_code, continuations}
  output.md     — claude --print 完整 stdout
  stderr.log    — claude --print stderr
  summary.md    — summarizer 写的口语版（status=ready 才存在）

状态机:
  created → running ──→ summarizing ──→ ready
                      │              ↘
                      │               failed (summarizer LLM 失败)
                      │
                      └─→ failed (后台进程非零退出)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger("volcengine-agent")

# 状态枚举
STATUS_CREATED = "created"
STATUS_RUNNING = "running"
STATUS_SUMMARIZING = "summarizing"
STATUS_READY = "ready"
STATUS_FAILED = "failed"

ALL_STATUSES = {STATUS_CREATED, STATUS_RUNNING, STATUS_SUMMARIZING, STATUS_READY, STATUS_FAILED}


@dataclass
class TaskRecord:
    id: str
    prompt: str
    status: str
    started_at: float
    finished_at: float | None = None
    exit_code: int | None = None
    continuations: list[str] = field(default_factory=list)
    archive_seq: int = 0  # 续接序号（每次续接后 +1；archive/v<N>/ 用这个 N）


def _tasks_root(workspace_root: Path) -> Path:
    return workspace_root / ".agent-tasks"


def _task_dir(workspace_root: Path, task_id: str) -> Path:
    return _tasks_root(workspace_root) / task_id


def _task_json_path(workspace_root: Path, task_id: str) -> Path:
    return _task_dir(workspace_root, task_id) / "task.json"


def _output_path(workspace_root: Path, task_id: str) -> Path:
    return _task_dir(workspace_root, task_id) / "output.md"


def _stderr_path(workspace_root: Path, task_id: str) -> Path:
    return _task_dir(workspace_root, task_id) / "stderr.log"


def _summary_path(workspace_root: Path, task_id: str) -> Path:
    return _task_dir(workspace_root, task_id) / "summary.md"


def new_task_id() -> str:
    return uuid.uuid4().hex[:8]


def load_task(workspace_root: Path, task_id: str) -> TaskRecord | None:
    """读取 task.json；不存在返回 None。"""
    p = _task_json_path(workspace_root, task_id)
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return TaskRecord(
        id=data["id"],
        prompt=data["prompt"],
        status=data["status"],
        started_at=data["started_at"],
        finished_at=data.get("finished_at"),
        exit_code=data.get("exit_code"),
        continuations=data.get("continuations", []),
        archive_seq=data.get("archive_seq", 0),
    )


def save_task(workspace_root: Path, rec: TaskRecord) -> None:
    p = _task_json_path(workspace_root, rec.id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(rec), ensure_ascii=False, indent=2), encoding="utf-8")


def update_status(workspace_root: Path, task_id: str, **fields: Any) -> TaskRecord:
    """读 task.json，更新指定字段后写回。字段不存在则抛 KeyError。"""
    rec = load_task(workspace_root, task_id)
    if rec is None:
        raise KeyError(f"task {task_id!r} not found")
    for k, v in fields.items():
        if not hasattr(rec, k):
            raise AttributeError(f"TaskRecord has no field {k!r}")
        setattr(rec, k, v)
    save_task(workspace_root, rec)
    return rec


def archive_current_outputs(workspace_root: Path, task_id: str) -> Path:
    """把当前 output.md 和 summary.md 备份到 archive/v<N>/（N = 当前 archive_seq + 1）。

    用于 claude_task_continue 在启动新一轮前归档旧产物。
    返回归档目录路径。
    """
    rec = load_task(workspace_root, task_id)
    if rec is None:
        raise KeyError(f"task {task_id!r} not found")
    next_seq = rec.archive_seq + 1
    archive_dir = _task_dir(workspace_root, task_id) / "archive" / f"v{next_seq}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    for src_name, dst_name in (("output.md", "output.md"), ("summary.md", "summary.md")):
        src = _task_dir(workspace_root, task_id) / src_name
        if src.is_file():
            (archive_dir / dst_name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    update_status(workspace_root, task_id, archive_seq=next_seq)
    return archive_dir


def _archive_old_outputs_on_completion(workspace_root: Path, task_id: str) -> None:
    """每次 complete 一轮（status 变 ready）时调用，把上一轮的 output/summary 归档。"""
    rec = load_task(workspace_root, task_id)
    if rec is None:
        return
    next_seq = rec.archive_seq + 1
    archive_dir = _task_dir(workspace_root, task_id) / "archive" / f"v{next_seq}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    for src_name in ("output.md", "summary.md"):
        src = _task_dir(workspace_root, task_id) / src_name
        if src.is_file():
            dst = archive_dir / src_name
            if not dst.exists():
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    update_status(workspace_root, task_id, archive_seq=next_seq)


# ---------------------------------------------------------------------------
# 后台进程包装
# ---------------------------------------------------------------------------


def _claude_exists() -> bool:
    """检查 claude CLI 是否在 PATH 里。"""
    import shutil
    return shutil.which("claude") is not None


async def _run_claude_subprocess(
    workspace_root: Path,
    task_id: str,
    prompt: str,
    add_dir: Path,
) -> int:
    """启动 claude --print 子进程；写 output.md / stderr.log；返回 exit_code。"""
    cmd = [
        "claude",
        "--print",
        prompt,
        "--add-dir", str(add_dir),
        "--append-system-prompt", "你是中文助手，结果用中文输出",
        "--output-format", "text",
        "--dangerously-skip-permissions",
    ]
    update_status(workspace_root, task_id, status=STATUS_RUNNING)
    output_path = _output_path(workspace_root, task_id)
    stderr_path = _stderr_path(workspace_root, task_id)

    logger.info("[claude_task] START task=%s cmd=%s", task_id, " ".join(cmd[:3]) + " ...")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    exit_code = proc.returncode if proc.returncode is not None else -1

    output_path.write_text(stdout.decode("utf-8", errors="replace"), encoding="utf-8")
    stderr_path.write_text(stderr.decode("utf-8", errors="replace"), encoding="utf-8")

    logger.info(
        "[claude_task] EXIT task=%s exit=%d stdout=%dB stderr=%dB",
        task_id, exit_code, len(stdout), len(stderr),
    )
    update_status(
        workspace_root, task_id,
        status=STATUS_SUMMARIZING,
        exit_code=exit_code,
        finished_at=time.time(),
    )
    return exit_code


async def _summarize(workspace_root: Path, task_id: str) -> None:
    """Summarizer: 把 output.md 压缩成 3-5 句口语版写入 summary.md。

    失败时降级为 output.md 前 500 字。
    """
    output_path = _output_path(workspace_root, task_id)
    full_output = output_path.read_text(encoding="utf-8") if output_path.is_file() else ""

    rec = load_task(workspace_root, task_id)
    if rec is None:
        return
    if rec.exit_code != 0:
        # 失败路径：写 stderr 前 500 字
        stderr_path = _stderr_path(workspace_root, task_id)
        stderr_text = stderr_path.read_text(encoding="utf-8") if stderr_path.is_file() else ""
        summary = (stderr_text or full_output)[:500]
        _summary_path(workspace_root, task_id).write_text(summary, encoding="utf-8")
        update_status(workspace_root, task_id, status=STATUS_FAILED)
        logger.warning("[claude_task] FAILED task=%s exit=%d summary=stderr[:500]", task_id, rec.exit_code)
        return

    # 成功路径：调 LLM 压缩
    summary = ""
    try:
        from livekit.plugins import volcengine
        llm = volcengine.LLM(
            model="doubao-1-5-pro-32k-250115",
            api_key=os.environ["VOLCENGINE_LLM_API_KEY"],
        )
        prompt = (
            "请把以下 Claude Code 调研结果压缩成 3-5 句中文口语版总结，"
            "用于语音助手告诉用户。不超过 100 字。保留关键结论和数据。"
            "不要 markdown、不要 emoji、不要项目符号。\n\n"
            f"原始输出:\n{full_output[:6000]}"
        )
        stream = llm.chat(messages=[{"role": "user", "content": prompt}])
        chunks: list[str] = []
        async for chunk in stream:
            delta = getattr(chunk, "delta", None)
            if delta is not None:
                content = getattr(delta, "content", None)
                if isinstance(content, str) and content:
                    chunks.append(content)
        summary = "".join(chunks).strip()
        logger.info("[claude_task] SUMMARIZED task=%s len=%d", task_id, len(summary))
    except Exception as e:
        logger.warning("[claude_task] SUMMARIZE_FAILED task=%s err=%r; falling back", task_id, e)

    if not summary:
        summary = full_output[:500]
        logger.info("[claude_task] SUMMARIZE_FALLBACK task=%s len=%d", task_id, len(summary))

    _summary_path(workspace_root, task_id).write_text(summary, encoding="utf-8")
    update_status(workspace_root, task_id, status=STATUS_READY)


# ---------------------------------------------------------------------------
# 公共 API — 3 个 @function_tool 调这里
# ---------------------------------------------------------------------------


def start_task(workspace_root: Path, prompt: str) -> tuple[TaskRecord | None, str]:
    """创建任务、起后台协程；返回 (rec or None, error_msg)。"""
    if not _claude_exists():
        return None, "claude CLI 未安装"

    task_id = new_task_id()
    task_dir = _task_dir(workspace_root, task_id)
    try:
        task_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return None, f"task_id 冲突 {task_id}（极罕见，重试即可）"

    rec = TaskRecord(
        id=task_id,
        prompt=prompt,
        status=STATUS_CREATED,
        started_at=time.time(),
    )
    save_task(workspace_root, rec)

    add_dir = workspace_root.parent  # 仓库根 = workspace 的父

    async def _runner() -> None:
        try:
            exit_code = await _run_claude_subprocess(workspace_root, task_id, prompt, add_dir)
            await _summarize(workspace_root, task_id)
            # 完整跑完一轮后归档（保留历史供用户回看）
            _archive_old_outputs_on_completion(workspace_root, task_id)
        except Exception as e:
            logger.exception("[claude_task] RUNNER_CRASH task=%s", task_id)
            try:
                update_status(workspace_root, task_id, status=STATUS_FAILED)
                summary = f"[ERROR] runner crashed: {e}"
                _summary_path(workspace_root, task_id).write_text(summary, encoding="utf-8")
            except Exception:
                pass

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_runner())
    except RuntimeError:
        # 没有 running loop（如直接调），用 asyncio.run 包一层
        asyncio.run(_runner())

    return rec, ""


def continue_task(workspace_root: Path, task_id: str, prompt: str) -> tuple[TaskRecord | None, str]:
    """在 ready 状态的任务上续接一条新指令。"""
    rec = load_task(workspace_root, task_id)
    if rec is None:
        return None, f"task {task_id!r} not found"
    if rec.status != STATUS_READY:
        return None, f"task not in ready state, current={rec.status}"

    rec.continuations.append(prompt)
    save_task(workspace_root, rec)
    archive_current_outputs(workspace_root, task_id)
    update_status(workspace_root, task_id, status=STATUS_RUNNING)

    add_dir = workspace_root.parent

    async def _runner() -> None:
        try:
            exit_code = await _run_claude_subprocess(workspace_root, task_id, prompt, add_dir)
            await _summarize(workspace_root, task_id)
            _archive_old_outputs_on_completion(workspace_root, task_id)
        except Exception as e:
            logger.exception("[claude_task] CONTINUE_CRASH task=%s", task_id)
            try:
                update_status(workspace_root, task_id, status=STATUS_FAILED)
                _summary_path(workspace_root, task_id).write_text(
                    f"[ERROR] continue crashed: {e}", encoding="utf-8"
                )
            except Exception:
                pass

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_runner())
    except RuntimeError:
        asyncio.run(_runner())

    return load_task(workspace_root, task_id), ""


def get_task_status(workspace_root: Path, task_id: str) -> tuple[str, str]:
    """返回 (status_label, body)。

    status_label: running | summarizing | ready | failed | not_found
    body: ready/failed 时是 summary.md 内容；其他状态是空串或状态描述
    """
    rec = load_task(workspace_root, task_id)
    if rec is None:
        return "not_found", f"task {task_id!r} not found"

    if rec.status in (STATUS_RUNNING, STATUS_CREATED):
        return "running", ""
    if rec.status == STATUS_SUMMARIZING:
        return "summarizing", ""

    summary_path = _summary_path(workspace_root, task_id)
    body = summary_path.read_text(encoding="utf-8") if summary_path.is_file() else ""
    return rec.status, body