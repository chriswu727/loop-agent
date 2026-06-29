"""Email tools: send via SMTP, read via IMAP.

Enabled per task (``use_email``) and only when SMTP/IMAP creds are configured.
``send_email`` is a side-effecting external action, so the loop routes it through
the human approval gate before it runs. ``read_inbox`` is read-only, and its
output is framed as untrusted [DATA] like any other observation. Both block
on the network, so they run in a worker thread.
"""

from __future__ import annotations

import asyncio
import email
import imaplib
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, ClassVar

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("email")


class EmailTools:
    tool_names: ClassVar[set[str]] = {"send_email", "read_inbox"}

    async def call(self, name: str, args: dict[str, Any]) -> str:
        if name == "send_email":
            return await asyncio.to_thread(self._send, args)
        if name == "read_inbox":
            return await asyncio.to_thread(self._read, args)
        return f"Unknown email tool {name!r}."

    def _send(self, args: dict[str, Any]) -> str:
        to = str(args.get("to", "")).strip()
        if not to:
            return "send_email needs a 'to' address."
        msg = EmailMessage()
        msg["From"] = settings.email_from or settings.smtp_user or ""
        msg["To"] = to
        msg["Subject"] = str(args.get("subject", "")).strip()
        msg.set_content(str(args.get("body", "")))
        with smtplib.SMTP(settings.smtp_host or "", settings.smtp_port, timeout=30) as server:
            if settings.smtp_starttls:
                server.starttls(context=ssl.create_default_context())
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        log.info("email.sent", to=to)
        return f"Email sent to {to} (subject: {msg['Subject']!r})."

    def _read(self, args: dict[str, Any]) -> str:
        limit = max(1, min(int(args.get("limit", 5) or 5), 20))
        host = settings.imap_host or settings.smtp_host or ""
        with imaplib.IMAP4_SSL(host) as box:
            box.login(settings.smtp_user or "", settings.smtp_password or "")
            box.select("INBOX")
            _typ, data = box.search(None, "ALL")
            ids = data[0].split()[-limit:]
            items: list[str] = []
            for msg_id in reversed(ids):
                _t, raw = box.fetch(msg_id, "(RFC822)")
                if not raw or not isinstance(raw[0], tuple):
                    continue
                parsed = email.message_from_bytes(raw[0][1])
                items.append(
                    f"- From: {parsed.get('From', '?')}\n"
                    f"  Subject: {parsed.get('Subject', '(none)')}\n"
                    f"  Date: {parsed.get('Date', '?')}\n"
                    f"  Preview: {_body_preview(parsed)}"
                )
        return "\n".join(items) or "(inbox empty)"


def _body_preview(parsed: EmailMessage, limit: int = 300) -> str:
    try:
        if parsed.is_multipart():
            for part in parsed.walk():
                if part.get_content_type() == "text/plain":
                    text = part.get_payload(decode=True) or b""
                    break
            else:
                text = b""
        else:
            text = parsed.get_payload(decode=True) or b""
        body = text.decode("utf-8", errors="replace").strip().replace("\n", " ")
        return body[:limit] + ("…" if len(body) > limit else "")
    except Exception:
        return "(could not read body)"
