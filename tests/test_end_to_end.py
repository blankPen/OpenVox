"""End-to-end smoke test: verify all 5 modules + content wire up correctly.

Doesn't require LiveKit or Volcengine — exercises everything offline.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _import_main():
    spec = importlib.util.spec_from_file_location(
        "main", Path(__file__).parent.parent / "main.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_end_to_end_wiring(workspace_root: Path, monkeypatch):
    """Build an agent and verify it has: persona, tools, skills, mcp slot."""
    main = _import_main()

    # Step 1: persona loads
    from agent_persona import load_persona
    p = load_persona(workspace_root)
    assert "小语" in p.combined
    assert "current_time" in p.tools_guide  # 工具说明里有 current_time

    # Step 2: skills register
    from agent_skills import scan_skills
    skills = scan_skills(workspace_root / "skills")
    assert "weather" in skills
    assert "天气" in skills["weather"].body  # weather skill 提到天气

    # Step 3: extensions load
    from agent_extensions import load_tools
    tools = load_tools(workspace_root / "extensions" / "tools")
    assert any(getattr(t, "__name__", "") == "current_time" for t in tools)

    # Step 4: build_agent
    agent = main.build_agent(workspace_root)
    tool_names = [getattr(t, "__name__", "") for t in agent.tools]
    assert "current_time" in tool_names
    assert "load_skill" in tool_names  # load_skill 是 build_agent 注入的

    # Step 5: memory read path
    from agent_memory import MemoryStore
    # 模拟一个用户的 user_root（写一份 User.md）
    user_root = workspace_root / "users" / "alice"
    user_root.mkdir(parents=True, exist_ok=True)
    (user_root / "User.md").write_text("name: alice\n住在北京\n", encoding="utf-8")
    store = MemoryStore(user_root)
    prompt = store.load_user_prompt()
    assert "alice" in prompt
    assert "北京" in prompt


def test_agent_instructions_contain_all_layers(workspace_root: Path, monkeypatch):
    """build_agent 输出的 instructions 应包含 persona 段 A。

    注：on_enter 的 memory 注入需要 self.session，smoke test 不跑 livekit，
    只验证 build_agent 阶段的内容。memory 注入由 build_agent 集成测试覆盖。
    """
    main = _import_main()
    agent = main.build_agent(workspace_root)
    # 段 A — persona
    assert "小语" in agent.instructions
    assert "current_time" in agent.instructions
