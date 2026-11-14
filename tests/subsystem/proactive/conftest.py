"""Proactive Row-Bot's own data folder per test, a fake clock, a fake desktop and scripted watchers."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from row_bot.proactive.model import Desktop

AT_DESK = Desktop(idle_seconds=40, locked=False, focus=False, mic_in_use=False, camera_in_use=False,
                  app_kind="editor")


@pytest.fixture
def proactive(tmp_path, monkeypatch, reload_for_data_dir):
    """The proactive modules bound to this test's data folder, with no watchers registered."""
    store, settings = reload_for_data_dir(tmp_path, "row_bot.proactive.store", "row_bot.proactive.settings")
    from row_bot.proactive import engine, feedback, timing, watchers

    monkeypatch.setattr(watchers, "_WATCHERS", {})
    return SimpleNamespace(store=store, settings=settings, engine=engine, feedback=feedback, timing=timing,
                           watchers=watchers)


def start_day(proactive, day: str = "2026-11-16") -> None:
    """The person's day starts at 07:45 with nothing waiting: its first break (and brief) has passed."""
    proactive.engine.tick(at(f"{day} 07:45"), desktop=AT_DESK)


def at(text: str) -> float:
    """A local time ("2026-11-16 10:00") as the engine's clock reads it."""
    return datetime.fromisoformat(text).timestamp()


@dataclass
class ScriptedWatcher:
    """A watcher whose source changes when the test says so; it counts its scans."""

    name: str = "fake_mail"
    source: str = "mail"
    every_seconds: int = 60
    connected: bool = True
    pending: list[Any] = field(default_factory=list)
    scans: int = 0
    stale: set[str] = field(default_factory=set)

    def available(self) -> bool:
        return self.connected

    def scan(self, cursor: dict[str, Any], now: float):
        self.scans += 1
        found, self.pending = self.pending, []
        return found, {"seen": int(cursor.get("seen", 0)) + len(found)}

    def recheck(self, moment: dict[str, Any], now: float) -> bool | None:
        return moment["dedupe_key"] not in self.stale


def candidate(key: str = "mail:m1", *, kind: str = "follow_up", title: str = "Ana hasn't replied about the deck",
              topic: str = "ana@example.com", urgency: str = "normal", due_at: float | None = None,
              expires_at: float | None = None, summary: str = "", untrusted: str = "", evidence=None):
    from row_bot.proactive.model import Candidate, Evidence

    return Candidate(kind=kind, key=key, title=title, summary=summary or title, topic=topic, urgency=urgency,
                     due_at=due_at, expires_at=expires_at, untrusted=untrusted,
                     evidence=tuple(evidence or (Evidence("message", key.split(":")[-1], title, "gmail"),)))
