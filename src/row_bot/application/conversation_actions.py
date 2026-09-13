"""Bounded conversation action views and reviewed local exports.

Rename and pin continue through :class:`ClientPlatformService`, which owns
their revision CAS and durable idempotency.  Conversation exports read only
the public user/assistant projection from a pinned checkpoint and publish an
opaque attachment under the existing conversation media owner.  Row-Bot has
no canonical archive state, so archive is reported as unavailable rather than
being confused with destructive deletion.
"""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions


_ACTIONS = {"conversation.rename", "conversation.pin", "conversation.archive", "conversation.export"}
_EXECUTABLE = {"conversation.rename", "conversation.pin", "conversation.export"}
_MAX_EXPORT_BYTES = 8 * 1024 * 1024
_MAX_EXPORT_MESSAGES = 20_000


class ConversationActionError(ValueError):
    """Stable public failure for the conversation action adapter."""

    def __init__(self, code: str, current_revision: str | None = None) -> None:
        self.code = code
        self.current_revision = current_revision
        super().__init__(code)


def _error(code: str, current_revision: str | None = None) -> ConversationActionError:
    return ConversationActionError(code, current_revision)


def _uuid(value: object) -> str:
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise _error("invalid_conversation_action") from None
    return value


def _metadata(service: Any, conversation_id: str, validate: Callable[[], None]) -> dict[str, Any]:
    validate()
    try:
        row = service._metadata(conversation_id)
    except ClientPlatformError as exc:
        raise _error(exc.code, exc.current_revision) from exc
    try:
        from row_bot.thread_cleanup import is_thread_deleting

        if is_thread_deleting(conversation_id, initialized_read=True):
            raise _error("conversation_deleting")
    except ConversationActionError:
        raise
    except Exception:
        raise _error("conversation_state_unavailable") from None
    validate()
    return row


def _checkpoint_revision(conversation_id: str) -> str:
    from row_bot import threads

    try:
        return threads.get_latest_checkpoint_revision(conversation_id)
    except Exception:
        raise _error("conversation_transcript_unavailable") from None


def _snapshot(service: Any, conversation_id: str, validate: Callable[[], None]) -> dict[str, Any]:
    row = _metadata(service, conversation_id, validate)
    revision = str(row["client_revision"])
    checkpoint_revision = _checkpoint_revision(conversation_id)
    validate()
    return {
        "schema_version": 1,
        "conversation_id": conversation_id,
        "revision": revision,
        "checkpoint_revision": checkpoint_revision,
        "title": str(row.get("name") or "")[:256],
        "pinned": bool(row.get("pinned_at")),
        "capabilities": {
            "rename": {"available": True, "code": None},
            "pin": {"available": True, "code": None},
            "archive": {"available": False, "code": "conversation_archive_unavailable"},
            "export": {"available": True, "code": None},
        },
    }


def read_conversation_actions(service: Any, conversation_id: str, *,
                              validate: Callable[[], None]) -> dict[str, Any]:
    """Return bounded local action state without loading transcript content."""
    if type(conversation_id) is not str or not conversation_id or len(conversation_id) > 128:
        raise _error("not_found")
    return _snapshot(service, conversation_id, validate)


def _review_payload(action: str, payload: object) -> dict[str, Any]:
    if action not in _ACTIONS or type(payload) is not dict:
        raise _error("invalid_conversation_action")
    if action == "conversation.archive":
        raise _error("conversation_archive_unavailable")
    expected = {"title"} if action == "conversation.rename" else {"pinned"} if action == "conversation.pin" else set()
    if set(payload) != expected:
        raise _error("invalid_conversation_action")
    if action == "conversation.rename":
        title = payload.get("title")
        if type(title) is not str or not title.strip() or len(title) > 120 or "\x00" in title:
            raise _error("invalid_conversation_action")
        return {"title": title.strip()}
    if action == "conversation.pin":
        if type(payload.get("pinned")) is not bool:
            raise _error("invalid_conversation_action")
        return {"pinned": payload["pinned"]}
    return {}


