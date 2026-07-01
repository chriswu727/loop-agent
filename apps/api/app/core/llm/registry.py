"""The provider registry — add a model backend by adding one entry.

Each provider maps a name to its adapter and the settings attribute holding its
API key. The fallback client iterates this registry, so a new provider (OpenAI,
a local Ollama/vLLM endpoint, …) is a one-line registration plus an adapter in
``providers.py`` — no change to the agent or the cascade logic.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.llm.providers import (
    call_anthropic,
    call_deepseek,
    call_gemini,
    call_glm,
    call_mock,
    call_ollama,
)

# An adapter: (http_client, api_key, system, user, *, max_tokens, temperature) -> (content, tokens)
Adapter = Callable[..., Awaitable[tuple[str, int]]]


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    adapter: Adapter
    key_attr: str  # settings attribute holding this provider's API key


PROVIDERS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(call_anthropic, "anthropic_api_key"),
    "deepseek": ProviderSpec(call_deepseek, "deepseek_api_key"),
    "gemini": ProviderSpec(call_gemini, "gemini_api_key"),
    "glm": ProviderSpec(call_glm, "glm_api_key"),
    # Local model: "key" is the base URL, so it's configured when that's set.
    "ollama": ProviderSpec(call_ollama, "ollama_base_url"),
    # Zero-key demo: configured exactly when DEMO_MODE is on.
    "mock": ProviderSpec(call_mock, "demo_mode"),
}


def configured_providers(primary: str) -> list[str]:
    """Providers that have an API key, primary first."""
    ordered = [primary, *[p for p in PROVIDERS if p != primary]]
    return [p for p in ordered if p in PROVIDERS and getattr(settings, PROVIDERS[p].key_attr, None)]


def api_key_for(provider: str) -> str | None:
    return getattr(settings, PROVIDERS[provider].key_attr, None)


def new_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=10.0))
