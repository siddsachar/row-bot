"""DeepSeek export: ``conversations.json`` with a ``mapping`` tree of message fragments.

Every branch is kept and there is no current-leaf pointer, so the newest leaf
is used. Roles come from fragment types (``REQUEST`` and ``RESPONSE``).
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from row_bot.memory_import.archive import Member
from row_bot.memory_import.models import ImportedConversation, ImportedMessage, build_conversation
from row_bot.memory_import.sink import Sink
from row_bot.memory_import.streaming import JsonStream
from row_bot.memory_import.tree import current_branch
from row_bot.memory_import.values import as_dict, as_list, as_text, to_utc


PROVIDER = "deepseek"


def _message(message: dict[str, Any]) -> tuple[str, str] | None:
    role = None
    pieces: list[str] = []
    for fragment in as_list(message.get("fragments")):
        fragment = as_dict(fragment)
        kind = as_text(fragment.get("type")).upper()
        if kind == "REQUEST":
            role = "user"
            pieces.append(as_text(fragment.get("content")))
        elif kind == "RESPONSE":
            role = role or "assistant"
            pieces.append(as_text(fragment.get("content")))
        elif kind == "FILE":
            name = as_text(fragment.get("file_name")) or as_text(fragment.get("name"))
            pieces.append(f"[File not included in the import: {name}]" if name else "")
    if role is None:
        return None
    text = "\n".join(piece for piece in pieces if piece).strip()
    return (role, text) if text else None


def parse_conversation(item: dict[str, Any]) -> ImportedConversation | None:
    mapping = as_dict(item.get("mapping"))
    parents: dict[str, str | None] = {}
    times: dict[str, float | None] = {}
    for node_id, node in mapping.items():
        node = as_dict(node)
        parent = node.get("parent")
        parents[node_id] = parent if isinstance(parent, str) else None
        stamp = to_utc(as_dict(node.get("message")).get("inserted_at"))
        times[node_id] = stamp.timestamp() if stamp else None
    for node_id, node in mapping.items():
        for child in as_list(as_dict(node).get("children")):
            if isinstance(child, str) and child in parents and parents[child] is None and child != node_id:
                parents[child] = node_id
    messages: list[ImportedMessage] = []
    for node_id in current_branch(parents, times, list(mapping)):
        message = as_dict(as_dict(mapping[node_id]).get("message"))
        found = _message(message) if message else None
        if found:
            messages.append(ImportedMessage(found[0], found[1], to_utc(message.get("inserted_at"))))
    return build_conversation(PROVIDER, as_text(item.get("id")), as_text(item.get("title")),
                              to_utc(item.get("inserted_at")), to_utc(item.get("updated_at")), messages)


def iter_conversations(member: Member, sink: Sink) -> Iterator[ImportedConversation]:
    with member.open() as raw:
        for item in JsonStream.from_bytes(raw).iter_array():
            if isinstance(item, dict) and item:
                conversation = parse_conversation(item)
                if conversation is not None:
                    yield conversation
