"""Channel-agnostic chat endpoint: send a message, run a task, get the reply.

This is the integration seam any messaging platform plugs into — Telegram uses
the same run_chat_turn internally, and Discord/Slack/a webhook would just POST
here. The call runs the turn synchronously (to completion or the next pause), so
it's meant for interactive messages, not multi-minute batch jobs.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.deps import rate_limit
from app.schemas.chat import ChatIn, ChatOut
from app.services.chat import reply_for, run_chat_turn

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "",
    response_model=ChatOut,
    summary="Send a message; run a turn and get the reply",
    # Each call runs a whole agent loop, so gate abuse like the publish endpoint.
    dependencies=[rate_limit(limit=20, window_seconds=60)],
)
async def chat(payload: ChatIn) -> ChatOut:
    task = await run_chat_turn(payload.chat_id, payload.message)
    if task is None:
        return ChatOut(reply="(the task could not be found)", task_id=None, status="unknown")
    return ChatOut(reply=reply_for(task), task_id=task.id, status=task.status)
