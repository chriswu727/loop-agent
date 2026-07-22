"""The fallback LLM client: one primary provider, the rest as a safety net.

On a retryable failure it cascades to the next configured provider and records
the handoff. If every provider fails, the last error propagates so the loop can
fail the task cleanly rather than hang.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict

import httpx

from app.core.config import settings
from app.core.llm.base import FallbackEvent, LLMError, LLMResult
from app.core.llm.providers import provider_model, wrap_parse_error
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
        token_budget: int | None = None,
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
        spent = 0
        input_bound = _token_estimate(system, user)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.llm_total_timeout_seconds

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

            # Retry this provider on a transient error before cascading, so one
            # blip (timeout/5xx/empty) doesn't fail the task on a single-provider setup.
            for attempt in range(settings.llm_max_retries + 1):
                remaining_seconds = deadline - loop.time()
                if remaining_seconds <= 0:
                    raise _total_deadline_error(spent)
                remaining = None if token_budget is None else token_budget - spent
                if remaining is not None and remaining <= input_bound:
                    raise LLMError(
                        "LLM call token budget exhausted before another attempt",
                        tokens_spent=spent,
                        budget_exhausted=True,
                    )
                attempt_max = max_tokens
                if remaining is not None:
                    attempt_max = min(max_tokens, remaining - input_bound)
                try:
                    async with asyncio.timeout(remaining_seconds):
                        content, tokens = await adapter(
                            self._client,
                            api_key,
                            system,
                            user,
                            max_tokens=attempt_max,
                            temperature=temperature,
                        )
                    if not content.strip():
                        raise LLMError(f"{provider} returned empty content", retryable=True)
                    if tokens:
                        success_tokens = tokens
                    else:
                        estimated = _token_estimate(system, user, content)
                        success_tokens = (
                            estimated if remaining is None else min(estimated, remaining)
                        )
                    return LLMResult(
                        content=content,
                        provider=provider,
                        tokens=spent + success_tokens,
                        fallbacks=fallbacks,
                        model=provider_model(provider),
                    )
                except TimeoutError:
                    spent += input_bound + attempt_max
                    raise _total_deadline_error(spent) from None
                except LLMError as exc:
                    last_error = exc
                except Exception as exc:  # unexpected shape -> normalise, maybe cascade
                    last_error = wrap_parse_error(provider, exc)
                spent += _failed_attempt_charge(last_error, input_bound, attempt_max)
                if not last_error.retryable:
                    last_error.tokens_spent += spent
                    raise last_error
                if token_budget is not None and token_budget - spent <= input_bound:
                    raise LLMError(
                        "LLM retry budget exhausted",
                        tokens_spent=min(spent, token_budget),
                        budget_exhausted=True,
                    ) from last_error
                if attempt < settings.llm_max_retries:
                    log.info("llm.retry", provider=provider, attempt=attempt + 1)
                    backoff = settings.llm_retry_backoff_seconds * (attempt + 1)
                    remaining_seconds = deadline - loop.time()
                    if remaining_seconds <= 0:
                        raise _total_deadline_error(spent)
                    await asyncio.sleep(min(backoff, remaining_seconds))

        assert last_error is not None
        last_error.tokens_spent += spent
        raise last_error

    async def aclose(self) -> None:
        await self._client.aclose()


_client: FallbackLLMClient | None = None
_verifier_client: FallbackLLMClient | None = None


def _token_estimate(*parts: str) -> int:
    total = 32
    for part in parts:
        ascii_chars = sum(character.isascii() for character in part)
        non_ascii_chars = len(part) - ascii_chars
        total += 16 + (ascii_chars + 2) // 3 + non_ascii_chars * 2
    return total


def _failed_attempt_charge(error: LLMError, input_bound: int, output_bound: int) -> int:
    message = str(error).lower()
    output_may_have_been_generated = any(
        marker in message
        for marker in ("connection", "empty content", "network", "parse", "timeout", "timed out")
    )
    return input_bound + (output_bound if output_may_have_been_generated else 0)


def _total_deadline_error(tokens_spent: int) -> LLMError:
    return LLMError(
        "LLM call exceeded its total timeout across retries and provider fallbacks",
        retryable=True,
        tokens_spent=tokens_spent,
    )


def get_llm_client() -> FallbackLLMClient:
    """Process-wide singleton so the HTTP connection pool is reused."""
    global _client
    if _client is None:
        _client = FallbackLLMClient()
    return _client


def get_verifier_client() -> FallbackLLMClient:
    global _verifier_client
    if _verifier_client is None:
        _verifier_client = FallbackLLMClient(
            primary=settings.llm_verifier_provider or settings.llm_default_provider
        )
    return _verifier_client


async def aclose_llm_client() -> None:
    """Close the shared client's connection pool on shutdown (no-op if unused)."""
    global _client, _verifier_client
    if _client is not None:
        await _client.aclose()
        _client = None
    if _verifier_client is not None:
        await _verifier_client.aclose()
        _verifier_client = None
