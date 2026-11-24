"""The company brain's routes (``row_bot_org.brain``).

For anyone signed in (a browser session or a device's bearer token):

- ``GET /api/brain/spaces``: the spaces they read, each with ``is_owner``;
- ``POST /api/brain/recall``: ``{"query", "k", "spaces"}`` to ranked hits with citations (``service.recall``).

For a space's owners, and admins: the space's documents and facts.

- ``PATCH /api/brain/spaces/{id}`` renames it;
- ``GET`` and ``POST /api/brain/spaces/{id}/documents`` (the upload: ``multipart/form-data`` with ``file`` and an
  optional ``title``), ``GET``, ``PUT`` (replace, same form) and ``DELETE /api/brain/documents/{id}``;
- ``GET`` and ``POST /api/brain/spaces/{id}/facts``, ``PATCH`` and ``DELETE /api/brain/facts/{id}``,
  ``POST /api/brain/facts/{id}/retire``.

Reading these needs reading the space; changing them needs owning it, or an admin's browser session (``roles``:
these routes act as an admin for one, never for a bearer). Recall and the space list never act as an admin: an
admin finds there what their groups give them, like anyone.

For admins only: ``GET``/``POST /api/admin/brain/spaces`` and ``PATCH``/``DELETE /api/admin/brain/spaces/{id}``,
which set who reads a space and who owns it.

A space, document or fact the caller can't read is ``404`` exactly like one that doesn't exist. Each change is
audited in its own transaction: ``space.changed``, ``document.uploaded`` (an upload or a replace),
``document.removed`` and ``fact.changed``.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import Message
from email.utils import collapse_rfc2231_value
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from row_bot.audit import Action, AuditLayer, Target, TargetKind
from row_bot.audit.vocabulary import GROUP_PATTERN
from row_bot.brain import InvalidRecord
from row_bot_org import audit
from row_bot_org.brain import parse, store
from row_bot_org.brain.service import BrainService, SpaceNotFound, owns
from row_bot_org.db.rls import principal_transaction
from row_bot_org.identity import roles
from row_bot_org.identity.gate import RequestPrincipal, request_principal
from row_bot_org.identity.web import principal_connection

# Room in an upload's body for the form's own parts (boundaries, headers, the title) beside the file.
MULTIPART_OVERHEAD = 64 * 1024
_GROUP = re.compile(GROUP_PATTERN)
MAX_LIST = 64


@dataclass(frozen=True)
class Caller:
    """A request's transaction, and whether it acts as an admin's."""

    conn: psycopg.Connection
    principal: RequestPrincipal
    admin: bool


@contextmanager
def _managing(request: Request, principal: RequestPrincipal) -> Iterator[Caller]:
    with principal_transaction(request.app.state.pool, principal.principal_id, principal.groups) as conn:
        yield Caller(conn, principal, roles.enter_admin(conn, principal, request.app.state.settings))


def manager_connection(request: Request, principal: RequestPrincipal = Depends(request_principal)) -> Iterator[Caller]:
    """The caller's transaction, acting as an admin's when the caller is an admin in a browser session."""
    with _managing(request, principal) as caller:
        yield caller


def _error(status: int, error: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": error, **extra}, status_code=status)


def _uuid(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _layer(space: store.SpaceRow) -> AuditLayer:
    return AuditLayer.COMPANY if space.kind == store.COMPANY else AuditLayer.TEAM


def _actor(caller: Caller) -> audit.Actor:
    return audit.admin(caller.principal.principal_id) if caller.admin else audit.person(caller.principal.principal_id)


def _space_seen(principal: RequestPrincipal, space: store.SpaceRow) -> dict[str, Any]:
    return {"id": space.id, "name": space.name, "kind": space.kind, "is_owner": owns(principal, space)}


def _readable(caller: Caller, space_id: str) -> store.SpaceRow | JSONResponse:
    found = _uuid(space_id)
    space = None if found is None else store.get_space(caller.conn, found)
    return _error(404, "space_not_found") if space is None else space


def _managed(caller: Caller, space_id: str) -> store.SpaceRow | JSONResponse:
    space = _readable(caller, space_id)
    if isinstance(space, store.SpaceRow) and not (caller.admin or owns(caller.principal, space)):
        return _error(403, "space_owner_required")
    return space


# -- bodies -----------------------------------------------------------------------------------------

def _plain(value: str, *, longest: int, lines: bool = False) -> str:
    value = value.strip()
    if not 1 <= len(value) <= longest:
        raise ValueError(f"must be 1 to {longest} characters")
    allowed = "\r\n\t" if lines else ""
    if any((ord(char) < 32 and char not in allowed) or ord(char) == 127 for char in value):
        raise ValueError("must have no control characters")
    return value


def _groups(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    if not all(isinstance(value, str) and _GROUP.fullmatch(value) for value in values):
        raise ValueError("each group must be 1 to 256 characters, with no control characters or space at either end")
    return list(dict.fromkeys(values))


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpaceIn(_Body):
    name: str
    reader_groups: list[str] = Field(default_factory=list, max_length=MAX_LIST)
    owner_groups: list[str] = Field(default_factory=list, max_length=MAX_LIST)
    owner_principals: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_LIST)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _plain(value, longest=160)

    @field_validator("reader_groups", "owner_groups")
    @classmethod
    def _check_groups(cls, value: list[str]) -> list[str]:
        return _groups(value)


class SpaceChange(_Body):
    name: str | None = None
    reader_groups: list[str] | None = Field(None, max_length=MAX_LIST)
    owner_groups: list[str] | None = Field(None, max_length=MAX_LIST)
    owner_principals: list[uuid.UUID] | None = Field(None, max_length=MAX_LIST)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str | None) -> str | None:
        return None if value is None else _plain(value, longest=160)

    @field_validator("reader_groups", "owner_groups")
    @classmethod
    def _check_groups(cls, value: list[str] | None) -> list[str] | None:
        return _groups(value)


class RenameIn(_Body):
    name: str

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _plain(value, longest=160)


class FactIn(_Body):
    text: str
    source_document_id: uuid.UUID | None = None

    @field_validator("text")
    @classmethod
    def _check_text(cls, value: str) -> str:
        return _plain(value, longest=2000, lines=True)


class FactChange(_Body):
    text: str | None = None
    # null takes the source away.
    source_document_id: uuid.UUID | None = None

    @field_validator("text")
    @classmethod
    def _check_text(cls, value: str | None) -> str | None:
        return None if value is None else _plain(value, longest=2000, lines=True)


class RecallIn(_Body):
    query: str = Field(min_length=1, max_length=1000)
    k: int = Field(8, ge=1, le=50)
    spaces: list[uuid.UUID] | None = Field(None, max_length=MAX_LIST)


# -- uploads ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Upload:
    file_name: str
    kind: parse.FileType
    content: bytes
    title: str | None


def multipart_fields(content_type: str, body: bytes) -> dict[str, tuple[str | None, bytes]]:
    """A ``multipart/form-data`` body's fields: name to (file name or None, bytes). ValueError when it isn't one
    (no boundary, a part without a name, a name twice, no closing delimiter). Standard library only: the server's
    image has no multipart package."""
    header = Message()
    header["content-type"] = content_type
    boundary = header.get_param("boundary")
    if header.get_content_type() != "multipart/form-data" or not isinstance(boundary, str) \
            or not 1 <= len(boundary) <= 70:
        raise ValueError("not multipart/form-data with a boundary")
    delimiter = b"\r\n--" + boundary.encode("latin-1")
    fields: dict[str, tuple[str | None, bytes]] = {}
    for section in (b"\r\n" + body).split(delimiter)[1:]:
        if section.startswith(b"--"):
            return fields
        if not section.startswith(b"\r\n"):
            raise ValueError("a part doesn't start on its own line")
        head, separator, content = section[2:].partition(b"\r\n\r\n")
        if not separator:
            raise ValueError("a part has no headers")
        disposition = None
        for line in head.split(b"\r\n"):
            name, colon, value = line.partition(b":")
            if not colon:
                raise ValueError("a part's header isn't one")
            if name.strip().lower() == b"content-disposition":
                disposition = value.decode("utf-8").strip()
        parsed = Message()
        parsed["content-disposition"] = disposition or ""
        field = parsed.get_param("name", header="content-disposition")
        field = collapse_rfc2231_value(field) if field else None
        if parsed.get_content_disposition() != "form-data" or not field or field in fields:
            raise ValueError("a part isn't a named form field, or repeats one")
        fields[field] = (parsed.get_filename(), content)
    raise ValueError("no closing delimiter")


def _file_name(value: str) -> str | None:
    name = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not 1 <= len(name) <= 255 or any(ord(char) < 32 or ord(char) == 127 for char in name):
        return None
    return name


async def _read_body(request: Request, limit: int) -> bytes | None:
    """The body, or None past ``limit`` bytes (read no further)."""
    declared = request.headers.get("content-length", "")
    if declared.isascii() and declared.isdigit() and int(declared) > limit:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            return None
    return bytes(body)


async def read_upload(request: Request, max_bytes: int) -> Upload | JSONResponse:
    """The upload in a request (``file``, and an optional ``title``), or the refusal to answer."""
    content_type = request.headers.get("content-type", "")
    too_large = _error(413, "file_too_large", max_bytes=max_bytes)
    if not content_type.lower().startswith("multipart/form-data"):
        return _error(400, "invalid_upload")
    body = await _read_body(request, max_bytes + MULTIPART_OVERHEAD)
    if body is None:
        return too_large
    try:
        fields = multipart_fields(content_type, body)
    except ValueError:
        return _error(400, "invalid_upload")
    file_name, content = fields.get("file", (None, b""))
    if not file_name:
        return _error(400, "file_required")
    if (name := _file_name(file_name)) is None:
        return _error(400, "invalid_file_name")
    if (kind := parse.file_type(name)) is None:
        return _error(415, "unsupported_type", supported=sorted(parse.BY_EXTENSION))
    if len(content) > max_bytes:
        return too_large
    title = None
    if "title" in fields:
        try:
            title = _plain(fields["title"][1].decode("utf-8"), longest=300)
        except ValueError:
            return _error(400, "invalid_title")
    return Upload(name, kind, content, title)


def _uploaded(conn: psycopg.Connection, caller: Caller, space: store.SpaceRow, document: dict[str, Any]) -> None:
    audit.emit(conn, Action.DOCUMENT_UPLOADED, actor=_actor(caller), layer=_layer(space),
               target=Target(TargetKind.DOCUMENT, document["id"]), device_id=caller.principal.device_id,
               details={"space_id": space.id, "byte_count": document["byte_count"]})


def brain_router(service: BrainService) -> APIRouter:
    router = APIRouter()
    caller_of = Depends(request_principal)
    manager = Depends(manager_connection, scope="function")
    member = Depends(principal_connection, scope="function")
    admin = Depends(roles.admin_connection, scope="function")

    def fact_changed(caller: Caller, space: store.SpaceRow, fact_id: str, change: str) -> None:
        audit.emit(caller.conn, Action.FACT_CHANGED, actor=_actor(caller), layer=_layer(space),
                   target=Target(TargetKind.FACT, fact_id), device_id=caller.principal.device_id,
                   details={"change": change, "space_id": space.id})

    def space_changed(conn: psycopg.Connection, actor: audit.Actor, space: store.SpaceRow, change: str,
                      device_id: str | None = None) -> None:
        audit.emit(conn, Action.SPACE_CHANGED, actor=actor, layer=_layer(space),
                   target=Target(TargetKind.SPACE, space.id), device_id=device_id, details={"change": change})

    # -- for everyone signed in ------------------------------------------------------------------

    @router.get("/api/brain/spaces")
    def my_spaces(conn: psycopg.Connection = member, principal: RequestPrincipal = caller_of) -> JSONResponse:
        return JSONResponse({"spaces": [_space_seen(principal, space) for space in store.list_spaces(conn)]})

    @router.post("/api/brain/recall")
    def recall(body: RecallIn, principal: RequestPrincipal = caller_of) -> JSONResponse:
        spaces = None if body.spaces is None else [str(space) for space in body.spaces]
        try:
            return JSONResponse(service.recall(principal, body.query, body.k, spaces))
        except SpaceNotFound:
            return _error(404, "space_not_found")
        except InvalidRecord:
            return _error(422, "invalid_query")

    # -- a space's owners, and admins ------------------------------------------------------------

    @router.patch("/api/brain/spaces/{space_id}")
    def rename(space_id: str, body: RenameIn, caller: Caller = manager) -> JSONResponse:
        space = _managed(caller, space_id)
        if isinstance(space, JSONResponse):
            return space
        try:
            with caller.conn.transaction():
                renamed = store.update_space(caller.conn, space.id, {"name": body.name})
        except psycopg.errors.UniqueViolation:
            return _error(409, "name_taken")
        if renamed is None:
            return _error(404, "space_not_found")
        space_changed(caller.conn, _actor(caller), renamed, "updated", caller.principal.device_id)
        return JSONResponse(_space_seen(caller.principal, renamed))

    @router.get("/api/brain/spaces/{space_id}/documents")
    def documents(space_id: str, caller: Caller = manager) -> JSONResponse:
        space = _readable(caller, space_id)
        if isinstance(space, JSONResponse):
            return space
        return JSONResponse({"documents": store.list_documents(caller.conn, space.id)})

    def check_space(request: Request, principal: RequestPrincipal, space_id: str) -> JSONResponse | None:
        with _managing(request, principal) as caller:
            space = _managed(caller, space_id)
        return space if isinstance(space, JSONResponse) else None

    def add(request: Request, principal: RequestPrincipal, space_id: str, upload: Upload) -> JSONResponse:
        with _managing(request, principal) as caller:
            space = _managed(caller, space_id)
            if isinstance(space, JSONResponse):
                return space
            document = store.add_document(
                caller.conn, space_id=space.id, title=upload.title or upload.file_name.rpartition(".")[0][:300],
                file_name=upload.file_name, media_type=upload.kind.media_type, content=upload.content,
                sha256=hashlib.sha256(upload.content).hexdigest(), uploader_groups=principal.groups)
            _uploaded(caller.conn, caller, space, document)
        service.wake()
        return JSONResponse(document, status_code=201)

    @router.post("/api/brain/spaces/{space_id}/documents")
    async def upload(space_id: str, request: Request, principal: RequestPrincipal = caller_of) -> Response:
        # Who may upload is checked before the body is read, and again with the write.
        if refused := await run_in_threadpool(check_space, request, principal, space_id):
            return refused
        received = await read_upload(request, service.max_upload_bytes)
        if isinstance(received, JSONResponse):
            return received
        return await run_in_threadpool(add, request, principal, space_id, received)

    def managed_document(caller: Caller, document_id: str) -> tuple[dict[str, Any], store.SpaceRow] | JSONResponse:
        found = _uuid(document_id)
        document = None if found is None else store.get_document(caller.conn, found)
        if document is None:
            return _error(404, "document_not_found")
        space = _managed(caller, document["space_id"])
        return space if isinstance(space, JSONResponse) else (document, space)

    @router.get("/api/brain/documents/{document_id}")
    def document(document_id: str, caller: Caller = manager) -> JSONResponse:
        found = _uuid(document_id)
        document = None if found is None else store.get_document(caller.conn, found)
        return _error(404, "document_not_found") if document is None else JSONResponse(document)

    def check_document(request: Request, principal: RequestPrincipal, document_id: str) -> JSONResponse | None:
        with _managing(request, principal) as caller:
            found = managed_document(caller, document_id)
        return found if isinstance(found, JSONResponse) else None

    def replace_with(request: Request, principal: RequestPrincipal, document_id: str, upload: Upload) -> JSONResponse:
        with _managing(request, principal) as caller:
            found = managed_document(caller, document_id)
            if isinstance(found, JSONResponse):
                return found
            current, space = found
            document = store.replace_document(
                caller.conn, current["id"], title=upload.title, file_name=upload.file_name,
                media_type=upload.kind.media_type, content=upload.content,
                sha256=hashlib.sha256(upload.content).hexdigest(), principal_id=principal.principal_id,
                uploader_groups=principal.groups)
            if document is None:
                return _error(404, "document_not_found")
            _uploaded(caller.conn, caller, space, document)
        service.wake()
        return JSONResponse(document)

    @router.put("/api/brain/documents/{document_id}")
    async def replace(document_id: str, request: Request, principal: RequestPrincipal = caller_of) -> Response:
        if refused := await run_in_threadpool(check_document, request, principal, document_id):
            return refused
        received = await read_upload(request, service.max_upload_bytes)
        if isinstance(received, JSONResponse):
            return received
        return await run_in_threadpool(replace_with, request, principal, document_id, received)

    @router.delete("/api/brain/documents/{document_id}")
    def delete_document(document_id: str, caller: Caller = manager) -> Response:
        found = managed_document(caller, document_id)
        if isinstance(found, JSONResponse):
            return found
        current, space = found
        if not store.delete_document(caller.conn, current["id"]):
            return _error(404, "document_not_found")
        audit.emit(caller.conn, Action.DOCUMENT_REMOVED, actor=_actor(caller), layer=_layer(space),
                   target=Target(TargetKind.DOCUMENT, current["id"]), device_id=caller.principal.device_id,
                   details={"space_id": space.id})
        return Response(status_code=204)

    # -- facts -----------------------------------------------------------------------------------

    def source_in(caller: Caller, space: store.SpaceRow, document_id: uuid.UUID | None) -> bool:
        if document_id is None:
            return True
        document = store.get_document(caller.conn, str(document_id))
        return document is not None and document["space_id"] == space.id

    @router.get("/api/brain/spaces/{space_id}/facts")
    def facts(space_id: str, retired: bool = False, caller: Caller = manager) -> JSONResponse:
        space = _readable(caller, space_id)
        if isinstance(space, JSONResponse):
            return space
        return JSONResponse({"facts": store.list_facts(caller.conn, space.id, include_retired=retired)})

    @router.post("/api/brain/spaces/{space_id}/facts")
    def add_fact(space_id: str, body: FactIn, caller: Caller = manager) -> JSONResponse:
        space = _managed(caller, space_id)
        if isinstance(space, JSONResponse):
            return space
        if not source_in(caller, space, body.source_document_id):
            return _error(422, "unknown_source_document")
        fact = store.add_fact(caller.conn, space_id=space.id, text=body.text,
                              source_document_id=None if body.source_document_id is None
                              else str(body.source_document_id), writer_groups=caller.principal.groups)
        fact_changed(caller, space, fact["id"], "created")
        service.wake()
        return JSONResponse(fact, status_code=201)

    def managed_fact(caller: Caller, fact_id: str) -> tuple[dict[str, Any], store.SpaceRow] | JSONResponse:
        found = _uuid(fact_id)
        fact = None if found is None else store.get_fact(caller.conn, found)
        if fact is None:
            return _error(404, "fact_not_found")
        space = _managed(caller, fact["space_id"])
        return space if isinstance(space, JSONResponse) else (fact, space)

    @router.patch("/api/brain/facts/{fact_id}")
    def change_fact(fact_id: str, body: FactChange, caller: Caller = manager) -> JSONResponse:
        found = managed_fact(caller, fact_id)
        if isinstance(found, JSONResponse):
            return found
        current, space = found
        clear = "source_document_id" in body.model_fields_set and body.source_document_id is None
        if not source_in(caller, space, body.source_document_id):
            return _error(422, "unknown_source_document")
        fact = store.update_fact(caller.conn, current["id"], text=body.text,
                                 source_document_id=None if body.source_document_id is None
                                 else str(body.source_document_id), clear_source=clear,
                                 principal_id=caller.principal.principal_id, writer_groups=caller.principal.groups)
        if fact is None:
            return _error(404, "fact_not_found")
        fact_changed(caller, space, fact["id"], "updated")
        service.wake()
        return JSONResponse(fact)

    @router.post("/api/brain/facts/{fact_id}/retire")
    def retire_fact(fact_id: str, caller: Caller = manager) -> JSONResponse:
        found = managed_fact(caller, fact_id)
        if isinstance(found, JSONResponse):
            return found
        current, space = found
        fact, retired = store.retire_fact(caller.conn, current["id"])
        if fact is None:
            return _error(404, "fact_not_found")
        if retired:
            fact_changed(caller, space, fact["id"], "retired")
        return JSONResponse(fact)

    @router.delete("/api/brain/facts/{fact_id}")
    def delete_fact(fact_id: str, caller: Caller = manager) -> Response:
        found = managed_fact(caller, fact_id)
        if isinstance(found, JSONResponse):
            return found
        current, space = found
        if not store.delete_fact(caller.conn, current["id"]):
            return _error(404, "fact_not_found")
        fact_changed(caller, space, current["id"], "deleted")
        return Response(status_code=204)

    # -- admins: spaces and who reads and owns them ----------------------------------------------

    def unknown_owners(conn: psycopg.Connection, owners: list[uuid.UUID] | None) -> bool:
        wanted = {str(owner) for owner in owners or []}
        return bool(wanted - store.known_principals(conn, sorted(wanted)))

    @router.get("/api/admin/brain/spaces")
    def all_spaces(conn: psycopg.Connection = admin) -> JSONResponse:
        return JSONResponse({"spaces": [space.to_json() for space in store.list_spaces(conn)]})

    @router.post("/api/admin/brain/spaces")
    def create_space(body: SpaceIn, conn: psycopg.Connection = admin,
                     principal: RequestPrincipal = caller_of) -> JSONResponse:
        if unknown_owners(conn, body.owner_principals):
            return _error(422, "unknown_person")
        try:
            with conn.transaction():
                space = store.create_space(conn, name=body.name, reader_groups=body.reader_groups,
                                           owner_groups=body.owner_groups,
                                           owner_principals=[str(owner) for owner in dict.fromkeys(
                                               body.owner_principals)])
        except psycopg.errors.UniqueViolation:
            return _error(409, "name_taken")
        space_changed(conn, audit.admin(principal.principal_id), space, "created")
        return JSONResponse(space.to_json(), status_code=201)

    @router.patch("/api/admin/brain/spaces/{space_id}")
    def change_space(space_id: str, body: SpaceChange, conn: psycopg.Connection = admin,
                     principal: RequestPrincipal = caller_of) -> JSONResponse:
        found = _uuid(space_id)
        current = None if found is None else store.get_space(conn, found)
        if current is None:
            return _error(404, "space_not_found")
        changes: dict[str, Any] = {name: getattr(body, name) for name in body.model_fields_set
                                   if getattr(body, name) is not None}
        if current.kind == store.COMPANY and changes.get("reader_groups"):
            return _error(400, "company_space_is_read_by_everyone")
        if unknown_owners(conn, changes.get("owner_principals")):
            return _error(422, "unknown_person")
        if "owner_principals" in changes:
            changes["owner_principals"] = [str(owner) for owner in dict.fromkeys(changes["owner_principals"])]
        if not changes:
            return JSONResponse(current.to_json())
        try:
            with conn.transaction():
                space = store.update_space(conn, current.id, changes)
        except psycopg.errors.UniqueViolation:
            return _error(409, "name_taken")
        if space is None:
            return _error(404, "space_not_found")
        space_changed(conn, audit.admin(principal.principal_id), space, "updated")
        return JSONResponse(space.to_json())

    @router.delete("/api/admin/brain/spaces/{space_id}")
    def delete_space(space_id: str, conn: psycopg.Connection = admin,
                     principal: RequestPrincipal = caller_of) -> Response:
        found = _uuid(space_id)
        current = None if found is None else store.get_space(conn, found)
        if current is None:
            return _error(404, "space_not_found")
        if current.kind == store.COMPANY:
            return _error(409, "company_space_stays")
        if not store.delete_space(conn, current.id):
            return _error(404, "space_not_found")
        space_changed(conn, audit.admin(principal.principal_id), current, "deleted")
        return Response(status_code=204)

    return router


__all__ = ["MULTIPART_OVERHEAD", "Caller", "Upload", "brain_router", "manager_connection", "multipart_fields",
           "read_upload"]
