"""Perplexity export: ``conversations-<date>_<time>-<hash>.json`` = ``{"conversations": [...]}``."""
from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from row_bot.memory_import.archive import Member
from row_bot.memory_import.models import ImportedConversation, ImportedMessage, build_conversation
from row_bot.memory_import.sink import Sink
from row_bot.memory_import.streaming import JsonStream
from row_bot.memory_import.values import as_dict, as_list, as_text, short_title, to_utc


PROVIDER = "perplexity"
_MARKERS = re.compile(r"\s?\[\d{1,3}\](?:\[\d{1,3}\])*")


def parse_conversation(item: dict[str, Any]) -> ImportedConversation | None:
    messages: list[ImportedMessage] = []
    for entry in as_list(item.get("entries")):
        entry = as_dict(entry)
        stamp = to_utc(entry.get("created_at"))
        query = as_text(entry.get("query")).strip()
        answer = _MARKERS.sub("", as_text(entry.get("answer"))).strip()  # the export keeps no sources
        if query:
            messages.append(ImportedMessage("user", query, stamp))
        if answer:
            messages.append(ImportedMessage("assistant", answer, stamp))
    title = short_title(as_text(item.get("context_title")))
    return build_conversation(PROVIDER, as_text(item.get("context_uuid")), title,
                              to_utc(item.get("created_at")), to_utc(item.get("updated_at")), messages)


def iter_conversations(member: Member, sink: Sink) -> Iterator[ImportedConversation]:
    with member.open() as raw:
        stream = JsonStream.from_bytes(raw)
        items: Iterator[Any]
        if stream.peek() == "[":
            items = stream.iter_array()
        else:
            items = _conversations_key(stream)
        for item in items:
            if isinstance(item, dict) and item:
                conversation = parse_conversation(item)
                if conversation is not None:
                    yield conversation


def _conversations_key(stream: JsonStream) -> Iterator[Any]:
    for key in stream.iter_object():
        if key == "conversations" and stream.peek() == "[":
            yield from stream.iter_array()
