"""Fake mail, calendar and chats with the sources' shape: the watchers read them, and nothing here can send."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from row_bot.proactive.sources import ChatLine, Event, MailThread


@dataclass
class FakeMail:
    """A mailbox. It has no way to send, reply or forward: there is nothing for an instruction to reach."""

    name: str = "gmail"
    connected: bool = True
    sent: list[MailThread] = field(default_factory=list)
    waiting: list[MailThread] = field(default_factory=list)
    replied: set[str] = field(default_factory=set)  # Threads that got a reply since.

    def available(self) -> bool:
        return self.connected

    def unanswered_sent(self, since: float, before: float) -> list[MailThread]:
        return [thread for thread in self.sent if since <= thread.at <= before and thread.thread_id not in self.replied]

    def waiting_on_me(self, since: float, before: float) -> list[MailThread]:
        return [thread for thread in self.waiting if since <= thread.at <= before]

    def still_last(self, thread_id: str, message_id: str) -> bool | None:
        return thread_id not in self.replied


@dataclass
class FakeCalendar:
    name: str = "google_calendar"
    connected: bool = True
    items: dict[str, Event] = field(default_factory=dict)

    def available(self) -> bool:
        return self.connected

    def add(self, event: Event) -> None:
        self.items[event.id] = event

    def events(self, start: float, end: float) -> list[Event]:
        return [event for event in self.items.values() if start <= event.start <= end]

    def event(self, event_id: str) -> Event | None:
        return self.items.get(event_id)


@dataclass
class FakeChats:
    name: str = "chats"
    chats: dict[str, list[str]] = field(default_factory=dict)
    titles: dict[str, str] = field(default_factory=dict)
    deleted: set[str] = field(default_factory=set)

    def available(self) -> bool:
        return True

    def say(self, conversation_id: str, text: str, title: str = "") -> None:
        self.chats.setdefault(conversation_id, []).append(text)
        self.titles.setdefault(conversation_id, title or conversation_id)

    def conversations(self, since: float) -> list[tuple[str, str]]:
        return [(key, self.titles[key]) for key in self.chats if key not in self.deleted]

    def lines(self, conversation_id: str, title: str = "") -> list[ChatLine]:
        return [ChatLine(conversation_id, title or self.titles.get(conversation_id, ""), text)
                for text in self.chats.get(conversation_id, [])]

    def still_there(self, conversation_id: str, line_id: str) -> bool | None:
        return conversation_id not in self.deleted and any(line.id == line_id for line in self.lines(conversation_id))


def meeting(event_id: str, start: float, *, title: str = "Design review", people: tuple[str, ...] = ("ana@example.com",),
            **values: Any) -> Event:
    return Event("google_calendar", event_id, title, start, start + 1800, people, **values)


def thread(thread_id: str, at: float, *, other: str = "ana@example.com", subject: str = "The Q4 deck",
           snippet: str = "") -> MailThread:
    return MailThread("gmail", thread_id, f"{thread_id}-last", other, subject, at, snippet)
