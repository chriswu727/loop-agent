"""Slack Events API webhook.

Slack pushes message events here (rather than us polling). Every request is
signature-verified before anything runs, so only the configured Slack app can drive
the agent. We ack in well under Slack's 3s budget and run the turn in the background,
then post the reply — so a multi-step task doesn't make Slack retry.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request

from app.api.v1.deps import CacheDep, rate_limit
from app.core.config import settings
from app.exceptions import NotFoundError, UnauthorizedError
from app.services.slack import handle_slack_event, verify_slack_signature

router = APIRouter(prefix="/slack", tags=["slack"])


@router.post(
    "/events",
    summary="Slack Events API webhook (signature-verified)",
    # Each accepted event spawns an agent run, so bound the rate (as /chat does) —
    # an authorized flood otherwise piles up unbounded background runs + task rows.
    dependencies=[rate_limit(limit=30, window_seconds=60)],
)
async def slack_events(
    request: Request, background: BackgroundTasks, cache: CacheDep
) -> dict[str, Any]:
    if not settings.slack_configured:
        raise NotFoundError("The Slack inlet is not configured.")
    raw = (await request.body()).decode("utf-8", "replace")
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    assert settings.slack_signing_secret is not None
    if not verify_slack_signature(settings.slack_signing_secret, timestamp, raw, signature):
        raise UnauthorizedError("Bad Slack signature.")

    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise UnauthorizedError("Malformed Slack payload.") from exc
    if not isinstance(payload, dict):  # valid JSON but not an object (123, [..], null)
        raise UnauthorizedError("Malformed Slack payload.")

    # Endpoint-setup handshake.
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}
    # We ack fast and process in the background, so ignore Slack's retries (a retry
    # would otherwise run the same message a second time).
    if request.headers.get("X-Slack-Retry-Num"):
        return {"ok": True}
    if payload.get("type") == "event_callback":
        # Run each Slack event at most once — dedups retries AND replays of a
        # captured signed request within the window.
        event_id = payload.get("event_id")
        if event_id and await cache.incr(f"slack:event:{event_id}", ttl_seconds=600) > 1:
            return {"ok": True}
        event = payload.get("event")
        if isinstance(event, dict):
            background.add_task(handle_slack_event, event)
    return {"ok": True}
