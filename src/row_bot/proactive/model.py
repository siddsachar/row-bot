"""What a candidate, a moment and the desktop's state are (Proactive Row-Bot).

A *candidate* is what a watcher saw change: short, factual, with the sources it came from. A *moment* is a
candidate the engine kept: one thing for the person, with its evidence, its expiry and what it offers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["follow_up", "meeting_prep", "brief", "date", "change", "workflow_offer"]
KINDS: tuple[str, ...] = ("follow_up", "meeting_prep", "brief", "date", "change", "workflow_offer")
KIND_WORDS = {"follow_up": "Follow-ups", "meeting_prep": "Meeting prep", "brief": "Morning brief",
              "date": "Dates that matter", "change": "Changes you care about",
              "workflow_offer": "Make it a workflow"}

State = Literal["pending", "shown", "acted", "dismissed", "ignored", "expired", "dropped_stale"]
STATES: tuple[str, ...] = ("pending", "shown", "acted", "dismissed", "ignored", "expired", "dropped_stale")
OPEN_STATES = frozenset({"pending", "shown"})

Urgency = Literal["normal", "urgent"]
# "brief": carried by the morning brief (which went out on a surface of its own).
Surface = Literal["", "for_you", "brief", "buddy", "notice", "desktop", "push", "channel"]

EvidenceType = Literal["event", "message", "thread", "memory", "conversation", "workflow_run", "check", "ask"]


@dataclass(frozen=True)
class Evidence:
    """One source a moment rests on: what it is, its id in that source, and how the person reads it."""

    type: EvidenceType
    id: str
    label: str = ""
    source: str = ""  # gmail, outlook, google_calendar, outlook_calendar, chats, memory, workflows, monitor
    # What it looked like when it was seen (a meeting's start, a thread's last message id): the re-check
    # compares it with how it is now.
    seen: str = ""

    def view(self) -> dict[str, str]:
        return {"type": self.type, "id": self.id, "label": self.label, "source": self.source, "seen": self.seen}

    @classmethod
    def from_view(cls, data: dict) -> "Evidence":
        return cls(type=data.get("type", "conversation"), id=str(data.get("id") or ""),
                   label=str(data.get("label") or ""), source=str(data.get("source") or ""),
                   seen=str(data.get("seen") or ""))


@dataclass(frozen=True)
class Candidate:
    """Something a watcher saw change; it may become a moment.

    ``summary`` is the short change summary triage reads. ``untrusted`` is text from outside (a mail's
    words): data only, never instructions. ``topic`` is who or what it is about, for the person's
    "never about X" rules.
    """

    kind: str
    key: str  # The dedupe key: one moment per key, ever.
    title: str
    summary: str
    evidence: tuple[Evidence, ...]
    watcher: str = ""
    topic: str = ""
    untrusted: str = ""
    urgency: str = "normal"
    due_at: float | None = None
    expires_at: float | None = None
    actions: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class Desktop:
    """The desktop's state for timing. ``None`` is a valid answer everywhere: it couldn't be told.

    ``app_kind`` is the foreground app's kind only (browser, editor, meeting, other), never its title.
    """

    idle_seconds: float | None = None
    locked: bool | None = None
    focus: bool | None = None  # OS Focus / Do Not Disturb, or a presentation or full-screen app.
    mic_in_use: bool | None = None
    camera_in_use: bool | None = None
    app_kind: str | None = None
    notes: tuple[str, ...] = field(default=())  # Why a signal is unknown on this computer.

    @property
    def on_call(self) -> bool:
        return bool(self.mic_in_use or self.camera_in_use or self.app_kind == "meeting")

    @property
    def away(self) -> bool:
        """Idle for a while or locked: the person isn't at the computer."""
        return bool(self.locked) or (self.idle_seconds is not None and self.idle_seconds >= AWAY_IDLE_SECONDS)

    @property
    def known(self) -> bool:
        return any(value is not None for value in (self.idle_seconds, self.locked, self.focus, self.mic_in_use,
                                                   self.camera_in_use, self.app_kind))


UNKNOWN = Desktop()
AWAY_IDLE_SECONDS = 5 * 60
# Input this recent means the person is mid-sentence or mid-click: wait for a pause.
TYPING_IDLE_SECONDS = 3.0
