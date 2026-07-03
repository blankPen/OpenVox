"""Load tools and MCP servers from workspace/extensions/."""
from __future__ import annotations

import importlib.util
import json
import warnings
from pathlib import Path
from typing import Any


def load_tools(tools_dir: Path) -> list[Any]:
    """Glob tools_dir/*.py, import each, call module.register() -> list.

    Files starting with `_` (incl. __init__.py) are skipped.
    Raises AttributeError if a file has no register() function.
    """
    if not tools_dir.is_dir():
        return []
    tools: list[Any] = []
    for path in sorted(tools_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"_agent_tool_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load spec for {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "register"):
            raise AttributeError(f"{path} missing register() function")
        tools.extend(module.register())
    return tools


def load_mcp_servers(mcp_dir: Path) -> list[Any]:
    """Read mcp_dir/*.json → list[StdioServerParams].

    v0.1: only stdio transport supported. Non-stdio entries are skipped
    with a warning.
    """
    if not mcp_dir.is_dir():
        return []
    from mcp.client.stdio import StdioServerParameters
    servers: list[Any] = []
    for path in sorted(mcp_dir.glob("*.json")):
        cfg = json.loads(path.read_text(encoding="utf-8"))
        if "command" not in cfg:
            raise ValueError(f"{path}: missing 'command' field")
        if cfg.get("transport", "stdio") != "stdio":
            warnings.warn(f"{path}: non-stdio transport not supported in v0.1, skipping")
            continue
        servers.append(StdioServerParameters(
            command=cfg["command"],
            args=cfg.get("args", []),
            env=cfg.get("env"),
        ))
    return servers
