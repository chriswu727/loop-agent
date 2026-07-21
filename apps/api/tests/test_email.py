"""Email tools, fully offline: SMTP send and IMAP read are mocked so the wire
logic is verified without a real mail server (live use needs SMTP/IMAP creds)."""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import settings
from app.tools.email import EmailTools


class _FakeSMTP:
    last: Any = None

    def __init__(self, host: str, port: int, timeout: int = 30) -> None:
        self.host, self.port = host, port
        self.tls = False
        self.creds: tuple[str, str] | None = None
        self.msg: Any = None
        _FakeSMTP.last = self

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *a: object) -> bool:
        return False

    def starttls(self, context: object = None) -> None:
        self.tls = True

    def login(self, user: str, password: str) -> None:
        self.creds = (user, password)

    def send_message(self, msg: Any) -> None:
        self.msg = msg


async def test_send_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_user", "me@example.com")
    monkeypatch.setattr(settings, "smtp_password", "app-pass")
    monkeypatch.setattr(settings, "email_from", "me@example.com")
    monkeypatch.setattr(settings, "smtp_starttls", True)
    monkeypatch.setattr("app.tools.email.smtplib.SMTP", _FakeSMTP)

    out = await EmailTools().call(
        "send_email",
        {
            "to": "you@example.com",
            "subject": "Hi",
            "body": "hello there",
            "operation_id": "47e20763-0997-43fe-ab29-c36112e9f495",
        },
    )
    assert "sent to you@example.com" in out
    sent = _FakeSMTP.last
    assert sent.tls is True
    assert sent.creds == ("me@example.com", "app-pass")
    assert sent.msg["To"] == "you@example.com"
    assert sent.msg["Subject"] == "Hi"
    assert sent.msg["Message-ID"] == "<47e20763-0997-43fe-ab29-c36112e9f495@loop>"
    assert "hello there" in sent.msg.get_content()


async def test_send_email_requires_to() -> None:
    out = await EmailTools().call("send_email", {"subject": "x"})
    assert "needs a 'to'" in out


async def test_send_email_requires_operation_id() -> None:
    out = await EmailTools().call("send_email", {"to": "you@example.com"})
    assert "operation_id" in out


class _FakeIMAP:
    def __init__(self, host: str) -> None:
        self.host = host

    def __enter__(self) -> _FakeIMAP:
        return self

    def __exit__(self, *a: object) -> bool:
        return False

    def login(self, user: str, password: str) -> None: ...
    def select(self, mailbox: str) -> None: ...

    def search(self, charset: object, criteria: str) -> tuple[str, list[bytes]]:
        return ("OK", [b"1 2"])

    def fetch(self, msg_id: bytes, parts: str) -> tuple[str, list[Any]]:
        raw = (
            b"From: alice@example.com\r\nSubject: Standup\r\n"
            b"Date: Mon, 1 Jun 2026\r\n\r\nNotes for today."
        )
        return ("OK", [(b"1 (RFC822 {x}", raw)])


async def test_read_inbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "smtp_user", "me@example.com")
    monkeypatch.setattr(settings, "smtp_password", "app-pass")
    monkeypatch.setattr(settings, "imap_host", "imap.example.com")
    monkeypatch.setattr("app.tools.email.imaplib.IMAP4_SSL", _FakeIMAP)

    out = await EmailTools().call("read_inbox", {"limit": 2})
    assert "alice@example.com" in out
    assert "Standup" in out
    assert "Notes for today." in out
