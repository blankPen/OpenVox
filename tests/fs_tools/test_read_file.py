"""read_file 完整测试矩阵。Spec §5.2 错误处理契约。"""
from __future__ import annotations

import asyncio
import logging
import sys

import pytest


def _run(coro):
    return asyncio.run(coro)


# ---- 成功路径 ----

def test_read_file_hello(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file
    target = tmp_path / "hello.txt"
    target.write_text("hello\nworld\n", encoding="utf-8")

    result = _run(read_file(str(target)))

    assert "hello" in result
    assert "world" in result


def test_read_file_with_start_line(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file
    target = tmp_path / "lines.txt"
    target.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")

    result = _run(read_file(str(target), start_line=1, end_line=3))

    assert "line1" not in result
    assert "line2" in result
    assert "line3" in result
    assert "line4" not in result


def test_read_file_with_start_only(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file
    target = tmp_path / "lines.txt"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")

    # 0-indexed: start_line=2 跳过 line1/line2，从 line3 开始
    result = _run(read_file(str(target), start_line=2))

    assert "line1" not in result
    assert "line2" not in result
    assert "line3" in result


def test_read_file_with_end_only(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file
    target = tmp_path / "lines.txt"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")

    result = _run(read_file(str(target), end_line=2))

    assert "line1" in result
    assert "line2" in result
    assert "line3" not in result


# ---- 错误路径 ----

def test_read_file_not_found(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file

    result = _run(read_file(str(tmp_path / "missing.txt")))

    assert result.startswith("[ERROR]")
    assert "不存在" in result


def test_read_file_is_directory(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file

    result = _run(read_file(str(tmp_path)))

    assert result.startswith("[ERROR]")
    assert "目录" in result


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS 忽略 chmod 0o000")
def test_read_file_permission_denied(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file
    no_read = tmp_path / "no_read.txt"
    no_read.write_text("secret")
    no_read.chmod(0o000)
    try:
        result = _run(read_file(str(no_read)))
        assert result.startswith("[ERROR]")
    finally:
        no_read.chmod(0o644)


def test_read_file_big_file_truncated(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file
    target = tmp_path / "big.txt"
    target.write_text("x" * 2_000_000, encoding="utf-8")

    result = _run(read_file(str(target)))

    assert "[TRUNCATED]" in result


# ---- 敏感路径 WARNING 日志 ----

def test_read_file_sensitive_path_warning(tmp_path, caplog):
    from workspace.extensions.tools.fs.read_file import read_file
    target = tmp_path / "fake_passwd"
    target.write_text("fake content")

    import workspace.extensions.tools.fs.read_file as rf_mod
    original = rf_mod._is_sensitive
    rf_mod._is_sensitive = lambda p: True
    try:
        with caplog.at_level(logging.WARNING, logger="volcengine-agent"):
            result = _run(read_file(str(target)))
        assert "SENSITIVE_PATH" in caplog.text
    finally:
        rf_mod._is_sensitive = original

    assert "fake content" in result


# ---- 默认参数 ----

def test_read_file_default_start_end(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file
    target = tmp_path / "x.txt"
    target.write_text("content", encoding="utf-8")

    result = _run(read_file(str(target)))

    assert "content" in result