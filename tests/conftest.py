"""Shared pytest fixtures for the agent extensibility test suite."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
WORKSPACE_ROOT = PROJECT_ROOT / "workspace"


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    """Provide a fresh workspace root, pre-populated from the project workspace/.

    Tests can write anything under tmp_path without polluting the real directory.
    """
    if WORKSPACE_ROOT.exists():
        shutil.copytree(WORKSPACE_ROOT, tmp_path, dirs_exist_ok=True)
    else:
        tmp_path.mkdir()
    return tmp_path


@pytest.fixture(autouse=True)
def _inject_workspace_path():
    """Auto-inject workspace/ into sys.path so agent_* modules are importable."""
    if str(WORKSPACE_ROOT) not in sys.path:
        sys.path.insert(0, str(WORKSPACE_ROOT))
    yield
