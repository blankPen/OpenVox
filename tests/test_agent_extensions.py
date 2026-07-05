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
    # 清空 fixture 复制来的 mcp/ 文件，只放本测试关心的
    for p in mcp_dir.glob("*.json"):
        p.unlink()
    (mcp_dir / "git.json").write_text(json.dumps({
        "command": "uvx", "args": ["mcp-server-git"]
    }), encoding="utf-8")
    servers = load_mcp_servers(mcp_dir)
    assert len(servers) == 1
    assert servers[0].command == "uvx"
    assert servers[0].args == ["mcp-server-git"]


def test_load_mcp_servers_missing_command_raises(workspace_root: Path):
    mcp_dir = workspace_root / "extensions" / "mcp"
    for p in mcp_dir.glob("*.json"):
        p.unlink()
    (mcp_dir / "bad.json").write_text(json.dumps({"args": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="dict"):
        load_mcp_servers(mcp_dir)


def test_load_mcp_servers_streamable_http(workspace_root: Path, monkeypatch):
    """v0.2 schema: 顶层 {<name>: <config>}，带 headers + 环境变量替换。"""
    mcp_dir = workspace_root / "extensions" / "mcp"
    for p in mcp_dir.glob("*.json"):
        p.unlink()
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test-abc")
    (mcp_dir / "websearch.json").write_text(json.dumps({
        "WebSearch": {
            "type": "streamableHttp",
            "isActive": True,
            "name": "AliyunBailianMCP_WebSearch",
            "baseUrl": "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp",
            "headers": {"Authorization": "Bearer ${DASHSCOPE_API_KEY}"},
        }
    }), encoding="utf-8")
    servers = load_mcp_servers(mcp_dir)
    assert len(servers) == 1
    # MCPServerHTTP 字段: url, headers
    assert "WebSearch/mcp" in servers[0].url
    assert servers[0].headers["Authorization"] == "Bearer sk-test-abc"


def test_load_mcp_servers_skips_inactive(workspace_root: Path, monkeypatch):
    mcp_dir = workspace_root / "extensions" / "mcp"
    for p in mcp_dir.glob("*.json"):
        p.unlink()
    (mcp_dir / "off.json").write_text(json.dumps({
        "OffServer": {"type": "streamableHttp", "isActive": False, "baseUrl": "http://x"}
    }), encoding="utf-8")
    servers = load_mcp_servers(mcp_dir)
    assert servers == []
