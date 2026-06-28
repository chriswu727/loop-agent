"""LLM client contract and shared types.

Kept free of any concrete provider or HTTP detail so the rest of the app can
depend on the *interface* and swap implementations (real cascade, fake, a single
provider) without changing a line of business code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class LLMError(Exception):
    """Raised when a model call fails. ``retryable`` drives fallback cascade."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(slots=True)
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(slots=True)
class FallbackEvent:
    """Recorded when one provider hands off to the next, for observability."""

    from_provider: str
    to_provider: str
    reason: str


@dataclass(slots=True)
class LLMResult:
    content: str
    provider: str
    tokens: int
    fallbacks: list[FallbackEvent] = field(default_factory=list)


# A small set of substrings that mark an error as worth retrying on another
# provider rather than aborting the whole run.
_RETRYABLE_MARKERS = (
    "timeout",
    "timed out",
    "etimedout",
    "econnreset",
    "429",
    "500",
    "502",
    "503",
    "504",
    "rate limit",
    "overloaded",
    "service unavailable",
    "internal error",
    "internal server",
    "network",
    "connection",
)


def is_retryable_message(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _RETRYABLE_MARKERS)


class LLMClient(Protocol):
    """The single capability the agent loop needs from any model backend."""

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResult: ...
