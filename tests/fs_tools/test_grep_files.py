"""grep_files 完整测试矩阵。Spec §3.3 / §5.2。"""
from __future__ import annotations

import asyncio
import json

import pytest


def _run(coro):
    return asyncio.run(coro)


def test_grep_files_single_file_match(tmp_path):
    from workspace.extensions.tools.fs.grep_files import grep_files
    target = tmp_path / "x.py"
    target.write_text("def foo():\n    pass\n", encoding="utf-8")

    result = _run(grep_files("def foo", str(tmp_path)))

    matches = json.loads(result)
    assert len(matches) == 1
    assert "x.py:1:def foo():" in matches[0]


def test_grep_files_multiple_files(tmp_path):
    from workspace.extensions.tools.fs.grep_files import grep_files
    (tmp_path / "a.py").write_text("target_text\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("target_text\nother\ntarget_text\n", encoding="utf-8")

    result = _run(grep_files("target_text", str(tmp_path), include="*.py"))

    matches = json.loads(result)
    assert len(matches) == 3  # a.py:1, b.py:1, b.py:3


def test_grep_files_include_filter(tmp_path):
    from workspace.extensions.tools.fs.grep_files import grep_files
    (tmp_path / "a.py").write_text("target\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("target\n", encoding="utf-8")

    result = _run(grep_files("target", str(tmp_path), include="*.py"))

    matches = json.loads(result)
    assert len(matches) == 1
    assert "a.py" in matches[0]


def test_grep_files_max_results(tmp_path):
    from workspace.extensions.tools.fs.grep_files import grep_files
    content = "\n".join(f"line {i}" for i in range(200))
    target = tmp_path / "big.txt"
    target.write_text(content, encoding="utf-8")

    result = _run(grep_files("line", str(tmp_path), max_results=10))

    matches = json.loads(result)
    assert len(matches) == 10


def test_grep_files_no_match(tmp_path):
    from workspace.extensions.tools.fs.grep_files import grep_files
    target = tmp_path / "x.txt"
    target.write_text("hello\n", encoding="utf-8")

    result = _run(grep_files("nonexistent", str(tmp_path)))

    assert json.loads(result) == []


def test_grep_files_invalid_regex(tmp_path):
    from workspace.extensions.tools.fs.grep_files import grep_files
    target = tmp_path / "x.txt"
    target.write_text("hello\n", encoding="utf-8")

    result = _run(grep_files("[invalid(", str(tmp_path)))

    assert result.startswith("[ERROR]")
    assert "regex" in result.lower() or "正则" in result