"""Per-user memory store backed by markdown files (read path in v0.1)."""
from __future__ import annotations

from pathlib import Path


class MemoryStore:
    """Read/write per-user memory under <user_root>/{User.md, MEMORY.md, memory/}.

    v0.1 only implements the read path. Write methods land in v0.2.
    """

    _USER_FILE = "User.md"
    _MEMORY_FILE = "MEMORY.md"
    _DAILY_DIR = "memory"

    def __init__(self, user_root: Path):
        self._root = user_root
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / self._USER_FILE).touch(exist_ok=True)
        (self._root / self._MEMORY_FILE).touch(exist_ok=True)
        (self._root / self._DAILY_DIR).mkdir(exist_ok=True)

    def load_user_prompt(self) -> str:
        """Concatenate User.md and MEMORY.md (both optional) for system prompt injection."""
        parts: list[str] = []
        for name in (self._USER_FILE, self._MEMORY_FILE):
            text = (self._root / name).read_text(encoding="utf-8").strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts)