def review_conversation_action(service: Any, conversation_id: str, action: str,
                               expected_revision: str, payload: dict[str, Any], *,
                               validate: Callable[[], None]) -> dict[str, Any]:
    """Review one exact action against the current conversation identities."""
    validate()
    fields = _review_payload(action, payload)
    snapshot = read_conversation_actions(service, conversation_id, validate=validate)
    if type(expected_revision) is not str or expected_revision != snapshot["revision"]:
        raise _error("revision_conflict", snapshot["revision"])
    if action == "conversation.export":
        fields = {"title": snapshot["title"]}
    intent = {
        "conversation_id": conversation_id,
        "action": action,
        "revision": snapshot["revision"],
        "checkpoint_revision": snapshot["checkpoint_revision"],
        "fields": fields,
    }
    digest = admissions.keyed_digest(intent)
    summaries = {
        "conversation.rename": "Rename this conversation while retaining its history and resources.",
        "conversation.pin": "Pin this conversation." if fields.get("pinned") else "Unpin this conversation.",
        "conversation.export": "Create a local Markdown copy of the saved user and assistant transcript.",
    }
    validate()
    return {
        "schema_version": 1,
        **intent,
        "action_digest": digest,
        "summary": summaries[action],
        "disclosures": (["The export excludes system instructions and tool-internal messages. A local copy is retained with this conversation."]
                        if action == "conversation.export" else []),
    }


def _export_markdown(conversation_id: str, title: str, checkpoint_revision: str, *,
                     validate: Callable[[], None]) -> bytes:
    from row_bot.runtime.checkpoint_reader import open_checkpoint

    heading = " ".join(str(title or "Conversation").replace("\x00", "").splitlines()).strip()[:256]
    output = bytearray((f"# {heading or 'Conversation'}\n\n" +
                        "_Exported from the saved Row-Bot conversation._\n\n").encode("utf-8"))

    def append(value: str) -> None:
        encoded = value.encode("utf-8")
        if len(output) + len(encoded) > _MAX_EXPORT_BYTES:
            raise _error("conversation_export_too_large")
        output.extend(encoded)

    validate()
    count = 0
    try:
        with open_checkpoint(conversation_id, checkpoint_revision) as reader:
            if checkpoint_revision and reader is None:
                raise _error("conversation_transcript_changed")
            if reader is not None:
                for _index, record in reader.records():
                    if record["role"] not in {"user", "assistant"}:
                        continue
                    count += 1
                    if count > _MAX_EXPORT_MESSAGES:
                        raise _error("conversation_export_too_large")
                    if count % 32 == 1:
                        validate()
                    append("## You\n\n" if record["role"] == "user" else "## Row-Bot\n\n")
                    for chunk in reader.public_text_chunks(record):
                        append(chunk)
                    append("\n")
    except ConversationActionError:
        raise
    except Exception:
        raise _error("conversation_transcript_unavailable") from None
    validate()
    return bytes(output)


