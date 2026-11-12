"""Microsoft Copilot activity CSV: ``Conversation,Time,Author,Message``, newest row first."""
from __future__ import annotations

import csv
import hashlib
from collections.abc import Iterator
from datetime import datetime, timedelta

from row_bot.memory_import.archive import Member
from row_bot.memory_import.models import ImportedConversation, ImportedMessage, build_conversation
from row_bot.memory_import.sink import Sink
from row_bot.memory_import.streaming import text_reader
from row_bot.memory_import.values import to_utc


PROVIDER = "copilot"
HEADER = ["Conversation", "Time", "Author", "Message"]
_ROLES = {"human": "user", "user": "user", "ai": "assistant", "assistant": "assistant", "copilot": "assistant"}
# Copilot has no chat ids: a title seen again after this long is a new chat.
_SAME_CHAT_GAP = timedelta(hours=12)


def is_copilot_csv(head: bytes) -> bool:
    text = head.removeprefix(b"\xef\xbb\xbf").decode("utf-8", "replace")
    first = text.splitlines()[0] if text else ""
    return [cell.strip().strip('"') for cell in first.split(",")] == HEADER


def iter_conversations(member: Member, sink: Sink) -> Iterator[ImportedConversation]:
    with member.open() as raw:
        rows = list(csv.reader(text_reader(raw)))
    if not rows or [cell.strip() for cell in rows[0]] != HEADER:
        return
    sink.warn("Copilot's file gives times without a time zone; they were read as UTC.")
    chats: list[tuple[str, list[ImportedMessage]]] = []
    open_chats: dict[str, int] = {}
    for row in reversed(rows[1:]):  # the file is newest first; reverse, never sort (replies share a time)
        if len(row) < 4:
            continue
        title, time_text, author, text = row[0], row[1], row[2], row[3]
        role = _ROLES.get(author.strip().lower())
        if role is None or not text.strip():
            continue
        stamp = to_utc(time_text)
        index = open_chats.get(title)
        if index is not None:
            last = chats[index][1][-1].timestamp if chats[index][1] else None
            if stamp and last and stamp - last > _SAME_CHAT_GAP:
                index = None
        if index is None:
            chats.append((title, []))
            index = len(chats) - 1
            open_chats[title] = index
        chats[index][1].append(ImportedMessage(role, text.strip(), stamp))
    for title, messages in chats:
        first: datetime | None = messages[0].timestamp if messages else None
        digest = hashlib.sha256(f"{title}|{first.isoformat() if first else ''}|{messages[0].text if messages else ''}"
                                .encode("utf-8", "replace")).hexdigest()[:24]
        conversation = build_conversation(PROVIDER, digest, title, None, None, messages)
        if conversation is not None:
            yield conversation
