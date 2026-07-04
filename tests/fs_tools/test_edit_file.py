"""edit_file 完整测试矩阵。Spec §3.3 / §5.2。"""
from __future__ import annotations

import asyncio
import logging

import pytest


def _run(coro):
    return asyncio.run(coro)


def test_edit_file_single_replace(tmp_path):
    from workspace.extensions.tools.fs.edit_file import edit_file
    target = tmp_path / "code.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")

    result = _run(edit_file(str(target), "return 1", "return 2"))

    assert result.startswith("[OK]")
    assert target.read_text(encoding="utf-8") == "def foo():\n    return 2\n"


def test_edit_file_replace_all(tmp_path):
    from workspace.extensions.tools.fs.edit_file import edit_file
    target = tmp_path / "code.py"
    target.write_text("a = 1\na = 1\na = 1\n", encoding="utf-8")

    result = _run(edit_file(str(target), "a = 1", "a = 2", replace_all=True))

    assert result.startswith("[OK]")
    assert target.read_text(encoding="utf-8") == "a = 2\na = 2\na = 2\n"


def test_edit_file_multiple_match_without_replace_all(tmp_path):
    from workspace.extensions.tools.fs.edit_file import edit_file
    target = tmp_path / "code.py"
    target.write_text("foo\nfoo\nfoo\n", encoding="utf-8")

    result = _run(edit_file(str(target), "foo", "bar"))

    assert result.startswith("[ERROR]")
    assert "3 次" in result
    assert "replace_all" in result


def test_edit_file_old_string_not_found(tmp_path):
    from workspace.extensions.tools.fs.edit_file import edit_file
    target = tmp_path / "code.py"
    target.write_text("hello world\n", encoding="utf-8")

    result = _run(edit_file(str(target), "nonexistent", "x"))

    assert result.startswith("[ERROR]")
    assert "找不到" in result


def test_edit_file_new_equals_old(tmp_path):
    from workspace.extensions.tools.fs.edit_file import edit_file
    target = tmp_path / "code.py"
    target.write_text("hello\n", encoding="utf-8")

    result = _run(edit_file(str(target), "hello", "hello"))

    assert "[OK]" in result
    assert "未变化" in result


def test_edit_file_missing_file(tmp_path):
    from workspace.extensions.tools.fs.edit_file import edit_file

    result = _run(edit_file(str(tmp_path / "missing.txt"), "old", "new"))

    assert result.startswith("[ERROR]")


def test_edit_file_warning_logged(tmp_path, caplog):
    from workspace.extensions.tools.fs.edit_file import edit_file
    target = tmp_path / "x.txt"
    target.write_text("a", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="volcengine-agent"):
        _run(edit_file(str(target), "a", "b"))
    assert "EDIT_OP" in caplog.text


def test_edit_file_case_sensitive(tmp_path):
    from workspace.extensions.tools.fs.edit_file import edit_file
    target = tmp_path / "x.txt"
    target.write_text("Foo\n", encoding="utf-8")

    # "foo" 不应匹配 "Foo"
    result = _run(edit_file(str(target), "foo", "bar"))

    assert result.startswith("[ERROR]")
    assert "找不到" in result