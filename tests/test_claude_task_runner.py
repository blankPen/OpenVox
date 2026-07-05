"""claude_task_runner 状态机 + 工具调度单元测试。

通过 monkeypatch 替换 _run_claude_subprocess 和 _summarize，验证：
- start_task 创建正确目录结构 + 状态流转
- continue_task 只允许在 ready 状态
- get_task_status 各分支
- archive 行为
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from claude_task_runner import (
    ALL_STATUSES,
    STATUS_CREATED,
    STATUS_FAILED,
    STATUS_READY,
    STATUS_RUNNING,
    STATUS_SUMMARIZING,
    TaskRecord,
    archive_current_outputs,
    get_task_status,
    load_task,
    new_task_id,
    save_task,
    start_task,
    continue_task,
    update_status,
)


def test_new_task_id_is_uuid() -> None:
    """task_id 现在是完整 UUID（Claude Code --session-id 要求）。"""
    from claude_task_runner import short_id
    import uuid as _uuid
    tid = new_task_id()
    parsed = _uuid.UUID(tid)
    assert str(parsed) == tid
    assert short_id(tid) == tid[:8]


def test_load_task_returns_none_for_missing(tmp_path: Path) -> None:
    assert load_task(tmp_path, "missing01") is None


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    rec = TaskRecord(
        id="abcd1234",
        prompt="调研 X",
        status=STATUS_CREATED,
        started_at=123.0,
    )
    save_task(tmp_path, rec)
    loaded = load_task(tmp_path, "abcd1234")
    assert loaded is not None
    assert loaded.id == "abcd1234"
    assert loaded.prompt == "调研 X"
    assert loaded.status == STATUS_CREATED
    assert loaded.started_at == 123.0


def test_update_status_writes_field(tmp_path: Path) -> None:
    save_task(tmp_path, TaskRecord(id="aaaa1111", prompt="p", status=STATUS_CREATED, started_at=1.0))
    updated = update_status(tmp_path, "aaaa1111", status=STATUS_RUNNING, exit_code=42)
    assert updated.status == STATUS_RUNNING
    assert updated.exit_code == 42
    # 落盘验证
    data = json.loads((tmp_path / ".agent-tasks" / "aaaa1111" / "task.json").read_text())
    assert data["status"] == "running"
    assert data["exit_code"] == 42


def test_update_status_rejects_unknown_field(tmp_path: Path) -> None:
    save_task(tmp_path, TaskRecord(id="aaaa1111", prompt="p", status=STATUS_CREATED, started_at=1.0))
    with pytest.raises(AttributeError):
        update_status(tmp_path, "aaaa1111", nonexistent_field="x")


def test_update_status_missing_task(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        update_status(tmp_path, "missing01", status=STATUS_RUNNING)


def test_archive_current_outputs(tmp_path: Path) -> None:
    """续接前归档旧产物。"""
    save_task(tmp_path, TaskRecord(id="bbbb2222", prompt="p", status=STATUS_READY, started_at=1.0))
    # 写入一些 output/summary
    task_dir = tmp_path / ".agent-tasks" / "bbbb2222"
    (task_dir / "output.md").write_text("output content", encoding="utf-8")
    (task_dir / "summary.md").write_text("summary content", encoding="utf-8")

    archive_dir = archive_current_outputs(tmp_path, "bbbb2222")
    assert archive_dir.exists()
    assert (archive_dir / "output.md").read_text() == "output content"
    assert (archive_dir / "summary.md").read_text() == "summary content"
    # archive_seq 已 +1
    rec = load_task(tmp_path, "bbbb2222")
    assert rec.archive_seq == 1


def test_get_task_status_running(tmp_path: Path) -> None:
    save_task(tmp_path, TaskRecord(id="cccc3333", prompt="p", status=STATUS_RUNNING, started_at=1.0))
    status, body = get_task_status(tmp_path, "cccc3333")
    assert status == "running"
    assert body == ""


def test_get_task_status_summarizing(tmp_path: Path) -> None:
    save_task(tmp_path, TaskRecord(id="cccc3333", prompt="p", status=STATUS_SUMMARIZING, started_at=1.0))
    status, _ = get_task_status(tmp_path, "cccc3333")
    assert status == "summarizing"


def test_get_task_status_ready_returns_summary(tmp_path: Path) -> None:
    save_task(tmp_path, TaskRecord(id="cccc3333", prompt="p", status=STATUS_READY, started_at=1.0))
    task_dir = tmp_path / ".agent-tasks" / "cccc3333"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "summary.md").write_text("调研结论：X 是 Y", encoding="utf-8")
    status, body = get_task_status(tmp_path, "cccc3333")
    assert status == "ready"
    assert "调研结论" in body


def test_get_task_status_failed_returns_summary(tmp_path: Path) -> None:
    save_task(tmp_path, TaskRecord(id="cccc3333", prompt="p", status=STATUS_FAILED, started_at=1.0))
    task_dir = tmp_path / ".agent-tasks" / "cccc3333"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "summary.md").write_text("错误摘要", encoding="utf-8")
    status, body = get_task_status(tmp_path, "cccc3333")
    assert status == "failed"
    assert body == "错误摘要"


def test_get_task_status_not_found(tmp_path: Path) -> None:
    status, body = get_task_status(tmp_path, "missing01")
    assert status == "not_found"
    assert "not found" in body


def test_continue_task_rejects_non_ready(tmp_path: Path) -> None:
    """continue_task 只允许在 ready 状态续接。"""
    save_task(tmp_path, TaskRecord(id="dddd4444", prompt="p", status=STATUS_RUNNING, started_at=1.0))
    rec, err = continue_task(tmp_path, "dddd4444", "再加一项")
    assert rec is None
    assert "not in ready state" in err
    assert "running" in err


def test_continue_task_rejects_missing(tmp_path: Path) -> None:
    rec, err = continue_task(tmp_path, "missing01", "再加一项")
    assert rec is None
    assert "not found" in err


def test_continue_task_appends_and_archives(tmp_path: Path, monkeypatch) -> None:
    """happy path：ready 状态下续接，continuations 追加 + 归档。"""
    save_task(tmp_path, TaskRecord(id="eeee5555", prompt="原 prompt", status=STATUS_READY, started_at=1.0))
    task_dir = tmp_path / ".agent-tasks" / "eeee5555"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "output.md").write_text("v1 output", encoding="utf-8")
    (task_dir / "summary.md").write_text("v1 summary", encoding="utf-8")

    # pytest 没在 event loop 里跑 — 构造一个 fake loop 让 continue_task 走 create_task 分支
    import asyncio
    called = {"n": 0}
    fake_loop = type("FakeLoop", (), {
        "create_task": staticmethod(lambda coro: (called.__setitem__("n", called["n"] + 1) or coro.close())),
    })()

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)
    # 兜底：万一仍走 RuntimeError 分支，也吃掉 asyncio.run
    monkeypatch.setattr(asyncio, "run", lambda coro: (called.__setitem__("n", called["n"] + 1) or coro.close()))

    rec, err = continue_task(tmp_path, "eeee5555", "新加一项")

    assert err == ""
    assert called["n"] == 1, "should have scheduled runner"
    # continuations 追加成功
    assert "新加一项" in rec.continuations
    # 归档目录创建
    assert (task_dir / "archive" / "v1" / "output.md").exists()
    # 状态已切回 running
    assert rec.status == STATUS_RUNNING


def test_start_task_no_claude_cli(tmp_path: Path, monkeypatch) -> None:
    """claude CLI 缺失时返回错误。"""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda x: None)

    rec, err = start_task(tmp_path, "调研 X")
    assert rec is None
    assert "claude CLI" in err


def test_list_tasks_returns_empty(tmp_path: Path) -> None:
    """空目录返回空列表（include_history=False 时）。"""
    from claude_task_runner import list_tasks
    assert list_tasks(tmp_path, include_history=False) == []
    assert list_tasks(tmp_path, status_filter="running", include_history=False) == []


def test_list_tasks_orders_by_started_at_desc(tmp_path: Path) -> None:
    from claude_task_runner import list_tasks, save_task, TaskRecord, STATUS_READY
    save_task(tmp_path, TaskRecord(id="aaa11111-1111-1111-1111-111111111111", prompt="first", status=STATUS_READY, started_at=100.0))
    save_task(tmp_path, TaskRecord(id="bbb22222-2222-2222-2222-222222222222", prompt="second", status=STATUS_READY, started_at=200.0))
    save_task(tmp_path, TaskRecord(id="ccc33333-3333-3333-3333-333333333333", prompt="third", status=STATUS_READY, started_at=300.0))
    assert [r.id for r in list_tasks(tmp_path, include_history=False)] == [
        "ccc33333-3333-3333-3333-333333333333",
        "bbb22222-2222-2222-2222-222222222222",
        "aaa11111-1111-1111-1111-111111111111",
    ]


def test_list_tasks_filters_by_status(tmp_path: Path) -> None:
    from claude_task_runner import list_tasks, save_task, TaskRecord, STATUS_READY, STATUS_FAILED
    save_task(tmp_path, TaskRecord(id="aaa11111-1111-1111-1111-111111111111", prompt="p1", status=STATUS_READY, started_at=100.0))
    save_task(tmp_path, TaskRecord(id="bbb22222-2222-2222-2222-222222222222", prompt="p2", status=STATUS_FAILED, started_at=200.0))
    save_task(tmp_path, TaskRecord(id="ccc33333-3333-3333-3333-333333333333", prompt="p3", status=STATUS_READY, started_at=300.0))
    ready = list_tasks(tmp_path, status_filter=STATUS_READY, include_history=False)
    assert [r.id for r in ready] == [
        "ccc33333-3333-3333-3333-333333333333",
        "aaa11111-1111-1111-1111-111111111111",
    ]
    failed = list_tasks(tmp_path, status_filter=STATUS_FAILED, include_history=False)
    assert [r.id for r in failed] == ["bbb22222-2222-2222-2222-222222222222"]


def test_list_tasks_includes_history(tmp_path: Path, monkeypatch) -> None:
    """include_history=True 时合并 ~/.claude/history.jsonl。"""
    from pathlib import Path as _Path
    from claude_task_runner import list_tasks

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "history.jsonl").write_text(
        '{"sessionId":"hist-uuid-1","display":"历史会话 1","timestamp":1700000000000,"project":"/some/path"}\n'
        '{"sessionId":"hist-uuid-2","display":"历史会话 2","timestamp":1700000001000,"project":"/some/path"}\n',
        encoding="utf-8",
    )
    # _read_history_jsonl 用 Path.home()，直接 monkeypatch Path 模块的 home
    monkeypatch.setattr(_Path, "home", staticmethod(lambda: fake_home))

    records = list_tasks(tmp_path, include_history=True, project_cwd="/some/path")
    ids = [r.id for r in records]
    assert "hist-uuid-1" in ids
    assert "hist-uuid-2" in ids
    history_records = [r for r in records if r.id.startswith("hist-uuid-")]
    for r in history_records:
        assert r.status == "history"


def test_list_tasks_history_filter_by_project(tmp_path: Path, monkeypatch) -> None:
    """project_cwd 过滤生效。"""
    from pathlib import Path as _Path
    from claude_task_runner import list_tasks

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "history.jsonl").write_text(
        '{"sessionId":"projA-1","display":"A","timestamp":1,"project":"/A"}\n'
        '{"sessionId":"projB-1","display":"B","timestamp":2,"project":"/B"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(_Path, "home", staticmethod(lambda: fake_home))

    only_a = list_tasks(tmp_path, include_history=True, project_cwd="/A")
    assert [r.id for r in only_a] == ["projA-1"]


def test_list_tasks_runner_takes_priority(tmp_path: Path, monkeypatch) -> None:
    """runner 管理的任务和 history 同 sessionId 时，runner 优先。"""
    from pathlib import Path as _Path
    from claude_task_runner import list_tasks, save_task, TaskRecord, STATUS_READY

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "history.jsonl").write_text(
        '{"sessionId":"same-uuid","display":"from history","timestamp":100,"project":"/p"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(_Path, "home", staticmethod(lambda: fake_home))

    save_task(tmp_path, TaskRecord(id="same-uuid", prompt="from runner", status=STATUS_READY, started_at=200.0))

    records = list_tasks(tmp_path, include_history=True, project_cwd="/p")
    same = [r for r in records if r.id == "same-uuid"]
    assert len(same) == 1
    assert same[0].status == STATUS_READY
    assert same[0].prompt == "from runner"


def test_count_active_tasks(tmp_path: Path) -> None:
    from claude_task_runner import (
        count_active_tasks, save_task, TaskRecord,
        STATUS_CREATED, STATUS_RUNNING, STATUS_SUMMARIZING, STATUS_READY, STATUS_FAILED,
    )
    save_task(tmp_path, TaskRecord(id="a1", prompt="p", status=STATUS_CREATED, started_at=1.0))
    save_task(tmp_path, TaskRecord(id="a2", prompt="p", status=STATUS_RUNNING, started_at=1.0))
    save_task(tmp_path, TaskRecord(id="a3", prompt="p", status=STATUS_SUMMARIZING, started_at=1.0))
    save_task(tmp_path, TaskRecord(id="r1", prompt="p", status=STATUS_READY, started_at=1.0))
    save_task(tmp_path, TaskRecord(id="f1", prompt="p", status=STATUS_FAILED, started_at=1.0))
    assert count_active_tasks(tmp_path) == 3


def test_concurrency_limit_blocks_when_full(tmp_path: Path, monkeypatch) -> None:
    from claude_task_runner import (
        start_task, save_task, TaskRecord, STATUS_RUNNING, MAX_CONCURRENT_TASKS,
    )
    import shutil
    monkeypatch.setattr(shutil, "which", lambda x: "/usr/local/bin/claude")
    for i in range(MAX_CONCURRENT_TASKS):
        save_task(tmp_path, TaskRecord(id=f"full{i:04d}", prompt="p", status=STATUS_RUNNING, started_at=1.0))

    class FakeLoop:
        def create_task(self, coro):
            coro.close()
            return None
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())

    rec, err = start_task(tmp_path, "调研 X")
    assert rec is None
    assert "并发上限" in err
    assert str(MAX_CONCURRENT_TASKS) in err


def test_concurrency_limit_allows_when_below(tmp_path: Path, monkeypatch) -> None:
    from claude_task_runner import start_task, save_task, TaskRecord, STATUS_RUNNING, MAX_CONCURRENT_TASKS
    import shutil
    monkeypatch.setattr(shutil, "which", lambda x: "/usr/local/bin/claude")
    for i in range(MAX_CONCURRENT_TASKS - 1):
        save_task(tmp_path, TaskRecord(id=f"f{i:04d}", prompt="p", status=STATUS_RUNNING, started_at=1.0))
    class FakeLoop:
        def create_task(self, coro):
            coro.close()
            return None
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())
    rec, err = start_task(tmp_path, "调研 X")
    assert err == ""
    assert rec is not None


def test_all_statuses_contains_expected() -> None:
    for s in (STATUS_CREATED, STATUS_RUNNING, STATUS_SUMMARIZING, STATUS_READY, STATUS_FAILED):
        assert s in ALL_STATUSES