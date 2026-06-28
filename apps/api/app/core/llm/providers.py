"""Concrete provider adapters.

Each adapter takes a (system, user) prompt and returns ``(content, tokens)`` or
raises :class:`LLMError`. Differences between the three vendors' wire formats are
contained entirely here; everything above sees one uniform shape.
"""

from __future__ import annotations

import httpx

from app.core.llm.base import LLMError, is_retryable_message

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GLM_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

DEEPSEEK_MODEL = "deepseek-chat"
GEMINI_MODEL = "gemini-2.0-flash"
GLM_MODEL = "glm-4-flash"


def _raise_for_status(resp: httpx.Response, provider: str) -> None:
    if resp.status_code >= 400:
        body = resp.text[:500]
        retryable = resp.status_code == 429 or resp.status_code >= 500
        raise LLMError(f"{provider} HTTP {resp.status_code}: {body}", retryable=retryable)


async def call_deepseek(
    client: httpx.AsyncClient,
    api_key: str,
    system: str,
    user: str,
    *,
    max_tokens: int,
    temperature: float,
) -> tuple[str, int]:
    try:
        resp = await client.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )
    except httpx.HTTPError as exc:  # network/timeout
        raise LLMError(f"deepseek request failed: {exc}", retryable=True) from exc

    _raise_for_status(resp, "deepseek")
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    tokens = int(data.get("usage", {}).get("total_tokens", 0))
    return content, tokens


async def call_gemini(
    client: httpx.AsyncClient,
    api_key: str,
    system: str,
    user: str,
    *,
    max_tokens: int,
    temperature: float,
) -> tuple[str, int]:
    try:
        resp = await client.post(
            GEMINI_URL.format(model=GEMINI_MODEL),
            params={"key": api_key},
            json={
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "temperature": temperature,
                },
            },
        )
    except httpx.HTTPError as exc:
        raise LLMError(f"gemini request failed: {exc}", retryable=True) from exc

    _raise_for_status(resp, "gemini")
    data = resp.json()
    candidates = data.get("candidates", [])
    if not candidates:
        # Safety blocks / empty completions are not worth retrying on another model.
        raise LLMError(f"gemini returned no candidates: {str(data)[:300]}", retryable=False)
    parts = candidates[0].get("content", {}).get("parts", [])
    content = "".join(p.get("text", "") for p in parts)
    tokens = int(data.get("usageMetadata", {}).get("totalTokenCount", 0))
    return content, tokens


async def call_glm(
    client: httpx.AsyncClient,
    api_key: str,
    system: str,
    user: str,
    *,
    max_tokens: int,
    temperature: float,
) -> tuple[str, int]:
    try:
        resp = await client.post(
            GLM_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": GLM_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )
    except httpx.HTTPError as exc:
        raise LLMError(f"glm request failed: {exc}", retryable=True) from exc

    _raise_for_status(resp, "glm")
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    tokens = int(data.get("usage", {}).get("total_tokens", 0))
    return content, tokens


def wrap_parse_error(provider: str, exc: Exception) -> LLMError:
    """Normalise an unexpected response shape into a (non-retryable) LLMError."""
    message = f"{provider} response parse error: {exc}"
    return LLMError(message, retryable=is_retryable_message(str(exc)))
