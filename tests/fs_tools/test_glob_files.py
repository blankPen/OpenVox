"""glob_files 完整测试矩阵。Spec §3.3 / §5.2。"""
from __future__ import annotations

import asyncio
import json

import pytest


def _run(coro):
    return asyncio.run(coro)


def test_glob_files_simple_pattern(tmp_path):
    from workspace.extensions.tools.fs.glob_files import glob_files
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "c.py").write_text("c")

    result = _run(glob_files("*.txt", str(tmp_path)))

    files = json.loads(result)
    assert sorted(files) == ["a.txt", "b.txt"]


def test_glob_files_recursive_double_star(tmp_path):
    from workspace.extensions.tools.fs.glob_files import glob_files
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.py").write_text("d")
    (tmp_path / "top.py").write_text("t")

    result = _run(glob_files("**/*.py", str(tmp_path)))

    files = json.loads(result)
    assert sorted(files) == ["sub/deep.py", "top.py"]


def test_glob_files_no_match(tmp_path):
    from workspace.extensions.tools.fs.glob_files import glob_files

    result = _run(glob_files("*.nonexistent", str(tmp_path)))

    assert json.loads(result) == []


def test_glob_files_path_not_exist(tmp_path):
    from workspace.extensions.tools.fs.glob_files import glob_files

    result = _run(glob_files("*.txt", str(tmp_path / "missing_dir")))

    assert result.startswith("[ERROR]")


def test_glob_files_default_path_is_cwd(tmp_path, monkeypatch):
    from workspace.extensions.tools.fs.glob_files import glob_files
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x.txt").write_text("x")

    result = _run(glob_files("*.txt"))

    files = json.loads(result)
    assert "x.txt" in files


def test_glob_files_returns_relative_paths(tmp_path):
    from workspace.extensions.tools.fs.glob_files import glob_files
    (tmp_path / "x.txt").write_text("x")

    result = _run(glob_files("*.txt", str(tmp_path)))

    files = json.loads(result)
    assert files == ["x.txt"]