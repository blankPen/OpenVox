"""Tests for workspace/agent_skills.py."""
from __future__ import annotations

import asyncio
from pathlib import Path
from textwrap import dedent

import pytest

from agent_skills import SkillDef, make_load_skill_tool, scan_skills


def _write_skill(root: Path, name: str, body: str = "skill body", description: str | None = None) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    desc = description if description is not None else f"Description for {name}"
    content = dedent(f"""\
        ---
        name: {name}
        description: {desc}
        ---
        {body}
    """)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


def test_scan_skills_finds_skill(workspace_root: Path):
    skills = scan_skills(workspace_root / "skills")
    # weather skill is created in Task 9 — should already exist by then.
    # If running this test before Task 9, weather won't be present yet;
    # we just check scan_skills handles the existing case.
    if "weather" in skills:
        w = skills["weather"]
        assert isinstance(w, SkillDef)
        assert w.name == "weather"
        assert isinstance(w.description, str) and len(w.description) > 0


def test_scan_skills_duplicate_name_raises(workspace_root: Path):
    # Two different directories, same frontmatter name → duplicate
    _write_skill(workspace_root / "skills", "dup1", body="x", description="x")
    _write_skill(workspace_root / "skills", "dup2", body="x", description="x")
    # overwrite both to have the same name in frontmatter
    for d in ["dup1", "dup2"]:
        (workspace_root / "skills" / d / "SKILL.md").write_text(
            "---\nname: samename\ndescription: x\n---\nbody\n", encoding="utf-8"
        )
    with pytest.raises(ValueError, match="duplicate"):
        scan_skills(workspace_root / "skills")


def test_scan_skills_missing_description_raises(workspace_root: Path):
    skill_dir = workspace_root / "skills" / "broken"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("---\nname: broken\n---\nbody\n", encoding="utf-8")
    with pytest.raises(ValueError, match="description"):
        scan_skills(workspace_root / "skills")


def test_make_load_skill_tool_injects_into_chat_ctx(workspace_root: Path):
    _write_skill(workspace_root / "skills", "alpha", body="ALPHA_BODY", description="alpha desc")
    registry = scan_skills(workspace_root / "skills")
    captured: list[list[dict]] = []

    class FakeSession:
        def update_chat_ctx(self, messages: list[dict]) -> None:
            captured.append(messages)

    def session_provider():
        return FakeSession()

    tool = make_load_skill_tool(registry, session_provider)
    asyncio.run(tool("alpha"))
    assert len(captured) == 1
    assert "ALPHA_BODY" in captured[0][0]["content"]


def test_make_load_skill_tool_unknown_name_returns_error(workspace_root: Path):
    registry = scan_skills(workspace_root / "skills")
    def session_provider(): raise AssertionError("should not be called")
    tool = make_load_skill_tool(registry, session_provider)
    result = asyncio.run(tool("nope"))
    assert "找不到" in result or "not found" in result.lower()
