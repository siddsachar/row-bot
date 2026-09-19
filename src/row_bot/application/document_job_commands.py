"""Reviewed document queue controls over the existing jobs/admissions owners."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlite3 import Connection
    from row_bot.document_jobs import DocumentJobService

import base64
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import json
import re

from row_bot import knowledge_views
from row_bot.application import knowledge_commands as common
from row_bot.data_paths import get_row_bot_data_dir
from row_bot.runtime import admissions

_ACTIONS = {"document.batch.pause":"pause", "document.batch.resume":"resume",
    "document.batch.cancel":"cancel_batch", "document.job.cancel":"cancel_job",
    "document.job.retry":"retry", "document.jobs.clear_finished":"clear"}
_OUTCOMES = {"pause":"paused", "resume":"resumed", "cancel_batch":"cancellation_requested",
    "cancel_job":"cancellation_requested", "retry":"retried", "clear":"cleared"}


@dataclass(frozen=True)
class QueueItem:
    id: str
    batch_id: str | None
    name: str
    status: str
    stage: str | None
    pause_requested: bool
    cancel_requested: bool
    attempt: int | None
    index_current: int | None
    index_total: int | None
    extraction_current: int | None
    extraction_total: int | None
    error_code: str | None
    revision: str


@dataclass(frozen=True)
class QueuePage:
    schema_version: int
    revision: str
    items: tuple[QueueItem, ...]
    total: int | None
    next_cursor: str | None
    availability: str


def _path():
    return get_row_bot_data_dir(create=False) / "document_ingestion" / "jobs.db"


def _raw(row):
    value = dict(row)
    value.pop("matched", None)
    if len(json.dumps(value)) > 2 * 1024 * 1024:
        raise ValueError("Document row exceeds review budget")
    common._id(value["id"])
    return value


def _item(row):
    from row_bot.document_jobs import JOB_STATUSES, JOB_STAGES, BATCH_STATUSES, is_safe_document_name
    value = _raw(row)
    job = "batch_id" in value
    if value["status"] not in (JOB_STATUSES if job else BATCH_STATUSES):
        raise ValueError("Unknown document state")
    if job and value["stage"] not in JOB_STAGES:
        raise ValueError("Unknown document stage")
    def number(key: str) -> int | None:
        result = value.get(key)
        if result is not None and (type(result) is not int or not 0 <= result <= 2**53-1):
            raise ValueError("Invalid document progress")
        return result
    name = value.get("original_name", "")
    if not isinstance(name, str) or len(name) > 256:
        raise ValueError("Invalid document name")
    if job and not is_safe_document_name(name):
        raise ValueError("Document name is not a canonical safe basename")
    for key in ("pause_requested","cancel_requested"):
        flag = value.get(key,0)
        if type(flag) is not int or flag not in {0,1}:
            raise ValueError("Invalid document request flag")
    code = value.get("error_code")
    if code and (not isinstance(code, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}",code)):
        code = "document_failed"
    return QueueItem(value["id"], common._id(value["batch_id"]) if job else None, name,
        value["status"],value.get("stage"),bool(value.get("pause_requested")),bool(value["cancel_requested"]),
        number("attempt"),number("index_progress_current"),number("index_progress_total"),
        number("extraction_progress_current"),number("extraction_progress_total"),code,common._digest(value))


def read_document_queue(*, kind: str = "batches", batch_id: str | None = None,
                        cursor: str | None = None, limit: int = 50, validate: Callable[[], None]) -> QueuePage:
    validate()
    if kind not in {"batches","jobs"} or type(limit) is not int or not 1 <= limit <= 50:
        raise common._error("invalid_document_queue")
    if batch_id is not None:
        common._id(batch_id)
    key = common._digest(["document-queue",kind,batch_id,limit])
    offset, expected = 0, None
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor) > 2048:
                raise ValueError
            value = json.loads(base64.b64decode(cursor,altchars=b"-_",validate=True))
            if (value.keys() != {"v","key","revision","offset"} or value["v"] != 1 or value["key"] != key
                    or type(value["offset"]) is not int or not 0 < value["offset"] < 2**53
                    or not isinstance(value["revision"],str) or not re.fullmatch("[a-f0-9]{64}",value["revision"])):
                raise ValueError
            offset, expected = value["offset"],value["revision"]
        except (ValueError,TypeError,AttributeError,RecursionError):
            raise common._error("invalid_document_queue") from None
    table = "document_batches" if kind == "batches" else "document_jobs"
    where = ("id=?" if kind == "batches" else "batch_id=?") if batch_id else "1"
    page = knowledge_views._read(_path(),{table:"id status cancel_requested"},
        f"SELECT *,1 matched FROM {table} WHERE {where} ORDER BY created_at,id",(batch_id,) if batch_id else (),
        _item,QueuePage,key,offset,expected,limit)
    validate()
    return page


def _find(kind, identifier):
    table = "document_jobs" if kind == "job" else "document_batches"
    page = knowledge_views._read(_path(),{table:"id status"},f"SELECT *,1 matched FROM {table} WHERE id=?",
        (common._id(identifier),),lambda row:common._Row(json.dumps(_raw(row))),common._Page,"document-control",0,None,1)
    if page.availability != "available" or not page.items:
        raise common._error("document_queue_unavailable")
    return json.loads(page.items[0].value)


def _snapshot(batch_ids):
    from row_bot.document_jobs import DocumentJobError, document_control_snapshot
    captured = None
    def select(conn: Connection, _tables: set[str]) -> str:
        nonlocal captured
        captured = document_control_snapshot(conn,batch_ids)
        return "SELECT 1 matched"
    try:
        page = knowledge_views._read(_path(),{"document_batches":"id status","document_jobs":"id batch_id status",
            "document_records":"document_id", "document_removals":"id target"},select,(),
            lambda row:common._Row("captured"),common._Page,"document-control",0,None,1)
    except DocumentJobError:
        raise common._error("document_queue_unavailable") from None
    if page.availability != "available" or captured is None:
        raise common._error("document_queue_unavailable")
    return captured


def _review(kind, payload):
    if kind not in _ACTIONS or not isinstance(payload,dict):
        raise common._error("invalid_document_control")
    action = _ACTIONS[kind]
    if action == "clear":
        targets = payload.get("targets")
        if (not isinstance(targets,list) or not 1 <= len(targets) <= 50
                or any(not isinstance(value,dict) or value.keys() != {"id","revision"} for value in targets)):
            raise common._error("invalid_document_control")
        batch_ids = [common._id(value["id"]) for value in targets]
        target_id = None
    else:
        target_id = common._id(payload.get("target_id"))
        targets = [{"id":target_id,"revision":payload.get("revision")}]
        row = _find("job" if action in {"cancel_job","retry"} else "batch",target_id)
        batch_ids = [row.get("batch_id",row["id"])]
    snapshot = _snapshot(batch_ids)
    source = snapshot["jobs"] if action in {"cancel_job","retry"} else snapshot["batches"]
    for target in targets:
        row = next((value for value in source if value["id"] == target["id"]),None)
        if row is None or common._digest(row) != target["revision"]:
            raise common._error("document_queue_changed")
        if ((action in {"pause","resume","cancel_batch"} and row["status"] in {"completed","completed_with_errors","cancelled"})
                or (action == "cancel_job" and row["status"] in {"completed","failed","cancelled","skipped_duplicate"})
                or (action == "retry" and row["status"] != "failed")):
            raise common._error("document_control_unavailable")
    if action in {"resume","retry"} and snapshot["removals"]:
        raise common._error("document_removal_pending")
    review = {"schema_version":1,"action":kind,"target_id":target_id,"batch_ids":batch_ids,
        "revision":common._digest(snapshot),"intent_digest":common._digest({k:v for k,v in payload.items() if k != "review_id"}),
        "provider_work":action in {"resume","retry"},"retains_work":action in {"clear","retry"}}
    return review,snapshot


def read_document_control_review(kind: str, payload: dict, *, validate: Callable[[], None]) -> dict:
    validate()
    review,_ = _review(kind,deepcopy(payload))
    validate()
    return review


def _scope(owner, authority, *, read_only=False):
    if not isinstance(authority,str) or not 1 <= len(authority) <= 256:
        raise common._error("action_denied")
    return admissions.keyed_digest({"document_queue_authority":authority,"owner":owner},read_only=read_only)


def read_document_control_command(*, owner_id: str, authority_id: str, command_id: str,
                                  validate: Callable[[], None]) -> dict:
    validate()
    command_id = common._uuid(command_id)
    metadata = admissions.read_command_metadata(owner_id,command_id)
    saved = admissions.read_command_receipt(owner_id,command_id)
    if (not metadata or metadata["target"] != "document-queue" or metadata["type"] not in _ACTIONS
            or not saved or saved.get("command_id") != command_id or not isinstance(saved.get("_document_queue"),dict) or
            saved.get("_document_queue",{}).get("scope") != _scope(owner_id,authority_id,read_only=True)):
        raise common._error("document_operation_unavailable")
    result = {"command_id":command_id,"status":"partial","code":"document_outcome_uncertain"}
    if saved.get("status") == "completed":
        action = _ACTIONS[metadata["type"]]
        if (saved.get("outcome") != _OUTCOMES[action] or type(saved.get("count")) is not int or not 0 <= saved["count"] <= 50
                or not isinstance(saved.get("batch_ids"),list) or not 1 <= len(saved["batch_ids"]) <= 50
                or type(saved.get("retained_work")) is not bool):
            raise common._error("document_operation_unavailable")
        result = {key:saved[key] for key in ("command_id","status","outcome","count","retained_work")}
        result["batch_ids"] = [common._id(value) for value in saved["batch_ids"]]
        result["target_id"] = common._id(saved["target_id"]) if saved.get("target_id") is not None else None
        result["saved_status"] = saved.get("saved_status")
        from row_bot.document_jobs import JOB_STATUSES, BATCH_STATUSES
        if result["saved_status"] is not None and result["saved_status"] not in JOB_STATUSES | BATCH_STATUSES:
            raise common._error("document_operation_unavailable")
    validate()
    return result


def execute_document_control(command: dict, *, service: DocumentJobService | None, owner_id: str, authority_id: str, key: str,
        validate: Callable[[],None], validate_action: Callable[[str],None],
        validate_review: Callable[[dict,dict],None]) -> dict:
    validate()
    command = deepcopy(command)
    command_id = common._uuid(command.get("command_id"))
    kind,payload = command.get("type"),command.get("payload")
    if kind not in _ACTIONS or not isinstance(payload,dict) or payload.keys() != (
            {"targets","review_id"} if _ACTIONS[kind] == "clear" else {"target_id","revision","review_id"}):
        raise common._error("invalid_document_control")
    def receipt() -> dict:
        return read_document_control_command(owner_id=owner_id,authority_id=authority_id,command_id=command_id,validate=validate)
    if admissions.read_command_metadata(owner_id,command_id) is not None:
        try:
            admissions.claim_command(owner_id,key,command,"document-queue")
        except admissions.AdmissionError as error:
            if str(error) != "operation_uncertain":
                raise common._error(str(error)) from error
        return receipt()
    review,snapshot = _review(kind,payload)
    authority_error = None
    def authority() -> None:
        nonlocal authority_error
        try:
            validate()
            validate_action(kind)
            validate_review(command,review)
        except Exception as error:
            authority_error = error
            raise
    authority()
    if service.db_path.absolute() != _path().absolute():
        raise common._error("document_queue_unavailable")
    try:
        prior = admissions.claim_command(owner_id,key,command,"document-queue")
    except admissions.AdmissionError as error:
        if str(error) != "operation_uncertain":
            raise common._error(str(error)) from error
        return receipt()
    if prior is not None:
        return receipt()
    proof = {"scope":_scope(owner_id,authority_id)}
    admissions.command_progress(owner_id,key,{"command_id":command_id,"status":"effect_started","_document_queue":proof})
    action,target = _ACTIONS[kind],review["target_id"]
    options = {"expected_snapshot":snapshot,"validate":authority}
    try:
        if action in {"pause","resume"}:
            result = service.pause_batch(target,action == "pause",**options)
        elif action == "cancel_batch":
            result = service.cancel_batch(target,**options)
        elif action == "cancel_job":
            result = service.cancel_job(target,**options)
        elif action == "retry":
            result = service.retry_failed(target,**options)
        else:
            result = service.clear_finished(**options)
    except Exception:
        if authority_error is not None:
            raise authority_error
        # A retained-work rename or finalization effect may already exist.
        # Never convert that uncertainty into a retryable rejected command.
        return receipt()
    saved = {"command_id":command_id,"status":"completed","outcome":_OUTCOMES[action],
        "target_id":target,"batch_ids":review["batch_ids"],"count":result if action == "clear" else 1,
        "saved_status":None if action == "clear" else result.status,"retained_work":action in {"clear","retry"},
        "_document_queue":proof}
    try:
        admissions.complete_command(owner_id,key,saved)
    except Exception:
        # The queue effect already happened. Re-read the exact original receipt
        # with current authority rather than offer a fresh retry of that effect.
        return receipt()
    return receipt()
