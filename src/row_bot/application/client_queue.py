"""Pending user inputs in the existing checkpoint owner; content-free admissions.

The private checkpoint namespace stages unsent Humans only. Dispatch transfers
the same identity into the canonical conversation; no graph sees future inputs.
"""
from __future__ import annotations

import base64
import copy
import json
import logging
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Literal
from collections.abc import Sequence

from row_bot.runtime import admissions

logger = logging.getLogger(__name__)
_NAMESPACE = "client_queue"
_MAX_PENDING = 256
_MAX_BYTES = 4 * 1024 * 1024
_MAX_TEXT = 64 * 1024
_PAGE_BYTES = 240 * 1024  # Leave room for the typed response envelope.


@dataclass(frozen=True)
class ClientQueueItem:
    id: str
    submission_id: str
    generation_id: str
    text: str
    revision: str
    state: Literal["queued", "dispatching", "consumed", "cancelled", "paused"]
    editable: bool
    removable: bool


@dataclass(frozen=True)
class ClientQueueView:
    conversation_id: str
    generation_id: str
    items: tuple[ClientQueueItem, ...]
    next_cursor: str | None = None
    has_more: bool = False


def _staged(conversation_id: str) -> tuple[Any, dict]:
    from row_bot import threads
    saved = threads.checkpointer.get_tuple({"configurable": {
        "thread_id": conversation_id, "checkpoint_ns": _NAMESPACE}})
    values = copy.deepcopy(saved.checkpoint.get("channel_values", {})) if saved else {}
    return saved, values


def _save(conversation_id: str, saved: Any, values: dict) -> str:
    from langgraph.checkpoint.base import empty_checkpoint
    from row_bot import threads
    if threads._thread_write_blocked(conversation_id):
        raise admissions.AdmissionError("conversation_deleting")
    measured = {**values, "messages": [message.model_dump(mode="json") for message in values.get("messages", [])]}
    if len(json.dumps(measured, ensure_ascii=False).encode()) > _MAX_BYTES:
        raise admissions.AdmissionError("queue_capacity")
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = values
    versions = dict(saved.checkpoint.get("channel_versions", {})) if saved else {}
    for key in values:
        versions[key] = threads.checkpointer.get_next_version(versions.get(key), None)
    checkpoint["channel_versions"] = versions
    config = saved.config if saved else {"configurable": {"thread_id": conversation_id, "checkpoint_ns": _NAMESPACE}}
    written = threads.checkpointer.put(config, checkpoint, {"source": "update", "step": -1}, versions)
    if str(written["configurable"].get("checkpoint_id")) != str(checkpoint["id"]):
        raise admissions.AdmissionError("checkpoint_unavailable")
    return str(checkpoint["id"])


def freeze_context(config: dict, bindings: tuple, targets: list | None) -> dict:
    from row_bot.conversation_resources import describe
    return {
        "configurable": {key: copy.deepcopy(config.get("configurable", {}).get(key)) for key in (
            "model_override", "approval_mode", "agent_profile_id", "runtime_mode", "runtime_surface",
            "agent_profile_snapshot", "agent_profile_frozen", "tool_allowlist", "reasoning_snapshot") if key in config.get("configurable", {})},
        "bindings": [asdict(binding) for binding in bindings],
        "resource_revisions": {binding.binding_id: describe(binding).resource_revision for binding in bindings},
        "write_targets": copy.deepcopy(targets),
    }


def remember_context(conversation_id: str, generation_id: str, config: dict, frozen: dict | None = None) -> None:
    from row_bot import threads
    from row_bot.conversation_resources import list_bindings
    frozen = frozen if frozen is not None else freeze_context(config, list_bindings(conversation_id).bindings, None)
    with threads.checkpoint_mutation(conversation_id):
        saved, values = _staged(conversation_id)
        values["accepted_context"] = {**copy.deepcopy(frozen), "generation_id": generation_id}
        _save(conversation_id, saved, values)


