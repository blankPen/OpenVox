"""Shared sensitive-path detection for fs tools (v0.1 demo: WARNING log only).

Spec reference: docs/superpowers/specs/2026-07-05-agent-filesystem-tools-design.md §5.3
"""
from __future__ import annotations

import re
from pathlib import Path

SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^/etc(/|$)"),
    re.compile(r"^/usr(/|$)"),
    re.compile(r"^/var(/|$)"),
    re.compile(r"^/private/etc(/|$)"),  # macOS
    re.compile(rf"^{re.escape(str(Path.home()))}/\.ssh(/|$)"),
    re.compile(rf"^{re.escape(str(Path.home()))}/\.aws(/|$)"),
]


def is_sensitive(path: str) -> bool:
    """绝对路径命中任意敏感模式 → True。

    Args:
        path: 待检测路径（绝对路径或相对路径，相对路径视为不敏感）。

    Returns:
        bool: 是否命中敏感模式。
    """
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = p.resolve()
    s = str(p)
    return any(pat.match(s) for pat in SENSITIVE_PATTERNS)