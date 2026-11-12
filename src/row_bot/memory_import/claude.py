"""Claude export: ``conversations.json``, ``memories.json`` / ``memories/<account>.json``, the manifest."""
from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from row_bot.memory_import.archive import Member
from row_bot.memory_import.markdown import memory_sections
from row_bot.memory_import.models import ImportedConversation, ImportedMessage, build_conversation
from row_bot.memory_import.sink import Sink, category_for_heading
from row_bot.memory_import.streaming import JsonStream
from row_bot.memory_import.tree import current_branch
from row_bot.memory_import.values import as_dict, as_list, as_text, iso_date, to_utc


PROVIDER = "claude"
MEMORY_LABEL = "Claude memory"
ROOT_PARENT = "00000000-0000-4000-8000-000000000000"
_SENDERS = {"human": "user", "user": "user", "assistant": "assistant"}
_SKIPPED_BLOCKS = {
    "thinking", "redacted_thinking", "tool_use", "tool_result", "server_tool_use", "web_search_tool_result",
    "injected_prompt_block", "token_budget", "voice_note", "image", "document",
}
_FRONT_MATTER = re.compile(r"\A\s*---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)


def _message_text(message: dict[str, Any], sink: Sink) -> str:
    pieces: list[str] = []
    for block in as_list(message.get("content")):
        block = as_dict(block)
        kind = as_text(block.get("type"))
        if kind == "text":
            pieces.append(as_text(block.get("text")))
        elif kind not in _SKIPPED_BLOCKS:
            sink.warn("Some Claude messages hold parts this version does not know; those parts were left out.")
    text = "\n".join(piece for piece in pieces if piece).strip()
    if not text and not as_list(message.get("content")):
        text = as_text(message.get("text")).strip()
    names = [as_text(as_dict(item).get("file_name")) for item in
             as_list(message.get("attachments")) + as_list(message.get("files"))]
    notes = [f"[File not included in the import: {name}]" for name in names if name]
    if text and notes:
        text = "\n".join([text, *notes])
    return text


def parse_conversation(item: dict[str, Any], sink: Sink) -> ImportedConversation | None:
    raw_messages = [as_dict(m) for m in as_list(item.get("chat_messages") or item.get("messages"))]
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, message in enumerate(raw_messages):
        message_id = as_text(message.get("uuid")) or f"#{index}"
        by_id[message_id] = message
        order.append(message_id)
    if any("parent_message_uuid" in message for message in raw_messages):
        parents: dict[str, str | None] = {}
        for message_id, message in by_id.items():
            parent = message.get("parent_message_uuid")
            parents[message_id] = parent if isinstance(parent, str) and parent != ROOT_PARENT else None
        times = {}
        for message_id, message in by_id.items():
            stamp = to_utc(message.get("created_at"))
            times[message_id] = stamp.timestamp() if stamp else None
        chosen = current_branch(parents, times, order)
    else:
        def sort_key(pair: tuple[int, str]) -> tuple[float, int]:
            stamp = to_utc(by_id[pair[1]].get("created_at"))
            return (stamp.timestamp() if stamp else float("inf"), pair[0])
        chosen = [message_id for _, message_id in sorted(enumerate(order), key=sort_key)]
    messages: list[ImportedMessage] = []
    for message_id in chosen:
        message = by_id[message_id]
        role = _SENDERS.get(as_text(message.get("sender")).lower())
        if role is None:
            continue
        text = _message_text(message, sink)
        if text:
            messages.append(ImportedMessage(role, text, to_utc(message.get("created_at"))))
    return build_conversation(PROVIDER, as_text(item.get("uuid")), as_text(item.get("name")),
                              to_utc(item.get("created_at")), to_utc(item.get("updated_at")), messages)


def iter_conversations(member: Member, sink: Sink) -> Iterator[ImportedConversation]:
    with member.open() as raw:
        for item in JsonStream.from_bytes(raw).iter_array():
            if isinstance(item, dict) and item:
                conversation = parse_conversation(item, sink)
                if conversation is not None:
                    yield conversation


def _add_markdown(text: str, sink: Sink, *, ref: str, date: str | None = None,
                  default_category: str | None = None) -> None:
    for heading, line in memory_sections(text):
        category, tags = category_for_heading(heading)
        if default_category and category == "fact" and not heading:
            category = default_category
        sink.add_facts(line, category=category, source=PROVIDER, source_label=MEMORY_LABEL, ref=ref,
                       date=date, tags=tags)


def _memory_file(entry: dict[str, Any], sink: Sink) -> None:
    content = as_text(entry.get("content"))
    path = as_text(entry.get("path"))
    match = _FRONT_MATTER.match(content)
    if match:
        content = content[match.end():]
    lowered = path.lower()
    category = "preference" if "preference" in lowered else "project" if "/topics/" in lowered else None
    _add_markdown(content, sink, ref=f"claude:memory-file:{path}", date=iso_date(entry.get("updated_at")),
                  default_category=category)


def read_memories(member: Member, sink: Sink) -> None:
    with member.open() as raw:
        data = JsonStream.from_bytes(raw).value()
    for record in as_list(data) if isinstance(data, list) else [data]:
        record = as_dict(record)
        _add_markdown(as_text(record.get("conversations_memory")), sink, ref="claude:memory")
        for project_id, text in as_dict(record.get("project_memories")).items():
            _add_markdown(as_text(text), sink, ref=f"claude:project-memory:{project_id}", default_category="project")
        for entry in as_list(record.get("memory_files")):
            _memory_file(as_dict(entry), sink)


def note_manifest(member: Member, sink: Sink) -> None:
    """A manifest only links to the export's zips; list them for the person to download."""
    with member.open() as raw:
        manifest = as_dict(JsonStream.from_bytes(raw).value())
    for entry in as_list(manifest.get("data_files")):
        entry = as_dict(entry)
        name = as_text(entry.get("filename")) or f"{as_text(entry.get('category')) or 'data'} file"
        sink.skip(name, "Only linked from Claude's export manifest. Download it from the link in Anthropic's email "
                        "and add the zip here; Row-Bot does not download it.")
    sink.warn("The Claude file you added is the export's list of downloads, not the data. Download the "
              "conversations and memories zips from the email link and add them.")
