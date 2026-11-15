"""Letta Agent File (``.af``): core memory blocks about the person, and agent messages.

Only blocks about the person (``human`` and similar labels) become facts;
``persona`` and other blocks describe the agent. Tools carry source code and
are never read.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from row_bot.memory_import.archive import Member
from row_bot.memory_import.models import ImportedConversation, ImportedMessage, build_conversation
from row_bot.memory_import.sink import Sink
from row_bot.memory_import.streaming import JsonStream
from row_bot.memory_import.values import as_dict, as_list, as_text, to_utc


PROVIDER = "letta"
LABEL = "Letta core memory"
_PERSON_LABEL = re.compile(r"^(?:human|user|me|about[_ -]?(?:me|user|human)|profile|preferences)$|human|user",
                           re.IGNORECASE)


def _load(member: Member) -> dict[str, Any]:
    with member.open() as raw:
        return as_dict(JsonStream.from_bytes(raw).value())


def _blocks(data: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = [as_dict(block) for block in as_list(data.get("blocks"))]
    for agent in as_list(data.get("agents")) or [data]:
        blocks += [as_dict(block) for block in as_list(as_dict(agent).get("core_memory"))]  # older v1 files
        blocks += [as_dict(block) for block in as_list(as_dict(agent).get("memory_blocks"))]
    return blocks


def read_blocks(member: Member, sink: Sink) -> None:
    data = _load(member)
    others: list[str] = []
    seen: set[str] = set()
    for index, block in enumerate(_blocks(data)):
        label = as_text(block.get("label"))
        value = as_text(block.get("value"))
        key = as_text(block.get("id")) or f"{label}:{index}"
        if key in seen or not value.strip():
            continue
        seen.add(key)
        if not _PERSON_LABEL.search(label):
            others.append(label or "unnamed")
            continue
        category = "preference" if "preference" in label.lower() else "fact"
        sink.add_facts(value, category=category, source=PROVIDER, source_label=LABEL,
                       ref=f"letta:{as_text(block.get('id')) or label}", subject_hint="User")
    if others:
        sink.skip(member.display, "Memory blocks about the agent, not you, were left out: "
                  + ", ".join(sorted(set(others))) + ".")


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(as_text(as_dict(block).get("text")) for block in as_list(content)
                     if as_text(as_dict(block).get("type")) in {"text", ""}).strip()


def iter_conversations(member: Member, sink: Sink) -> Iterator[ImportedConversation]:
    data = _load(member)
    for index, agent in enumerate(as_list(data.get("agents")) or [data]):
        agent = as_dict(agent)
        messages = []
        for message in as_list(agent.get("messages")):
            message = as_dict(message)
            role = as_text(message.get("role"))
            text = _text(message.get("content")) or as_text(message.get("text"))
            if role in {"user", "assistant"} and text.strip():
                messages.append(ImportedMessage(role, text.strip(), to_utc(message.get("created_at"))))
        name = as_text(agent.get("name"))
        # Ids are local to the file (agent-0, agent-1), so the name goes into the key too.
        conversation = build_conversation(PROVIDER, f"{as_text(agent.get('id')) or f'agent-{index}'}:{name}",
                                          f"Letta agent {name}" if name else None, None, None, messages)
        if conversation is not None:
            yield conversation
