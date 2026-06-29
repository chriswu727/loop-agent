"""The provider registry, adapters, and fallback cascade — all offline via a
mock HTTP transport (no real API calls, no keys needed)."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from app.core.llm.base import LLMError
from app.core.llm.client import FallbackLLMClient
from app.core.llm.providers import call_anthropic, call_deepseek
from app.core.llm.registry import PROVIDERS


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_registry_lists_all_providers() -> None:
    assert set(PROVIDERS) == {"anthropic", "deepseek", "gemini", "glm"}


async def test_anthropic_adapter_parses_and_signs() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-api-key")
        seen["version"] = request.headers.get("anthropic-version")
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "hi from claude"}],
                "usage": {"input_tokens": 12, "output_tokens": 8},
            },
        )

    content, tokens = await call_anthropic(
        _client(handler), "sk-ant", "be helpful", "say hi", max_tokens=64, temperature=0.3
    )
    assert content == "hi from claude"
    assert tokens == 20  # input + output
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["key"] == "sk-ant"
    assert seen["version"] == "2023-06-01"


async def test_deepseek_adapter_parses() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "hi"}}], "usage": {"total_tokens": 7}},
        )

    content, tokens = await call_deepseek(
        _client(handler), "k", "s", "u", max_tokens=10, temperature=0.5
    )
    assert content == "hi" and tokens == 7


async def test_fallback_cascades_to_next_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", "a")
    monkeypatch.setattr(settings, "deepseek_api_key", "d")
    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.setattr(settings, "glm_api_key", None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.anthropic.com":
            return httpx.Response(503, text="overloaded")  # retryable -> cascade
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "from deepseek"}}],
                  "usage": {"total_tokens": 3}},
        )

    llm = FallbackLLMClient(primary="anthropic", client=_client(handler))
    result = await llm.complete("s", "u")
    assert result.provider == "deepseek"
    assert result.content == "from deepseek"
    assert len(result.fallbacks) == 1
    assert result.fallbacks[0].from_provider == "anthropic"


async def test_no_provider_configured_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    for attr in ("anthropic_api_key", "deepseek_api_key", "gemini_api_key", "glm_api_key"):
        monkeypatch.setattr(settings, attr, None)
    with pytest.raises(LLMError):
        await FallbackLLMClient(client=_client(lambda r: httpx.Response(200))).complete("s", "u")
