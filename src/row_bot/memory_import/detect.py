"""What a file is, from its bytes and structure. A file name only ever explains a skip."""
from __future__ import annotations

import re
from typing import Any

from row_bot.memory_import.archive import Member, normal_name, sniff
from row_bot.memory_import.copilot import is_copilot_csv
from row_bot.memory_import.streaming import JsonFormatError, JsonStream
from row_bot.memory_import.values import as_dict, as_list


ACCOUNT_NAMES = re.compile(
    r"^(?:user|users|user-settings|login-history|account|profile)\.json$|^prod-mc-.*\.json$|^user-data-.*",
)
_SNIFF_KEYS = 40
_SNIFF_ITEMS = 5


def _record_kind(item: dict[str, Any]) -> str | None:
    """The kind of a top-level array of records like ``item``."""
    if "mapping" in item:
        nodes = list(as_dict(item.get("mapping")).values())[:20]
        if "inserted_at" in item or any("fragments" in as_dict(as_dict(node).get("message")) for node in nodes):
            return "deepseek"
        return "chatgpt"
    if "chat_messages" in item or ("uuid" in item and any(
            "sender" in as_dict(message) for message in as_list(item.get("messages"))[:5])):
        return "claude_conversations"
    if {"conversations_memory", "memory_files", "project_memories"} & item.keys():
        return "claude_memory"
    if isinstance(item.get("memory"), str) and ({"hash", "user_id", "agent_id", "id"} & item.keys()):
        return "mem0"
    if "safeHtmlItem" in item or "activityControls" in item or {"header", "time", "title"} <= item.keys():
        return "gemini"
    if "context_uuid" in item and "entries" in item:
        return "perplexity"
    if "conversation" in item and "responses" in item:
        return "grok"
    if isinstance(item.get("content"), str) and "state" in item and "id" in item:
        return "openmemory_lines"
    if "prompt_template" in item or "is_starter_project" in item:
        return "claude_projects"
    if {"email_address", "full_name", "verified_phone_number", "email", "phone_number", "ip_address"} & item.keys():
        return "account"
    return None


def _object_kind(seen: dict[str, Any], *, final: bool) -> str | None:
    first = {key: as_dict(value) for key, value in seen.items()}
    if "data_files" in seen and ({"export_url", "category", "filename"} & first["data_files"].keys()):
        return "claude_manifest"
    if "conversations" in seen:
        kind = _record_kind(first["conversations"]) if first["conversations"] else None
        if kind in {"perplexity", "grok"}:
            return kind
    if "results" in seen and isinstance(first["results"].get("memory"), str):
        return "mem0"
    if "memories" in seen:
        record = first["memories"]
        if isinstance(record.get("memory"), str):
            return "mem0"
        if "content" in record or "export_meta" in seen or "access_controls" in seen:
            return "openmemory"
    if "agents" in seen and ({"block_ids", "core_memory", "messages", "llm_config", "system"} & first["agents"].keys()):
        return "letta"
    if "agents" in seen and "blocks" in seen:
        return "letta"
    if {"conversations_memory", "memory_files", "project_memories"} & seen.keys():
        return "claude_memory"
    if "logical_files" in seen or "export_files" in seen:
        return "chatgpt_manifest"
    if isinstance(seen.get("content"), str) and "state" in seen and "id" in seen:
        return "openmemory_lines"
    if final:
        if "mapping" in seen and "current_node" in seen:
            return "chatgpt_single"
        if {"email", "email_address", "phone_number", "birth_year"} & seen.keys():
            return "account"
    return None


def _classify_json(member: Member) -> str:
    with member.open() as raw:
        stream = JsonStream.from_bytes(raw)
        start = stream.peek()
        if start == "[":
            for count, item in enumerate(stream.iter_array()):
                if isinstance(item, dict) and item:
                    return _record_kind(item) or "unknown"
                if count >= _SNIFF_ITEMS:
                    break
            return "empty"
        seen: dict[str, Any] = {}
        for count, key in enumerate(stream.iter_object()):
            if stream.peek() == "[":
                items = stream.iter_array()
                seen[key] = next(items, None)
                kind = _object_kind(seen, final=False)
                if kind:
                    return kind
                for _ in items:  # pass over the rest without holding it
                    pass
            else:
                seen[key] = stream.value()
            kind = _object_kind(seen, final=False)
            if kind:
                return kind
            if count >= _SNIFF_KEYS:
                break
        return _object_kind(seen, final=True) or "unknown"


def classify(member: Member) -> str:
    """One of the reader kinds, or ``account``, ``html``, ``text``, ``empty`` or ``unknown``."""
    kind = sniff(member.head)
    if ACCOUNT_NAMES.match(member.base) or member.base.endswith(".xlsx"):
        return "account"
    if kind == "json":
        try:
            return _classify_json(member)
        except (JsonFormatError, UnicodeError):
            return "broken_json"
    if kind == "text":
        return "copilot" if is_copilot_csv(member.head) else "text"
    if kind == "html":
        return "html"
    return "unknown"


def shard_number(member: Member) -> int:
    match = re.search(r"(\d+)\.json$", normal_name(member.name))
    return int(match.group(1)) if match else -1
