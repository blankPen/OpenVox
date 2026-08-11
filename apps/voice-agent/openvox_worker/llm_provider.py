"""Resolve configured LLM providers and construct OpenAI-compatible clients."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .config import Config, ConfigError


class PlannedProviderError(ConfigError):
    """Raised when a known provider is planned but not yet supported."""


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    api_base: str
    api_key: str


def resolve_llm_settings(cfg: Config) -> LLMSettings:
    """Resolve the selected provider's OpenAI-compatible connection settings.

    ``claude`` is a CLI-level alias for the ``agentd`` backend (the agentd
    daemon routes ``agentd/claude`` models through the Claude Code CLI).
    We resolve the alias here so a config that simply writes
    ``llm.provider=claude`` works without going through cli.py's
    ``PROVIDER_ALIASES`` mapping first.
    """
    provider = cfg.get("llm.provider", "claude")
    if provider in ("codex", "openclaw"):
        raise PlannedProviderError(f"llm provider {provider} is planned")
    # CLI-level aliases (claude, codex, openclaw → agentd). Both cli.py and
    # this module share the alias set so the same config string means the
    # same thing in either entry point.
    if provider == "claude":
        provider = "agentd"
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
        api_key=settings.api_key or "sk-placeholder",
    )
