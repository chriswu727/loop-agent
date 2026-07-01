"""Calendar tools, offline: list + create against a mocked CalDAV calendar."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.tools.calendar import CalendarTools


class _FakeEvent:
    def __init__(self, comp: Any) -> None:
        self.icalendar_component = comp


class _FakeCalendar:
    def __init__(self) -> None:
        self.saved: list[str] = []

    def search(self, *, start: Any, end: Any, event: bool, expand: bool) -> list[_FakeEvent]:
        from icalendar import Event

        ev = Event()
        ev.add("summary", "Standup")
        ev.add("dtstart", datetime(2026, 7, 2, 15, 0, tzinfo=UTC))
        return [_FakeEvent(ev)]

    def save_event(self, ical: str) -> None:
        self.saved.append(ical)


async def test_list_events(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = CalendarTools()
    monkeypatch.setattr(tools, "_calendar", lambda: _FakeCalendar())
    out = await tools.call("list_events", {"days": 7})
    assert "Standup" in out
    assert "2026-07-02" in out


async def test_create_event(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = CalendarTools()
    fake = _FakeCalendar()
    monkeypatch.setattr(tools, "_calendar", lambda: fake)
    out = await tools.call("create_event", {"summary": "Dentist", "start": "2026-07-02T15:00:00"})
    assert "Event created" in out and "Dentist" in out
    assert len(fake.saved) == 1
    assert "Dentist" in fake.saved[0]  # the iCalendar payload carries the summary


async def test_create_event_needs_summary() -> None:
    out = await CalendarTools().call("create_event", {"start": "2026-07-02T15:00:00"})
    assert "needs a 'summary'" in out


async def test_create_event_needs_valid_start() -> None:
    out = await CalendarTools().call("create_event", {"summary": "X", "start": "not-a-date"})
    assert "ISO 'start'" in out
