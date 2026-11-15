"""The scan result: facts, conversations, counts, warnings and what was skipped."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import NamedTuple


ENTITY_TYPES = (
    "person", "preference", "fact", "event", "place", "project",
    "organisation", "concept", "skill", "media",
)


@dataclass(frozen=True)
class ImportedFact:
    """One standalone sentence about the person, waiting for their review."""

    text: str
    category: str
    source: str
    source_label: str
    source_ref: str
    subject_hint: str | None = None
    date: str | None = None
    tags: tuple[str, ...] = ()


class ImportedMessage(NamedTuple):
    role: str  # "user" or "assistant"
    text: str
    timestamp: datetime | None


@dataclass(frozen=True)
class ImportedConversation:
    """One past chat, its current branch only, oldest message first."""

    provider: str
    id: str
    title: str
    created_at: datetime | None
    updated_at: datetime | None
    messages: tuple[ImportedMessage, ...]


def build_conversation(provider: str, conversation_id: str, title: str | None,
                       created_at: datetime | None, updated_at: datetime | None,
                       messages: list[ImportedMessage]) -> ImportedConversation | None:
    """A conversation with its gaps filled from its messages; ``None`` when it has no messages."""
    messages = [message for message in messages if message.text.strip()]
    if not messages or not conversation_id:
        return None
    stamps = [message.timestamp for message in messages if message.timestamp is not None]
    title = " ".join((title or "").split())
    if not title or title.lower() == "new chat":
        first = next((m.text for m in messages if m.role == "user"), messages[0].text)
        line = " ".join(first.split())
        title = line if len(line) <= 80 else line[:79].rsplit(" ", 1)[0] + "…"
    return ImportedConversation(
        provider=provider,
        id=str(conversation_id),
        title=title,
        created_at=created_at or (min(stamps) if stamps else None),
        updated_at=updated_at or (max(stamps) if stamps else None),
        messages=tuple(messages),
    )


@dataclass(frozen=True)
class SkippedItem:
    """Something in the files that was deliberately not read."""

    name: str
    reason: str


@dataclass
class SourceCounts:
    label: str
    facts: int = 0
    conversations: int = 0
    messages: int = 0


ConversationStream = Callable[[], Iterator[ImportedConversation]]


@dataclass
class ImportScan:
    """What a set of files or a pasted answer holds. Building it changes nothing.

    Conversations are not kept in memory: :meth:`iter_conversations` reads them
    again from the picked files, one at a time, each time it is called.
    """

    facts: list[ImportedFact] = field(default_factory=list)
    counts: dict[str, SourceCounts] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    skipped: list[SkippedItem] = field(default_factory=list)
    more_remain: bool = False
    _streams: list[ConversationStream] = field(default_factory=list, repr=False)

    @property
    def conversation_count(self) -> int:
        return sum(count.conversations for count in self.counts.values())

    def iter_conversations(self) -> Iterator[ImportedConversation]:
        seen: set[tuple[str, str]] = set()
        for stream in self._streams:
            for conversation in stream():
                key = (conversation.provider, conversation.id)
                if key in seen:
                    continue
                seen.add(key)
                yield conversation
