"""Phase 0 read_file 最小版本测试（仅 path 参数）。

完整测试矩阵在 Task 4 补全。
"""
from __future__ import annotations

import asyncio

import pytest


def _run(coro):
    """运行 async 工具函数并返回结果。"""
    return asyncio.run(coro)


def test_read_file_hello(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file
    target = tmp_path / "hello.txt"
    target.write_text("hello world\n", encoding="utf-8")

    result = _run(read_file(str(target)))

    assert "hello world" in result


def test_read_file_not_found(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file

    result = _run(read_file(str(tmp_path / "missing.txt")))

    assert result.startswith("[ERROR]")


def test_read_file_is_directory(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file

    result = _run(read_file(str(tmp_path)))

    assert result.startswith("[ERROR]")