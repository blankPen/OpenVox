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
    cwd: str = ""  # claude 子进程的 cwd（用户指定时填绝对路径；空 = 默认仓库根）


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
    """生成新的 task_id = 完整 UUID 字符串。

    必须是 UUID 因为 Claude Code 的 --session-id / --resume 参数要求 valid UUID。
    短码 = task_id[:8]，给用户口头/UI 友好用，内部全用完整 UUID。
    """
    return str(uuid.uuid4())


def short_id(task_id: str) -> str:
    """从 UUID 字符串取前 8 位当短码。仅展示用。"""
    return task_id[:8] if task_id else ""


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
        cwd=data.get("cwd", ""),
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
    resume: bool = False,
    exec_cwd: Path | None = None,
) -> tuple[int, str]:
    """启动 claude --print 子进程；写 output.md / stderr.log。

    Args:
        exec_cwd: 子进程的工作目录。None = 用 add_dir（默认仓库根）。
                  用户指定的 cwd 让 claude 在某个具体项目目录调研。

    Returns:
        (exit_code, final_task_id) —— final_task_id 在 CLI 分配真实 sessionId 后
        会跟传入的 task_id 不同（已迁移目录）。caller 必须用返回值。
    """
    # 用 json 输出，方便抓 session_id
    cmd = [
        "claude",
        "--print",
        prompt,
        "--add-dir", str(add_dir),
        "--append-system-prompt", "你是中文助手，结果用中文输出",
        "--output-format", "json",
        "--dangerously-skip-permissions",
    ]
    if resume:
        cmd += ["--resume", task_id]
    update_status(workspace_root, task_id, status=STATUS_RUNNING)
    output_path = _output_path(workspace_root, task_id)
    stderr_path = _stderr_path(workspace_root, task_id)

    logger.info(
        "[claude_task] START task=%s mode=%s cmd=%s",
        task_id, "resume" if resume else "new", " ".join(cmd[:3]) + " ...",
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(exec_cwd) if exec_cwd else None,
    )
    stdout, stderr = await proc.communicate()
    exit_code = proc.returncode if proc.returncode is not None else -1

    stdout_text = stdout.decode("utf-8", errors="replace")
    stderr_text = stderr.decode("utf-8", errors="replace")

    # 从 json 输出里抓 CLI 真实分配的 session_id，**覆盖**初始 task_id
    if not resume:
        real_session_id = _extract_session_id_from_json(stdout_text)
        if real_session_id and real_session_id != task_id:
            logger.info(
                "[claude_task] CLI assigned session_id=%s (was task_id=%s), migrating",
                real_session_id, task_id,
            )
            # 把 task.json 的 id 改成真实 session_id，并迁移目录
            _migrate_task_id(workspace_root, task_id, real_session_id)
            task_id = real_session_id
            output_path = _output_path(workspace_root, task_id)
            stderr_path = _stderr_path(workspace_root, task_id)

    output_path.write_text(stdout_text, encoding="utf-8")
    stderr_path.write_text(stderr_text, encoding="utf-8")

    # 兼容 json 输出：再单独写一份 .text.md 存 result 字段（方便 summarizer 用）
    if not resume:
        _write_text_extract(output_path, stdout_text)

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
    return (exit_code, task_id)


def _extract_session_id_from_json(stdout: str) -> str | None:
    """从 claude --print --output-format json 的 stdout 抓 session_id 字段。"""
    import json as _json
    try:
        data = _json.loads(stdout)
    except _json.JSONDecodeError:
        return None
    sid = data.get("session_id")
    return sid if isinstance(sid, str) else None


