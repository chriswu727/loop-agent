"""Concrete provider adapters.

Each adapter takes a (system, user) prompt and returns ``(content, tokens)`` or
raises :class:`LLMError`. Differences between the three vendors' wire formats are
contained entirely here; everything above sees one uniform shape.
"""

from __future__ import annotations

import json

import httpx

from app.core.config import settings
from app.core.llm.base import LLMError, is_retryable_message

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GLM_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

GEMINI_MODEL = "gemini-2.5-flash"
GLM_MODEL = "glm-4-flash"
ANTHROPIC_MODEL = "claude-sonnet-4-6"


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
                "model": settings.deepseek_model,
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
    message = data["choices"][0]["message"]
    content = message.get("content") or ""
    if not content.strip():
        # deepseek-reasoner (R1) intermittently returns empty `content` with its whole
        # answer left in the chain-of-thought field. Fall back to it rather than treat
        # the call as empty — the reasoning still carries the JSON the caller parses,
        # and a persistent empty response would otherwise exhaust retries and kill the run.
        content = message.get("reasoning_content") or ""
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


async def call_anthropic(
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
            ANTHROPIC_URL,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "temperature": temperature,
            },
        )
    except httpx.HTTPError as exc:
        raise LLMError(f"anthropic request failed: {exc}", retryable=True) from exc

    _raise_for_status(resp, "anthropic")
    data = resp.json()
    content = "".join(
        block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
    )
    usage = data.get("usage", {})
    tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
    return content, tokens


async def call_ollama(
    client: httpx.AsyncClient,
    api_key: str,  # carries the base URL (Ollama needs no key)
    system: str,
    user: str,
    *,
    max_tokens: int,
    temperature: float,
) -> tuple[str, int]:
    base = (api_key or "http://localhost:11434").rstrip("/")
    try:
        resp = await client.post(
            f"{base}/v1/chat/completions",  # Ollama's OpenAI-compatible endpoint
            json={
                "model": settings.ollama_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )
    except httpx.HTTPError as exc:
        raise LLMError(f"ollama request failed: {exc}", retryable=True) from exc

    _raise_for_status(resp, "ollama")
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    tokens = int(data.get("usage", {}).get("total_tokens", 0))
    return content, tokens


async def call_gemini_vision(
    client: httpx.AsyncClient, api_key: str, prompt: str, image: bytes, mime: str
) -> str:
    """Describe/answer about an image with Gemini (which is multimodal)."""
    import base64

    try:
        resp = await client.post(
            GEMINI_URL.format(model=GEMINI_MODEL),
            params={"key": api_key},
            json={
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": prompt},
                            {
                                "inline_data": {
                                    "mime_type": mime,
                                    "data": base64.b64encode(image).decode(),
                                }
                            },
                        ],
                    }
                ],
                "generationConfig": {"maxOutputTokens": 800, "temperature": 0.2},
            },
        )
    except httpx.HTTPError as exc:
        raise LLMError(f"gemini vision request failed: {exc}", retryable=True) from exc

    _raise_for_status(resp, "gemini-vision")
    data = resp.json()
    candidates = data.get("candidates", [])
    if not candidates:
        return "(the vision model returned no description)"
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts) or "(no description)"


_DEMO_FIB = "a, b = 0, 1\nfor _ in range(12):\n    print(a, end=' ')\n    a, b = b, a + b\n"
_DEMO_SEQ = "0 1 1 2 3 5 8 13 21 34 55 89"


async def call_mock(
    client: httpx.AsyncClient,
    api_key: str,
    system: str,
    user: str,
    *,
    max_tokens: int,
    temperature: float,
) -> tuple[str, int]:
    """A deterministic, offline 'model' for DEMO_MODE. It drives one real task —
    write fib.py, run it, finish with checks the verifier re-runs — so a fresh
    clone shows the full verified loop (and a Receipt) with no API key. It reads
    the run history in the prompt to decide the next step, so it self-sequences."""
    if "JSON array of 3 to 6" in user:  # understand
        content = json.dumps(
            ["Prints the first 12 Fibonacci numbers", "The script runs without error"]
        )
    elif '"met"' in user:  # verify
        content = json.dumps({"met": True, "score": 96, "missing": []})
    elif "[run_command]" in user:  # already ran it -> finish with proof
        content = json.dumps(
            {
                "thought": "It runs and prints the sequence — done.",
                "tool": "finish",
                "args": {
                    "summary": "Wrote fib.py; verified it prints the first 12 Fibonacci numbers.",
                    "checks": [
                        {
                            "kind": "command",
                            "command": "python3 fib.py",
                            "expect_stdout": _DEMO_SEQ,
                        },
                        {"kind": "file_exists", "path": "fib.py"},
                    ],
                },
            }
        )
    elif "[write_file]" in user:  # wrote it -> run it
        content = json.dumps(
            {
                "thought": "Run it to confirm the output.",
                "tool": "run_command",
                "args": {"command": "python3 fib.py"},
            }
        )
    else:  # first step -> write the script
        content = json.dumps(
            {
                "thought": "Write the Fibonacci script.",
                "tool": "write_file",
                "args": {"path": "fib.py", "content": _DEMO_FIB},
            }
        )
    return content, 12


def wrap_parse_error(provider: str, exc: Exception) -> LLMError:
    """Normalise an unexpected response shape into a (non-retryable) LLMError."""
    message = f"{provider} response parse error: {exc}"
    return LLMError(message, retryable=is_retryable_message(str(exc)))
