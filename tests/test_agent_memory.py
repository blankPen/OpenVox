"""Tests for workspace/agent_memory.py (v0.1 read path only)."""
from __future__ import annotations

from pathlib import Path

from agent_memory import MemoryStore


def test_init_creates_directory_structure(tmp_path: Path):
    user_root = tmp_path / "alice"
    MemoryStore(user_root)
    assert user_root.is_dir()
    assert (user_root / "User.md").is_file()  # 空文件
    assert (user_root / "MEMORY.md").is_file()  # 空文件
    assert (user_root / "memory").is_dir()


def test_load_user_prompt_reads_both_files(tmp_path: Path):
    user_root = tmp_path / "alice"
    store = MemoryStore(user_root)
    (user_root / "User.md").write_text("# USER\nname: alice\n", encoding="utf-8")
    (user_root / "MEMORY.md").write_text("# MEMORY\nlikes coffee\n", encoding="utf-8")
    out = store.load_user_prompt()
    assert "alice" in out
    assert "coffee" in out
    # 两段之间有分隔
    assert "\n\n" in out


def test_load_user_prompt_empty_when_no_content(tmp_path: Path):
    user_root = tmp_path / "bob"
    store = MemoryStore(user_root)
    assert store.load_user_prompt() == ""


def test_load_user_prompt_only_user_file(tmp_path: Path):
    user_root = tmp_path / "carol"
    store = MemoryStore(user_root)
    (user_root / "User.md").write_text("only user", encoding="utf-8")
    out = store.load_user_prompt()
    assert "only user" in out


def test_load_user_prompt_only_memory_file(tmp_path: Path):
    user_root = tmp_path / "dave"
    store = MemoryStore(user_root)
    (user_root / "MEMORY.md").write_text("only memory", encoding="utf-8")
    out = store.load_user_prompt()
    assert "only memory" in out
