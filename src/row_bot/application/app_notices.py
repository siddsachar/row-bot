"""Background notices for every open client (parity rows 10 and 11).

``notifications.notify()`` (workflow results, reminders, approvals waiting,
account health, Buddy generation, document batches, memory policy, API
errors) and the start-up warnings (plugins that failed to load, a tunnel that
did not start, expiring tokens) land in one bounded journal that the
client-platform event stream carries to React, which shows them through its
notice primitive.

The journal never grows: it keeps the latest ``MAX_NOTICES`` and folds a
repeat of the same notice into the one already kept (with a count) rather
than adding another. Notices are text written for people; they never carry
prompts, file contents, secrets or paths beyond what ``notify()`` was given.
"""
from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Literal

Level = Literal["info", "warning", "error"]

MAX_NOTICES = 64
MAX_STARTUP_WARNINGS = 32
COALESCE_SECONDS = 600.0
_TITLE_LIMIT = 120
_MESSAGE_LIMIT = 500
_SOURCE_LIMIT = 32


def _clean(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    # A leading warning glyph reads twice next to the notice's own tone.
    for glyph in ("⚠️", "⚠", "❌", "✅"):
        if text.startswith(glyph):
            text = text[len(glyph):].lstrip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass(frozen=True)
class Notice:
    id: int
    level: Level
    title: str
    message: str
    source: str
    requested: bool
    startup: bool
    count: int
    at: str

    def view(self) -> dict:
        return asdict(self)


class NoticeJournal:
    """Thread-safe, bounded, coalescing journal for one server process."""

    def __init__(self, *, clock=None) -> None:
        self.epoch = str(uuid.uuid4())
        self._lock = threading.Lock()
        self._notices: deque[Notice] = deque(maxlen=MAX_NOTICES)
        self._startup: deque[str] = deque(maxlen=MAX_STARTUP_WARNINGS)
        self._next = 1
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def post(self, *, title: str, message: str, level: Level = "info", source: str = "app",
             requested: bool = False, startup: bool = False) -> Notice:
        if level not in ("info", "warning", "error"):
            level = "info"
        title = _clean(title, _TITLE_LIMIT) or "Row-Bot"
        message = _clean(message, _MESSAGE_LIMIT)
        source = _clean(source, _SOURCE_LIMIT) or "app"
        now = self._clock()
        key = (level, source, title, message)
        with self._lock:
            for index in range(len(self._notices) - 1, -1, -1):
                existing = self._notices[index]
                if (existing.level, existing.source, existing.title, existing.message) != key:
                    continue
                age = now.timestamp() - datetime.fromisoformat(existing.at).timestamp()
                if age > COALESCE_SECONDS:
                    break
                # A repeat folds into the notice already kept: it keeps its id
                # so clients that showed it do not show it again.
                folded = replace(existing, count=existing.count + 1, at=now.isoformat(),
                                 requested=existing.requested or requested)
                self._notices[index] = folded
                return folded
            notice = Notice(self._next, level, title, message, source, bool(requested), bool(startup), 1,
                            now.isoformat())
            self._next += 1
            self._notices.append(notice)
            if startup:
                self._startup.append(message or title)
            return notice

    def startup_warning(self, message: str, *, source: str = "startup") -> Notice:
        return self.post(title="Start-up warning", message=message, level="warning", source=source,
                         startup=True)

    def since(self, after: int = 0) -> list[Notice]:
        with self._lock:
            return [notice for notice in self._notices if notice.id > after]

    def latest(self) -> int:
        with self._lock:
            return self._notices[-1].id if self._notices else 0

    def startup_warnings(self) -> list[str]:
        with self._lock:
            return list(self._startup)

    def page(self, after: int = 0) -> dict:
        """The public page: notices newer than ``after`` plus start-up warnings."""
        return {"server_epoch": self.epoch, "latest": self.latest(),
                "notices": [notice.view() for notice in self.since(after)],
                "startup_warnings": self.startup_warnings()}


app_notices = NoticeJournal()
