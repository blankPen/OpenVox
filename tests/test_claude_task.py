"""claude_task 工具（@function_tool）单元测试。

工具接口已改为口语化 — 不再返回 task_id / 短码。
所有工具接受 task_ref = "编号" 或 "prompt 关键词"。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest


@pytest.fixture
def fake_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    yield tmp_path


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_claude_task_create_no_cli(fake_workspace: Path, monkeypatch) -> None:
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


def test_claude_task_create_returns_natural_language(fake_workspace: Path, monkeypatch) -> None:
    """返回的不是 task_id，是口语化描述。"""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda x: "/usr/local/bin/claude")

    class FakeLoop:
        def create_task(self, coro):
            coro.close()
            return None

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())

    from extensions.tools.claude_task import claude_task_create
    result = _run(claude_task_create("调研 LiveKit Agents 1.6 新特性"))
    assert "调研任务已开起" in result
    assert "调研 LiveKit Agents 1.6 新特性" in result
    assert "task_id=" not in result  # 确认没暴露 ID


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_claude_task_list_empty_runner_only(fake_workspace: Path) -> None:
    """scope='runner' 且无 runner 任务时返回 '当前没有调研任务'。"""
    from extensions.tools.claude_task import claude_task_list
    result = _run(claude_task_list("runner"))
    assert "当前没有调研任务" in result


def test_claude_task_list_natural_language(fake_workspace: Path) -> None:
    """返回口语化列表 — 第 N 个：状态——prompt 摘要。"""
    from claude_task_runner import save_task, TaskRecord, STATUS_READY
    from extensions.tools.claude_task import claude_task_list
    save_task(fake_workspace, TaskRecord(
        id="a1b2c3d4-0000-0000-0000-000000000001",
        prompt="调研竞品 X",
        status=STATUS_READY, started_at=100.0,
    ))
    save_task(fake_workspace, TaskRecord(
        id="a1b2c3d4-0000-0000-0000-000000000002",
        prompt="分析竞品 Y",
        status=STATUS_READY, started_at=200.0,
    ))
    result = _run(claude_task_list("runner"))
    assert "第 1 个" in result
    assert "第 2 个" in result
    assert "调研竞品 X" in result
    assert "分析竞品 Y" in result


# ---------------------------------------------------------------------------
# status — task_ref 解析
# ---------------------------------------------------------------------------


def test_status_by_number(fake_workspace: Path) -> None:
    """task_ref='1' 解析为最新（第一个）任务。"""
    from claude_task_runner import save_task, TaskRecord, STATUS_READY
    from extensions.tools.claude_task import claude_task_status
    save_task(fake_workspace, TaskRecord(
        id="a1b2c3d4-0000-0000-0000-000000000001",
        prompt="调研竞品 X", status=STATUS_READY, started_at=100.0,
    ))
    save_task(fake_workspace, TaskRecord(
        id="a1b2c3d4-0000-0000-0000-000000000002",
        prompt="调研竞品 Y", status=STATUS_READY, started_at=200.0,
    ))
    # 写 summary
    (fake_workspace / ".agent-tasks" / "a1b2c3d4-0000-0000-0000-000000000002" / "summary.md").parent.mkdir(parents=True, exist_ok=True)
    sd = fake_workspace / ".agent-tasks" / "a1b2c3d4-0000-0000-0000-000000000002"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "summary.md").write_text("竞品 Y 是 Z 公司产品", encoding="utf-8")

    result = _run(claude_task_status("1"))
    assert "竞品 Y" in result
    assert "竞品 Y 是 Z 公司产品" in result


def test_status_by_keyword(fake_workspace: Path) -> None:
    """task_ref='竞品 X' 关键词匹配。"""
    from claude_task_runner import save_task, TaskRecord, STATUS_READY
    from extensions.tools.claude_task import claude_task_status
    save_task(fake_workspace, TaskRecord(
        id="a1b2c3d4-0000-0000-0000-000000000001",
        prompt="调研竞品 X 的功能", status=STATUS_READY, started_at=100.0,
    ))
    sd = fake_workspace / ".agent-tasks" / "a1b2c3d4-0000-0000-0000-000000000001"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "summary.md").write_text("竞品 X 推出了新版本", encoding="utf-8")

    result = _run(claude_task_status("竞品 X"))
    assert "竞品 X" in result


def test_status_keyword_ambiguous(fake_workspace: Path) -> None:
    """多个匹配时返回候选列表让小语反问。"""
    from claude_task_runner import save_task, TaskRecord, STATUS_READY
    from extensions.tools.claude_task import claude_task_status
    save_task(fake_workspace, TaskRecord(
        id="a1b2c3d4-0000-0000-0000-000000000001",
        prompt="竞品 X 调研", status=STATUS_READY, started_at=100.0,
    ))
    save_task(fake_workspace, TaskRecord(
        id="a1b2c3d4-0000-0000-0000-000000000002",
        prompt="竞品 X vs Y", status=STATUS_READY, started_at=200.0,
    ))
    result = _run(claude_task_status("竞品 X"))
    assert "[ERROR]" in result
    assert "2 个任务" in result or "匹配" in result


def test_status_no_match(fake_workspace: Path) -> None:
    from extensions.tools.claude_task import claude_task_status
    result = _run(claude_task_status("不存在的关键词"))
    assert "[ERROR]" in result


def test_status_running(fake_workspace: Path) -> None:
    from claude_task_runner import save_task, TaskRecord, STATUS_RUNNING
    from extensions.tools.claude_task import claude_task_status
    save_task(fake_workspace, TaskRecord(
        id="a1b2c3d4-0000-0000-0000-000000000001",
        prompt="p", status=STATUS_RUNNING, started_at=1.0,
    ))
    result = _run(claude_task_status("1"))
    assert "还在跑" in result


def test_status_out_of_range(fake_workspace: Path) -> None:
    from claude_task_runner import save_task, TaskRecord, STATUS_READY
    from extensions.tools.claude_task import claude_task_status
    save_task(fake_workspace, TaskRecord(
        id="a1b2c3d4-0000-0000-0000-000000000001",
        prompt="p", status=STATUS_READY, started_at=1.0,
    ))
    result = _run(claude_task_status("99"))
    assert "[ERROR]" in result
    assert "超出范围" in result


# ---------------------------------------------------------------------------
# continue
# ---------------------------------------------------------------------------


def test_continue_by_keyword(fake_workspace: Path, monkeypatch) -> None:
    from claude_task_runner import save_task, TaskRecord, STATUS_READY, update_status, STATUS_RUNNING
    from extensions.tools.claude_task import claude_task_continue
    save_task(fake_workspace, TaskRecord(
        id="a1b2c3d4-0000-0000-0000-000000000001",
        prompt="竞品 X 调研", status=STATUS_READY, started_at=1.0,
    ))

    class FakeLoop:
        def create_task(self, coro):
            coro.close()
            return None
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())

    result = _run(claude_task_continue("竞品 X", "顺便对比 Y"))
    assert "追加指令" in result
    assert "竞品 X" in result


def test_continue_by_number(fake_workspace: Path, monkeypatch) -> None:
    from claude_task_runner import save_task, TaskRecord, STATUS_READY
    from extensions.tools.claude_task import claude_task_continue
    save_task(fake_workspace, TaskRecord(
        id="a1b2c3d4-0000-0000-0000-000000000001",
        prompt="p1", status=STATUS_READY, started_at=1.0,
    ))
    save_task(fake_workspace, TaskRecord(
        id="a1b2c3d4-0000-0000-0000-000000000002",
        prompt="p2", status=STATUS_READY, started_at=2.0,
    ))

    class FakeLoop:
        def create_task(self, coro):
            coro.close()
            return None
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())

    result = _run(claude_task_continue("2", "再加一项"))
    assert "追加指令" in result


def test_continue_empty_prompt(fake_workspace: Path) -> None:
    from extensions.tools.claude_task import claude_task_continue
    result = _run(claude_task_continue("1", ""))
    assert "[ERROR]" in result


def test_continue_wrong_state(fake_workspace: Path) -> None:
    from claude_task_runner import save_task, TaskRecord, STATUS_RUNNING
    from extensions.tools.claude_task import claude_task_continue
    save_task(fake_workspace, TaskRecord(
        id="a1b2c3d4-0000-0000-0000-000000000001",
        prompt="p", status=STATUS_RUNNING, started_at=1.0,
    ))
    result = _run(claude_task_continue("1", "再加一项"))
    assert "[ERROR]" in result
    assert "not in ready state" in result


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


def test_register_returns_four_tools() -> None:
    from extensions.tools.claude_task import register
    tools = register()
    assert len(tools) == 4
    names = {t.__name__ for t in tools}
    assert "claude_task_create" in names
    assert "claude_task_list" in names
    assert "claude_task_status" in names
    assert "claude_task_continue" in names


# ---------------------------------------------------------------------------
# cwd 参数（tool 层）
# ---------------------------------------------------------------------------


def test_create_with_cwd_passes_through(fake_workspace: Path, monkeypatch, tmp_path) -> None:
    """cwd 参数透传到 runner.start_task 并写入 task.json。"""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda x: "/usr/local/bin/claude")

    class FakeLoop:
        def create_task(self, coro):
            coro.close()
            return None
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())

    target = tmp_path / "project_x"
    target.mkdir()
    from extensions.tools.claude_task import claude_task_create
    result = _run(claude_task_create("调研 Y", cwd=str(target)))
    assert "调研任务已开起" in result
    assert str(target.resolve()) in result  # cwd 注脚


def test_create_invalid_cwd_returns_error(fake_workspace: Path, monkeypatch, tmp_path) -> None:
    """cwd 不存在时返回 [ERROR]。"""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda x: "/usr/local/bin/claude")
    from extensions.tools.claude_task import claude_task_create
    result = _run(claude_task_create("p", cwd="/no/such/dir/xyz"))
    assert "[ERROR]" in result
    assert "不存在" in result


# ---------------------------------------------------------------------------
# list limit 参数（tool 层）
# ---------------------------------------------------------------------------


def test_list_default_limit_is_10(fake_workspace: Path, monkeypatch) -> None:
    """默认 limit=10。"""
    from pathlib import Path as _Path
    from extensions.tools.claude_task import claude_task_list

    fake_home = fake_workspace / "fake_home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "history.jsonl").write_text(
        "\n".join(
            f'{{"sessionId":"s{i:03d}","display":"x{i}","timestamp":{i},"project":"/p"}}'
            for i in range(30)
        ) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: _Path("/p")))

    result = _run(claude_task_list("all"))
    # 应该恰好返回 10 行（第 1 到第 10 个）
    lines = [l for l in result.split("\n") if l.strip()]
    assert len(lines) == 10
    assert "第 1 个" in lines[0]
    assert "第 10 个" in lines[-1]


def test_list_explicit_limit(fake_workspace: Path, monkeypatch) -> None:
    """limit=N 指定条数。"""
    from pathlib import Path as _Path
    from extensions.tools.claude_task import claude_task_list

    fake_home = fake_workspace / "fake_home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "history.jsonl").write_text(
        "\n".join(
            f'{{"sessionId":"s{i:03d}","display":"x{i}","timestamp":{i},"project":"/p"}}'
            for i in range(20)
        ) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: _Path("/p")))

    result = _run(claude_task_list("all", limit=5))
    lines = [l for l in result.split("\n") if l.strip()]
    assert len(lines) == 5