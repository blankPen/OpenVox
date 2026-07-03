"""Scan workspace/skills/ for SKILL.md files and build a load_skill() tool."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class SkillDef:
    name: str
    description: str
    body: str
    scripts_dir: Path | None


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _parse_skill_md(path: Path) -> SkillDef:
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"{path}: missing YAML frontmatter (---...---)")
    fm, body = m.group(1), m.group(2)
    name: str | None = None
    description: str | None = None
    for line in fm.splitlines():
        line = line.strip()
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("description:"):
            description = line.split(":", 1)[1].strip()
    if not name:
        raise ValueError(f"{path}: frontmatter missing 'name'")
    if not description:
        raise ValueError(f"{path}: frontmatter missing 'description'")
    scripts_dir = path.parent / "scripts"
    return SkillDef(name=name, description=description, body=body.strip(),
                    scripts_dir=scripts_dir if scripts_dir.is_dir() else None)


def scan_skills(skills_root: Path) -> dict[str, SkillDef]:
    """Glob skills_root/*/SKILL.md and return {name: SkillDef}.

    Raises ValueError on duplicate name or malformed SKILL.md.
    """
    registry: dict[str, SkillDef] = {}
    if not skills_root.is_dir():
        return registry
    for skill_md in sorted(skills_root.glob("*/SKILL.md")):
        skill = _parse_skill_md(skill_md)
        if skill.name in registry:
            raise ValueError(f"duplicate skill name: {skill.name!r}")
        registry[skill.name] = skill
    return registry


def make_load_skill_tool(
    registry: dict[str, SkillDef],
    session_provider: Callable[[], object],
) -> Callable:
    """Return an async function load_skill(name: str) -> str suitable as a tool.

    When called, injects the skill's body into the session's chat context
    as a system message.
    """
    async def load_skill(name: str) -> str:
        skill = registry.get(name)
        if skill is None:
            available = ", ".join(sorted(registry)) or "(无)"
            return f"找不到 skill {name!r}，可用：{available}"
        session = session_provider()
        session.update_chat_ctx(messages=[{"role": "system", "content": skill.body}])
        return f"已加载 skill {name!r}，可使用其指引。"

    load_skill.__name__ = "load_skill"
    load_skill.__doc__ = (
        "加载名为 <name> 的 skill。加载后该 skill 的指引会注入对话上下文。"
        "可用 skill 名见当前 tool 列表。"
    )
    return load_skill
