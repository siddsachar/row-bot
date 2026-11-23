"""The audit routes.

For admins (``identity.roles.admin_connection``):
- ``GET /api/admin/audit``: stored links, newest first: ``{"events": [{"seq", "prev_hash", "hash", "event"}],
  "next_before": <seq> | null}``. Filters: ``action``, ``actor`` (an actor id: a principal id, or ``cli``),
  ``target`` (a target ref), ``from`` and ``to`` (the time recorded; ``to`` exclusive; a time without a zone
  is UTC), ``before`` (a seq, for the next page) and ``limit`` (1-200, 50).
- ``GET /api/admin/audit/verify``: ``verify_chain`` over the whole stored chain: ``{"ok", "checked", "head",
  "broken_at", "reason"}``.
- ``GET /api/admin/audit/export``: the matching links as JSON Lines, oldest first, at most 10,000 at a time
  (``after`` continues from a seq: ``X-Row-Bot-Audit-Next`` names it when more match). Each export is itself
  recorded as ``data.exported`` (scope ``audit``).

For a signed-in person (their Row-Bot's sender, C4):
- ``POST /api/audit/events`` with ``{"events": [<AuditEvent JSON>, ...]}``: at most 100 events and 256 KiB.
  Each must pass the contract's ``from_dict`` and be an action a device may send (``DEVICE_ACTIONS``: an export
  only of the person's own data, scope ``person``), else 422 with its ``index``. The server sets the actor (the
  session's person), the organisation and the time recorded, and stores the event at the metadata level; a
  ``device_id`` must be one of the person's devices (422 ``unknown_device``, 403 ``device_revoked``), and a
  lockdown names it as its target too (422 ``device_mismatch``). With a device's bearer token, a ``device_id`` must
  be that device (422 ``device_mismatch``), and an event without one is given it. An event whose id the person already stored is
  skipped (a retry): ``{"accepted": n, "duplicates": m}``; an id stored for anyone else refuses the batch (409
  ``event_id_taken``). The whole batch is stored in one transaction, or none of it.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from row_bot.audit import (
    Action, AuditEvent, InvalidAuditEvent, RedactionLevel, Target, TargetKind, from_dict, redact,
)
from row_bot_org import audit
from row_bot_org.identity import roles
from row_bot_org.identity.gate import RequestPrincipal, request_principal
from row_bot_org.identity.web import principal_connection

PAGE_DEFAULT = 50
PAGE_MAX = 200
EXPORT_MAX = 10_000
NEXT_HEADER = "X-Row-Bot-Audit-Next"
MAX_BATCH = 100
MAX_BATCH_BYTES = 256 * 1024
# What a Row-Bot may report: tool actions under company policy, exports, and an offline lockdown.
DEVICE_ACTIONS = frozenset({Action.TOOL_CALLED, Action.DATA_EXPORTED, Action.DEVICE_LOCKED_DOWN})


def _error(status: int, error: str, **fields: Any) -> JSONResponse:
    return JSONResponse({"error": error, **fields}, status_code=status)


def _utc(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _filters(action: str | None, actor: str | None, target: str | None, start: datetime | None,
             end: datetime | None) -> dict[str, Any] | JSONResponse:
    if action is not None and action not in {member.value for member in Action}:
        return _error(422, "unknown_action")
    return {"action": action, "actor": actor, "target": target, "start": _utc(start), "end": _utc(end)}


async def _batch(request: Request) -> bytes | None:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BATCH_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _device_state(conn: psycopg.Connection, device_id: str) -> str:
    """``ok``, ``revoked`` or ``unknown``: one of the caller's own devices (row-level security), or not."""
    try:
        uuid.UUID(device_id)
    except ValueError:
        return "unknown"
    row = conn.execute("SELECT revoked_at FROM row_bot_org.devices WHERE id = %s", (device_id,)).fetchone()
    return "unknown" if row is None else "ok" if row[0] is None else "revoked"