def _migrate_task_id(workspace_root: Path, old_id: str, new_id: str) -> None:
    """CLI 分配了真实 sessionId 后，把 runner 目录从 old_id 改名到 new_id。

    关键：new_id 才是后续 --resume 用的 ID。
    """
    old_dir = _task_dir(workspace_root, old_id)
    new_dir = _task_dir(workspace_root, new_id)
    # 防御性：确认 old_dir 真的在 _tasks_root 下，避免 rename 把父目录搬走
    tasks_root = _tasks_root(workspace_root)
    try:
        old_dir.relative_to(tasks_root)
        new_dir.relative_to(tasks_root)
    except ValueError:
        logger.error(
            "[claude_task] migrate path escape detected! old=%s new=%s root=%s",
            old_dir, new_dir, tasks_root,
        )
        return
    if new_dir.exists():
        logger.warning(
            "[claude_task] migrate target exists, skipping: old=%s new=%s",
            old_id, new_id,
        )
        return
    if not old_dir.is_dir():
        logger.warning("[claude_task] migrate source missing: %s", old_dir)
        return
    if old_dir == new_dir:
        return
    logger.info("[claude_task] rename %s -> %s", old_dir.name, new_dir.name)
    old_dir.rename(new_dir)
    task_json = new_dir / "task.json"
    if task_json.is_file():
        try:
            data = json.loads(task_json.read_text(encoding="utf-8"))
            data["id"] = new_id
            task_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("[claude_task] update task.json id failed: %r", e)


def _write_text_extract(output_path: Path, stdout: str) -> None:
    """从 json 输出里抽 result 字段写到 .text.md，summarizer 优先读这个。

    json 失败时 fallback 到 stdout 全文。
    """
    import json as _json
    text = None
    try:
        data = _json.loads(stdout)
        text = data.get("result")
    except _json.JSONDecodeError:
        pass
    if not text:
        return
    text_path = output_path.with_suffix(".text.md")
    text_path.write_text(text, encoding="utf-8")


async def _summarize(workspace_root: Path, task_id: str) -> None:
    """Summarizer: 把 output 压缩成 3-5 句口语版写入 summary.md。

    优先读 .text.md（json 输出里的 result 字段，纯文本）。
    失败时降级为 output.md 前 500 字。
    """
    output_path = _output_path(workspace_root, task_id)
    text_path = output_path.with_suffix(".text.md")
    # 优先 text 提取（CLI json 输出里的 result 字段）
    if text_path.is_file():
        full_output = text_path.read_text(encoding="utf-8")
    else:
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
        from livekit.agents.llm import ChatContext
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
        chat_ctx = ChatContext(items=[{"role": "user", "content": prompt}])
        stream = llm.chat(chat_ctx=chat_ctx)
        chunks: list[str] = []
        async for chunk in stream:
            # LLMStream 产出 ChatChunk；ChatChunk 有 .choices[0].delta.content
            choices = getattr(chunk, "choices", None) or []
            for choice in choices:
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
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


def start_task(
    workspace_root: Path,
    prompt: str,
    cwd: str = "",
) -> tuple[TaskRecord | None, str]:
    """创建任务、起后台协程；返回 (rec or None, error_msg)。

    Args:
        workspace_root: runner 自身管理的 workspace 目录
        prompt: 调研内容
        cwd: 调研执行的目录（claude 子进程的 cwd）。
              空字符串 = 用 runner 默认目录（workspace 的父目录 = 仓库根）。
              必须是已存在的绝对路径。
    """
    if not _claude_exists():
        return None, "claude CLI 未安装"

    # cwd 校验
    if cwd:
        cwd_path = Path(cwd).expanduser().resolve()
        if not cwd_path.is_dir():
            return None, f"指定的目录 {cwd_path} 不存在或不是目录"
    else:
        cwd_path = None

    # 并发上限拦截（AGENTS.md 要求 ≤ 3）
    active = count_active_tasks(workspace_root)
    if active >= MAX_CONCURRENT_TASKS:
        return None, (
            f"并发上限 {MAX_CONCURRENT_TASKS}，当前已有 {active} 个任务在跑。"
            f"请先用 claude_task_status 查一下，等其中一些跑完再开新任务。"
        )

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
        cwd=str(cwd_path) if cwd_path else "",
    )
    save_task(workspace_root, rec)

    # claude --add-dir 始终是仓库根（workspace 的父）；cwd 是子进程 cwd
    add_dir = workspace_root.parent

    async def _runner() -> None:
        try:
            exit_code, final_id = await _run_claude_subprocess(
                workspace_root, task_id, prompt, add_dir, exec_cwd=cwd_path,
            )
            await _summarize(workspace_root, final_id)
            _archive_old_outputs_on_completion(workspace_root, final_id)
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
    rec_for_cwd = load_task(workspace_root, task_id)
    continue_cwd = Path(rec_for_cwd.cwd) if rec_for_cwd and rec_for_cwd.cwd else None

    async def _runner() -> None:
        try:
            exit_code, final_id = await _run_claude_subprocess(
                workspace_root, task_id, prompt, add_dir,
                resume=True, exec_cwd=continue_cwd,
            )
            await _summarize(workspace_root, final_id)
            _archive_old_outputs_on_completion(workspace_root, final_id)
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

