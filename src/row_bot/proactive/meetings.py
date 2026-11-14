"""The calendar: meeting prep about ten minutes before, and meetings that moved or were cancelled.

- **Meeting prep**: an event with other people or files, starting within ``LEAD_SECONDS``, is urgent: it
  doesn't wait for a break. Row-Bot prepares a brief for it (read-only) while it waits.
- **Changes**: an event with other people that moved or was cancelled. The first pass only remembers what
  is there; changes are what differs from the last pass.

Every moment is re-checked just before it is shown: a meeting that was cancelled or moved since drops its
prep moment, and a "moved" moment whose meeting moved again is stale too.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from row_bot.proactive import sources as proactive_sources
from row_bot.proactive.model import Candidate, Evidence
from row_bot.proactive.watchers import Watcher

LEAD_SECONDS = 15 * 60
AHEAD_SECONDS = 24 * 3600


def _clock(value: float) -> str:
    moment = datetime.fromtimestamp(value)
    return moment.strftime("%H:%M") if moment.date() == datetime.now().date() else moment.strftime("%a %H:%M")


class CalendarWatcher(Watcher):
    name = "calendar"
    source = "calendar"
    every_seconds = 5 * 60

    def __init__(self, calendars: list[Any] | None = None) -> None:
        self.calendars = proactive_sources.calendar_sources() if calendars is None else calendars

    def _ready(self) -> list[Any]:
        return [source for source in self.calendars if source.available()]

    def available(self) -> bool:
        return bool(self._ready())

    def scan(self, cursor: dict[str, Any], now: float) -> tuple[list[Candidate], dict[str, Any]]:
        known: dict[str, dict[str, Any]] | None = cursor.get("events")
        current: dict[str, dict[str, Any]] = {}
        found: list[Candidate] = []
        ready = self._ready()
        for source in ready:
            for event in source.events(now, now + AHEAD_SECONDS):
                if event.cancelled:
                    continue
                current[event.id] = {"source": source.name, "title": event.title, "start": event.start,
                                     "people": len(event.attendees)}
                if (event.attendees or event.has_files) and 0 < event.start - now <= LEAD_SECONDS:
                    found.append(self._prep(source.name, event, now))
        if known is not None:
            for event_id, before in known.items():
                if before["start"] <= now or not before["people"]:
                    continue
                if event_id in current:
                    if current[event_id]["start"] != before["start"]:
                        found.append(self._moved(event_id, before, current[event_id]["start"]))
                    continue
                source = next((item for item in ready if item.name == before["source"]), None)
                if source is None:
                    continue
                event = source.event(event_id)
                if event is None or event.cancelled:
                    found.append(self._cancelled(event_id, before))
                elif event.start != before["start"]:
                    found.append(self._moved(event_id, before, event.start))
        return found, {"events": current}

    @staticmethod
    def _prep(source: str, event: Any, now: float) -> Candidate:
        minutes = max(1, round((event.start - now) / 60))
        people = len(event.attendees)
        return Candidate(
            kind="meeting_prep", key=f"meeting:{event.id}:{int(event.start)}",
            title=f"{event.title or 'A meeting'} in {minutes} minutes",
            summary=(f"A meeting starts at {_clock(event.start)} with {people} other "
                     f"{'person' if people == 1 else 'people'}{' and files' if event.has_files else ''}."),
            evidence=(Evidence("event", event.id, event.title, source, seen=str(int(event.start))),),
            topic=event.title, urgency="urgent", expires_at=event.start + 5 * 60,
            untrusted=f"Event title: {event.title}\nAttendees: {', '.join(event.attendees[:10])}",
            actions=({"id": "open_brief", "label": "Open brief"},))

    @staticmethod
    def _moved(event_id: str, before: dict[str, Any], start: float) -> Candidate:
        return Candidate(
            kind="change", key=f"moved:{event_id}:{int(start)}", title=f"“{before['title']}” moved to {_clock(start)}",
            summary=f"A meeting with {before['people']} others moved from {_clock(before['start'])} to {_clock(start)}.",
            evidence=(Evidence("event", event_id, before["title"], before["source"], seen=str(int(start))),),
            topic=before["title"], expires_at=start, untrusted=f"Event title: {before['title']}",
            actions=({"id": "open_source", "label": "Open calendar"},))

    @staticmethod
    def _cancelled(event_id: str, before: dict[str, Any]) -> Candidate:
        return Candidate(
            kind="change", key=f"cancelled:{event_id}:{int(before['start'])}",
            title=f"“{before['title']}” at {_clock(before['start'])} was cancelled",
            summary=f"A meeting with {before['people']} others at {_clock(before['start'])} was cancelled.",
            evidence=(Evidence("event", event_id, before["title"], before["source"], seen="cancelled"),),
            topic=before["title"], expires_at=before["start"], untrusted=f"Event title: {before['title']}",
            actions=({"id": "open_source", "label": "Open calendar"},))

    def recheck(self, moment: dict[str, Any], now: float) -> bool | None:
        evidence = next((item for item in moment["evidence"] if item["type"] == "event"), None)
        source = next((item for item in self.calendars if evidence and item.name == evidence["source"]), None)
        if evidence is None or source is None or not source.available():
            return None
        event = source.event(evidence["id"])
        if evidence["seen"] == "cancelled":
            return event is None or event.cancelled
        return event is not None and not event.cancelled and str(int(event.start)) == evidence["seen"]