def audit_router() -> APIRouter:
    router = APIRouter()
    admin = Depends(roles.admin_connection, scope="function")

    @router.get("/api/admin/audit")
    def search(action: str | None = None, actor: str | None = None, target: str | None = None,
               start: datetime | None = Query(None, alias="from"), end: datetime | None = Query(None, alias="to"),
               before: int | None = Query(None, ge=0), limit: int = Query(PAGE_DEFAULT, ge=1, le=PAGE_MAX),
               conn: psycopg.Connection = admin) -> JSONResponse:
        filters = _filters(action, actor, target, start, end)
        if isinstance(filters, JSONResponse):
            return filters
        links = audit.find(conn, limit=limit, before=before, **filters)
        return JSONResponse({"events": links,
                             "next_before": links[-1]["seq"] if len(links) == limit else None})

    @router.get("/api/admin/audit/verify")
    def verify(conn: psycopg.Connection = admin) -> JSONResponse:
        return JSONResponse(dataclasses.asdict(audit.verify(conn)))

    @router.get("/api/admin/audit/export")
    def export(request: Request, action: str | None = None, actor: str | None = None, target: str | None = None,
               start: datetime | None = Query(None, alias="from"), end: datetime | None = Query(None, alias="to"),
               after: int | None = Query(None, ge=0), conn: psycopg.Connection = admin,
               principal: RequestPrincipal = Depends(request_principal)) -> Response:
        filters = _filters(action, actor, target, start, end)
        if isinstance(filters, JSONResponse):
            return filters
        links = audit.find(conn, limit=EXPORT_MAX + 1, newest_first=False, after=after, **filters)
        more = len(links) > EXPORT_MAX
        links = links[:EXPORT_MAX]
        body = "".join(json.dumps(link, ensure_ascii=False, separators=(",", ":")) + "\n" for link in links)
        data = body.encode("utf-8")
        audit.emit(conn, Action.DATA_EXPORTED, actor=audit.admin(principal.principal_id),
                   target=Target(TargetKind.EXPORT, str(uuid.uuid4())),
                   details={"scope": "audit", "item_count": len(links), "byte_count": len(data)})
        headers = {"Content-Disposition": 'attachment; filename="audit.jsonl"'}
        if more:
            headers[NEXT_HEADER] = str(links[-1]["seq"])
        return Response(data, media_type="application/x-ndjson", headers=headers)

    @router.post("/api/audit/events")
    def ingest(principal: RequestPrincipal = Depends(request_principal), body: bytes | None = Depends(_batch),
               conn: psycopg.Connection = Depends(principal_connection, scope="function")) -> JSONResponse:
        if body is None:
            return _error(413, "batch_too_large")
        try:
            raw = json.loads(body)
        except (UnicodeDecodeError, ValueError):
            return _error(422, "invalid_batch")
        events = raw.get("events") if isinstance(raw, dict) and raw.keys() == {"events"} else None
        if not isinstance(events, list) or not events:
            return _error(422, "invalid_batch")
        if len(events) > MAX_BATCH:
            return _error(413, "batch_too_large")
        now = datetime.now(timezone.utc)
        checked: list[AuditEvent] = []
        for index, data in enumerate(events):
            try:
                event = from_dict(data)
            except InvalidAuditEvent as error:
                return _error(422, "invalid_event", index=index, detail=str(error))
            if event.action not in DEVICE_ACTIONS or (
                    event.action == Action.DATA_EXPORTED and event.details.get("scope") != "person"):
                return _error(422, "action_not_accepted", index=index)
            # A device's bearer reports for itself only, and names itself when the event doesn't.
            if principal.device_id is not None:
                if event.device_id not in (None, principal.device_id):
                    return _error(422, "device_mismatch", index=index)
                event = dataclasses.replace(event, device_id=principal.device_id)
            if event.device_id is not None and (state := _device_state(conn, event.device_id)) != "ok":
                return _error(403, "device_revoked", index=index) if state == "revoked" else _error(
                    422, "unknown_device", index=index)
            if event.action == Action.DEVICE_LOCKED_DOWN and (
                    event.device_id is None or event.target is None or event.target.ref != event.device_id):
                return _error(422, "device_mismatch", index=index)
            # The actor is the session's person, whatever the device said; nothing above metadata is kept.
            try:
                checked.append(redact(dataclasses.replace(
                    event, actor=audit.person(principal.principal_id), org_id=audit.ORG_ID, recorded_at=now),
                    RedactionLevel.METADATA))
            except InvalidAuditEvent as error:  # An action the person, as the actor, can't take.
                return _error(422, "invalid_event", index=index, detail=str(error))
        try:
            with conn.transaction():
                accepted = sum(audit.record(conn, event) for event in checked)
        except psycopg.errors.UniqueViolation:  # An id stored for someone else: nothing of the batch is kept.
            return _error(409, "event_id_taken")
        return JSONResponse({"accepted": accepted, "duplicates": len(checked) - accepted})

    return router


__all__ = ["DEVICE_ACTIONS", "EXPORT_MAX", "MAX_BATCH", "MAX_BATCH_BYTES", "NEXT_HEADER", "audit_router"]
