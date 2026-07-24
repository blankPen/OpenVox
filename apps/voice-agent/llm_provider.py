"""Resolve configured LLM providers and construct OpenAI-compatible clients."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from config import Config, ConfigError


class PlannedProviderError(ConfigError):
    """Raised when a known provider is planned but not yet supported."""


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    api_base: str
    api_key: str


def resolve_llm_settings(cfg: Config) -> LLMSettings:
    """Resolve the selected provider's OpenAI-compatible connection settings."""
    provider = cfg.get("llm.provider", "hermes")
    if provider in ("codex", "openclaw"):
        raise PlannedProviderError(f"llm provider {provider} is planned")
    if provider not in ("hermes", "agentd"):
        raise ConfigError("unknown llm provider")

    return LLMSettings(
        provider=provider,
        model=cfg.require(f"{provider}.model"),
        api_base=cfg.require(f"{provider}.api_base"),
        api_key=cfg.require(f"{provider}.api_key"),
    )


def build_llm(cfg: Config, llm_constructor: Callable[..., Any]) -> Any:
    """Construct an LLM client from the selected provider settings."""
    settings = resolve_llm_settings(cfg)
    return llm_constructor(
        model=settings.model,
        base_url=settings.api_base,
        api_key=settings.api_key,
    )
