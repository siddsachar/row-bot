"""Dates that matter, from memory: birthdays and anniversaries a week ahead, renewals and deadlines three days.

A date is a memory's property (``birthday: 1965-03-15``, ``renewal: 2026-12-01``). Birthdays and
anniversaries come round every year; the rest happen once. One moment per date per year, re-checked just
before it is shown (the memory still says that date).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from row_bot.proactive import sources as proactive_sources
from row_bot.proactive.model import Candidate, Evidence
from row_bot.proactive.watchers import Watcher

YEARLY = frozenset({"birthday", "anniversary"})
LEAD_DAYS = {"birthday": 7, "anniversary": 7}
DEFAULT_LEAD_DAYS = 3
_FULL = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_MONTH_DAY = re.compile(r"^(?:--)?(\d{2})-(\d{2})$")


def next_date(value: str, field: str, today: date) -> date | None:
    """The next time *value* comes round from *today* (yearly fields), or the date itself; None if past or
    unreadable."""
    text = value.strip()
    full, month_day = _FULL.match(text), _MONTH_DAY.match(text)
    try:
        if full:
            year, month, day = (int(part) for part in full.groups())
        elif month_day and field in YEARLY:
            year, (month, day) = today.year, (int(part) for part in month_day.groups())
        else:
            return None
        if field in YEARLY:
            occurrence = _on(today.year, month, day)
            return occurrence if occurrence >= today else _on(today.year + 1, month, day)
        found = date(year, month, day)
    except ValueError:
        return None
    return found if found >= today else None


def _on(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError:  # 29 February in a year without one.
        return date(year, month, day - 1)


def _days_words(days: int, occurrence: date) -> str:
    return "today" if days == 0 else "tomorrow" if days == 1 else f"on {occurrence:%A %d %B}"


class DatesWatcher(Watcher):
    name = "dates"
    source = "memory"
    every_seconds = 6 * 3600

    def __init__(self, memory: Any | None = None) -> None:
        self.memory = proactive_sources.MemorySource() if memory is None else memory

    def scan(self, cursor: dict[str, Any], now: float) -> tuple[list[Candidate], dict[str, Any]]:
        today = datetime.fromtimestamp(now).date()
        found: list[Candidate] = []
        for item in self.memory.dated():
            occurrence = next_date(item.value, item.field, today)
            if occurrence is None:
                continue
            days = (occurrence - today).days
            if days > LEAD_DAYS.get(item.field, DEFAULT_LEAD_DAYS):
                continue
            when = _days_words(days, occurrence)
            title = (f"{item.subject}'s {item.field} is {when}" if item.field in YEARLY
                     else f"{item.subject}: {item.field} {when}")
            end_of_day = datetime.combine(occurrence + timedelta(days=1), datetime.min.time()).timestamp()
            found.append(Candidate(
                kind="date", key=f"date:{item.entity_id}:{item.field}:{occurrence.isoformat()}", title=title,
                summary=f"Memory says {item.subject}'s {item.field} is {occurrence.isoformat()} ({when}).",
                evidence=(Evidence("memory", item.entity_id, item.subject, "memory", seen=item.value),),
                topic=item.subject, expires_at=end_of_day, actions=({"id": "open_source", "label": "Open memory"},)))
        return found, {}

    def recheck(self, moment: dict[str, Any], now: float) -> bool | None:
        evidence = moment["evidence"][0]
        field = moment["dedupe_key"].split(":")[2]
        return self.memory.value(evidence["id"], field) == evidence["seen"]
