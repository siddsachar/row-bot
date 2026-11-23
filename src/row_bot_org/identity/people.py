"""The people admin API, under ``/api/admin/people`` (admins only, ``roles.admin_connection``):

- ``GET /api/admin/people``: everyone who has signed in: ``id``, ``display_name``, ``email``, ``groups`` (from
  their last sign-in), ``role`` (stored: ``admin`` or ``member``), ``admin`` (the stored role or an admin
  group), ``last_sign_in_at`` and ``suspended``.
- ``PUT /api/admin/people/{id}/role`` with ``{"role": "admin" | "member"}``: set or clear the stored admin role.
- ``POST /api/admin/people/{id}/suspend`` and ``.../resume``: a suspended person's sessions are revoked and
  sign-in refuses them until they are resumed.

An admin can't change their own role or suspend themself (409 ``cannot_change_your_own_role`` /
``cannot_suspend_yourself``).
Each change emits an audit event: ``role.changed`` (role ``admin``, change ``granted`` or ``removed``),
``person.suspended`` or ``person.resumed``.
"""

from __future__ import annotations

import uuid
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from row_bot_org import audit
from row_bot_org.identity import roles, store
from row_bot_org.identity.gate import RequestPrincipal, request_principal


class RoleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["admin", "member"]


def _error(status: int, error: str) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status)


def person_dict(person: store.Person, admin_groups: frozenset[str]) -> dict[str, object]:
    return {"id": person.id, "display_name": person.display_name, "email": person.email,
            "groups": list(person.groups), "role": person.role,
            "admin": person.role == roles.ADMIN or bool(admin_groups & set(person.groups)),
            "last_sign_in_at": person.last_sign_in_at.isoformat(), "suspended": person.status == "suspended"}


def role_changed(conn: psycopg.Connection, *, actor: str, target: str, granted: bool, via: str) -> None:
    audit.emit(conn, "role.changed", actor=actor, details={
        "target": target, "role": roles.ADMIN, "change": "granted" if granted else "removed", "via": via})


def people_router() -> APIRouter:
    router = APIRouter()
    admin = Depends(roles.admin_connection, scope="function")

    def answer(request: Request, conn: psycopg.Connection, principal_id: str) -> JSONResponse:
        person = store.get_person(conn, principal_id)
        if person is None:
            return _error(404, "person_not_found")
        return JSONResponse(person_dict(person, roles.admin_groups(request.app.state.settings)))

    def target(principal_id: str) -> str | None:
        try:
            return str(uuid.UUID(principal_id))
        except ValueError:
            return None

    @router.get("/api/admin/people")
    def people(request: Request, conn: psycopg.Connection = admin) -> JSONResponse:
        groups = roles.admin_groups(request.app.state.settings)
        return JSONResponse({"people": [person_dict(person, groups) for person in store.list_people(conn)]})

    @router.put("/api/admin/people/{principal_id}/role")
    def set_role(principal_id: str, body: RoleIn, request: Request, conn: psycopg.Connection = admin,
                 principal: RequestPrincipal = Depends(request_principal)) -> JSONResponse:
        found = target(principal_id)
        # Not even a grant: an admin through admin_groups would keep a stored role the provider can't take back.
        if found == principal.principal_id:
            return _error(409, "cannot_change_your_own_role")
        previous = store.set_role(conn, found, body.role) if found else None
        if previous is None:
            return _error(404, "person_not_found")
        if previous != body.role:
            role_changed(conn, actor=principal.principal_id, target=found, granted=body.role == roles.ADMIN,
                         via="api")
        return answer(request, conn, found)

    def suspension(request: Request, conn: psycopg.Connection, principal: RequestPrincipal, principal_id: str,
                   status: str) -> JSONResponse:
        found = target(principal_id)
        if found == principal.principal_id:
            return _error(409, "cannot_suspend_yourself")
        previous = store.set_status(conn, found, status) if found else None
        if previous is None:
            return _error(404, "person_not_found")
        if previous != status:
            audit.emit(conn, "person.suspended" if status == "suspended" else "person.resumed",
                       actor=principal.principal_id, details={"target": found})
        return answer(request, conn, found)

    @router.post("/api/admin/people/{principal_id}/suspend")
    def suspend(principal_id: str, request: Request, conn: psycopg.Connection = admin,
                principal: RequestPrincipal = Depends(request_principal)) -> JSONResponse:
        return suspension(request, conn, principal, principal_id, "suspended")

    @router.post("/api/admin/people/{principal_id}/resume")
    def resume(principal_id: str, request: Request, conn: psycopg.Connection = admin,
               principal: RequestPrincipal = Depends(request_principal)) -> JSONResponse:
        return suspension(request, conn, principal, principal_id, "active")

    return router


__all__ = ["RoleIn", "people_router", "person_dict", "role_changed"]
