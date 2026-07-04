"""E2E 烟雾测试骨架：验证 6 个 fs 工具在 realtime 模式下能被调起来。

前置：
- worker 用 `python main.py start` 在后台运行
- LiveKit server 在跑（lk dispatch 依赖）
- 环境变量 LIVEKIT_URL/API_KEY/SECRET 已配置

跑法（实跑，需要客户端集成）：
    source .venv/bin/activate
    pytest tests/e2e_fs_tools.py -v -s

当前 plan 仅交付骨架 + 数据驱动参数化结构。**实跑 e2e**（dispatch agent +
监听 TTS 回复 + 验证工具副作用）需要单独的客户端程序，超出单元测试范畴，
由 orchestrator 在 Phase 0 baseline 验证后单独安排。

本骨架的价值：把测试矩阵和数据驱动结构定下来，未来补实跑时不用重新设计。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


# 6 工具的 e2e 场景：(触发 prompt 模板, 验证函数)
SCENARIOS = [
    (
        "请帮我读 {path} 的内容",
        lambda result, path, content: content in result,
    ),
    (
        "请把 'foo' 写到 {path}",
        lambda result, path, content: Path(path).read_text(encoding="utf-8") == "foo",
    ),
    (
        "请把 {path} 里的 'foo' 改成 'bar'",
        lambda result, path, content: Path(path).read_text(encoding="utf-8") == "bar",
    ),
    (
        "请列 {dir} 下所有 .txt",
        lambda result, path, content: Path(path).name in result,
    ),
    (
        "请在 {dir} 下找包含 'foo' 的文件",
        lambda result, path, content: Path(path).name in result,
    ),
    (
        "请运行 echo hi",
        lambda result, path, content: "hi" in result,
    ),
]


@pytest.mark.parametrize("prompt_template,validate", SCENARIOS)
def test_fs_tool_e2e(prompt_template, validate, tmp_path):
    """每个工具一个最小 e2e 场景（骨架）。

    实跑需要：
    1. dispatch agent 到新房间
    2. LiveKit SDK 加入房间
    3. 发文本 prompt
    4. 监听 agent 回复（STT 输出）
    5. 调用 validate 验证副作用

    当前全部 skip——实跑逻辑未来作为 Phase 1 集成测试单独交付。
    """
    pytest.skip("Phase 1 E2E 实跑需要客户端集成，由 orchestrator 单独安排")