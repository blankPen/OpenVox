"""Read agent persona (SOUL/AGENTS/TOOLS) from workspace/persona/."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Persona:
    soul: str
    agents: str
    tools_guide: str
    combined: str


def load_persona(workspace_root: Path) -> Persona:
    """Read workspace/persona/{SOUL,AGENTS,TOOLS}.md and return a Persona.

    Raises FileNotFoundError if any of the three files is missing.
    """
    persona_dir = workspace_root / "persona"
    soul = (persona_dir / "SOUL.md").read_text(encoding="utf-8")
    agents = (persona_dir / "AGENTS.md").read_text(encoding="utf-8")
    tools_guide = (persona_dir / "TOOLS.md").read_text(encoding="utf-8")
    combined = "\n\n".join([soul, agents, tools_guide])
    return Persona(soul=soul, agents=agents, tools_guide=tools_guide, combined=combined)
