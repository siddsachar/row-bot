"""The policy routes.

- ``GET /api/policy/effective``: the signed-in person's effective policy (the company policy widened by
  their groups' overrides, ``row_bot.policy.resolve``) with its ``version``, an ``ETag`` of the body, and
  304 for a matching ``If-None-Match``.
- ``GET /api/admin/policy``: the newest version of every scope, as written (a cleared group's is
  ``{"version": N}``).
- ``PUT /api/admin/policy`` and ``PUT /api/admin/policy/groups/{group}``: a new version of the company
  policy or of one group's override, checked strictly (422 with the setting at fault). The body's
  ``version`` is required (428 without one) and must be the newest one of that scope (409 otherwise). An
  override must widen the current company policy in everything it sets (422 naming what adds nothing). An
  empty override (``{}``) takes the group's override away. The answer is ``{"policy": ...,
  "unknown_models": [...]}``, the ``models.allowed`` names no catalog model has (a warning, not a refusal: a
  model may be added to the catalog later); a company write adds ``still_granted_by``, the overrides that
  still grant what it took away, for the console to warn about. Each write emits ``policy.changed``.
- ``GET /api/admin/policy/history``: every version, newest first (``limit``, ``before``).

The admin routes need an admin (``identity.roles.admin_connection``; 403 otherwise, before the body is read).
"""

from __future__ import annotations

import hashlib
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from row_bot.policy import (
    ALL_MODELS, MAX_DOCUMENT_BYTES, ONLY_WIDEN, PolicyError, canonical_json, document_to_dict, loads, parse_override, parse_policy,
    policy_to_dict,
)
from row_bot.audit import Action
from row_bot_org import audit
from row_bot_org.identity import roles
from row_bot_org.identity.gate import RequestPrincipal, request_principal
from row_bot_org.identity.web import principal_connection
from row_bot_org.policy import store

MAX_GROUP_NAME = 256
# The policy contract's version (contracts/policy/v1), in each policy.changed event.
POLICY_SCHEMA_VERSION = 1


async def _body(request: Request) -> bytes | None:
    """The request body, or None when it is larger than a policy may be."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_DOCUMENT_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _error(status: int, error: str, **fields: Any) -> JSONResponse:
    return JSONResponse({"error": error, **fields}, status_code=status)


def _matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    tags = {tag.strip() for tag in if_none_match.split(",")}
    return "*" in tags or etag in tags or f"W/{etag}" in tags


def _valid_group(group: str) -> bool:
    return (0 < len(group) <= MAX_GROUP_NAME and group == group.strip()
            and not any(ord(char) < 32 or ord(char) == 127 for char in group))


def _revision(revision: store.Revision) -> dict[str, Any]:
    return {"version": revision.version, "group": revision.group, "changed_by": revision.changed_by,
            "changed_at": revision.changed_at.isoformat(), "policy": document_to_dict(revision.document)}


def policy_router() -> APIRouter:
    router = APIRouter()
    admin = Depends(roles.admin_connection, scope="function")

    @router.get("/api/policy/effective")
    def effective(request: Request, principal: RequestPrincipal = Depends(request_principal),
                  conn: psycopg.Connection = Depends(principal_connection, scope="function")) -> Response:
        body = canonical_json(policy_to_dict(store.effective(conn, principal.groups)))
        etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        if _matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers={"ETag": etag})
        return Response(body, media_type="application/json", headers={"ETag": etag})

    @router.get("/api/admin/policy")
    def current(conn: psycopg.Connection = admin) -> JSONResponse:
        company, overrides = store.current(conn)
        return JSONResponse({
            "company": document_to_dict(company.document) if company else {"version": 0},
            "groups": {revision.group: document_to_dict(revision.document) for revision in overrides},
        })

    def _put(conn: psycopg.Connection, principal: RequestPrincipal, group: str | None,
             body: bytes | None) -> JSONResponse:
        if group is not None and not _valid_group(group):
            return _error(422, "invalid_group")
        if body is None:
            return _error(413, "policy_too_large")
        try:
            raw = loads(body)
            document = parse_policy(raw) if group is None else parse_override(raw)
        except PolicyError as error:
            return _error(422, "invalid_policy", detail=error.detail)
        if document.version is None:
            return _error(428, "version_required")
        try:
            written = store.put(conn, group, document, changed_by=principal.principal_id)
        except store.VersionConflict as conflict:
            return _error(409, "version_conflict", current=conflict.current)
        except store.NotWider as narrow:
            return _error(422, "invalid_policy", settings=narrow.settings,
                          detail=f"{ONLY_WIDEN}: {', '.join(narrow.settings)} add nothing to the company policy")
        revision = written.revision
        stored = {key: value for key, value in document_to_dict(revision.document).items() if key != "version"}
        audit.emit(conn, Action.POLICY_CHANGED, actor=audit.admin(principal.principal_id), details={
            "digest": hashlib.sha256(canonical_json(stored)).hexdigest(), "schema_version": POLICY_SCHEMA_VERSION,
            **({} if group is None else {"group": group})})
        named = revision.document.settings.get("models", {}).get("allowed", frozenset()) - {ALL_MODELS}
        known = {name for (name,) in conn.execute("SELECT name FROM row_bot_org.catalog_models"
                                                  " WHERE name = ANY(%s)", (sorted(named),))}
        answer: dict[str, Any] = {"policy": document_to_dict(revision.document),
                                  "unknown_models": sorted(named - known)}
        if group is None:
            answer["still_granted_by"] = written.still_granted_by
        return JSONResponse(answer)

    @router.put("/api/admin/policy")
    def put_company(conn: psycopg.Connection = admin, body: bytes | None = Depends(_body),
                    principal: RequestPrincipal = Depends(request_principal)) -> JSONResponse:
        return _put(conn, principal, None, body)

    @router.put("/api/admin/policy/groups/{group:path}")
    def put_group(group: str, conn: psycopg.Connection = admin, body: bytes | None = Depends(_body),
                  principal: RequestPrincipal = Depends(request_principal)) -> JSONResponse:
        return _put(conn, principal, group, body)

    @router.get("/api/admin/policy/history")
    def history(conn: psycopg.Connection = admin, limit: int = Query(50, ge=1, le=200),
                before: int | None = Query(None, ge=1)) -> JSONResponse:
        return JSONResponse({"entries": [_revision(revision)
                                         for revision in store.history(conn, limit=limit, before=before)]})

    return router


__all__ = ["policy_router"]
