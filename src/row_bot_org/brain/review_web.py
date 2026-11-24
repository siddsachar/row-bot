"""The review queue's routes (``row_bot_org.brain``, migration 0010): shares people offer to a company space, and
their owners' (or an admin's) decisions.

For anyone signed in (a browser session or a device's bearer), always as a member:

- ``POST /api/brain/spaces/{id}/submissions``: ``{"title", "text", "source_note"}`` to a space they read, while
  their company's policy allows Share to company (``data.share_to_company``) and they have fewer than
  ``MAX_PENDING`` waiting (else 429 ``too_many_pending``);
- ``GET /api/brain/submissions?mine=1&status=``: their own, whatever became of them;
- ``POST /api/brain/submissions/{id}/withdraw``: one of their own, while it waits.

For a space's owners, and admins (any space), in a browser session only (a bearer gets 403
``browser_session_required``):

- ``GET /api/brain/review?space=&status=``: the submissions of the spaces they review (``pending`` unless
  ``status`` says otherwise: ``approved``, ``rejected``, ``withdrawn`` or ``all``), with those spaces and how many
  wait;
- ``POST /api/brain/review/{id}/approve``: ``{"title", "text", "space_id"}``, each optional (the share as sent, in
  its space, unless edited or moved to another space the reviewer manages). In one transaction it becomes a fact
  of that space, written by the reviewer with the share's submitter as its source;
- ``POST /api/brain/review/{id}/reject``: ``{"reason"}``.

A submission holds what a person wrote in a chat: before approval only its submitter and its space's managers see
it (row-level security), and to anyone else it is 404 exactly like one that doesn't exist. Each step is audited in
its own transaction as ``share.submitted``, ``share.withdrawn``, ``share.approved`` (with ``fact.changed`` for the
fact it made) or ``share.rejected``: ids and kinds only, never the title, text, note or reason.
"""

from __future__ import annotations

import uuid
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import field_validator

from row_bot.audit import Action, AuditLayer, Target, TargetKind
from row_bot_org import audit
from row_bot_org.brain import review_store, store
from row_bot_org.brain.review_store import SubmissionRow
from row_bot_org.brain.service import BrainService, owns
from row_bot_org.brain.web import Caller, _actor, _Body, _error, _layer, _managed, _plain, _uuid, manager_connection
from row_bot_org.identity.gate import RequestPrincipal, request_principal
from row_bot_org.identity.web import principal_connection
from row_bot_org.policy import store as policy_store

MAX_TITLE = 200
MAX_TEXT = 8000
MAX_NOTE = 500
MAX_REASON = 500
# What a fact holds (0008): an approved share's text must fit, edited if need be.
MAX_FACT = 2000
# A share becomes a fact: CON-1's item kind for it.
ITEM_KIND = "fact"

Shown = Literal["pending", "approved", "rejected", "withdrawn", "all"]


class SubmissionIn(_Body):
    title: str
    text: str
    source_note: str | None = None

    @field_validator("title")
    @classmethod
    def _check_title(cls, value: str) -> str:
        return _plain(value, longest=MAX_TITLE)

    @field_validator("text")
    @classmethod
    def _check_text(cls, value: str) -> str:
        return _plain(value, longest=MAX_TEXT, lines=True)

    @field_validator("source_note")
    @classmethod
    def _check_note(cls, value: str | None) -> str | None:
        return None if value is None or not value.strip() else _plain(value, longest=MAX_NOTE)


class ApproveIn(_Body):
    title: str | None = None
    text: str | None = None
    space_id: uuid.UUID | None = None

    @field_validator("title")
    @classmethod
    def _check_title(cls, value: str | None) -> str | None:
        return None if value is None else _plain(value, longest=MAX_TITLE)

    @field_validator("text")
    @classmethod
    def _check_text(cls, value: str | None) -> str | None:
        return None if value is None else _plain(value, longest=MAX_TEXT, lines=True)


