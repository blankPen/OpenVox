"""Tests for workspace/agent_extensions.py."""
from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from agent_extensions import load_mcp_servers, load_tools


def test_load_tools_collects_register_calls(tmp_path: Path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "fake_a.py").write_text(dedent("""\
        async def fake_a() -> str:
            '''Tool A.'''
            return 'a'
        def register():
            return [fake_a]
    """), encoding="utf-8")
    (tools_dir / "fake_b.py").write_text(dedent("""\
        async def fake_b() -> str:
            '''Tool B.'''
            return 'b'
        def register():
            return [fake_b]
    """), encoding="utf-8")
    tools = load_tools(tools_dir)
    assert len(tools) == 2


def test_load_tools_skips_dunder_and_underscore(tmp_path: Path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "_private.py").write_text("def register(): return []", encoding="utf-8")
    tools = load_tools(tools_dir)
    assert tools == []  # _private.py 跳过


def test_load_tools_missing_register_raises(tmp_path: Path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "broken.py").write_text("x = 1", encoding="utf-8")
    with pytest.raises(AttributeError, match="register"):
        load_tools(tools_dir)


def test_load_mcp_servers_reads_json(workspace_root: Path):
    mcp_dir = workspace_root / "extensions" / "mcp"
    (mcp_dir / "git.json").write_text(json.dumps({
        "command": "uvx", "args": ["mcp-server-git"]
    }), encoding="utf-8")
    servers = load_mcp_servers(mcp_dir)
    assert len(servers) == 1
    assert servers[0].command == "uvx"
    assert servers[0].args == ["mcp-server-git"]


def test_load_mcp_servers_missing_command_raises(workspace_root: Path):
    mcp_dir = workspace_root / "extensions" / "mcp"
    (mcp_dir / "bad.json").write_text(json.dumps({"args": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="command"):
        load_mcp_servers(mcp_dir)
