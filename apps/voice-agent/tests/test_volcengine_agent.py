"""Tests for the simplified ``VolcengineAgent`` in ``main.py``.

Task 2 收尾测试，验证以下不变量：

* ``VolcengineAgent`` 默认 instructions 是中文小语人设
* 可以通过 ``instructions=`` 参数覆盖默认值
* ``on_enter`` 触发 ``generate_reply``（pipeline 是唯一模式）
* ``main.py`` 不再 ``import agent_persona``
* ``main.py`` 不再有 ``_session_holder`` 模块级变量
* ``main.py`` 不再有 ``build_agent`` 函数
* ``entrypoint`` 不再引用 ``WORKSPACE_ROOT`` / ``agent_memory``
"""
from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

MAIN_PATH = Path(__file__).resolve().parents[1] / "main.py"
MAIN_SRC = MAIN_PATH.read_text(encoding="utf-8")


def _load_main_module():
    """强制 reload ``main`` 以保证测试读到的状态是当前文件。"""
    if "main" in sys.modules:
        del sys.modules["main"]
    # 清理相关缓存
    for mod_name in list(sys.modules):
        if mod_name.startswith(("agent_persona", "agent_skills", "agent_extensions", "agent_memory")):
            del sys.modules[mod_name]
    return __import__("main")


# ---------------------------------------------------------------------------
# 默认行为
# ---------------------------------------------------------------------------


def test_agent_instructions_default():
    """默认 instructions 必须是中文「小语」人设的描述。"""
    main = _load_main_module()
    agent = main.VolcengineAgent()
    # 默认值必须是中文
    assert agent.instructions, "instructions 不应为空"
    assert "小语" in agent.instructions, (
        f"默认 instructions 应包含「小语」人设标识，实际: {agent.instructions!r}"
    )
    # 不应使用 Markdown / 表情符号（与原版一致的口吻约束）
    assert "**" not in agent.instructions
    assert "```" not in agent.instructions


def test_agent_instructions_override():
    """显式传入 instructions 必须覆盖默认值。"""
    main = _load_main_module()
    custom = "你是一个只会说英语的助手，名字叫 Bob。"
    agent = main.VolcengineAgent(instructions=custom)
    assert agent.instructions == custom


def test_on_enter_pipeline_calls_generate_reply():
    """pipeline 模式下 on_enter 必须调用 ``self.session.generate_reply()``，
    且必须传 ``user_input=`` 注入占位 user 消息。

    不传 user_input 时 livekit-agents 1.6.x 的 ``_pipeline_reply_task_impl``
    不会往 chat_ctx 插入 user 消息，Hermes 网关会回 400
    "No user message found in messages"。
    """
    main = _load_main_module()

    # ``livekit.agents.Agent.session`` 是只读 property（通过 ``_get_activity_or_raise`` 解析）。
    # 测试侧定义一个本地子类覆盖为可读写属性，把 mock session 注入。
    class _TestableAgent(main.VolcengineAgent):
        def __init__(self):
            super().__init__()
            self._test_session = None

        @property
        def session(self):
            return self._test_session

        @session.setter
        def session(self, value):
            self._test_session = value

    mock_session = MagicMock()
    mock_session.generate_reply = AsyncMock()

    agent = _TestableAgent()
    agent.session = mock_session

    asyncio.run(agent.on_enter())

    mock_session.generate_reply.assert_awaited_once()
    # 必须显式传 user_input（不能仅依赖 generate_reply() 默认行为），
    # 否则 chat_ctx 里只有 system(instr) 没有 user，Hermes 400。
    call_kwargs = mock_session.generate_reply.call_args.kwargs
    assert "user_input" in call_kwargs, (
        f"on_enter 调 generate_reply 时必须传 user_input=... 占位 user 消息，"
        f"否则 Hermes 网关会 400 'No user message found in messages'。"
        f"实际 kwargs={call_kwargs!r}"
    )
    user_input = call_kwargs["user_input"]
    assert isinstance(user_input, str) and user_input.strip(), (
        f"user_input 必须是非空字符串（成为 chat 里 role='user' 的 ChatMessage），"
        f"实际={user_input!r}"
    )


# ---------------------------------------------------------------------------
# 静态源码扫描 — 这些是 main.py 不应再出现的字面 / 标识符
# ---------------------------------------------------------------------------


def test_no_build_agent_function():
    """main.py 不应再定义 ``build_agent`` 函数（persona/skills/extensions 装配流程已废弃）。"""
    tree = ast.parse(MAIN_SRC)
    func_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "build_agent" not in func_names, (
        "main.py 仍定义 build_agent() — Task 2 要求彻底删除"
    )


def test_no_agent_persona_import():
    """main.py 不应再 import agent_persona（也覆盖 agent_skills / agent_extensions / agent_memory）。"""
    forbidden = ["agent_persona", "agent_skills", "agent_extensions", "agent_memory"]
    lowered = MAIN_SRC.lower()
    for mod in forbidden:
        assert mod not in lowered, (
            f"main.py 仍含 {mod} 引用 — Task 2 要求彻底移除"
        )


def test_no_session_holder_module_global():
    """main.py 不应再有 ``_session_holder`` 模块级变量。"""
    tree = ast.parse(MAIN_SRC)
    # 找模块顶层赋值
    top_level_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    top_level_names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            top_level_names.add(node.target.id)
    assert "_session_holder" not in top_level_names, (
        "main.py 顶层仍含 _session_holder — Task 2 要求彻底删除"
    )
    # 兜底：源码里也不应出现该字面
    assert "_session_holder" not in MAIN_SRC, (
        "main.py 仍含 _session_holder 字面"
    )


def test_no_workspace_root_in_entrypoint():
    """entrypoint 函数体里不应再使用 ``WORKSPACE_ROOT`` 或 ``agent_memory``。"""
    tree = ast.parse(MAIN_SRC)
    # 找到 entrypoint 函数体
    entrypoint_body = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "entrypoint":
            entrypoint_body = node
            break
    assert entrypoint_body is not None, "main.py 缺少 entrypoint 函数"

    # 收集 entrypoint 函数体内所有 Name 节点
    referenced = set()
    for sub in ast.walk(entrypoint_body):
        if isinstance(sub, ast.Name):
            referenced.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            # 不递归到 attribute.value (避免误报), 但扫模块名
            if isinstance(sub.value, ast.Name):
                referenced.add(sub.value.id)

    assert "WORKSPACE_ROOT" not in referenced, (
        "entrypoint() 仍引用 WORKSPACE_ROOT — Task 2 要求彻底移除"
    )
    assert "agent_memory" not in referenced, (
        "entrypoint() 仍 import agent_memory — Task 2 要求彻底移除"
    )