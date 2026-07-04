"""Shared pytest fixtures for fs tools tests."""
from __future__ import annotations

import sys

import pytest
from pathlib import Path


@pytest.fixture
def tmp_workspace(tmp_path) -> Path:
    """预置几个测试文件。

    macOS 忽略非 own 用户的 chmod 0o000，无法制造真实 EACCES；该平台跳过 no_read 相关断言。
    """
    (tmp_path / "hello.txt").write_text("hello\nworld\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "data.json").write_text('{"k": "v"}', encoding="utf-8")
    (tmp_path / "big.txt").write_text("x" * 2_000_000, encoding="utf-8")  # 2MB
    (tmp_path / "no_read").write_text("secret")
    if sys.platform != "darwin":
        (tmp_path / "no_read").chmod(0o000)
    return tmp_path