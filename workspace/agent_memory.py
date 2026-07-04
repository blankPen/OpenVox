"""Per-user memory store backed by markdown files (read path in v0.1)."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("volcengine-agent")


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
        logger.info(
            f"[memory] MemoryStore init: root={user_root} "
            f"(User.md / MEMORY.md / memory/ ensured)"
        )

    def load_user_prompt(self) -> str:
        """Concatenate User.md and MEMORY.md (both optional) for system prompt injection."""
        parts: list[str] = []
        sizes: dict[str, int] = {}
        for name in (self._USER_FILE, self._MEMORY_FILE):
            path = self._root / name
            text = path.read_text(encoding="utf-8").strip()
            sizes[name] = len(text)
            if text:
                parts.append(text)
        result = "\n\n".join(parts)
        logger.info(
            f"[memory] load_user_prompt: User.md={sizes['User.md']}c, "
            f"MEMORY.md={sizes['MEMORY.md']}c → returned {len(result)}c "
            f"({'empty' if not result else 'injected'})"
        )
        return result
