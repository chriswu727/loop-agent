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

from app.core.config import settings
from app.core.logging import get_logger
from app.services.chat import reply_for, run_chat_turn

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


async def handle_chat_message(client: TelegramClient, chat_id: str, text: str) -> None:
    if len(text) < 4:
        await client.send_message(chat_id, "Send me a task (a few words or more).")
        return
    task = await run_chat_turn(chat_id, text)  # shared with the HTTP /chat endpoint
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
