"""write_file 完整测试矩阵。Spec §3.3 / §5.2 / §5.5。"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest


def _run(coro):
    return asyncio.run(coro)


# ---- 成功路径 ----

def test_write_file_overwrite_creates(tmp_path):
    from workspace.extensions.tools.fs.write_file import write_file
    target = tmp_path / "out.txt"

    result = _run(write_file(str(target), "hello world"))

    assert result.startswith("[OK]")
    assert target.read_text(encoding="utf-8") == "hello world"


def test_write_file_overwrite_existing(tmp_path):
    from workspace.extensions.tools.fs.write_file import write_file
    target = tmp_path / "out.txt"
    target.write_text("OLD", encoding="utf-8")

    result = _run(write_file(str(target), "NEW"))

    assert target.read_text(encoding="utf-8") == "NEW"


def test_write_file_append_mode(tmp_path):
    from workspace.extensions.tools.fs.write_file import write_file
    target = tmp_path / "out.txt"
    target.write_text("line1\n", encoding="utf-8")

    result = _run(write_file(str(target), "line2\n", mode="append"))

    assert target.read_text(encoding="utf-8") == "line1\nline2\n"


def test_write_file_creates_parent_dirs(tmp_path):
    from workspace.extensions.tools.fs.write_file import write_file
    target = tmp_path / "sub" / "nested" / "out.txt"

    result = _run(write_file(str(target), "content"))

    assert target.read_text(encoding="utf-8") == "content"


# ---- 错误路径 ----

def test_write_file_invalid_mode(tmp_path):
    from workspace.extensions.tools.fs.write_file import write_file
    target = tmp_path / "out.txt"

    result = _run(write_file(str(target), "x", mode="invalid"))

    assert result.startswith("[ERROR]")
    assert "overwrite" in result and "append" in result


def test_write_file_non_utf8(tmp_path):
    from workspace.extensions.tools.fs.write_file import write_file
    target = tmp_path / "out.bin"

    # surrogate pair（未配对的代理项）encode utf-8 必失败
    result = _run(write_file(str(target), "abc\udcff\udcfe"))

    assert result.startswith("[ERROR]")
    assert "UTF-8" in result


# ---- 原子写 ----

def test_write_file_atomic_no_leftover_tmp(tmp_path):
    from workspace.extensions.tools.fs.write_file import write_file
    target = tmp_path / "out.txt"

    _run(write_file(str(target), "content"))

    leftovers = list(tmp_path.glob(".tmp_*"))
    assert leftovers == []


def test_write_file_write_op_warning_logged(tmp_path, caplog):
    import logging
    from workspace.extensions.tools.fs.write_file import write_file
    target = tmp_path / "out.txt"
    with caplog.at_level(logging.WARNING, logger="volcengine-agent"):
        _run(write_file(str(target), "x"))
    assert "WRITE_OP" in caplog.text


# ---- 权限拒绝（用 monkeypatch 模拟，不依赖真实 chmod）----

def test_write_file_permission_denied(tmp_path, monkeypatch):
    """用 monkeypatch 模拟 PermissionError，避免依赖真实 chmod（macOS 失效）。"""
    from workspace.extensions.tools.fs import write_file as wf_mod
    target = tmp_path / "no_write.txt"
    target.write_text("existing")

    # Patch os.write 抛 PermissionError（原子写路径里调 os.write）
    import os as _os
    real_open = _os.open

    def _raise_open(*args, **kwargs):
        raise PermissionError(13, "Permission denied", str(target))

    monkeypatch.setattr(_os, "open", _raise_open)

    result = _run(wf_mod.write_file(str(target), "new content"))
    assert result.startswith("[ERROR]")