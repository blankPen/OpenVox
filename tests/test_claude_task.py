"""claude_task 工具（@function_tool）单元测试。

3 个工具：create / status / continue
通过 env var AGENT_WORKSPACE_ROOT 指向 tmp_path
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

# 必须在导入 tool 之前设置，否则 _workspace_root() 走兜底逻辑
@pytest.fixture
def fake_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    yield tmp_path


def _run(coro):
    """裸 asyncio.run — 不依赖 pytest-asyncio。"""
    return asyncio.run(coro)


def test_claude_task_create_no_cli(fake_workspace: Path, monkeypatch) -> None:
    """claude CLI 缺失时返回 [ERROR]。"""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda x: None)

    from extensions.tools.claude_task import claude_task_create
    result = _run(claude_task_create("调研竞品 X"))
    assert result.startswith("[ERROR]")
    assert "claude CLI" in result


def test_claude_task_create_empty_prompt(fake_workspace: Path) -> None:
    from extensions.tools.claude_task import claude_task_create
    result = _run(claude_task_create(""))
    assert "[ERROR]" in result
    assert "空" in result


def test_claude_task_create_returns_task_id(fake_workspace: Path, monkeypatch) -> None:
    """claude 存在但不让真跑 — fake get_running_loop 让 continue_task/create_task 走 loop.create_task 分支。"""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda x: "/usr/local/bin/claude")

    called = {"n": 0}

    class FakeLoop:
        def create_task(self, coro):
            called["n"] += 1
            coro.close()
            return None

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())

    from extensions.tools.claude_task import claude_task_create
    result = _run(claude_task_create("调研 X"))

    assert called["n"] == 1
    assert result.startswith("task_id=")
    assert "status=running" in result
    # task.json 已写入
    task_id = result.split("=")[1].split(" ")[0]
    assert (fake_workspace / ".agent-tasks" / task_id / "task.json").is_file()


def test_claude_task_status_running(fake_workspace: Path) -> None:
    """状态为 running 时直接返回 status=running。"""
    from claude_task_runner import save_task, TaskRecord, STATUS_RUNNING
    from extensions.tools.claude_task import claude_task_status
    save_task(fake_workspace, TaskRecord(id="abcd1234", prompt="p", status=STATUS_RUNNING, started_at=1.0))
    result = _run(claude_task_status("abcd1234"))
    assert result == "status=running"


def test_claude_task_status_not_found(fake_workspace: Path) -> None:
    from extensions.tools.claude_task import claude_task_status
    result = _run(claude_task_status("missing01"))
    assert "[ERROR]" in result
    assert "not found" in result


def test_claude_task_status_ready_returns_summary(fake_workspace: Path) -> None:
    from claude_task_runner import save_task, TaskRecord, STATUS_READY
    from extensions.tools.claude_task import claude_task_status
    save_task(fake_workspace, TaskRecord(id="abcd1234", prompt="p", status=STATUS_READY, started_at=1.0))
    summary_dir = fake_workspace / ".agent-tasks" / "abcd1234"
    summary_dir.mkdir(parents=True, exist_ok=True)
    (summary_dir / "summary.md").write_text("调研结论：竞品 X 在 Y 方面领先", encoding="utf-8")

    result = _run(claude_task_status("abcd1234"))
    assert "status=ready" in result
    assert "调研结论" in result


def test_claude_task_continue_empty_task_id(fake_workspace: Path) -> None:
    from extensions.tools.claude_task import claude_task_continue
    result = _run(claude_task_continue("", "再加一项"))
    assert "[ERROR]" in result


def test_claude_task_continue_not_found(fake_workspace: Path) -> None:
    from extensions.tools.claude_task import claude_task_continue
    result = _run(claude_task_continue("missing01", "再加一项"))
    assert "[ERROR]" in result
    assert "not found" in result


def test_claude_task_continue_wrong_state(fake_workspace: Path) -> None:
    from claude_task_runner import save_task, TaskRecord, STATUS_RUNNING
    from extensions.tools.claude_task import claude_task_continue
    save_task(fake_workspace, TaskRecord(id="abcd1234", prompt="p", status=STATUS_RUNNING, started_at=1.0))
    result = _run(claude_task_continue("abcd1234", "再加一项"))
    assert "[ERROR]" in result
    assert "not in ready state" in result


def test_register_returns_three_tools() -> None:
    from extensions.tools.claude_task import register
    tools = register()
    assert len(tools) == 3
    names = {t.__name__ for t in tools}
    assert "claude_task_create" in names
    assert "claude_task_status" in names
    assert "claude_task_continue" in names