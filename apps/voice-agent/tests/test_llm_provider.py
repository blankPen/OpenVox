from unittest.mock import Mock

import pytest

from config import Config, ConfigError
from llm_provider import PlannedProviderError, build_llm, resolve_llm_settings


def test_missing_selector_keeps_hermes_compatibility():
    cfg = Config({
        "hermes": {
            "model": "h",
            "api_base": "http://h/v1",
            "api_key": "hk",
        }
    })

    assert resolve_llm_settings(cfg).provider == "hermes"


def test_agentd_maps_its_own_endpoint():
    cfg = Config({
        "llm": {"provider": "agentd"},
        "agentd": {
            "model": "agentd/claude",
            "api_base": "http://127.0.0.1:8787/v1",
            "api_key": "ak",
        },
    })
    constructor = Mock(return_value="llm")

    assert build_llm(cfg, constructor) == "llm"
    constructor.assert_called_once_with(
        model="agentd/claude",
        base_url="http://127.0.0.1:8787/v1",
        api_key="ak",
    )


def test_planned_provider_is_rejected():
    cfg = Config({"llm": {"provider": "codex"}})

    with pytest.raises(PlannedProviderError, match="planned"):
        resolve_llm_settings(cfg)


def test_unknown_provider_error_does_not_echo_value():
    cfg = Config({"llm": {"provider": "private-provider-value"}})

    with pytest.raises(ConfigError, match="unknown llm provider") as exc_info:
        resolve_llm_settings(cfg)

    assert "private-provider-value" not in str(exc_info.value)
