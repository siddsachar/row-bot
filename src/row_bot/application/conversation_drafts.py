"""Revisioned access to the retained thread draft owner, shared by both clients."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from row_bot.application.client_platform import ClientPlatformError, _COMMAND_LOCK


def read_draft(service: Any, conversation_id: str) -> dict:
    from row_bot import threads
    from row_bot.application.conversation_search import _require_readable
    _require_readable(service, conversation_id)
    value = threads.load_thread_draft(conversation_id) or {}
    text = str(value.get("text") or "")
    attachments = value.get("attachments") or []
    revision = hashlib.sha256(json.dumps([text, attachments], sort_keys=True).encode()).hexdigest()
    _require_readable(service, conversation_id)
    return {"conversation_id": conversation_id, "revision": revision, "text": text, "attachments": attachments}


def save_draft(service: Any, conversation_id: str, value: dict, validate: Any = None) -> dict:
    from row_bot import threads
    from row_bot.application.attachments import inspect_attachment
    with _COMMAND_LOCK, threads.checkpoint_mutation(conversation_id):
        if validate:
            validate()
        previous = read_draft(service, conversation_id)
        if any(not ref.startswith(conversation_id + ":") for ref in value["attachment_refs"]):
            raise ClientPlatformError("action_denied")
        attachments = [inspect_attachment(ref) for ref in value["attachment_refs"]]
        if previous["text"] == value["text"] and previous["attachments"] == attachments:
            return previous
        if previous["revision"] != value["expected_revision"]:
            raise ClientPlatformError("draft_revision_conflict")
        if threads._thread_write_blocked(conversation_id):
            raise ClientPlatformError("conversation_deleting")
        threads.save_thread_draft(conversation_id, value["text"], source="unified_client", attachments=attachments)
        result = read_draft(service, conversation_id)
        if result["text"] != value["text"] or result["attachments"] != attachments:
            raise ClientPlatformError("draft_save_failed")
        return result
