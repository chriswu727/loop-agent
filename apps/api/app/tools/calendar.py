"""Calendar tools over CalDAV: list upcoming events, create an event.

Enabled per task (``use_calendar``) and only when CalDAV creds are configured.
``create_event`` writes to your real calendar, so the loop routes it through the
human approval gate (like send_email). ``list_events`` is read-only and its
output is framed as untrusted [DATA]. Both block on the network, so they run in
a worker thread.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("calendar")


class CalendarTools:
    tool_names: ClassVar[set[str]] = {"list_events", "create_event"}

    async def call(self, name: str, args: dict[str, Any]) -> str:
        if name == "list_events":
            return await asyncio.to_thread(self._list, args)
        if name == "create_event":
            return await asyncio.to_thread(self._create, args)
        return f"Unknown calendar tool {name!r}."

    def _calendar(self) -> Any:
        import caldav

        client = caldav.DAVClient(  # type: ignore[operator]  # caldav ships no stubs
            url=settings.caldav_url or "",
            username=settings.caldav_user or "",
            password=settings.caldav_password or "",
        )
        calendars = client.principal().calendars()
        if not calendars:
            return None
        if settings.caldav_calendar:
            for cal in calendars:
                if cal.name == settings.caldav_calendar:
                    return cal
        return calendars[0]

    def _list(self, args: dict[str, Any]) -> str:
        days = max(1, min(int(args.get("days", 7) or 7), 60))
        cal = self._calendar()
        if cal is None:
            return "No calendar found for these credentials."
        start = datetime.now(UTC)
        events = cal.search(start=start, end=start + timedelta(days=days), event=True, expand=True)
        lines: list[str] = []
        for ev in events[:25]:
            comp = ev.icalendar_component
            summary = comp.get("summary", "(no title)")
            dtstart = comp.get("dtstart")
            when = dtstart.dt.isoformat() if dtstart else "?"
            lines.append(f"- {when}: {summary}")
        return "\n".join(lines) or f"(no events in the next {days} days)"

    def _create(self, args: dict[str, Any]) -> str:
        from icalendar import Calendar, Event

        summary = str(args.get("summary", "")).strip()
        if not summary:
            return "create_event needs a 'summary'."
        try:
            operation_id = str(uuid.UUID(str(args["operation_id"])))
        except (KeyError, ValueError):
            return "create_event requires a Loop operation_id for duplicate protection."
        try:
            start = datetime.fromisoformat(str(args["start"]))
            end = (
                datetime.fromisoformat(str(args["end"]))
                if args.get("end")
                else start + timedelta(hours=1)
            )
        except (KeyError, ValueError):
            return "create_event needs ISO 'start' (and optional 'end'), e.g. 2026-07-02T15:00:00."
        cal = self._calendar()
        if cal is None:
            return "No calendar found for these credentials."
        obj = Calendar()  # type: ignore[no-untyped-call]  # icalendar ships no stubs
        event = Event()  # type: ignore[no-untyped-call]
        event.add("summary", summary)
        event.add("dtstart", start)
        event.add("dtend", end)
        event.add("uid", f"{operation_id}@loop")
        if args.get("description"):
            event.add("description", str(args["description"]))
        obj.add_component(event)
        cal.save_event(obj.to_ical().decode())
        log.info("calendar.created", summary=summary)
        return f"Event created: {summary!r} at {start.isoformat()}."
