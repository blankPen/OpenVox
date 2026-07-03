"""Sanity test: workspace/ directory structure exists."""
from __future__ import annotations

from pathlib import Path


def test_workspace_directories_exist():
    root = Path(__file__).parent.parent / "workspace"
    for sub in ["persona", "skills", "extensions/tools", "extensions/mcp", "users", "sandbox"]:
        assert (root / sub).is_dir(), f"missing workspace/{sub}"


def test_persona_files_exist():
    root = Path(__file__).parent.parent / "workspace" / "persona"
    for f in ["SOUL.md", "AGENTS.md", "TOOLS.md"]:
        assert (root / f).is_file(), f"missing persona/{f}"
