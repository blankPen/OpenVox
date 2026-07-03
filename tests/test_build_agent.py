"""Integration test for build_agent() in main.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _import_main():
    """Load main.py as a module from the project root."""
    spec = importlib.util.spec_from_file_location(
        "main", Path(__file__).parent.parent / "main.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_build_agent_returns_agent_instance(workspace_root: Path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["main"])
    main = _import_main()
    agent = main.build_agent(workspace_root)
    # Agent 类有 instructions / tools 属性
    assert hasattr(agent, "instructions")
    assert hasattr(agent, "tools")
    # instructions 包含 SOUL 内容
    assert "小语" in agent.instructions
    # tools 至少包含 load_skill（不一定有 current_time，取决于 workspace fixture）
    tool_names = [t.__name__ for t in agent.tools if hasattr(t, "__name__")]
    assert "load_skill" in tool_names


def test_build_agent_loads_current_time_when_present(workspace_root: Path, monkeypatch):
    # 自己写一份 current_time.py 到 fixture
    tools_dir = workspace_root / "extensions" / "tools"
    (tools_dir / "current_time.py").write_text(
        "from livekit.agents import function_tool\n"
        "@function_tool()\n"
        "async def current_time() -> str:\n"
        "    '''Get current time.'''\n"
        "    return 'now'\n"
        "def register():\n"
        "    return [current_time]\n",
        encoding="utf-8",
    )
    main = _import_main()
    agent = main.build_agent(workspace_root)
    tool_names = [t.__name__ for t in agent.tools if hasattr(t, "__name__")]
    assert "current_time" in tool_names
