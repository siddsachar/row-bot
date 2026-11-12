"""Synthetic assistant exports for the memory import tests.

Shapes follow the export research (memory-534); every name, place and line of
content is invented.
"""
from __future__ import annotations

import gzip
import io
import json
import zipfile
from pathlib import Path
from typing import Any


def write_zip(path: Path, members: dict[str, bytes | str | Any]) -> Path:
    """A zip at ``path``; ``str`` and ``bytes`` are written as is, anything else as JSON."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            if isinstance(content, str):
                content = content.encode("utf-8")
            elif not isinstance(content, bytes):
                content = json.dumps(content).encode("utf-8")
            archive.writestr(name, content)
    return path


def zip_bytes(members: dict[str, bytes | str | Any]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            if not isinstance(content, (str, bytes)):
                content = json.dumps(content)
            archive.writestr(name, content)
    return buffer.getvalue()


def gzip_lines(records: list[dict[str, Any]]) -> bytes:
    return gzip.compress("\n".join(json.dumps(record) for record in records).encode("utf-8"))


# ChatGPT ---------------------------------------------------------------------

def chatgpt_message(node_id: str, parent: str | None, role: str, text: str, when: float,
                    **extra: Any) -> dict[str, Any]:
    message = {"id": node_id, "author": {"role": role}, "create_time": when,
               "content": {"content_type": "text", "parts": [text]}}
    message.update(extra)
    return {"id": node_id, "parent": parent, "message": message}


def chatgpt_conversation(conversation_id: str, title: str, when: float, *, current_node: str | None = "c3",
                         profile: str | None = None, instructions: str | None = None) -> dict[str, Any]:
    """A chat whose first prompt was edited: u1 → c1 is abandoned, u2 → c3 is current."""
    mapping: dict[str, Any] = {"root": {"id": "root", "message": None, "parent": None}}
    if profile or instructions:
        mapping["h1"] = {"id": "h1", "parent": "root", "message": {
            "id": "h1", "author": {"role": "user"},
            "content": {"content_type": "user_editable_context",
                        "user_profile": f"The user provided the following information about themselves. "
                                        f"Treat it as context.\n```\n{profile or ''}\n```",
                        "user_instructions": "The user provided the additional info about how they would like "
                                             f"you to respond:\n```\n{instructions or ''}\n```"},
            "metadata": {"is_visually_hidden_from_conversation": True}}}
    first_parent = "h1" if "h1" in mapping else "root"
    mapping.update({
        "sys": {"id": "sys", "parent": first_parent, "message": {
            "id": "sys", "author": {"role": "system"}, "create_time": when,
            "content": {"content_type": "text", "parts": ["You are a helpful assistant."]}}},
        "u1": chatgpt_message("u1", first_parent, "user", "How often should I feed a starter kept in the fridge?",
                              when + 1),
        "c1": chatgpt_message("c1", "u1", "assistant", "Once a week is usually enough.", when + 5),
        "u2": chatgpt_message("u2", first_parent, "user", "How often should I feed a rye starter in the fridge?",
                              when + 60),
        "c3": chatgpt_message("c3", "u2", "assistant",
                              "Rye starters ferment faster citeturn0search3so every 5 days.",
                              when + 65),
    })
    conversation = {"id": conversation_id, "conversation_id": conversation_id, "title": title,
                    "create_time": when, "update_time": when + 65, "mapping": mapping,
                    "default_model_slug": "gpt-5"}
    if current_node is not None:
        conversation["current_node"] = current_node
    return conversation


def chatgpt_simple(conversation_id: str, when: float, text: str) -> dict[str, Any]:
    return {"id": conversation_id, "conversation_id": conversation_id, "title": f"Chat {conversation_id}",
            "create_time": when, "update_time": when + 1, "current_node": "a",
            "mapping": {"root": {"id": "root", "message": None, "parent": None},
                        "q": chatgpt_message("q", "root", "user", text, when),
                        "a": chatgpt_message("a", "q", "assistant", f"Answer to {text}", when + 1)}}


# Claude ----------------------------------------------------------------------

CLAUDE_ROOT = "00000000-0000-4000-8000-000000000000"


def claude_message(uuid: str, sender: str, text: str, created: str, parent: str | None = CLAUDE_ROOT,
                   blocks: list[dict[str, Any]] | None = None, plain_text: str = "") -> dict[str, Any]:
    message = {"uuid": uuid, "sender": sender, "text": plain_text,
               "content": blocks if blocks is not None else [{"type": "text", "text": text, "citations": []}],
               "attachments": [], "files": [], "created_at": created, "updated_at": created}
    if parent is not None:
        message["parent_message_uuid"] = parent
    return message


def claude_conversation() -> dict[str, Any]:
    """Two branches from a regenerated reply; m-0004 (newest) ends the current branch."""
    return {
        "uuid": "a1b2c3d4-1111-4222-8333-444455556666", "name": "Allotment planting plan", "summary": "",
        "created_at": "2026-10-03T09:12:44.512301+00:00", "updated_at": "2026-10-03T09:20:01.004512+00:00",
        "account": {"uuid": "9f8e7d6c-0000-4000-8000-123456789abc"},
        "chat_messages": [
            claude_message("m-0002", "assistant", "Broad beans and garlic.", "2026-10-03T09:12:50+00:00",
                           parent="m-0001", plain_text=""),
            claude_message("m-0001", "human", "What can I sow in October?", "2026-10-03T09:12:44+00:00",
                           blocks=[{"type": "injected_prompt_block", "injection_source": "memory_block_head",
                                    "text": "Injected memory context"},
                                   {"type": "text", "text": "What can I sow in October?"}]),
            claude_message("m-0003", "human", "And in a cold frame?", "2026-10-03T09:15:00+00:00", parent="m-0002"),
            claude_message("m-0004", "assistant", "Lettuce and spinach do well.", "2026-10-03T09:19:00+00:00",
                           parent="m-0003",
                           blocks=[{"type": "thinking", "thinking": "private reasoning"},
                                   {"type": "text", "text": "Lettuce and spinach do well."}]),
            claude_message("m-0005", "assistant", "An older answer that was replaced.", "2026-10-03T09:16:00+00:00",
                           parent="m-0003"),
        ],
    }


def claude_memories_old() -> list[dict[str, Any]]:
    return [{
        "account_uuid": "9f8e7d6c-0000-4000-8000-123456789abc",
        "conversations_memory": "**Work context**\nThe user is a nurse who works night shifts. "
                                "They mentor two students.\n\n**Personal context**\n- Lives in Porto with a partner"
                                "\n- Prefers metric units\n\n**Top of mind**\n- Planning an allotment",
        "project_memories": {"3c1d0000-0000-4000-8000-000000000001": "Planning a community garden; budget 400 pounds."},
    }]


def claude_memories_new() -> dict[str, Any]:
    return {
        "conversations_memory": "**Personal context**\n- Keeps bees on the allotment",
        "memory_files": [{"path": "/preferences.md", "updated_at": "2026-10-20T08:00:00Z",
                          "content": "---\nname: Preferences\ndescription: How the user likes answers\n---\n"
                                     "- Short answers first, detail on request\n- British spelling"}],
    }


# Others ----------------------------------------------------------------------

def gemini_record(prompt: str, html: str, when: str, chat: str | None, *, header: str = "Gemini Apps",
                  title_prefix: str = "Prompted ") -> dict[str, Any]:
    record: dict[str, Any] = {"header": header, "title": f"{title_prefix}{prompt}", "time": when,
                              "products": [header], "activityControls": [f"{header} Activity"],
                              "safeHtmlItem": [{"html": html}]}
    if chat:
        record["details"] = [{"name": "Chat", "url": f"https://gemini.google.com/app/{chat}"}]
    return record


def grok_backend() -> dict[str, Any]:
    def stamp(ms: int) -> dict[str, Any]:
        return {"$date": {"$numberLong": str(ms)}}
    return {
        "conversations": [{
            "conversation": {"id": "2b9d0000", "title": "Bike chain keeps slipping",
                             "create_time": "2026-09-10T07:31:02.114Z", "modify_time": "2026-09-10T07:40:55.000Z",
                             "summary": "", "leaf_response_id": None},
            "responses": [
                {"response": {"_id": "r2", "sender": "ASSISTANT", "message": "Usually a worn chain.<grok:render "
                              "card_id=\"x1\">card</grok:render>", "model": "grok-4",
                              "create_time": stamp(1757489470551), "parent_response_id": None}},
                {"response": {"_id": "r1", "sender": "human", "message": "My chain slips under load. Why?",
                              "model": "grok-4", "create_time": stamp(1757489470551), "parent_response_id": None}},
            ],
        }],
        "media_posts": [{"id": "p1", "original_prompt": "a fox in snow", "media_type": "image"}],
        "projects": [], "tasks": [],
    }


def deepseek_conversations() -> list[dict[str, Any]]:
    def node(node_id: str, parent: str | None, children: list[str], fragments: list[dict[str, str]] | None,
             when: str | None) -> dict[str, Any]:
        message = {"fragments": fragments, "inserted_at": when} if fragments is not None else None
        return {"id": node_id, "parent": parent, "children": children, "message": message}
    return [{
        "id": "ds-1", "title": "Tomato blight", "inserted_at": "2026-08-01T10:00:00Z",
        "updated_at": "2026-08-01T10:10:00Z",
        "mapping": {
            "root": node("root", None, ["1"], None, None),
            "1": node("1", "root", ["2", "3"], [{"type": "REQUEST", "content": "Why are my tomato leaves brown?"}],
                      "2026-08-01T10:00:00Z"),
            "2": node("2", "1", [], [{"type": "THINK", "content": "hidden"},
                                     {"type": "RESPONSE", "content": "Possibly early blight."}],
                      "2026-08-01T10:01:00Z"),
            "3": node("3", "1", [], [{"type": "SEARCH", "content": "results"},
                                     {"type": "RESPONSE", "content": "It looks like late blight; remove the leaves."}],
                      "2026-08-01T10:05:00Z"),
        },
    }]


def letta_agent_file() -> dict[str, Any]:
    return {
        "agents": [{
            "id": "agent-0", "name": "helper", "system": "You are a helper.", "block_ids": ["block-0", "block-1"],
            "messages": [
                {"id": "message-1", "role": "user", "created_at": "2026-09-01T10:00:00Z",
                 "content": [{"type": "text", "text": "Remember that I'm vegetarian."}]},
                {"id": "message-2", "role": "assistant", "created_at": "2026-09-01T10:00:03Z", "content": [],
                 "tool_calls": [{"id": "call_1", "type": "function",
                                 "function": {"name": "core_memory_append", "arguments": "{}"}}]},
                {"id": "message-3", "role": "tool", "content": [{"type": "text", "text": "OK"}]},
                {"id": "message-4", "role": "assistant", "created_at": "2026-09-01T10:00:05Z",
                 "content": [{"type": "text", "text": "Noted, no meat in any recipe."}]},
            ],
        }],
        "blocks": [
            {"id": "block-0", "label": "human", "value": "Name: Sam. Vegetarian. Lives in Bristol.", "limit": 5000},
            {"id": "block-1", "label": "persona", "value": "I am a friendly helper.", "limit": 5000},
        ],
        "tools": [{"name": "core_memory_append", "source_code": "def core_memory_append(): pass"}],
    }
