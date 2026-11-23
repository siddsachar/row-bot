"""The device registry: the Row-Bots people connect (migration 0006).

For a signed-in person, only for their own devices (row-level security):
- ``POST /api/devices`` with ``{"name", "platform": "windows" | "macos" | "linux", "app_version"}``: a new
  device, 201 with its record (``id`` is the device's id), recorded as ``device.joined``. With ``"id"`` of one
  of their devices it registers that one again (name and version refreshed): 200, or 403 ``device_revoked``.
- ``POST /api/devices/{id}/seen``, optionally with ``{"app_version"}``: refreshes ``last_seen_at``; 403
  ``device_revoked`` once an admin revoked it, 404 when it isn't theirs.

For admins: ``GET /api/admin/devices`` (``person`` a principal id, ``revoked`` true or false) and
``POST /api/admin/devices/{id}/revoke`` (``device.removed``, reason ``revoked``; revoking again changes
nothing).

**Device credentials (C1a, ``identity.device``).** A Row-Bot that joins through ``/auth/device`` gets a device
here (``create_device``, ``device.joined``), or joins again as its own device (``rejoin_device``), and bearer
tokens bound to its id. Every bearer request checks that the device's ``revoked_at`` is NULL and its person
active, so an admin's revoke stops the credential at once. With a bearer, ``id`` above must be the bearer's own
device (403 ``device_mismatch``).
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from row_bot.audit import Action, Target, TargetKind
from row_bot.audit.vocabulary import VERSION_PATTERN
from row_bot_org import audit
from row_bot_org.identity import roles
from row_bot_org.identity.gate import RequestPrincipal, request_principal
from row_bot_org.identity.web import principal_connection

_COLUMNS = "id, principal_id, name, platform, app_version, first_seen_at, last_seen_at, revoked_at"
_VERSION = re.compile(VERSION_PATTERN)


class DeviceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    platform: Literal["windows", "macos", "linux"]
    app_version: str
    id: uuid.UUID | None = None

    @field_validator("name")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("must have no control characters and no space at either end")
        return value

    @field_validator("app_version")
    @classmethod
    def _version(cls, value: str) -> str:
        if not _VERSION.fullmatch(value):
            raise ValueError("must be a version such as 5.4.0")
        return value


class SeenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_version: str | None = None

    @field_validator("app_version")
    @classmethod
    def _version(cls, value: str | None) -> str | None:
        return value if value is None else DeviceIn._version(value)


def _device(row: tuple) -> dict[str, Any]:
    return {"id": str(row[0]), "principal_id": str(row[1]), "name": row[2], "platform": row[3],
            "app_version": row[4], "first_seen_at": row[5].isoformat(), "last_seen_at": row[6].isoformat(),
            "revoked_at": row[7].isoformat() if row[7] else None}


def _error(status: int, error: str) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status)


def _uuid(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _refresh(conn: psycopg.Connection, device_id: str, changes: dict[str, str]) -> JSONResponse:
    """Refresh one of the caller's devices: 200 with it, 403 when revoked, 404 when it isn't theirs."""
    sets = "".join(f", {name} = %({name})s" for name in changes)  # Names from the request models only.
    row = conn.execute(f"UPDATE row_bot_org.devices SET last_seen_at = now(){sets} WHERE id = %(id)s"
                       f" RETURNING {_COLUMNS}", {**changes, "id": device_id}).fetchone()
    if row is not None:
        return JSONResponse(_device(row))
    found = conn.execute("SELECT revoked_at FROM row_bot_org.devices WHERE id = %s", (device_id,)).fetchone()
    return _error(404, "device_not_found") if found is None else _error(403, "device_revoked")


def create_device(conn: psycopg.Connection, *, name: str, platform: str,
                  app_version: str) -> dict[str, Any]:
    """A new device for the current principal (``conn`` is inside its principal_transaction). Its
    ``device.joined`` event is ``record_joined``'s, emitted last in the same transaction."""
    row = conn.execute(f"INSERT INTO row_bot_org.devices (name, platform, app_version) VALUES (%s, %s, %s)"
                       f" RETURNING {_COLUMNS}", (name, platform, app_version)).fetchone()
    return _device(row)


