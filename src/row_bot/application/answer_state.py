"""The person's own state on a reply's components (F3): checklist ticks, which choice they sent, and what
they entered on a surface (generative UI: its edited data model).

It is kept beside the chat (``thread_ui/<id>.answers.json``), never in the transcript, so a tick never
reaches the model by itself; a choice reaches it only as the message the person's tap sent. Each change
is checked against the revision the client read, so two devices can't overwrite each other unseen, and
the whole state stays under ``MAX_STATE_BYTES``.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from row_bot.application.client_platform import ClientPlatformError, _COMMAND_LOCK

MAX_STATE_BYTES = 512 * 1024
BLOCK_ID = re.compile(r"^block:[0-9a-f]{24}$")
ITEM_ID = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
MAX_CHOICE = 40


def _path(conversation_id: str) -> Path:
    from row_bot import threads
    from row_bot.thread_cleanup import resolve_managed_path

    return resolve_managed_path(threads._THREAD_UI_DIR, f"{conversation_id}.answers.json")  # noqa: SLF001


def _load(conversation_id: str) -> dict[str, Any]:
    path = _path(conversation_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        value = {}
    ticks = value.get("ticks") if isinstance(value, dict) else None
    choices = value.get("choices") if isinstance(value, dict) else None
    surfaces = value.get("surfaces") if isinstance(value, dict) else None
    return {
        "ticks": {
            block: {item: done for item, done in items.items() if isinstance(done, bool)}
            for block, items in (ticks or {}).items()
            if isinstance(items, dict)
        },
        "choices": {block: label for block, label in (choices or {}).items() if isinstance(label, str)},
        "surfaces": {block: data for block, data in (surfaces or {}).items() if isinstance(data, dict)},
    }


def _revision(state: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_answer_state(service: Any, conversation_id: str) -> dict[str, Any]:
    from row_bot.application.conversation_search import _require_readable

    _require_readable(service, conversation_id)
    state = _load(conversation_id)
    return {"conversation_id": conversation_id, "revision": _revision(state), **state}


def save_answer_state(service: Any, conversation_id: str, change: dict[str, Any], validate: Any = None) -> dict[str, Any]:
    """Apply one change (a tick, or the choice sent) if the client read the current state."""

    from row_bot import threads
    from row_bot.application.conversation_search import _require_readable

    block = str(change.get("block_id") or "")
    if not BLOCK_ID.match(block):
        raise ClientPlatformError("invalid_command")
    with _COMMAND_LOCK, threads.checkpoint_mutation(conversation_id):
        if validate:
            validate()
        _require_readable(service, conversation_id)
        state = _load(conversation_id)
        if _revision(state) != change.get("expected_revision"):
            raise ClientPlatformError("answer_state_revision_conflict")
        if change.get("surface_data") is not None:
            # A surface's data model as the person left it: replaced whole, never merged.
            if not isinstance(change["surface_data"], dict):
                raise ClientPlatformError("invalid_command")
            state["surfaces"][block] = change["surface_data"]
        elif change.get("choice") is not None:
            choice = str(change["choice"])
            if not choice or len(choice) > MAX_CHOICE:
                raise ClientPlatformError("invalid_command")
            # One choice per block: the buttons it sent from stay as they were.
            if state["choices"].get(block, choice) != choice:
                raise ClientPlatformError("answer_choice_taken")
            state["choices"][block] = choice
        else:
            item = str(change.get("item_id") or "")
            if not ITEM_ID.match(item) or not isinstance(change.get("done"), bool):
                raise ClientPlatformError("invalid_command")
            state["ticks"].setdefault(block, {})[item] = change["done"]
        text = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(text.encode("utf-8")) > MAX_STATE_BYTES:
            raise ClientPlatformError("answer_state_full")
        if threads._thread_write_blocked(conversation_id):  # noqa: SLF001
            raise ClientPlatformError("conversation_deleting")
        path = _path(conversation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(text.encode("utf-8"))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return {"conversation_id": conversation_id, "revision": _revision(state), **state}
