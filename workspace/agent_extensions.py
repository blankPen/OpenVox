"""Load tools and MCP servers from workspace/extensions/."""
from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import os
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
    """Read mcp_dir/*.json → list[MCPServer].

    支持两种 schema（兼容处理）：

    v0.1 stdio: 文件顶层直接是 stdio 配置
        {"command": "...", "args": [...], "env": {...}}

    v0.2 streamableHttp: 文件顶层是 {<server_name>: <config>}
        {"WebSearch": {"type": "streamableHttp", "baseUrl": "...", "headers": {...}}}

    对 v0.2: 跳过 isActive=false；headers 里的 ${VAR} 会从 os.environ 展开。
    返回 livekit.agents.mcp 的实例（MCPServerHTTP 或 MCPServerStdio）。
    """
    if not mcp_dir.is_dir():
        logger.info(f"[mcp] load_mcp_servers: dir not found {mcp_dir}, returning empty")
        return []
    json_files = sorted(mcp_dir.glob("*.json"))
    logger.info(f"[mcp] load_mcp_servers: scanning {len(json_files)} configs in {mcp_dir}")

    # 延后 import，避免 mcp 缺失时阻塞 tools 加载
    try:
        from livekit.agents.llm.mcp import MCPServerHTTP, MCPServerStdio
    except ImportError as e:
        logger.warning(f"[mcp] mcp SDK not available: {e}; skipping all")
        return []

    def _expand_env(value: Any) -> Any:
        if isinstance(value, str):
            return os.path.expandvars(value)
        if isinstance(value, dict):
            return {k: _expand_env(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_expand_env(v) for v in value]
        return value

    servers: list[Any] = []
    for path in json_files:
        cfg = json.loads(path.read_text(encoding="utf-8"))

        # v0.1 stdio schema: 顶层是 dict 且有 command 字段
        if isinstance(cfg, dict) and "command" in cfg:
            servers.append(MCPServerStdio(
                command=cfg["command"],
                args=cfg.get("args", []),
                env=_expand_env(cfg.get("env")),
            ))
            logger.info(
                f"[mcp]   + {path.name} (stdio) command={cfg['command']!r}"
            )
            continue

        # v0.2 streamableHttp schema: 顶层是 {<name>: <config>}
        if not isinstance(cfg, dict):
            raise ValueError(f"{path}: top-level must be dict (stdio or streamableHttp schema)")
        for server_name, server_cfg in cfg.items():
            if not isinstance(server_cfg, dict):
                raise ValueError(f"{path}/{server_name}: config must be dict")
            if not server_cfg.get("isActive", True):
                logger.info(f"[mcp]   skipped {path.name}/{server_name} (isActive=false)")
                continue
            transport_type = server_cfg.get("type", "streamableHttp")
            if transport_type not in ("streamableHttp", "streamable_http"):
                logger.warning(
                    f"[mcp]   skipped {path.name}/{server_name}: unsupported transport={transport_type}"
                )
                continue
            headers = _expand_env(server_cfg.get("headers", {}))
            url = _expand_env(server_cfg["baseUrl"])
            srv = MCPServerHTTP(
                url=url,
                transport_type="streamable_http",
                headers=headers,
                timeout=server_cfg.get("timeout", 30.0),
            )
            servers.append(srv)
            logger.info(
                f"[mcp]   + {path.name}/{server_name} (streamableHttp) "
                f"url={url!r} headers_keys={list(headers.keys())}"
            )

    logger.info(f"[mcp] total loaded: {len(servers)} server(s)")
    return servers
