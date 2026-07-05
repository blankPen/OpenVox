"""Shared pytest fixtures for fs tools tests."""
from __future__ import annotations

import pytest
from pathlib import Path


@pytest.fixture
def tmp_workspace(tmp_path) -> Path:
    """预置几个测试文件。"""
    (tmp_path / "hello.txt").write_text("hello\nworld\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "data.json").write_text('{"k": "v"}', encoding="utf-8")
    (tmp_path / "big.txt").write_text("x" * 2_000_000, encoding="utf-8")  # 2MB
    return tmp_path