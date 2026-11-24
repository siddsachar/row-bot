"""The connectors' admin routes (browser session, admins only: ``roles.admin_connection``).

- ``GET``/``POST /api/admin/connectors``; ``GET``/``PATCH``/``DELETE /api/admin/connectors/{id}``;
- ``POST /api/admin/connectors/{id}/pause``, ``/resume``, ``/sync`` (sync now) and ``/test`` (sign in and look at
  the root folder: one cheap call after the sign-in).

A connector's secret (a Microsoft client secret, a Google service account's key file) is write-only: answers say
only ``has_secret``. Each change is audited in its own transaction (``connector.changed``: which fields, never their
values); a test isn't a change. Deleting a connector deletes its documents.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Literal

import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from row_bot.audit import Action, AuditLayer, Target, TargetKind
from row_bot.audit.vocabulary import CONNECTOR_FIELDS, GROUP_PATTERN
from row_bot_org import audit
from row_bot_org.brain import store as brain_store
from row_bot_org.connectors import secrets, store
from row_bot_org.connectors.sync import ConnectorService
from row_bot_org.db.rls import principal_transaction
from row_bot_org.identity import roles
from row_bot_org.identity.gate import RequestPrincipal, request_principal

_SOURCE_ID = re.compile(r"^[A-Za-z0-9!._,-]{1,512}$")
_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_SECRET = re.compile(r"^[!-~]{1,1024}$")
_EMAIL = re.compile(r"^[^\s@]{1,256}@[^\s@]{1,253}$")
_GROUP = re.compile(GROUP_PATTERN)
MAX_MAP = 200
MAX_KEY_FILE = 16 * 1024


def _error(status: int, error: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": error, **extra}, status_code=status)


def _uuid(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _name(value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= 160 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("must be 1 to 160 characters, with no control characters")
    return value


def _source_id(value: str | None) -> str | None:
    if value is not None and not _SOURCE_ID.fullmatch(value):
        raise ValueError("must be the source's id: letters, digits and ! . _ , -")
    return value


def _guid(value: str | None) -> str | None:
    if value is not None and not _GUID.fullmatch(value):
        raise ValueError("must be a GUID")
    return None if value is None else value.lower()


def _secret(value: str | None) -> str | None:
    if value is not None and not _SECRET.fullmatch(value):
        raise ValueError("must be 1 to 1024 printable ASCII characters")
    return value


def _subject(value: str | None) -> str | None:
    if value is not None and not _EMAIL.fullmatch(value):
        raise ValueError("must be an email address")
    return value


def _principal_map(value: dict[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None
    for key, group in value.items():
        if not 1 <= len(key) <= 300 or any(ord(char) < 32 or ord(char) == 127 for char in key):
            raise ValueError("each key must be 1 to 300 characters, with no control characters")
        if not isinstance(group, str) or not _GROUP.fullmatch(group):
            raise ValueError("each group must be 1 to 256 characters, with no control characters or space at either "
                             "end")
    return dict(value)


def service_account(text: str) -> tuple[str, str]:
    """A Google service account's key file (its JSON): its email and RSA private key (PEM). ValueError, saying
    nothing of the file, when it isn't one."""
    problem = "must be a Google service account's JSON key file"
    if len(text) > MAX_KEY_FILE:
        raise ValueError(problem)
    try:
        found = json.loads(text)
    except ValueError:
        raise ValueError(problem) from None
    if not isinstance(found, dict) or found.get("type") != "service_account":
        raise ValueError(problem)
    email, key = found.get("client_email"), found.get("private_key")
    if not isinstance(email, str) or not _EMAIL.fullmatch(email) or len(email) > 320 or not isinstance(key, str):
        raise ValueError(problem)
    try:
        loaded = serialization.load_pem_private_key(key.encode("utf-8"), password=None)
    except (TypeError, ValueError):
        raise ValueError(problem) from None
    if not isinstance(loaded, rsa.RSAPrivateKey):
        raise ValueError(problem)
    return email, key


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectorIn(_Body):
    kind: Literal["microsoft", "google"]
    space_id: uuid.UUID
    name: str
    drive_id: str | None = None
    folder_id: str | None = None
    # Microsoft: the app registration.
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    # Google: the service account's key file, and the user it acts as with domain-wide delegation.
    service_account: str | None = None
    subject: str | None = None
    principal_map: dict[str, str] = Field(default_factory=dict, max_length=MAX_MAP)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _name(value)

    @field_validator("drive_id", "folder_id")
    @classmethod
    def _check_source(cls, value: str | None) -> str | None:
        return _source_id(value)

    @field_validator("tenant_id", "client_id")
    @classmethod
    def _check_guid(cls, value: str | None) -> str | None:
        return _guid(value)

    @field_validator("client_secret")
    @classmethod
    def _check_secret(cls, value: str | None) -> str | None:
        return _secret(value)

    @field_validator("subject")
    @classmethod
    def _check_subject(cls, value: str | None) -> str | None:
        return _subject(value)

    @field_validator("principal_map")
    @classmethod
    def _check_map(cls, value: dict[str, str]) -> dict[str, str]:
        return _principal_map(value)

    @model_validator(mode="after")
    def _for_its_kind(self) -> ConnectorIn:
        if self.kind == "microsoft":
            if not (self.drive_id and self.tenant_id and self.client_id and self.client_secret):
                raise ValueError("a Microsoft connector needs drive_id, tenant_id, client_id and client_secret")
            if self.service_account is not None or self.subject is not None:
                raise ValueError("service_account and subject are for Google connectors")
        else:
            if not (self.drive_id or self.folder_id) or self.service_account is None:
                raise ValueError("a Google connector needs drive_id or folder_id, and service_account")
            if self.tenant_id is not None or self.client_id is not None or self.client_secret is not None:
                raise ValueError("tenant_id, client_id and client_secret are for Microsoft connectors")
            service_account(self.service_account)
        return self


