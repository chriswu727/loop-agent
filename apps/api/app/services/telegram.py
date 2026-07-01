"""Telegram chat inlet: messages in become tasks, results go back out.

This does not change the agent at all — it bridges a chat to the existing
publish → run → (pause for input) → respond → resume path. A new message
publishes a task and replies with the result; if the agent pauses to ask a
question or for approval, the bot relays that, and the next message from that
chat is the answer (resuming the same task). A chat allowlist gates who may
command the bot, since it can run code and send email.
"""

from __future__ import annotations

import asyncio
import contextlib

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.task import TaskModel
from app.db.session import get_sessionmaker
from app.domain.task import StopReason, TaskStatus
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.schemas.task import TaskCreate
from app.services.runner import execute_task
from app.services.task import TaskService

log = get_logger("telegram")


class TelegramClient:
    def __init__(self, token: str, client: httpx.AsyncClient) -> None:
        self._base = f"https://api.telegram.org/bot{token}"
        self._client = client

    async def get_updates(self, offset: int, poll_seconds: int = 20) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/getUpdates",
            params={"offset": offset, "timeout": poll_seconds},
            timeout=poll_seconds + 10,
        )
        resp.raise_for_status()
        return resp.json().get("result", [])

    async def send_message(self, chat_id: str, text: str) -> None:
        await self._client.post(
            f"{self._base}/sendMessage", json={"chat_id": chat_id, "text": text[:4000]}
        )


def reply_for(task: TaskModel) -> str:
    """Turn a task's terminal/paused state into a chat reply."""
    if task.status == TaskStatus.AWAITING_INPUT.value:
        return task.pending_question or "I need a bit more information to continue."
    if task.status == TaskStatus.COMPLETED.value:
        if task.stop_reason == StopReason.GOAL_ACHIEVED.value:
            return f"Done. {task.summary or ''}".strip()
        return f"Stopped ({task.stop_reason}). {task.summary or ''}".strip()
    if task.status == TaskStatus.FAILED.value:
        return f"Failed: {task.error or 'unknown error'}"
    return f"Status: {task.status}"


async def _find_awaiting(session, chat_id: str) -> TaskModel | None:
    result = await session.execute(
        select(TaskModel)
        .where(TaskModel.chat_id == chat_id)
        .where(TaskModel.status == TaskStatus.AWAITING_INPUT.value)
        .order_by(TaskModel.updated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def handle_chat_message(client: TelegramClient, chat_id: str, text: str) -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        awaiting = await _find_awaiting(session, chat_id)
        service = TaskService(TaskRepository(session), StepRepository(session))
        if awaiting is not None:
            await service.respond(awaiting.id, text)  # answer the open question
            task_id = awaiting.id
        else:
            if len(text) < 4:
                await client.send_message(chat_id, "Send me a task (a few words or more).")
                return
            task = await service.publish(TaskCreate(goal=text, chat_id=chat_id))
            task_id = task.id

    await execute_task(task_id)  # runs to completion or the next pause (own session)

    async with sessionmaker() as session:
        task = await TaskRepository(session).get(task_id)
        if task is not None:
            await client.send_message(chat_id, reply_for(task))


async def run_telegram_bot(stop: asyncio.Event) -> None:
    token = settings.telegram_bot_token
    if not token:
        return
    allow = settings.telegram_allowlist()
    if not allow and not settings.telegram_allow_public:
        # Fail closed: a bot that can run code + send email must not answer the
        # whole internet. Set TELEGRAM_ALLOWED_CHAT_IDS, or opt in explicitly.
        log.error("telegram.refused_no_allowlist")
        return
    log.info("telegram.started", allowlisted=len(allow) or "public")
    offset = 0
    async with httpx.AsyncClient(timeout=40) as http:
        client = TelegramClient(token, http)
        while not stop.is_set():
            try:
                updates = await client.get_updates(offset, poll_seconds=20)
            except Exception:
                await asyncio.sleep(3)  # transient network/API error; keep polling
                continue
            for update in updates:
                offset = int(update.get("update_id", offset)) + 1
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                chat_id = str(chat.get("id", "")).strip()
                msg_text = (message.get("text") or "").strip()
                if not chat_id or not msg_text:
                    continue
                if allow and chat_id not in allow:
                    log.warning("telegram.unauthorized", chat_id=chat_id)
                    with contextlib.suppress(Exception):
                        await client.send_message(chat_id, "Not authorized.")
                    continue
                try:
                    await handle_chat_message(client, chat_id, msg_text)
                except Exception:
                    log.exception("telegram.handle_failed", chat_id=chat_id)
                    with contextlib.suppress(Exception):
                        await client.send_message(chat_id, "Sorry — something went wrong.")
    log.info("telegram.stopped")
