"""The watcher framework: small deterministic jobs that say something only when something changed.

A watcher reads one source (mail, the calendar, chats, memory, workflow runs, Monitor's checks), compares
it with its cursor (what it saw last time, kept in ``proactive.db``) and returns candidates only for what
is new. No model runs here, and nothing runs at all for a source that is off or isn't connected.

Each watcher also re-checks its own evidence just before a moment is shown (``recheck``): the meeting still
exists, the thread is still unanswered. A watcher that can't tell says so with ``None`` and the moment
waits; ``False`` drops it as stale.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from row_bot.proactive import store
from row_bot.proactive.model import Candidate

logger = logging.getLogger(__name__)


class Watcher:
    """One source's watcher. Subclasses set ``name``, ``source`` and ``every_seconds`` and implement
    ``scan``; ``available`` says whether the source is connected; ``recheck`` verifies a moment's evidence."""

    name = ""
    source = ""  # One of settings.SOURCES.
    every_seconds = 15 * 60

    def available(self) -> bool:
        return True

    def scan(self, cursor: dict[str, Any], now: float) -> tuple[list[Candidate], dict[str, Any]]:
        raise NotImplementedError

    def recheck(self, moment: dict[str, Any], now: float) -> bool | None:
        return True


_WATCHERS: dict[str, Watcher] = {}


def register(watcher: Watcher) -> None:
    _WATCHERS[watcher.name] = watcher


def unregister(name: str) -> None:
    _WATCHERS.pop(name, None)


def registered() -> list[Watcher]:
    return list(_WATCHERS.values())


def get(name: str) -> Watcher | None:
    return _WATCHERS.get(name)


def run_due(now: float, settings: dict[str, Any]) -> tuple[list[Candidate], list[str], list[str]]:
    """Run every watcher that is due, on, and connected; (candidates, watchers that ran, skipped ones)."""
    candidates: list[Candidate] = []
    ran: list[str] = []
    skipped: list[str] = []
    for watcher in registered():
        if not settings["sources"].get(watcher.source, False):
            continue
        cursor = store.cursor(watcher.name)
        if now - float(cursor.get("_last_run", 0)) < watcher.every_seconds:
            continue
        try:
            connected = watcher.available()
        except Exception:
            logger.debug("Watcher %s couldn't tell whether its source is connected", watcher.name, exc_info=True)
            connected = False
        if not connected:
            skipped.append(watcher.name)
            store.save_cursor(watcher.name, {**cursor, "_last_run": now}, now=now)
            continue
        try:
            found, kept = watcher.scan({key: value for key, value in cursor.items() if key != "_last_run"}, now)
        except Exception:
            logger.warning("Watcher %s failed; its cursor is kept for the next pass", watcher.name, exc_info=True)
            store.save_cursor(watcher.name, {**cursor, "_last_run": now}, now=now)
            skipped.append(watcher.name)
            continue
        store.save_cursor(watcher.name, {**kept, "_last_run": now}, now=now)
        ran.append(watcher.name)
        candidates.extend(
            candidate if candidate.watcher else replace(candidate, watcher=watcher.name)
            for candidate in found)
    return candidates, ran, skipped