class ConnectorChange(_Body):
    name: str | None = None
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    service_account: str | None = None
    # null stops acting as a user (no domain-wide delegation).
    subject: str | None = None
    principal_map: dict[str, str] | None = Field(None, max_length=MAX_MAP)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str | None) -> str | None:
        return None if value is None else _name(value)

    @field_validator("tenant_id", "client_id")
    @classmethod
    def _check_guid(cls, value: str | None) -> str | None:
        return _guid(value)

    @field_validator("client_secret")
    @classmethod
    def _check_secret(cls, value: str | None) -> str | None:
        return _secret(value)

    @field_validator("subject")
    @classmethod
    def _check_subject(cls, value: str | None) -> str | None:
        return _subject(value)

    @field_validator("principal_map")
    @classmethod
    def _check_map(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return _principal_map(value)

    @model_validator(mode="after")
    def _key_file(self) -> ConnectorChange:
        if self.service_account is not None:
            service_account(self.service_account)
        return self


def _layer(space: brain_store.SpaceRow | None) -> AuditLayer:
    return AuditLayer.COMPANY if space is not None and space.kind == brain_store.COMPANY else AuditLayer.TEAM


def connectors_router(service: ConnectorService, brain: Any) -> APIRouter:
    router = APIRouter()
    caller_of = Depends(request_principal)
    admin = Depends(roles.admin_connection, scope="function")

    def shown(conn: psycopg.Connection, row: store.ConnectorRow) -> dict[str, Any]:
        return store.to_json(row, store.counts(conn, row.id))

    def changed(conn: psycopg.Connection, principal: RequestPrincipal, row: store.ConnectorRow, change: str,
                **details: Any) -> None:
        audit.emit(conn, Action.CONNECTOR_CHANGED, actor=audit.admin(principal.principal_id),
                   target=Target(TargetKind.CONNECTOR, row.id), layer=_layer(brain_store.get_space(conn, row.space_id)),
                   details={"change": change, "kind": row.kind, "space_id": row.space_id, **details})

    def found(conn: psycopg.Connection, connector_id: str) -> store.ConnectorRow | JSONResponse:
        known = _uuid(connector_id)
        row = None if known is None else store.get_connector(conn, known)
        return _error(404, "connector_not_found") if row is None else row

    @router.get("/api/admin/connectors")
    def connectors(conn: psycopg.Connection = admin) -> JSONResponse:
        return JSONResponse({"connectors": [shown(conn, row) for row in store.list_connectors(conn)]})

    @router.post("/api/admin/connectors")
    def create(body: ConnectorIn, conn: psycopg.Connection = admin,
               principal: RequestPrincipal = caller_of) -> JSONResponse:
        space = brain_store.get_space(conn, str(body.space_id))
        if space is None:
            return _error(404, "space_not_found")
        connector_id = str(uuid.uuid4())
        if body.kind == "microsoft":
            client_id, secret = body.client_id, body.client_secret
        else:
            client_id, secret = service_account(body.service_account)
        sealed = secrets.seal(service.server_key, connector_id, body.kind, body.tenant_id, client_id, secret)
        row = store.create_connector(conn, connector_id=connector_id, kind=body.kind, space_id=space.id,
                                     name=body.name, drive_id=body.drive_id, folder_id=body.folder_id,
                                     tenant_id=body.tenant_id, client_id=client_id, subject=body.subject,
                                     secret_sealed=sealed, principal_map=body.principal_map)
        changed(conn, principal, row, "created")
        brain.wake()
        return JSONResponse(shown(conn, row), status_code=201)

    @router.get("/api/admin/connectors/{connector_id}")
    def connector(connector_id: str, conn: psycopg.Connection = admin) -> JSONResponse:
        row = found(conn, connector_id)
        return row if isinstance(row, JSONResponse) else JSONResponse(shown(conn, row))

    @router.patch("/api/admin/connectors/{connector_id}")
    def change(connector_id: str, body: ConnectorChange, conn: psycopg.Connection = admin,
               principal: RequestPrincipal = caller_of) -> JSONResponse:
        row = found(conn, connector_id)
        if isinstance(row, JSONResponse):
            return row
        given = body.model_fields_set
        tenant_id, client_id, subject, secret = row.tenant_id, row.client_id, row.subject, None
        if row.kind == "microsoft":
            if given & {"service_account", "subject"}:
                return _error(422, "not_for_this_kind")
            tenant_id = body.tenant_id or row.tenant_id
            client_id = body.client_id or row.client_id
            secret = body.client_secret
            if (tenant_id, client_id) != (row.tenant_id, row.client_id) and secret is None:
                return _error(422, "secret_required")
        else:
            if given & {"tenant_id", "client_id", "client_secret"}:
                return _error(422, "not_for_this_kind")
            if body.service_account is not None:
                client_id, secret = service_account(body.service_account)
            if "subject" in given:
                subject = body.subject
        name = body.name or row.name
        principal_map = row.principal_map if body.principal_map is None else body.principal_map
        credentials = secret is not None or subject != row.subject
        fields = [field for field, touched in zip(CONNECTOR_FIELDS, (
            name != row.name, credentials, principal_map != row.principal_map)) if touched]
        if not fields:
            return JSONResponse(shown(conn, row))
        sealed = None if secret is None else secrets.seal(service.server_key, row.id, row.kind, tenant_id,
                                                          client_id, secret)
        updated = store.update_connector(conn, row.id, name=name, tenant_id=tenant_id, client_id=client_id,
                                         subject=subject, secret_sealed=sealed, principal_map=principal_map,
                                         run_as=principal.principal_id, credentials_changed=credentials,
                                         map_changed="principal_map" in fields)
        if updated is None:
            return _error(404, "connector_not_found")
        changed(conn, principal, updated, "updated", fields=",".join(fields))
        brain.wake()
        return JSONResponse(shown(conn, updated))

    def set_state(connector_id: str, conn: psycopg.Connection, principal: RequestPrincipal, state: str,
                  change: str) -> JSONResponse:
        row = found(conn, connector_id)
        if isinstance(row, JSONResponse):
            return row
        if store.set_state(conn, row.id, state, run_as=principal.principal_id if state == "active" else None):
            row = store.get_connector(conn, row.id)
            changed(conn, principal, row, change)
            if state == "active":
                brain.wake()
        return JSONResponse(shown(conn, row))

    @router.post("/api/admin/connectors/{connector_id}/pause")
    def pause(connector_id: str, conn: psycopg.Connection = admin,
              principal: RequestPrincipal = caller_of) -> JSONResponse:
        return set_state(connector_id, conn, principal, "paused", "paused")

    @router.post("/api/admin/connectors/{connector_id}/resume")
    def resume(connector_id: str, conn: psycopg.Connection = admin,
               principal: RequestPrincipal = caller_of) -> JSONResponse:
        return set_state(connector_id, conn, principal, "active", "resumed")

    @router.post("/api/admin/connectors/{connector_id}/sync")
    def sync_now(connector_id: str, conn: psycopg.Connection = admin,
                 principal: RequestPrincipal = caller_of) -> JSONResponse:
        row = found(conn, connector_id)
        if isinstance(row, JSONResponse):
            return row
        if row.state != "active":
            return _error(409, "connector_not_active")
        store.sync_now(conn, row.id)
        changed(conn, principal, row, "sync_requested")
        brain.wake()
        return JSONResponse(shown(conn, store.get_connector(conn, row.id)), status_code=202)

    @router.post("/api/admin/connectors/{connector_id}/test")
    def test(connector_id: str, request: Request, principal: RequestPrincipal = caller_of) -> JSONResponse:
        # Not through admin_connection: the call to the source is made after this transaction ends.
        with principal_transaction(request.app.state.pool, principal.principal_id, principal.groups) as conn:
            if not roles.enter_admin(conn, principal, request.app.state.settings):
                raise roles.AdminRequired
            row = found(conn, connector_id)
            sealed = None if isinstance(row, JSONResponse) else store.sealed_secret(conn, row.id)
        if isinstance(row, JSONResponse):
            return row
        problem = service.check(row, sealed)
        return JSONResponse({"ok": problem is None, "problem": problem})

    @router.delete("/api/admin/connectors/{connector_id}")
    def delete(connector_id: str, conn: psycopg.Connection = admin,
               principal: RequestPrincipal = caller_of) -> Response:
        row = found(conn, connector_id)
        if isinstance(row, JSONResponse):
            return row
        removed = store.delete_connector(conn, row.id)
        if removed is None:
            return _error(404, "connector_not_found")
        changed(conn, principal, row, "deleted", documents_removed=removed)
        return Response(status_code=204)

    return router


__all__ = ["ConnectorChange", "ConnectorIn", "connectors_router", "service_account"]