def _publish(conversation_id: str, generation_id: str, ids: list[str], service: Any = None) -> None:
    from row_bot.projection.conversation import conversation_projection
    projection = service.projection if service is not None else conversation_projection
    for offset in range(0, len(ids), 256):
        try:
            projection.publish(conversation_id, "queue.changed", {
                "generation_id": generation_id, "submission_ids": ids[offset:offset + 256]})
        except Exception:
            logger.debug("Queue observer unavailable; durable queue remains readable", exc_info=True)


def _text(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 16000 or len(value.encode()) > _MAX_TEXT:
        raise admissions.AdmissionError("invalid_queue_text")
    return value


def enqueue(service: Any, conversation_id: str, submission_id: str, text: str, *, command_id: str = "") -> dict:
    from langchain_core.messages import HumanMessage
    from row_bot import threads
    text = _text(text)
    if not submission_id or len(submission_id) > 128:
        raise admissions.AdmissionError("invalid_submission_id")
    active = next((handle for handle in service.registry.active(conversation_id)
                   if handle.domain == "conversation"), None)
    if active is None or active.cancel_scope.is_cancelled():
        raise admissions.AdmissionError("generation_not_steerable")
    with threads.checkpoint_mutation(conversation_id):
        saved, values = _staged(conversation_id)
        context = values.get("accepted_context") or {}
        if context.get("generation_id") != active.generation_id:
            raise admissions.AdmissionError("generation_not_steerable")
        messages = list(values.get("messages", []))
        with admissions.transaction() as conn:
            lifecycle = conn.execute("SELECT state,next_sequence FROM conversation_lifecycle WHERE conversation_id=?", (conversation_id,)).fetchone()
            if not lifecycle or lifecycle["state"] != "active":
                raise admissions.AdmissionError("conversation_deleting")
            if conn.execute("SELECT 1 FROM generation_passes WHERE submission_id=?", (submission_id,)).fetchone():
                raise admissions.AdmissionError("idempotency_mismatch")
            count = conn.execute("SELECT COUNT(*) FROM generation_passes WHERE conversation_id=? AND queue_state IN ('preparing','queued','paused','dispatching')", (conversation_id,)).fetchone()[0]
            if count >= _MAX_PENDING:
                raise admissions.AdmissionError("queue_capacity")
            sequence = int(lifecycle["next_sequence"]) + 1
            pass_id, generation_id = str(uuid.uuid4()), str(uuid.uuid4())
            conn.execute("UPDATE conversation_lifecycle SET next_sequence=? WHERE conversation_id=?", (sequence, conversation_id))
            conn.execute("INSERT INTO generation_passes(pass_id,conversation_id,submission_id,generation_id,admission_sequence,state,command_id,queue_state,queue_source_generation_id,queue_epoch) VALUES(?,?,?,?,?,'queue_preparing',?,'preparing',?,?)",
                         (pass_id, conversation_id, submission_id, generation_id, sequence, command_id, active.generation_id, service.server_epoch))
        messages.append(HumanMessage(content=text, id=submission_id,
                                     additional_kwargs={"client_queue": {"revision": 0, "context": context}}))
        try:
            revision = _save(conversation_id, saved, {**values, "messages": messages})
            with admissions.transaction() as conn:
                changed = conn.execute("UPDATE generation_passes SET state='queued',queue_state='queued',queue_checkpoint_revision=? WHERE pass_id=? AND state='queue_preparing' AND EXISTS(SELECT 1 FROM conversation_lifecycle WHERE conversation_id=? AND state='active')", (revision, pass_id, conversation_id)).rowcount
                if changed != 1:
                    raise admissions.AdmissionError("conversation_deleting")
        except Exception:
            with admissions.transaction() as conn:
                conn.execute("UPDATE generation_passes SET state='cancelled',queue_state='cancelled' WHERE pass_id=? AND state='queue_preparing'", (pass_id,))
            raise
    _publish(conversation_id, active.generation_id, [submission_id], service)
    return {"conversation_id": conversation_id, "submission_id": submission_id,
            "generation_id": generation_id, "pass_id": pass_id, "status": "accepted"}


def _row(conversation_id: str, submission_id: str) -> dict:
    with admissions.transaction() as conn:
        row = conn.execute("SELECT * FROM generation_passes WHERE conversation_id=? AND submission_id=? AND queue_state!=''", (conversation_id, submission_id)).fetchone()
    if row is None:
        raise admissions.AdmissionError("not_found")
    return dict(row)


def _editable(row: dict) -> bool:
    return row["state"] == "queued" and row["queue_state"] in {"queued", "paused"}


def change(service: Any, conversation_id: str, submission_id: str, expected_revision: str, *, text: str | None = None, remove: bool = False) -> dict:
    from row_bot import threads
    with threads.checkpoint_mutation(conversation_id):
        row = _row(conversation_id, submission_id)
        if str(row["queue_revision"]) != str(expected_revision) or not _editable(row):
            raise admissions.AdmissionError("queue_revision_conflict", str(row["queue_revision"]))
        if admissions.deletion_state(conversation_id) != "active":
            raise admissions.AdmissionError("conversation_deleting")
        saved, values = _staged(conversation_id)
        messages = list(values.get("messages", []))
        match = next((message for message in messages if message.id == submission_id), None)
        if match is None:
            raise admissions.AdmissionError("queue_content_unavailable")
        if int(match.additional_kwargs["client_queue"]["revision"]) > int(row["queue_revision"]):
            raise admissions.AdmissionError("queue_revision_conflict", str(match.additional_kwargs["client_queue"]["revision"]))
        next_revision = int(row["queue_revision"]) + 1
        if remove:
            messages = [message for message in messages if message.id != submission_id]
        else:
            replacement = match.model_copy(update={"content": _text(text), "additional_kwargs": {
                "client_queue": {**match.additional_kwargs["client_queue"], "revision": next_revision}}})
            messages = [replacement if message.id == submission_id else message for message in messages]
        checkpoint_revision = _save(conversation_id, saved, {**values, "messages": messages})
        with admissions.transaction() as conn:
            changed = conn.execute("UPDATE generation_passes SET queue_revision=?,queue_checkpoint_revision=?,queue_state=?,state=? WHERE pass_id=? AND queue_revision=? AND state='queued'",
                (next_revision, checkpoint_revision, "cancelled" if remove else row["queue_state"], "cancelled" if remove else "queued", row["pass_id"], int(expected_revision))).rowcount
            if changed != 1:
                raise admissions.AdmissionError("queue_revision_conflict")
    _publish(conversation_id, row["queue_source_generation_id"], [submission_id], service)
    return {"conversation_id": conversation_id, "submission_id": submission_id,
            "revision": str(next_revision), "status": "completed"}


def read_queue(service: Any, conversation_id: str, *, generation_id: str = "", cursor: str | None = None, limit: int = 100) -> ClientQueueView:
    service._metadata(conversation_id)
    if type(limit) is not int or not 1 <= limit <= 256:
        raise admissions.AdmissionError("invalid_queue_page")
    after = 0
    if cursor:
        try:
            if len(cursor) > 2048:
                raise ValueError
            thread, after = json.loads(base64.urlsafe_b64decode(cursor))
            if thread != conversation_id or type(after) is not int or after < 0:
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise admissions.AdmissionError("invalid_queue_cursor") from exc
    with admissions.transaction() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM generation_passes WHERE conversation_id=? AND queue_state!='' AND admission_sequence>? ORDER BY admission_sequence LIMIT ?", (conversation_id, after, limit + 1))]
    _, values = _staged(conversation_id)
    messages = {str(message.id): str(message.content) for message in values.get("messages", [])}
    missing = {row["submission_id"] for row in rows[:limit] if row["submission_id"] not in messages and row["queue_state"] != "cancelled"}
    if missing:
        from row_bot.runtime.checkpoint_reader import open_checkpoint
        with open_checkpoint(conversation_id) as reader:
            if reader:
                for _, record in reader.records():
                    if record["message_id"] in missing:
                        position = record.get("content_position")
                        if position is not None and reader.node(position).kind == "str":
                            messages[record["message_id"]] = reader.text(position, maximum=_MAX_TEXT)
    items = []
    page_bytes = 0
    for row in rows[:limit]:
        state = row["queue_state"]
        if state == "preparing" or (state == "queued" and row["queue_epoch"] != service.server_epoch):
            state = "paused"
        can_edit = _editable(row)
        item = ClientQueueItem(row["submission_id"], row["submission_id"], row["generation_id"],
            messages.get(row["submission_id"], ""), str(row["queue_revision"]), state, can_edit, can_edit)
        size = len(json.dumps(asdict(item), ensure_ascii=True).encode()) + 1
        if items and page_bytes + size > _PAGE_BYTES:
            break
        items.append(item)
        page_bytes += size
    has_more = len(rows) > len(items)
    next_cursor = base64.urlsafe_b64encode(json.dumps([conversation_id, rows[len(items) - 1]["admission_sequence"]]).encode()).decode() if has_more else None
    return ClientQueueView(conversation_id, generation_id, tuple(items), next_cursor, has_more)


def pause_pending(service: Any, conversation_id: str) -> None:
    from row_bot import threads
    with threads.checkpoint_mutation(conversation_id), admissions.transaction() as conn:
        rows = list(conn.execute("SELECT submission_id,queue_source_generation_id FROM generation_passes WHERE conversation_id=? AND queue_state IN ('queued','dispatching')", (conversation_id,)))
        conn.execute("UPDATE generation_passes SET queue_state='paused',queue_revision=queue_revision+1 WHERE conversation_id=? AND queue_state IN ('queued','dispatching')", (conversation_id,))
    if rows:
        _publish(conversation_id, str(rows[0]["queue_source_generation_id"]), [str(row["submission_id"]) for row in rows], service)


def dispatch(service: Any, conversation_id: str, *, submission_id: str = "", expected_revision: str | None = None, automatic: bool = False) -> dict | None:
    from row_bot import threads
    if service.registry.active(conversation_id):
        if automatic:
            return None
        raise admissions.AdmissionError("generation_active")
    with threads.checkpoint_mutation(conversation_id):
        with admissions.transaction() as conn:
            row = conn.execute("SELECT * FROM generation_passes WHERE conversation_id=? AND state IN ('queued','queue_preparing') ORDER BY admission_sequence LIMIT 1", (conversation_id,)).fetchone()
        if row is None:
            if automatic:
                return None
            raise admissions.AdmissionError("queue_requires_resume")
        row = dict(row)
        if automatic and (row["queue_epoch"] != service.server_epoch or row["queue_state"] != "queued"):
            return None
        if not automatic and (row["submission_id"] != submission_id or str(row["queue_revision"]) != str(expected_revision) or not _editable(row)):
            raise admissions.AdmissionError("queue_revision_conflict", str(row["queue_revision"]))
        _, values = _staged(conversation_id)
        message = next((item for item in values.get("messages", []) if item.id == row["submission_id"]), None)
        if message is None:
            raise admissions.AdmissionError("queue_content_unavailable")
        context = copy.deepcopy(message.additional_kwargs["client_queue"]["context"])
        with admissions.transaction() as conn:
            if conn.execute("SELECT 1 FROM approval_requests WHERE source_thread_id=? AND resume_kind='conversation' AND status='pending'", (conversation_id,)).fetchone():
                raise admissions.AdmissionError("approval_required")
            conn.execute("UPDATE generation_passes SET queue_state='dispatching',queue_revision=queue_revision+1,queue_epoch=? WHERE pass_id=? AND state='queued' AND queue_revision=?", (service.server_epoch, row["pass_id"], row["queue_revision"]))
        try:
            result = service._start(conversation_id, {"submission_id": row["submission_id"], "text": str(message.content)},
                                    resume=False, command_id=row["command_id"], queue_record=row, frozen_context=context)
        except Exception:
            pause_pending(service, conversation_id)
            raise
    _publish(conversation_id, row["queue_source_generation_id"], [row["submission_id"]], service)
    return result


def acknowledge_consumed(handle: Any, message_ids: Sequence[str] | None = None) -> None:
    """Called by the real prepared model boundary, never by parent completion."""
    from row_bot import threads
    with threads.checkpoint_mutation(handle.conversation_id):
        with admissions.transaction() as conn:
            if message_ids is None:
                rows = list(conn.execute("SELECT pass_id,submission_id,queue_source_generation_id FROM generation_passes WHERE pass_id=? AND state='started' AND queue_state='dispatching'", (handle.pass_id,)))
            else:
                identities = set(str(value) for value in message_ids if value)
                rows = [row for row in conn.execute("SELECT pass_id,submission_id,queue_source_generation_id FROM generation_passes WHERE conversation_id=? AND state IN ('admitted','started','terminal','interrupted') AND queue_state IN ('dispatching','paused') ORDER BY admission_sequence", (handle.conversation_id,)) if row["submission_id"] in identities]
            if not rows:
                return
            conn.executemany("UPDATE generation_passes SET queue_state='consumed',queue_revision=queue_revision+1 WHERE pass_id=? AND queue_state IN ('dispatching','paused')", [(row["pass_id"],) for row in rows])
        consumed_ids = [str(row["submission_id"]) for row in rows]
        try:
            saved, values = _staged(handle.conversation_id)
            values["messages"] = [message for message in values.get("messages", []) if message.id not in consumed_ids]
            _save(handle.conversation_id, saved, values)
        except Exception:
            logger.debug("Consumed input staging cleanup deferred", exc_info=True)
    _publish(handle.conversation_id, rows[0]["queue_source_generation_id"], consumed_ids)


def recover_queue(epoch: str) -> None:
    """Expose retained inputs after process loss without dispatching any work."""
    with admissions.transaction() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM generation_passes WHERE queue_state!='' AND queue_epoch!=? AND queue_state NOT IN ('consumed','cancelled')", (epoch,))]
    for row in rows:
        if row["state"] == "started" and admissions._owner_alive(row):
            continue
        from row_bot import threads
        with threads.checkpoint_mutation(row["conversation_id"]):
            _, values = _staged(row["conversation_id"])
            message = next((item for item in values.get("messages", []) if item.id == row["submission_id"]), None)
            state = "queued" if message is not None and row["state"] == "queue_preparing" else row["state"]
            queue_state = "paused" if message is not None or state != "queue_preparing" else "cancelled"
            if message is None and state == "queued":
                queue_state = "cancelled"
            if queue_state == "cancelled":
                state = "cancelled"
            revision = max(int(row["queue_revision"]), int(message.additional_kwargs["client_queue"]["revision"]) if message is not None else 0) + 1
            with admissions.transaction() as conn:
                conn.execute("UPDATE generation_passes SET state=?,queue_state=?,queue_revision=?,queue_epoch=? WHERE pass_id=? AND queue_state NOT IN ('consumed','cancelled')", (state, queue_state, revision, epoch, row["pass_id"]))
                if row["command_id"] and message is not None:
                    result = {"command_id": row["command_id"], "conversation_id": row["conversation_id"],
                              "submission_id": row["submission_id"], "generation_id": row["generation_id"],
                              "pass_id": row["pass_id"], "status": "accepted"}
                    conn.execute("UPDATE client_commands SET status='completed',result_json=? WHERE command_id=? AND target=? AND status='admitting'",
                                 (json.dumps(result), row["command_id"], row["conversation_id"]))
