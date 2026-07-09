"""Unit tests for config.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from config import Config, ConfigError, get_config, reset_config, set_config


# ───────── 单元测试：Config 自身 ─────────


def test_get_simple_key():
    cfg = Config({"foo": "bar"})
    assert cfg.get("foo") == "bar"


def test_get_nested_key():
    cfg = Config({"a": {"b": {"c": 42}}})
    assert cfg.get("a.b.c") == 42


def test_get_returns_default_for_missing_key():
    cfg = Config({"foo": "bar"})
    assert cfg.get("missing", "fallback") == "fallback"
    assert cfg.get("missing") is None


def test_get_returns_default_for_partial_nested_missing():
    """路径中途缺失应返回 default，而不是抛 KeyError。"""
    cfg = Config({"a": {"b": 1}})
    assert cfg.get("a.b.c.d", "x") == "x"
    assert cfg.get("a.x.y", "x") == "x"


def test_require_raises_for_missing_key():
    cfg = Config({"foo": "bar"})
    with pytest.raises(ConfigError, match=r"missing required config key: missing"):
        cfg.require("missing")


def test_require_raises_for_partial_nested_missing():
    cfg = Config({"a": {"b": 1}})
    with pytest.raises(ConfigError, match=r"missing required config key: a.b.c"):
        cfg.require("a.b.c")


def test_require_returns_value_for_present_key():
    cfg = Config({"bridge": {"model": "hermes-agent"}})
    assert cfg.require("bridge.model") == "hermes-agent"


def test_load_from_file(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"foo": {"bar": 42}}), encoding="utf-8")
    cfg = Config.load(cfg_path)
    assert cfg.require("foo.bar") == 42


def test_load_raises_for_missing_file():
    missing = Path("/nonexistent/path/config.json")
    with pytest.raises(ConfigError, match=r"config not found"):
        Config.load(missing)


def test_load_raises_for_bad_json(tmp_path):
    bad = tmp_path / "config.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"config parse error"):
        Config.load(bad)


def test_load_raises_for_non_object_root(tmp_path):
    bad = tmp_path / "config.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"config root must be object"):
        Config.load(bad)


def test_as_dict_returns_underlying_data():
    data = {"foo": {"bar": 1}}
    cfg = Config(data)
    assert cfg.as_dict() == data


# ───────── 单例：get_config / set_config / reset_config ─────────


def test_set_and_get_config(monkeypatch):
    """set_config 注入一个 Config 后，get_config 应返回它（不走磁盘）。"""
    # 清掉任何缓存
    reset_config()
    fake = Config({"k": "v"})
    set_config(fake)
    try:
        assert get_config() is fake
        assert get_config().require("k") == "v"
    finally:
        reset_config()


def test_reset_config_clears_cache(monkeypatch):
    """reset_config 后 get_config 会重新走 Config.load（默认路径）。"""
    fake = Config({"k": "v"})
    set_config(fake)
    assert get_config() is fake
    reset_config()
    # 缓存清掉后，get_config() 会读默认 ~/.openvox/config.json。
    # 用 monkeypatch 改 OPENVOX_CONFIG 让它读一个临时文件，确认 reset 生效。
    tmp = Path("/tmp/_openvox_test_config.json")
    tmp.write_text(json.dumps({"k": "from-tmp"}), encoding="utf-8")
    monkeypatch.setenv("OPENVOX_CONFIG", str(tmp))
    try:
        cfg = get_config()
        assert cfg.require("k") == "from-tmp"
    finally:
        tmp.unlink(missing_ok=True)
        reset_config()


def test_default_path_is_openvox_config(monkeypatch):
    """没设 OPENVOX_CONFIG 时默认读 ~/.openvox/config.json。"""
    monkeypatch.delenv("OPENVOX_CONFIG", raising=False)
    import config as cfg_module
    expected = Path("~/.openvox/config.json").expanduser()
    assert cfg_module.CONFIG_PATH == expected