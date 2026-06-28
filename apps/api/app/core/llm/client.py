"""The fallback LLM client: one primary provider, the rest as a safety net.

On a retryable failure it cascades to the next configured provider and records
the handoff. If every provider fails, the last error propagates so the loop can
fail the task cleanly rather than hang.
"""

from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.llm.base import FallbackEvent, LLMError, LLMResult
from app.core.llm.providers import (
    call_deepseek,
    call_gemini,
    call_glm,
    wrap_parse_error,
)
from app.core.logging import get_logger

log = get_logger("llm")

# provider id -> (adapter, settings attribute holding its api key)
_PROVIDERS = {
    "deepseek": (call_deepseek, "deepseek_api_key"),
    "gemini": (call_gemini, "gemini_api_key"),
    "glm": (call_glm, "glm_api_key"),
}


class FallbackLLMClient:
    """Cascading client. Construct once; reuse the pooled HTTP connection."""

    def __init__(self, primary: str | None = None) -> None:
        self.primary = primary or settings.llm_default_provider
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=10.0),
        )

    def _chain(self) -> list[str]:
        """Configured providers, primary first, only those that have an API key."""
        ordered = [self.primary, *[p for p in _PROVIDERS if p != self.primary]]
        return [p for p in ordered if getattr(settings, _PROVIDERS[p][1], None)]

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
                "No LLM provider configured — set DEEPSEEK_API_KEY, GEMINI_API_KEY or GLM_API_KEY",
                retryable=False,
            )

        fallbacks: list[FallbackEvent] = []
        last_error: LLMError | None = None

        for index, provider in enumerate(chain):
            adapter, key_attr = _PROVIDERS[provider]
            api_key = getattr(settings, key_attr)
            if index > 0 and last_error is not None:
                event = FallbackEvent(
                    from_provider=chain[index - 1],
                    to_provider=provider,
                    reason=str(last_error)[:200],
                )
                fallbacks.append(event)
                log.warning("llm.fallback", **event.__dict__)

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
