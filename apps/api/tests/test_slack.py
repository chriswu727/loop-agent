"""The Slack inlet: signature verification (security-critical), the webhook endpoint,
and the message handler bridging to run_chat_turn."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.services.slack import _is_actionable, handle_slack_event, verify_slack_signature

SECRET = "8f742231b10e8888abcd99yyyzzz85a5"


def _sign(secret: str, ts: str, body: str) -> str:
    base = f"v0:{ts}:{body}".encode()
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def test_verify_signature_accepts_valid_rejects_forgeries() -> None:
    ts = str(int(time.time()))
    body = '{"type":"event_callback"}'
    good = _sign(SECRET, ts, body)
    assert verify_slack_signature(SECRET, ts, body, good) is True
    # Tampered body — the signature no longer matches.
    assert verify_slack_signature(SECRET, ts, body + " ", good) is False
    # Wrong secret.
    assert verify_slack_signature("other-secret", ts, body, good) is False
    # Missing signature / secret.
    assert verify_slack_signature(SECRET, ts, body, "") is False
    assert verify_slack_signature("", ts, body, good) is False


def test_verify_signature_rejects_stale_timestamp_replay() -> None:
    old = str(int(time.time()) - 600)  # 10 min old, beyond the 5-min window
    body = '{"type":"event_callback"}'
    sig = _sign(SECRET, old, body)  # a correctly-signed but OLD request
    assert verify_slack_signature(SECRET, old, body, sig) is False
    assert verify_slack_signature(SECRET, "not-a-number", body, sig) is False


def test_is_actionable_filters_noise() -> None:
    assert _is_actionable({"type": "message", "channel": "C1", "text": "hello"}) is True
    assert (
        _is_actionable({"type": "message", "channel": "C1", "text": "x", "bot_id": "B1"}) is False
    )
    assert (
        _is_actionable({"type": "message", "channel": "C1", "text": "x", "subtype": "edit"})
        is False
    )
    assert _is_actionable({"type": "message", "channel": "C1", "text": "  "}) is False
    assert _is_actionable({"type": "reaction_added", "channel": "C1", "text": "x"}) is False


async def test_endpoint_404_when_not_configured(client: AsyncClient) -> None:
    resp = await client.post("/slack/events", json={"type": "url_verification"})
    assert resp.status_code == 404


async def test_endpoint_rejects_bad_signature(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "slack_bot_token", "xoxb-token")
    monkeypatch.setattr(settings, "slack_signing_secret", SECRET)
    resp = await client.post(
        "/slack/events",
        content=b'{"type":"event_callback"}',
        headers={"X-Slack-Request-Timestamp": str(int(time.time())), "X-Slack-Signature": "v0=bad"},
    )
    assert resp.status_code == 401


async def test_endpoint_url_verification_and_event(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "slack_bot_token", "xoxb-token")
    monkeypatch.setattr(settings, "slack_signing_secret", SECRET)
    scheduled: list[dict[str, Any]] = []

    async def fake_handle(event: dict[str, Any]) -> None:
        scheduled.append(event)

    monkeypatch.setattr("app.api.v1.routes.slack.handle_slack_event", fake_handle)

    # 1) url_verification handshake echoes the challenge.
    ts = str(int(time.time()))
    body = json.dumps({"type": "url_verification", "challenge": "chal-123"})
    resp = await client.post(
        "/slack/events",
        content=body.encode(),
        headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": _sign(SECRET, ts, body)},
    )
    assert resp.status_code == 200 and resp.json()["challenge"] == "chal-123"

    # 2) a real message event is accepted and handed to the background handler.
    body = json.dumps(
        {"type": "event_callback", "event": {"type": "message", "channel": "C1", "text": "do it"}}
    )
    resp = await client.post(
        "/slack/events",
        content=body.encode(),
        headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": _sign(SECRET, ts, body)},
    )
    assert resp.status_code == 200
    assert scheduled and scheduled[0]["text"] == "do it"

    # 3) a Slack retry is ignored (no duplicate processing).
    scheduled.clear()
    resp = await client.post(
        "/slack/events",
        content=body.encode(),
        headers={
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": _sign(SECRET, ts, body),
            "X-Slack-Retry-Num": "1",
        },
    )
    assert resp.status_code == 200 and scheduled == []


async def test_handle_event_respects_channel_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "slack_bot_token", "xoxb-token")
    monkeypatch.setattr(settings, "slack_signing_secret", SECRET)
    monkeypatch.setattr(settings, "slack_allowed_channels", "C-ALLOWED")
    posted: list[tuple[str, str]] = []

    async def fake_run(chat_id: str, message: str) -> None:
        raise AssertionError("must not run a turn for a non-allowlisted channel")

    monkeypatch.setattr("app.services.slack.run_chat_turn", fake_run)
    # A message from a channel not on the allowlist is dropped before run_chat_turn.
    await handle_slack_event({"type": "message", "channel": "C-OTHER", "text": "hi"})
    assert posted == []
