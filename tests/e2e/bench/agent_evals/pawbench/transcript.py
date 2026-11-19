"""A Row-Bot conversation as PawBench's transcript: OpenClaw session JSONL in ``<workspace>/sessions/``.

PawBench's grader reads ``<workspace>/sessions/*.jsonl`` (``pawbench/agents/transcript.py``,
``_openclaw_session_jsonl_events``): one JSON object per line, ``type`` ``session`` first, then
``message`` events whose ``message`` has a ``role`` (``user``, ``assistant``, ``toolResult``) and
``content`` blocks (``text``; ``toolCall`` with ``name`` and ``arguments``). Standard library only: the
in-container worker and the tests both use it.
"""
from __future__ import annotations

import json
from typing import Any, Iterable


def _text(content: Any) -> str:
    if isinstance(content, list):
        return "".join(str(part.get("text") or "") if isinstance(part, dict) else str(part) for part in content)
    return str(content or "")


def events(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Plain message dicts (``role``: ``user``/``assistant``/``tool``; ``content``; ``tool_calls`` with
    ``name`` and ``args``) as PawBench transcript events. System messages are left out, as PawBench's own
    converters do (the system prompt is prepended separately)."""
    found: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "")
        text = _text(message.get("content"))
        if role == "user" and text:
            found.append({"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": text}]}})
        elif role == "assistant":
            blocks: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
            for call in message.get("tool_calls") or []:
                blocks.append({"type": "toolCall", "name": str(call.get("name") or ""),
                               "arguments": dict(call.get("args") or call.get("arguments") or {})})
            if blocks:
                found.append({"type": "message", "message": {"role": "assistant", "content": blocks}})
        elif role == "tool":
            found.append({"type": "message", "message": {"role": "toolResult",
                                                         "content": [{"type": "text", "text": text}]}})
    return found


def session_jsonl(session_id: str, messages: Iterable[dict[str, Any]], *, model: str, cwd: str) -> str:
    header = {"type": "session", "version": 3, "id": session_id, "cwd": cwd, "harness": "row-bot", "model": model}
    return "\n".join(json.dumps(line, ensure_ascii=False) for line in [header, *events(messages)]) + "\n"


def from_langchain(messages: Iterable[Any]) -> list[dict[str, Any]]:
    """A LangGraph checkpoint's messages (``threads.get_latest_checkpoint_messages``) as plain dicts."""
    roles = {"human": "user", "ai": "assistant", "tool": "tool", "system": "system"}
    return [{"role": roles.get(str(getattr(message, "type", "")), "other"), "content": getattr(message, "content", ""),
             "tool_calls": [{"name": call.get("name"), "args": call.get("args")}
                            for call in getattr(message, "tool_calls", None) or []]}
            for message in messages]
