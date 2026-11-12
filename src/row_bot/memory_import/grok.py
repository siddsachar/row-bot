"""Grok export: ``prod-grok-backend.json`` with conversations and their responses."""
from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from row_bot.memory_import.archive import Member
from row_bot.memory_import.models import ImportedConversation, ImportedMessage, build_conversation
from row_bot.memory_import.sink import Sink
from row_bot.memory_import.streaming import JsonStream
from row_bot.memory_import.tree import current_branch
from row_bot.memory_import.values import as_dict, as_list, as_text, to_utc


PROVIDER = "grok"
_ROLES = {"human": "user", "user": "user", "assistant": "assistant"}
_RENDER = re.compile(r"<grok:render\b[^>]*?(?:/>|>.*?</grok:render>)", re.DOTALL)
_ARTIFACT_TAG = re.compile(r"</?xaiArtifact\b[^>]*>")


def _clean(text: str) -> str:
    return _ARTIFACT_TAG.sub("", _RENDER.sub("", text)).strip()


def parse_conversation(item: dict[str, Any]) -> ImportedConversation | None:
    head = as_dict(item.get("conversation"))
    responses: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, wrapper in enumerate(as_list(item.get("responses"))):
        response = as_dict(as_dict(wrapper).get("response") or wrapper)
        response_id = as_text(response.get("_id")) or as_text(response.get("id")) or f"#{index}"
        responses[response_id] = response
        order.append(response_id)

    times: dict[str, float | None] = {}
    for response_id, response in responses.items():
        moment = to_utc(response.get("create_time"))
        times[response_id] = moment.timestamp() if moment else None

    if any(as_text(response.get("parent_response_id")) for response in responses.values()):
        parents = {rid: (as_text(r.get("parent_response_id")) or None) for rid, r in responses.items()}
        chosen = current_branch(parents, times, order, as_text(head.get("leaf_response_id")) or None)
    else:  # time order, the person's message first when times tie
        position = {rid: index for index, rid in enumerate(order)}
        chosen = sorted(order, key=lambda rid: (
            times[rid] if times[rid] is not None else float("inf"),
            0 if as_text(responses[rid].get("sender")).lower() == "human" else 1,
            position[rid],
        ))
    messages: list[ImportedMessage] = []
    for response_id in chosen:
        response = responses[response_id]
        role = _ROLES.get(as_text(response.get("sender")).lower())  # never infer the role from ``model``
        text = _clean(as_text(response.get("message")))
        if role and text:
            messages.append(ImportedMessage(role, text, to_utc(response.get("create_time"))))
    return build_conversation(PROVIDER, as_text(head.get("id")), as_text(head.get("title")),
                              to_utc(head.get("create_time")), to_utc(head.get("modify_time")), messages)


def iter_conversations(member: Member, sink: Sink) -> Iterator[ImportedConversation]:
    with member.open() as raw:
        stream = JsonStream.from_bytes(raw)
        for key in stream.iter_object():
            if key == "conversations" and stream.peek() == "[":
                for item in stream.iter_array():
                    if isinstance(item, dict) and item:
                        conversation = parse_conversation(item)
                        if conversation is not None:
                            yield conversation
            elif key == "media_posts" and stream.peek() == "[":
                posts = sum(1 for _ in stream.iter_array())
                if posts:
                    sink.skip(f"{member.display} (media_posts)",
                              f"{posts} Grok Imagine posts are pictures, not chats, and were left out.")
