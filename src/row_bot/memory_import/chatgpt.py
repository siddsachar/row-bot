"""ChatGPT export: ``conversations.json`` (or its shards) and custom instructions."""
from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from row_bot.memory_import.archive import Member
from row_bot.memory_import.models import ImportedConversation, ImportedMessage, build_conversation
from row_bot.memory_import.sink import Sink
from row_bot.memory_import.streaming import JsonStream
from row_bot.memory_import.tree import current_branch
from row_bot.memory_import.values import as_dict, as_list, as_text, iso_date, to_utc


PROVIDER = "chatgpt"
PROFILE_KEY = "chatgpt:profile"
INSTRUCTIONS_KEY = "chatgpt:instructions"
_TEXT_TYPES = {"text", "multimodal_text"}
_CITATION = re.compile(r"[^]*")
_FENCE = re.compile(r"```[^\n]*\n?(.*?)```", re.DOTALL)


def _part_text(part: Any) -> str:
    if isinstance(part, str):
        return part
    part = as_dict(part)
    kind = as_text(part.get("content_type"))
    if kind.endswith("asset_pointer") or "asset_pointer" in part:
        return "[Image not included in the export]"
    return as_text(part.get("text"))


def _message_text(message: dict[str, Any]) -> str:
    content = as_dict(message.get("content"))
    if as_text(content.get("content_type")) not in _TEXT_TYPES:
        return ""
    text = "\n".join(piece for piece in (_part_text(part) for part in as_list(content.get("parts"))) if piece)
    return _CITATION.sub("", text).strip()


def _instruction_text(raw: Any) -> str:
    text = as_text(raw).strip()
    match = _FENCE.search(text)
    if match:
        return match.group(1).strip()
    lines = [line for line in text.splitlines() if not line.strip().lower().startswith("the user provided")]
    return "\n".join(lines).strip()


def _harvest_instructions(message: dict[str, Any], when: float, sink: Sink) -> None:
    content = as_dict(message.get("content"))
    context = as_dict(as_dict(message.get("metadata")).get("user_context_message_data"))
    if as_text(content.get("content_type")) == "user_editable_context":
        sink.offer_latest(PROFILE_KEY, when, _instruction_text(content.get("user_profile")))
        sink.offer_latest(INSTRUCTIONS_KEY, when, _instruction_text(content.get("user_instructions")))
    if context:
        sink.offer_latest(PROFILE_KEY, when, _instruction_text(context.get("about_user_message")))
        sink.offer_latest(INSTRUCTIONS_KEY, when, _instruction_text(context.get("about_model_message")))


def _is_visible(message: dict[str, Any]) -> bool:
    role = as_text(as_dict(message.get("author")).get("role"))
    if role not in {"user", "assistant"}:
        return False
    if as_dict(message.get("metadata")).get("is_visually_hidden_from_conversation"):
        return False
    recipient = message.get("recipient")
    return recipient in (None, "all")


def parse_conversation(item: dict[str, Any], sink: Sink) -> ImportedConversation | None:
    mapping = as_dict(item.get("mapping"))
    parents: dict[str, str | None] = {}
    times: dict[str, float | None] = {}
    created = to_utc(item.get("create_time"))
    updated = to_utc(item.get("update_time"))
    when = (updated or created).timestamp() if (updated or created) else 0.0
    for node_id, node in mapping.items():
        node = as_dict(node)
        parent = node.get("parent")
        parents[node_id] = parent if isinstance(parent, str) else None
        message = as_dict(node.get("message"))
        stamp = to_utc(message.get("create_time"))
        times[node_id] = stamp.timestamp() if stamp else None
        if message:
            _harvest_instructions(message, when, sink)
    for node_id, node in mapping.items():  # older exports: children lists only
        for child in as_list(as_dict(node).get("children")):
            if isinstance(child, str) and child in parents and parents[child] is None and child != node_id:
                parents[child] = node_id
    leaf = item.get("current_node")
    if leaf is not None and leaf not in mapping:
        sink.warn("Some ChatGPT chats point to a message that is not in the export; their newest reply was used.")
    messages: list[ImportedMessage] = []
    for node_id in current_branch(parents, times, list(mapping), leaf if isinstance(leaf, str) else None):
        message = as_dict(as_dict(mapping[node_id]).get("message"))
        if not message or not _is_visible(message):
            continue
        text = _message_text(message)
        if text:
            role = as_text(as_dict(message.get("author")).get("role"))
            messages.append(ImportedMessage(role, text, to_utc(message.get("create_time"))))
    conversation_id = as_text(item.get("conversation_id")) or as_text(item.get("id"))
    return build_conversation(PROVIDER, conversation_id, as_text(item.get("title")), created, updated, messages)


def iter_conversations(member: Member, sink: Sink) -> Iterator[ImportedConversation]:
    with member.open() as raw:
        stream = JsonStream.from_bytes(raw)
        for item in stream.iter_array():
            if isinstance(item, dict) and item:
                conversation = parse_conversation(item, sink)
                if conversation is not None:
                    yield conversation


def add_instruction_facts(sink: Sink) -> None:
    """The newest custom instructions seen in any chat, as facts tagged ``instruction``."""
    profile = sink.latest.get(PROFILE_KEY)
    if profile:
        sink.add_facts(profile.text, category="fact", source=PROVIDER, source_label="ChatGPT custom instructions",
                       ref="chatgpt:about-you", date=iso_date(profile.when) if profile.when else None,
                       subject_hint="User", tags=("instruction", "about_you"))
    instructions = sink.latest.get(INSTRUCTIONS_KEY)
    if instructions:
        sink.add_facts(instructions.text, category="preference", source=PROVIDER,
                       source_label="ChatGPT custom instructions", ref="chatgpt:how-to-respond",
                       date=iso_date(instructions.when) if instructions.when else None, tags=("instruction",))


def manifest_shards(member: Member) -> tuple[list[str], int | None]:
    """The conversation shard names an ``export_manifest.json`` lists, and its shard count."""
    with member.open() as raw:
        manifest = as_dict(JsonStream.from_bytes(raw).value())
    names: list[str] = []
    count: int | None = None
    logical = as_dict(as_dict(manifest.get("logical_files")).get("conversations.json"))
    for entry in as_list(logical.get("files")):
        name = entry if isinstance(entry, str) else as_text(as_dict(entry).get("path"))
        if name:
            names.append(name)
    if isinstance(logical.get("shard_count"), int):
        count = logical["shard_count"]
    for entry in as_list(manifest.get("export_files")):
        name = as_text(as_dict(entry).get("path"))
        if re.search(r"conversations(?:-\d+)?\.json$", name, re.IGNORECASE):
            names.append(name)
    return names, count
