"""The provider registry, adapters, and fallback cascade — all offline via a
mock HTTP transport (no real API calls, no keys needed)."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from app.core.llm.base import LLMError
from app.core.llm.client import FallbackLLMClient
from app.core.llm.providers import call_anthropic, call_deepseek, call_ollama
from app.core.llm.registry import PROVIDERS, configured_providers


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_registry_lists_all_providers() -> None:
    assert set(PROVIDERS) == {"anthropic", "deepseek", "gemini", "glm", "ollama", "mock"}


async def test_ollama_adapter_hits_local_openai_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ollama_model", "llama3.2")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "local hi"}}], "usage": {"total_tokens": 5}},
        )

    content, tokens = await call_ollama(
        _client(handler), "http://localhost:11434", "s", "u", max_tokens=10, temperature=0.5
    )
    assert content == "local hi" and tokens == 5
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"
    assert seen["auth"] is None  # no API key for a local model


def test_ollama_configured_only_when_base_url_set(monkeypatch: pytest.MonkeyPatch) -> None:
    for attr in ("anthropic_api_key", "deepseek_api_key", "gemini_api_key", "glm_api_key"):
        monkeypatch.setattr(settings, attr, None)
    monkeypatch.setattr(settings, "ollama_base_url", None)
    assert "ollama" not in configured_providers("ollama")
    monkeypatch.setattr(settings, "ollama_base_url", "http://localhost:11434")
    assert configured_providers("ollama") == ["ollama"]


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


async def test_deepseek_falls_back_to_reasoning_content_when_content_empty() -> None:
    # deepseek-reasoner (R1) intermittently returns empty `content` with the answer
    # left in `reasoning_content`. The adapter must use that rather than report empty
    # (which would exhaust retries and fail an otherwise-fine run).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "", "reasoning_content": '{"tool": "finish"}'}}
                ],
                "usage": {"total_tokens": 5},
            },
        )

    content, _ = await call_deepseek(
        _client(handler), "k", "s", "u", max_tokens=10, temperature=0.2
    )
    assert content == '{"tool": "finish"}'  # recovered from the reasoning field


async def test_fallback_cascades_to_next_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", "a")
    monkeypatch.setattr(settings, "deepseek_api_key", "d")
    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.setattr(settings, "glm_api_key", None)
    monkeypatch.setattr(settings, "llm_max_retries", 0)  # test pure cascade, not retry

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.anthropic.com":
            return httpx.Response(503, text="overloaded")  # retryable -> cascade
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "from deepseek"}}],
                "usage": {"total_tokens": 3},
            },
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
    with pytest.raises(LLMError) as err:
        await FallbackLLMClient(client=_client(lambda r: httpx.Response(200))).complete("s", "u")
    # The message points to the zero-key escape hatches, not just API keys.
    assert "DEMO_MODE" in str(err.value) and "OLLAMA_BASE_URL" in str(err.value)


async def test_retries_same_provider_on_transient_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A transient 5xx should be retried on the same provider, not fail the task.
    monkeypatch.setattr(settings, "deepseek_api_key", "d")
    for attr in ("anthropic_api_key", "gemini_api_key", "glm_api_key"):
        monkeypatch.setattr(settings, attr, None)
    monkeypatch.setattr(settings, "llm_retry_backoff_seconds", 0.0)  # no sleep in test
    monkeypatch.setattr(settings, "llm_max_retries", 2)

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="overloaded")  # transient, retryable
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 2}},
        )

    llm = FallbackLLMClient(primary="deepseek", client=_client(handler))
    result = await llm.complete("s", "u")
    assert result.content == "ok"
    assert calls["n"] == 3  # two retries, then success
    assert result.fallbacks == []  # same provider, no cascade


async def test_default_retry_budget_rides_out_a_sustained_blip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A reasoning model can be overloaded for several seconds; the DEFAULT budget must
    # ride that out rather than discarding a partially-complete run. Four transient
    # failures in a row then success -> the task should still get its completion.
    monkeypatch.setattr(settings, "deepseek_api_key", "d")
    for attr in ("anthropic_api_key", "gemini_api_key", "glm_api_key"):
        monkeypatch.setattr(settings, attr, None)
    monkeypatch.setattr(settings, "llm_retry_backoff_seconds", 0.0)  # no real sleep in test
    # NOTE: no llm_max_retries override — this asserts the shipped default is resilient.

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 5:  # four transient failures before recovery
            return httpx.Response(503, text="overloaded")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 2}},
        )

    llm = FallbackLLMClient(primary="deepseek", client=_client(handler))
    result = await llm.complete("s", "u")
    assert result.content == "ok"  # survived; the default budget covers 4 retries
    assert calls["n"] == 5
