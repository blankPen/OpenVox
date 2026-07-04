"""Load tools and MCP servers from workspace/extensions/."""
from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import warnings
from pathlib import Path
from typing import Any

logger = logging.getLogger("volcengine-agent")


def _tool_name(tool: Any) -> str:
    """Best-effort name extraction for a @function_tool-decorated function."""
    # livekit-agents' function_tool returns an object with .info.name
    if hasattr(tool, "info") and getattr(tool.info, "name", None):
        return tool.info.name
    if callable(tool):
        return getattr(tool, "__name__", repr(tool))
    return repr(tool)


def load_tools(tools_dir: Path) -> list[Any]:
    """Glob tools_dir/**​/*.py recursively, import each, call module.register() -> list.

    Files starting with `_` (incl. __init__.py) are skipped.
    __pycache__ directories are excluded.
    Raises AttributeError if a file has no register() function.
    """
    if not tools_dir.is_dir():
        logger.info(f"[tools] load_tools: dir not found {tools_dir}, returning empty")
        return []
    py_files = [
        p for p in sorted(tools_dir.glob("**/*.py"))
        if not p.name.startswith("_")
        and "__pycache__" not in p.parts
    ]
    logger.info(f"[tools] load_tools: scanning {len(py_files)} files in {tools_dir}")
    tools: list[Any] = []
    for path in py_files:
        module_name = f"_agent_tool_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load spec for {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "register"):
            raise AttributeError(f"{path} missing register() function")
        registered = module.register()
        tools.extend(registered)
        names = [_tool_name(t) for t in registered]
        logger.info(
            f"[tools]   + {path.name} → register() returned "
            f"{len(registered)} tool(s): {names}"
        )
    logger.info(f"[tools] total registered: {[ _tool_name(t) for t in tools]}")
    return tools


def load_mcp_servers(mcp_dir: Path) -> list[Any]:
    """Read mcp_dir/*.json → list[StdioServerParameters].

    v0.1: only stdio transport supported. Non-stdio entries are skipped
    with a warning.
    """
    if not mcp_dir.is_dir():
        logger.info(f"[mcp] load_mcp_servers: dir not found {mcp_dir}, returning empty")
        return []
    from mcp.client.stdio import StdioServerParameters
    json_files = sorted(mcp_dir.glob("*.json"))
    logger.info(f"[mcp] load_mcp_servers: scanning {len(json_files)} configs in {mcp_dir}")
    servers: list[Any] = []
    for path in json_files:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        if "command" not in cfg:
            raise ValueError(f"{path}: missing 'command' field")
        if cfg.get("transport", "stdio") != "stdio":
            warnings.warn(f"{path}: non-stdio transport not supported in v0.1, skipping")
            logger.warning(f"[mcp]   skipped {path.name}: non-stdio transport")
            continue
        params = StdioServerParameters(
            command=cfg["command"],
            args=cfg.get("args", []),
            env=cfg.get("env"),
        )
        servers.append(params)
        logger.info(
            f"[mcp]   + {path.name} → command={cfg['command']!r} args={cfg.get('args', [])}"
        )
    logger.info(f"[mcp] total loaded: {len(servers)} server(s)")
    return servers
