"""Proactive Row-Bot's own data folder per test, a fake clock, a fake desktop, scripted watchers, a scripted
main model for triage and recorded surfaces."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from row_bot.proactive.model import Desktop

AT_DESK = Desktop(idle_seconds=40, locked=False, focus=False, mic_in_use=False, camera_in_use=False,
                  app_kind="editor")


class ScriptedModel:
    """The main model as triage calls it: keeps every change in its own words unless told otherwise."""

    def __init__(self) -> None:
        self.prompts: list[list[dict]] = []
        self.answer: Any = None  # A JSON string, or a function of the case; None keeps everything.

    def __call__(self, prompt: list[dict]) -> str:
        self.prompts.append(prompt)
        case = json.loads(prompt[1]["content"])
        if callable(self.answer):
            return self.answer(case)
        if self.answer is not None:
            return self.answer
        return json.dumps({"decisions": [{"id": change["id"], "keep": True, "title": change["suggested_title"],
                                          "body": change["what_changed"]} for change in case["changes"]]})


class DayRuns:
    """The morning brief's and end-of-day wrap's workflow runs, finished at once: each run's message as the
    workflow expands it, and the reply its conversation gets (the message itself, unless a test sets
    ``reply``, a string or a function of the message)."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.reply: Any = None
        self.status = "completed"

    def __call__(self, task: dict, trigger: str) -> None:
        from row_bot import tasks, workflow_turns
        from row_bot.proactive import day, store

        message = tasks.expand_template_vars(workflow_turns.messages(task)[0], task_id=task["id"])
        self.messages.append(message)
        name = day.which(task["id"])
        reply = self.reply(message) if callable(self.reply) else message if self.reply is None else self.reply
        day.finished(tasks.get_task(task["id"]), f"run-{name}-{len(self.messages)}", self.status, reply, "",
                     now=store.get_state("day_runs")[name]["started"])


@pytest.fixture
def proactive(tmp_path, monkeypatch, reload_for_data_dir):
    """The proactive modules bound to this test's data folder, with no watchers registered, a scripted main
    model, Buddy on, no phone or chat app, surfaces recorded in ``sent`` and preparations in ``prepared``;
    no calendar or mail connected unless a test adds them (``calendars``, ``mail``); the brief's workflow runs
    in ``day_runs``."""
    store, settings, tasks = reload_for_data_dir(tmp_path, "row_bot.proactive.store", "row_bot.proactive.settings",
                                                 "row_bot.tasks")
    from row_bot.proactive import day, deliver, engine, feedback, prepare, sources, timing, triage, watchers

    calendars: list = []
    mail: list = []
    monkeypatch.setattr(sources, "calendar_sources", lambda: calendars)
    monkeypatch.setattr(sources, "mail_sources", lambda: mail)
    day_runs = DayRuns()
    monkeypatch.setattr(day, "start_run", day_runs)
    monkeypatch.setattr(day, "_GATHERED", {})
    monkeypatch.setattr(watchers, "_WATCHERS", {})
    model = ScriptedModel()
    monkeypatch.setattr(triage, "_call", model)
    sent: list[tuple[str, str]] = []
    real_send = deliver.send
    monkeypatch.setattr(deliver, "send", lambda surface, moment, chosen: sent.append((surface, moment["id"])) or True)
    monkeypatch.setattr(deliver, "buddy_on", lambda: True)
    monkeypatch.setattr(deliver, "push_ready", lambda: False)
    monkeypatch.setattr(deliver, "channel_ready", lambda chosen: False)
    prepared: list[tuple[str, str]] = []
    monkeypatch.setattr(prepare, "queue", lambda moment_id, untrusted="": prepared.append((moment_id, untrusted)))
    return SimpleNamespace(store=store, settings=settings, engine=engine, feedback=feedback, timing=timing,
                           real_send=real_send,
                           watchers=watchers, deliver=deliver, model=model, sent=sent, prepared=prepared,
                           tasks=tasks, day=day, day_runs=day_runs, calendars=calendars, mail=mail)


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


def balanced(proactive) -> None:
    """Balanced: moments reach a surface at natural breaks. (Quiet, the default, brings up only the day's brief
    and what's about to happen; the rest waits in For you.)"""
    proactive.settings.save({"level": "balanced"})


def candidate(key: str = "mail:m1", *, kind: str = "follow_up", title: str = "Ana hasn't replied about the deck",
              topic: str = "ana@example.com", urgency: str = "normal", due_at: float | None = None,
              expires_at: float | None = None, summary: str = "", untrusted: str = "", evidence=None):
    from row_bot.proactive.model import Candidate, Evidence

    return Candidate(kind=kind, key=key, title=title, summary=summary or title, topic=topic, urgency=urgency,
                     due_at=due_at, expires_at=expires_at, untrusted=untrusted,
                     evidence=tuple(evidence or (Evidence("message", key.split(":")[-1], title, "gmail"),)))