def list_tasks(
    workspace_root: Path,
    status_filter: str = "",
    include_history: bool = False,
    project_cwd: str | None = None,
    limit: int | None = None,
) -> list[TaskRecord]:
    """列出任务，按 started_at 倒序。

    数据源（合并去重）：
    1. .agent-tasks/<task_id>/ —— runner 管理的任务（带 status/summary.md）
    2. ~/.claude/history.jsonl —— Claude Code 全局历史（display + sessionId）
       按 sessionId 分组取最新一条；可选按 project_cwd 过滤。

    Args:
        status_filter: 可选状态过滤（running/ready/failed/...）。
                       **仅对 runner 管理的任务生效**，history 里没有 status 概念。
        include_history: True = 合并 ~/.claude/history.jsonl；False = 只看 runner
        project_cwd: 过滤 history.jsonl 里 project 字段等于此值的会话。
                     None 或空 = 不过滤。
        limit: 返回上限。None = 全返回；正整数 = 取前 N 条（已排序）。
    """
    out: dict[str, TaskRecord] = {}

    # 源 1: runner 管理的任务
    tasks_root = _tasks_root(workspace_root)
    if tasks_root.is_dir():
        for d in tasks_root.iterdir():
            if not d.is_dir() or d.name.startswith("."):
                continue
            rec = load_task(workspace_root, d.name)
            if rec is None:
                continue
            if status_filter and rec.status != status_filter:
                continue
            out[rec.id] = rec

    # 源 2: ~/.claude/history.jsonl —— 真实 Claude Code 历史会话（按 sessionId 分组）
    if include_history:
        for hist_rec in _read_history_jsonl(project_cwd):
            # runner 管理的优先级高（带完整 status/summary）
            if hist_rec.id in out:
                continue
            out[hist_rec.id] = hist_rec

    result = list(out.values())
    result.sort(key=lambda r: r.started_at, reverse=True)
    if limit is not None and limit > 0:
        result = result[:limit]
    return result


def _read_history_jsonl(project_cwd: str | None) -> list[TaskRecord]:
    """解析 ~/.claude/history.jsonl，过滤出当前项目的会话。

    **按 sessionId 分组**：同一个 sessionId 多条 entry 只保留时间戳最新的那条
    （--resume 续接会 push 新 display，按用户最后一条意图展示）。

    每行 JSON: {display, pastedContents, timestamp, project, sessionId}
    """
    history_path = Path.home() / ".claude" / "history.jsonl"
    if not history_path.is_file():
        return []
    grouped: dict[str, TaskRecord] = {}
    try:
        with history_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                session_id = entry.get("sessionId")
                display = entry.get("display", "")
                timestamp_ms = entry.get("timestamp", 0)
                project = entry.get("project", "")
                if not session_id:
                    continue
                if project_cwd and project != project_cwd:
                    continue
                started_at = timestamp_ms / 1000.0
                # 同 sessionId 多条 → 取时间戳最大的
                existing = grouped.get(session_id)
                if existing is None or started_at > existing.started_at:
                    grouped[session_id] = TaskRecord(
                        id=session_id,
                        prompt=display or "(empty)",
                        status="history",
                        started_at=started_at,
                    )
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("[claude_task] read history.jsonl failed: %r", e)
    return list(grouped.values())


def count_active_tasks(workspace_root: Path) -> int:
    """统计当前处于 created/running/summarizing 状态的任务数。"""
    active = {STATUS_CREATED, STATUS_RUNNING, STATUS_SUMMARIZING}
    return sum(1 for r in list_tasks(workspace_root) if r.status in active)


# 并发上限：AGENTS.md 说"不要同时启动超过 3 个 claude_task"
MAX_CONCURRENT_TASKS = 3
