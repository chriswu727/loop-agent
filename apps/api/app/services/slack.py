"""Slack chat inlet (Events API).

Unlike Telegram (a long-poller), Slack pushes events to a webhook, so this is a
signature-verified endpoint (``POST /slack/events``) rather than a background task.
Every request is authenticated with the app's signing secret before anything runs,
so only your Slack app can drive the agent; the bot token is used only to post the
reply. It bridges to the same channel-agnostic seam as Telegram and the /chat route
(``run_chat_turn`` + ``reply_for``) — nothing here touches the agent loop directly.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.services.chat import reply_for, run_chat_turn

log = get_logger("slack")

# Reject requests whose timestamp is older than this, so a captured request can't be
# replayed later (Slack's own recommended window).
_MAX_TIMESTAMP_SKEW = 60 * 5


def verify_slack_signature(signing_secret: str, timestamp: str, body: str, signature: str) -> bool:
    """Verify Slack's ``X-Slack-Signature`` over ``v0:{timestamp}:{body}`` (HMAC-SHA256),
    rejecting a stale timestamp first (replay protection). Constant-time compare."""
    if not signing_secret or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > _MAX_TIMESTAMP_SKEW:
            return False
    except (TypeError, ValueError):
        return False
    basestring = f"v0:{timestamp}:{body}".encode()
    expected = "v0=" + hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class SlackClient:
    def __init__(self, token: str, client: httpx.AsyncClient) -> None:
        self._token = token
        self._client = client

    async def post_message(self, channel: str, text: str) -> None:
        await self._client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {self._token}"},
            json={"channel": channel, "text": text[:3000]},
        )


def _is_actionable(event: dict[str, Any]) -> bool:
    """A plain user message we should act on — not a bot echo, edit, join notice, or
    other subtype (those would loop or be noise)."""
    return (
        event.get("type") == "message"
        and not event.get("bot_id")
        and not event.get("subtype")
        and bool(str(event.get("text", "")).strip())
        and bool(event.get("channel"))
    )


async def handle_slack_event(event: dict[str, Any]) -> None:
    """Run one Slack message as a chat turn and post the reply back. Runs in the
    background (Slack needs a sub-3s ack), so it opens its own HTTP client."""
    if not _is_actionable(event) or not settings.slack_configured:
        return
    channel = str(event["channel"])
    # Fail CLOSED, like the Telegram inlet: this bot runs shell code, so with no
    # channel allowlist it refuses unless you explicitly opt into a public bot.
    allowlist = settings.slack_allowlist()
    if not allowlist and not settings.slack_allow_public:
        log.error("slack.refused_no_allowlist", channel=channel)
        return
    if allowlist and channel not in allowlist:
        log.warning("slack.channel_not_allowed", channel=channel)
        return

    assert settings.slack_bot_token is not None
    reply = "Sorry — something went wrong running that. Please try again."
    try:
        task = await run_chat_turn(channel, str(event["text"]))
        reply = reply_for(task) if task is not None else ""
    except Exception:  # a run failure must not vanish silently in a background task
        log.exception("slack.turn_failed", channel=channel)
    if not reply:
        return
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            await SlackClient(settings.slack_bot_token, http).post_message(channel, reply)
    except Exception:
        log.exception("slack.post_failed", channel=channel)