def _publish_export(conversation_id: str, command_id: str, data: bytes,
                    checkpoint_revision: str, *,
                    validate: Callable[[], None]) -> dict[str, Any]:
    """Create or recover the deterministic attachment for one export command."""
    from row_bot import threads
    from row_bot.application import attachments

    attachment_id = str(uuid5(UUID(command_id), "conversation-export"))
    reference = f"{conversation_id}:{attachment_id}"
    name = "conversation-export.md"
    folder = Path(threads._MEDIA_DIR) / conversation_id
    content_path = folder / f"attachment_{attachment_id}.bin"
    metadata_path = folder / f"attachment_{attachment_id}.json"
    metadata = {
        "attachment_ref": reference,
        "name": name,
        "mime_type": "application/octet-stream",
        "size_bytes": len(data),
        "revision": "1",
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    with attachments._LOCK:
        validate()
        attachments._conversation(conversation_id)
        attachments._safe_path(Path(threads._MEDIA_DIR), folder)
        folder.mkdir(parents=True, exist_ok=True)
        attachments._safe_path(Path(threads._MEDIA_DIR), folder)
        if content_path.exists():
            with attachments._open(Path(threads._MEDIA_DIR), content_path) as source:
                existing = source.read(_MAX_EXPORT_BYTES + 1)
            if existing != data:
                raise _error("conversation_export_unconfirmed")
        else:
            attachments._write(Path(threads._MEDIA_DIR), content_path, data)
        validate()
        if metadata_path.exists():
            try:
                saved, existing = attachments.read_attachment(reference)
            except Exception:
                raise _error("conversation_export_unconfirmed") from None
            if existing != data or saved != {key: metadata[key] for key in ("attachment_ref", "name", "mime_type", "size_bytes", "revision")}:
                raise _error("conversation_export_unconfirmed")
        else:
            attachments._write(Path(threads._MEDIA_DIR), metadata_path,
                               json.dumps(metadata, separators=(",", ":")).encode("utf-8"))
        validate()
    return {
        "attachment_ref": reference,
        "file_name": name,
        "size_bytes": len(data),
        "checkpoint_revision": checkpoint_revision,
    }


def _public_receipt(value: dict[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(value[key]) for key in
            ("command_id", "status", "code", "action", "conversation", "export") if key in value}


def read_conversation_action_receipt(service: Any, conversation_id: str, command_id: str, *,
                                     owner_id: str, validate: Callable[[], None]) -> dict[str, Any] | None:
    """Read only receipts owned by this conversation action surface."""
    validate()
    command_id = _uuid(command_id)
    metadata = admissions.read_command_metadata(owner_id, command_id)
    if metadata is None or metadata["target"] != conversation_id or metadata["type"] not in _EXECUTABLE:
        validate()
        return None
    receipt = admissions.read_command_receipt(owner_id, command_id)
    if receipt is None:
        return None
    action = metadata["type"]
    if metadata["status"] not in {"completed", "rejected"}:
        receipt = {"command_id": command_id, "status": "partial",
                   "code": "conversation_action_unconfirmed", "action": action}
    elif action in {"conversation.rename", "conversation.pin"} and receipt.get("status") == "completed":
        snapshot = read_conversation_actions(service, conversation_id, validate=validate)
        receipt = {"command_id": command_id, "status": "completed", "action": action,
                   "conversation": {key: snapshot[key] for key in ("conversation_id", "revision", "title", "pinned")}}
    else:
        receipt["action"] = action
    validate()
    return _public_receipt(receipt)


def execute_conversation_action(service: Any, conversation_id: str, command: dict[str, Any], *,
                                owner_id: str, key: str, validate: Callable[[], None],
                                validate_review: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Execute the exact reviewed action with durable, non-replaying recovery."""
    validate()
    if type(command) is not dict:
        raise _error("invalid_conversation_action")
    command_id = _uuid(command.get("command_id"))
    if key != command_id or command.get("type") not in _EXECUTABLE:
        raise _error("invalid_conversation_action")
    action = str(command["type"])
    payload = command.get("payload")
    plain_keys = {"title"} if action == "conversation.rename" else {"pinned"} if action == "conversation.pin" else set()
    expected_keys = plain_keys | {"checkpoint_revision", "action_digest"}
    if action == "conversation.export":
        expected_keys.add("export_title")
    if (type(payload) is not dict or set(payload) != expected_keys
            or type(command.get("expected_revision")) is not str
            or type(payload.get("checkpoint_revision")) is not str
            or type(payload.get("action_digest")) is not str):
        raise _error("invalid_conversation_action")
    fields = {name: deepcopy(payload[name]) for name in plain_keys}

    def frozen_review() -> dict[str, Any]:
        reviewed_fields = ({"title": payload["export_title"]}
                           if action == "conversation.export" else deepcopy(fields))
        if action == "conversation.export" and (type(payload["export_title"]) is not str
                or len(payload["export_title"]) > 256 or "\x00" in payload["export_title"]):
            raise _error("invalid_conversation_action")
        intent = {"conversation_id": conversation_id, "action": action,
                  "revision": command["expected_revision"],
                  "checkpoint_revision": payload["checkpoint_revision"], "fields": reviewed_fields}
        if admissions.keyed_digest(intent) != payload["action_digest"]:
            raise _error("conversation_review_changed", command["expected_revision"])
        return {"schema_version": 1, **intent, "action_digest": payload["action_digest"],
                "summary": ({"conversation.rename": "Rename this conversation while retaining its history and resources.",
                             "conversation.pin": "Pin this conversation." if fields.get("pinned") else "Unpin this conversation.",
                             "conversation.export": "Create a local Markdown copy of the saved user and assistant transcript."}[action]),
                "disclosures": (["The export excludes system instructions and tool-internal messages. A local copy is retained with this conversation."]
                                if action == "conversation.export" else [])}

    previous = admissions.read_command_metadata(owner_id, command_id)
    if previous is None:
        review = review_conversation_action(service, conversation_id, action,
                                            command["expected_revision"], fields, validate=validate)
        if (review["checkpoint_revision"] != payload["checkpoint_revision"]
                or review["action_digest"] != payload["action_digest"]
                or action == "conversation.export" and review["fields"]["title"] != payload["export_title"]):
            raise _error("conversation_review_changed", review["revision"])
    else:
        if previous["target"] != conversation_id or previous["type"] != action:
            raise _error("idempotency_mismatch")
        review = frozen_review()

    def admitted_validate() -> None:
        validate()
        validate_review(review)

    if action in {"conversation.rename", "conversation.pin"}:
        core = {key: deepcopy(value) for key, value in command.items() if key != "payload"}
        core["payload"] = fields
        try:
            result = service.execute(owner_id=owner_id, idempotency_key=key, command=core,
                                     target=conversation_id, validate=admitted_validate)
            snapshot = read_conversation_actions(service, conversation_id, validate=validate)
        except admissions.AdmissionError as exc:
            raise _error(str(exc), exc.current_revision) from exc
        except ClientPlatformError as exc:
            raise _error(exc.code, exc.current_revision) from exc
        return _public_receipt({"command_id": command_id, "status": result["status"], "action": action,
            "conversation": {name: snapshot[name] for name in ("conversation_id", "revision", "title", "pinned")}})

    if previous is not None:
        try:
            saved = admissions.claim_command(owner_id, key, command, conversation_id, exclusive_target=True)
            if saved is not None:
                if saved.get("status") != "partial" or saved.get("code") != "conversation_export_unconfirmed":
                    return _public_receipt(saved)
        except admissions.AdmissionError as exc:
            if str(exc) != "operation_uncertain":
                raise _error(str(exc), exc.current_revision) from exc
        admitted_validate()
        data = _export_markdown(conversation_id, str(review["fields"]["title"]),
                                review["checkpoint_revision"], validate=admitted_validate)
        try:
            exported = _publish_export(conversation_id, command_id, data, review["checkpoint_revision"],
                                       validate=admitted_validate)
        except Exception:
            return {"command_id": command_id, "status": "partial", "action": action,
                    "code": "conversation_export_unconfirmed"}
        result = {"command_id": command_id, "status": "completed", "action": action, "export": exported}
        admissions.complete_command(owner_id, key, result)
        return _public_receipt(result)
    validate_review(review)
    data = _export_markdown(conversation_id, str(review["fields"]["title"]),
                            review["checkpoint_revision"], validate=validate)
    try:
        admissions.claim_command(owner_id, key, command, conversation_id, exclusive_target=True,
                                 initial_result={"command_id": command_id, "status": "accepted", "action": action})
    except admissions.AdmissionError as exc:
        raise _error(str(exc), exc.current_revision) from exc
    try:
        admitted_validate()
        exported = _publish_export(conversation_id, command_id, data, review["checkpoint_revision"],
                                   validate=admitted_validate)
        result = {"command_id": command_id, "status": "completed", "action": action, "export": exported}
    except Exception:
        result = {"command_id": command_id, "status": "partial", "action": action,
                  "code": "conversation_export_unconfirmed"}
    admissions.complete_command(owner_id, key, result)
    return _public_receipt(result)