class RejectIn(_Body):
    reason: str

    @field_validator("reason")
    @classmethod
    def _check_reason(cls, value: str) -> str:
        return _plain(value, longest=MAX_REASON, lines=True)


def _status(shown: Shown) -> str | None:
    return None if shown == "all" else shown


def review_router(service: BrainService) -> APIRouter:
    router = APIRouter()
    caller_of = Depends(request_principal)
    manager = Depends(manager_connection, scope="function")
    member = Depends(principal_connection, scope="function")

    def share_event(conn: psycopg.Connection, action: Action, actor: audit.Actor, layer: AuditLayer, space_id: str,
                    submission_id: str, device_id: str | None, **details: str) -> None:
        audit.emit(conn, action, actor=actor, layer=layer, target=Target(TargetKind.SHARE, submission_id),
                   device_id=device_id, details={"item_kind": ITEM_KIND, "space_id": space_id, **details})

    # -- the person who shares -------------------------------------------------------------------

    @router.post("/api/brain/spaces/{space_id}/submissions")
    def submit(space_id: str, body: SubmissionIn, conn: psycopg.Connection = member,
               principal: RequestPrincipal = caller_of) -> JSONResponse:
        found = _uuid(space_id)
        space = None if found is None else store.get_space(conn, found)
        if space is None:
            return _error(404, "space_not_found")
        if not policy_store.effective(conn, principal.groups).data.share_to_company:
            return _error(403, "share_to_company_off")
        review_store.hold_submitter(conn, principal.principal_id)
        if review_store.pending_count(conn, principal.principal_id) >= review_store.MAX_PENDING:
            return _error(429, "too_many_pending", max_pending=review_store.MAX_PENDING)
        submission = review_store.add_submission(conn, space_id=space.id, title=body.title, text=body.text,
                                                 source_note=body.source_note, submitter_groups=principal.groups)
        share_event(conn, Action.SHARE_SUBMITTED, audit.person(principal.principal_id), _layer(space), space.id,
                    submission.id, principal.device_id)
        return JSONResponse(submission.to_json(), status_code=201)

    @router.get("/api/brain/submissions")
    def mine(mine: bool = Query(), status: Shown = "all", conn: psycopg.Connection = member,
             principal: RequestPrincipal = caller_of) -> JSONResponse:
        # Only one's own are listed here (the review routes list a space's).
        if not mine:
            return _error(422, "mine_required")
        rows, truncated = review_store.list_own(conn, principal.principal_id, _status(status))
        return JSONResponse({"submissions": [row.to_json() for row in rows], "truncated": truncated})

    @router.post("/api/brain/submissions/{submission_id}/withdraw")
    def withdraw(submission_id: str, conn: psycopg.Connection = member,
                 principal: RequestPrincipal = caller_of) -> JSONResponse:
        found = _uuid(submission_id)
        current = None if found is None else review_store.get_submission(conn, found)
        if current is None or current.submitted_by != principal.principal_id:
            return _error(404, "submission_not_found")
        withdrawn = review_store.withdraw(conn, current.id, principal.principal_id)
        if withdrawn is None:
            decided = review_store.get_submission(conn, current.id)
            return _error(409, "submission_decided", status=decided.status if decided else current.status)
        # A space its submitter no longer reads is a group space (everyone reads the company space).
        space = store.get_space(conn, current.space_id)
        share_event(conn, Action.SHARE_WITHDRAWN, audit.person(principal.principal_id),
                    AuditLayer.TEAM if space is None else _layer(space), current.space_id, current.id,
                    principal.device_id)
        return JSONResponse(withdrawn.to_json())

    # -- reviewers: a space's owners, and admins, in a browser session ---------------------------

    def reviewed(caller: Caller, submission_id: str) -> tuple[SubmissionRow, store.SpaceRow] | JSONResponse:
        """The pending submission (locked) and its space, when the caller decides it; else the refusal."""
        if caller.principal.device_id is not None:
            return _error(403, "browser_session_required")
        found = _uuid(submission_id)
        current = None if found is None else review_store.get_submission(caller.conn, found)
        if current is None:
            return _error(404, "submission_not_found")
        space = _managed(caller, current.space_id)
        if isinstance(space, JSONResponse):
            # Its submitter sees it, but doesn't decide it; anyone else can't see it at all.
            return space if space.status_code == 403 else _error(404, "submission_not_found")
        current = review_store.get_submission(caller.conn, current.id, lock=True)
        if current is None:
            return _error(404, "submission_not_found")
        if current.status != review_store.PENDING:
            return _error(409, "submission_decided", status=current.status)
        return current, space

    @router.get("/api/brain/review")
    def queue(space: str | None = None, status: Shown = "pending", caller: Caller = manager) -> JSONResponse:
        if caller.principal.device_id is not None:
            return _error(403, "browser_session_required")
        spaces = [row for row in store.list_spaces(caller.conn) if caller.admin or owns(caller.principal, row)]
        named = spaces
        if space is not None:
            chosen = _managed(caller, space)
            if isinstance(chosen, JSONResponse):
                return chosen
            named = [chosen]
        ids = [row.id for row in named]
        rows, truncated = review_store.list_for_review(caller.conn, ids, _status(status))
        return JSONResponse({
            "submissions": [row.to_json() for row in rows], "truncated": truncated,
            "pending_count": review_store.count_pending(caller.conn, ids),
            "spaces": [{"id": row.id, "name": row.name, "kind": row.kind} for row in spaces],
        })

    @router.post("/api/brain/review/{submission_id}/approve")
    def approve(submission_id: str, body: ApproveIn, caller: Caller = manager) -> JSONResponse:
        found = reviewed(caller, submission_id)
        if isinstance(found, JSONResponse):
            return found
        current, space = found
        target = space
        if body.space_id is not None and str(body.space_id) != space.id:
            target = _managed(caller, str(body.space_id))
            if isinstance(target, JSONResponse):
                return target
        title = body.title if body.title is not None else current.title
        text = body.text if body.text is not None else current.text
        if len(text) > MAX_FACT:
            return _error(422, "text_too_long_for_a_fact", max_length=MAX_FACT)
        fact_id = review_store.add_fact_from(caller.conn, current, space_id=target.id, text=text,
                                             writer_groups=caller.principal.groups)
        approved = review_store.approve(caller.conn, current.id, principal_id=caller.principal.principal_id,
                                        space_id=target.id, title=title, text=text, fact_id=fact_id)
        fact = store.get_fact(caller.conn, fact_id)
        details: dict[str, str] = {"fact_id": fact_id}
        edited = [field for field, before, after in (("title", current.title, title), ("text", current.text, text))
                  if before != after]
        if edited:
            details["edited"] = ",".join(edited)
        if target.id != space.id:
            details["from_space_id"] = space.id
        audit.emit(caller.conn, Action.FACT_CHANGED, actor=_actor(caller), layer=_layer(target),
                   target=Target(TargetKind.FACT, fact_id), details={"change": "created", "space_id": target.id})
        share_event(caller.conn, Action.SHARE_APPROVED, _actor(caller), _layer(target), target.id, current.id, None,
                    **details)
        service.wake()
        return JSONResponse({"submission": approved.to_json(), "fact": fact})

    @router.post("/api/brain/review/{submission_id}/reject")
    def reject(submission_id: str, body: RejectIn, caller: Caller = manager) -> JSONResponse:
        found = reviewed(caller, submission_id)
        if isinstance(found, JSONResponse):
            return found
        current, space = found
        rejected = review_store.reject(caller.conn, current.id, principal_id=caller.principal.principal_id,
                                       reason=body.reason)
        share_event(caller.conn, Action.SHARE_REJECTED, _actor(caller), _layer(space), space.id, current.id, None)
        return JSONResponse(rejected.to_json())

    return router


__all__ = ["MAX_FACT", "MAX_NOTE", "MAX_REASON", "MAX_TEXT", "MAX_TITLE", "review_router"]
