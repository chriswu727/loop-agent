"""The fallback LLM client: one primary provider, the rest as a safety net.

On a retryable failure it cascades to the next configured provider and records
the handoff. If every provider fails, the last error propagates so the loop can
fail the task cleanly rather than hang.
"""

from __future__ import annotations

from dataclasses import asdict

import httpx

from app.core.config import settings
from app.core.llm.base import FallbackEvent, LLMError, LLMResult
from app.core.llm.providers import wrap_parse_error
from app.core.llm.registry import PROVIDERS, api_key_for, configured_providers, new_http_client
from app.core.logging import get_logger

log = get_logger("llm")


class FallbackLLMClient:
    """Cascading client. Construct once; reuse the pooled HTTP connection."""

    def __init__(
        self, primary: str | None = None, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self.primary = primary or settings.llm_default_provider
        self._client = client or new_http_client()

    def _chain(self) -> list[str]:
        """Configured providers, primary first, only those that have an API key."""
        return configured_providers(self.primary)

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResult:
        chain = self._chain()
        if not chain:
            raise LLMError(
                "No LLM provider configured. Set an API key (DEEPSEEK_API_KEY, "
                "ANTHROPIC_API_KEY, GEMINI_API_KEY or GLM_API_KEY), or run without a "
                "key via DEMO_MODE=1 (scripted demo) or OLLAMA_BASE_URL (local model).",
                retryable=False,
            )

        fallbacks: list[FallbackEvent] = []
        last_error: LLMError | None = None

        for index, provider in enumerate(chain):
            adapter = PROVIDERS[provider].adapter
            api_key = api_key_for(provider)
            if index > 0 and last_error is not None:
                event = FallbackEvent(
                    from_provider=chain[index - 1],
                    to_provider=provider,
                    reason=str(last_error)[:200],
                )
                fallbacks.append(event)
                log.warning("llm.fallback", **asdict(event))

            try:
                content, tokens = await adapter(
                    self._client,
                    api_key,
                    system,
                    user,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                if not content.strip():
                    raise LLMError(f"{provider} returned empty content", retryable=True)
                return LLMResult(
                    content=content, provider=provider, tokens=tokens, fallbacks=fallbacks
                )
            except LLMError as exc:
                last_error = exc
                if not exc.retryable:
                    raise
            except Exception as exc:  # unexpected shape -> normalise, maybe cascade
                last_error = wrap_parse_error(provider, exc)
                if not last_error.retryable:
                    raise last_error from exc

        assert last_error is not None
        raise last_error

    async def aclose(self) -> None:
        await self._client.aclose()


_client: FallbackLLMClient | None = None


def get_llm_client() -> FallbackLLMClient:
    """Process-wide singleton so the HTTP connection pool is reused."""
    global _client
    if _client is None:
        _client = FallbackLLMClient()
    return _client


async def aclose_llm_client() -> None:
    """Close the shared client's connection pool on shutdown (no-op if unused)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
