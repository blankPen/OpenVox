"""JSON 配置加载器。

从 ``~/.openz/config.json`` 读取 worker / bridge 启动所需的全部配置项。
取代之前散落在 ``.env`` 里的环境变量。

设计原则：
- 嵌套结构按业务段分组（volcengine / livekit / bridge / hermes）
- 配置项读取走点路径，例如 ``cfg.require("volcengine.stt.app_id")``
- ``require`` 缺 key 时抛 ``ConfigError``，fail-fast 在 worker 启动时
- 默认路径 ``~/.openz/config.json``；测试可通过 ``OPENZ_CONFIG`` 环境变量覆盖
- 模块导入即单例缓存 ``_cfg``；测试用 ``reset_config()`` 清掉

扩展时：
- 新增业务段：在 ``~/.openz/config.json`` 顶层加 key
- 新增字段：在所属业务段加 sub-key，调用方用 ``cfg.require("new.field")``
- 缺默认值：``cfg.get("foo", default="x")``
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(
    os.environ.get("OPENZ_CONFIG", "~/.openz/config.json")
).expanduser()


def _resolve_default_path() -> Path:
    """运行时重读 OPENZ_CONFIG（让测试可以 monkeypatch）。"""
    return Path(
        os.environ.get("OPENZ_CONFIG", "~/.openz/config.json")
    ).expanduser()


class ConfigError(RuntimeError):
    """配置缺失或格式错误时抛。"""


class Config:
    """只读配置视图，支持点路径访问嵌套字段。"""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        if path is None:
            path = _resolve_default_path()
        if not path.exists():
            raise ConfigError(f"config not found: {path}")
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError(f"config parse error in {path}: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"config root must be object, got {type(data).__name__}")
        return cls(data)

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """按点路径读字段。任何一段缺失返回 default。"""
        cur: Any = self._data
        for seg in dotted_key.split("."):
            if isinstance(cur, dict) and seg in cur:
                cur = cur[seg]
            else:
                return default
        return cur

    def require(self, dotted_key: str) -> Any:
        """按点路径读字段；缺失抛 ConfigError（fail-fast）。"""
        cur: Any = self._data
        for seg in dotted_key.split("."):
            if not isinstance(cur, dict) or seg not in cur:
                raise ConfigError(f"missing required config key: {dotted_key}")
            cur = cur[seg]
        return cur

    def as_dict(self) -> dict[str, Any]:
        """返回原始 dict（用于调试 / 测试断言）。"""
        return self._data


_cfg: Config | None = None


def get_config() -> Config:
    """进程级单例：第一次调用读文件，后续直接返回缓存。"""
    global _cfg
    if _cfg is None:
        _cfg = Config.load()
    return _cfg


def reset_config() -> None:
    """清掉单例缓存（仅测试用）。下次 get_config() 会重新读盘。"""
    global _cfg
    _cfg = None


def set_config(cfg: Config) -> None:
    """注入一个 Config 对象（仅测试用）。"""
    global _cfg
    _cfg = cfg