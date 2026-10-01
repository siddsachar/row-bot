"""Revisioned access to the retained thread draft owner, shared by both clients."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from row_bot.application.client_platform import ClientPlatformError, _COMMAND_LOCK

# conversation -> (draft revision the server consumed, digest of what was sent)
_CONSUMED: dict[str, tuple[str, str]] = {}


def _digest(text: str, attachment_refs: list[str]) -> str:
    return hashlib.sha256(json.dumps([text.strip(), list(attachment_refs)]).encode()).hexdigest()


def consume_admitted_draft(service: Any, conversation_id: str, text: str, attachment_refs: list[str]) -> bool:
    """A message the server admitted is no longer a draft (B151).

    The client clears its draft once the send is confirmed, but that clear
    can be lost (the page lost its server, a reload): the message and its
    files then came back as a draft and sending it again duplicated it. The
    server clears the draft itself when it admits the same text and files,
    and remembers that, so a save of the sent text still in flight stays
    ignored and an edit based on it continues from the empty draft.
    """
    from row_bot import threads
    with threads.checkpoint_mutation(conversation_id):
        current = read_draft(service, conversation_id)
        refs = [str(item.get("attachment_ref") or "") for item in current["attachments"]]
        if not current["text"].strip() and not refs:
            return False
        if current["text"].strip() != str(text or "").strip() or refs != list(attachment_refs):
            return False
        threads.delete_thread_draft(conversation_id)
        _CONSUMED[conversation_id] = (current["revision"], _digest(current["text"], refs))
        return True


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
        consumed = _CONSUMED.get(conversation_id)
        if (consumed is not None and value["expected_revision"] == consumed[0]
                and not previous["text"] and not previous["attachments"]):
            if _digest(value["text"], list(value["attachment_refs"])) == consumed[1]:
                return previous  # The sent message's own save, arriving late.
            # An edit made from the draft that was just sent continues here.
        elif previous["revision"] != value["expected_revision"]:
            raise ClientPlatformError("draft_revision_conflict")
        if threads._thread_write_blocked(conversation_id):
            raise ClientPlatformError("conversation_deleting")
        threads.save_thread_draft(conversation_id, value["text"], source="unified_client", attachments=attachments)
        _CONSUMED.pop(conversation_id, None)
        result = read_draft(service, conversation_id)
        if result["text"] != value["text"] or result["attachments"] != attachments:
            raise ClientPlatformError("draft_save_failed")
        return result