def rejoin_device(conn: psycopg.Connection, device_id: str, *, name: str, app_version: str) -> dict[str, Any] | None:
    """One of the current principal's live devices, its name and version refreshed; None when it isn't one."""
    row = conn.execute(f"UPDATE row_bot_org.devices SET name = %s, app_version = %s, last_seen_at = now()"
                       f" WHERE id = %s AND revoked_at IS NULL RETURNING {_COLUMNS}",
                       (name, app_version, device_id)).fetchone()
    return None if row is None else _device(row)


def record_joined(conn: psycopg.Connection, principal_id: str, device: dict[str, Any]) -> None:
    audit.emit(conn, Action.DEVICE_JOINED, actor=audit.person(principal_id),
               target=Target(TargetKind.DEVICE, device["id"]), device_id=device["id"],
               details={"platform": device["platform"], "app_version": device["app_version"]})


def _not_the_bearers(principal: RequestPrincipal, device_id: str) -> bool:
    return principal.device_id is not None and principal.device_id != device_id


def devices_router() -> APIRouter:
    router = APIRouter()
    admin = Depends(roles.admin_connection, scope="function")
    signed_in = Depends(principal_connection, scope="function")

    @router.post("/api/devices")
    def register(body: DeviceIn, principal: RequestPrincipal = Depends(request_principal),
                 conn: psycopg.Connection = signed_in) -> JSONResponse:
        if body.id is not None:
            if _not_the_bearers(principal, str(body.id)):
                return _error(403, "device_mismatch")
            return _refresh(conn, str(body.id), {"name": body.name, "app_version": body.app_version})
        device = create_device(conn, name=body.name, platform=body.platform,
                               app_version=body.app_version)
        record_joined(conn, principal.principal_id, device)
        return JSONResponse(device, status_code=201)

    @router.post("/api/devices/{device_id}/seen")
    def seen(device_id: str, body: SeenIn | None = None, principal: RequestPrincipal = Depends(request_principal),
             conn: psycopg.Connection = signed_in) -> JSONResponse:
        found = _uuid(device_id)
        if found is None:
            return _error(404, "device_not_found")
        if _not_the_bearers(principal, found):
            return _error(403, "device_mismatch")
        return _refresh(conn, found, {} if body is None or body.app_version is None
                        else {"app_version": body.app_version})

    @router.get("/api/admin/devices")
    def list_devices(person: str | None = None, revoked: bool | None = None,
                     conn: psycopg.Connection = admin) -> JSONResponse:
        if person is not None and (person := _uuid(person)) is None:
            return JSONResponse({"devices": []})
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM row_bot_org.devices"
            " WHERE (%(person)s::uuid IS NULL OR principal_id = %(person)s::uuid)"
            " AND (%(revoked)s::boolean IS NULL OR (revoked_at IS NOT NULL) = %(revoked)s::boolean)"
            " ORDER BY last_seen_at DESC, id", {"person": person, "revoked": revoked}).fetchall()
        return JSONResponse({"devices": [_device(row) for row in rows]})

    @router.post("/api/admin/devices/{device_id}/revoke")
    def revoke(device_id: str, conn: psycopg.Connection = admin,
               principal: RequestPrincipal = Depends(request_principal)) -> JSONResponse:
        found = _uuid(device_id)
        if found is None:
            return _error(404, "device_not_found")
        row = conn.execute(f"UPDATE row_bot_org.devices SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL"
                           f" RETURNING {_COLUMNS}", (found,)).fetchone()
        if row is not None:
            audit.emit(conn, Action.DEVICE_REMOVED, actor=audit.admin(principal.principal_id),
                       target=Target(TargetKind.DEVICE, found), details={"reason": "revoked"})
        else:
            row = conn.execute(f"SELECT {_COLUMNS} FROM row_bot_org.devices WHERE id = %s", (found,)).fetchone()
        return JSONResponse(_device(row)) if row else _error(404, "device_not_found")

    return router


__all__ = ["DeviceIn", "SeenIn", "create_device", "devices_router", "record_joined", "rejoin_device"]
