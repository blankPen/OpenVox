"""Tests for workspace/agent_persona.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_persona import Persona, load_persona


def test_load_persona_returns_dataclass(workspace_root: Path):
    p = load_persona(workspace_root)
    assert isinstance(p, Persona)
    assert isinstance(p.soul, str)
    assert isinstance(p.agents, str)
    assert isinstance(p.tools_guide, str)
    assert isinstance(p.combined, str)


def test_load_persona_combined_contains_all_three(workspace_root: Path):
    p = load_persona(workspace_root)
    assert p.soul in p.combined
    assert p.agents in p.combined
    assert p.tools_guide in p.combined


def test_load_persona_missing_file_raises(tmp_path: Path):
    # tmp_path 没有 persona 目录 → 应该 fail-fast
    with pytest.raises(FileNotFoundError):
        load_persona(tmp_path)


def test_load_persona_partial_files_raises(tmp_path: Path):
    # 只建 SOUL.md → 缺另外两个 → 失败
    (tmp_path / "persona").mkdir()
    (tmp_path / "persona" / "SOUL.md").write_text("soul", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        load_persona(tmp_path)
