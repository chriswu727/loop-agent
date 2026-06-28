"""Multi-provider LLM client with automatic fallback.

A small port of the provider-cascade pattern: try a primary model, and on a
*retryable* failure (timeout, rate limit, 5xx, network) cascade to the next
provider instead of failing the whole loop. Every call reports the tokens it
consumed so the agent loop can enforce a hard budget.

The agent loop depends only on the :class:`LLMClient` protocol, so tests inject
a deterministic fake and never touch the network.
"""

from __future__ import annotations

from app.core.llm.base import LLMClient, LLMError, LLMResult, Message
from app.core.llm.client import FallbackLLMClient, get_llm_client

__all__ = [
    "FallbackLLMClient",
    "LLMClient",
    "LLMError",
    "LLMResult",
    "Message",
    "get_llm_client",
]
