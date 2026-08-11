from unittest.mock import Mock

import pytest

from openvox_worker.config import Config, ConfigError
from openvox_worker.llm_provider import PlannedProviderError, build_llm, resolve_llm_settings


def test_missing_selector_defaults_to_claude_agentd_backend():
    """Without llm.provider the API now defaults to ``claude``, which is a CLI
    alias for the ``agentd`` backend. The provider name reported back is the
    resolved backend so callers don't need to know about the alias layer.
    """
    cfg = Config({
        "agentd": {
            "model": "agentd/claude",
            "api_base": "http://127.0.0.1:8787/v1",
            "api_key": "ak",
        }
    })

    assert resolve_llm_settings(cfg).provider == "agentd"


def test_explicit_claude_provider_resolves_to_agentd():
    """``llm.provider=claude`` is the new default; resolve_llm_settings should
    transparently route it to the agentd backend without going through cli.py's
    PROVIDER_ALIASES mapping.
    """
    cfg = Config({
        "llm": {"provider": "claude"},
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


def test_hermes_can_still_be_selected_explicitly():
    """Existing hermes setups keep working when the operator pins the provider
    explicitly. Compat regression for users whose config.json still has
    ``llm.provider=hermes``.
    """
    cfg = Config({
        "llm": {"provider": "hermes"},
        "hermes": {
            "model": "hermes-agent",
            "api_base": "http://h/v1",
            "api_key": "hk",
        },
    })
    constructor = Mock(return_value="llm")

    assert build_llm(cfg, constructor) == "llm"
    constructor.assert_called_once_with(
        model="hermes-agent",
        base_url="http://h/v1",
        api_key="hk",
    )


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
