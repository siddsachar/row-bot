"""A space owner's company documents, from the app (Row-Bot for Teams, B3).

- The spaces the person owns (``is_owner`` in B1a's ``GET /api/brain/spaces``), and one space's documents with
  each one's status and its waiting or failed reason (B1a), and how far its knowledge got (B7's ``graph_status``).
- Upload, replace and delete call B1a's routes with the device's bearer (``multipart/form-data`` for a file). The
  file's name, type and size are checked here first, against the server's documented limits: owners can't read
  the configured ones (``max_upload_bytes`` is on the admin route only), so a 413 or 415 from the server says them
  instead, and the answer passes them on.
- Every call answers the space's list as it is afterwards, with how the action went (``outcome``).
- Not joined: None, and nothing is asked. Disconnected or locked down (C3a): a state, and nothing is asked. A
  server that can't be used: a state (B8's, ``brain._read``).
"""

from __future__ import annotations

import logging
from typing import Any

from row_bot.company import brain, store

logger = logging.getLogger(__name__)

# B1a's defaults: ROW_BOT_ORG_BRAIN_MAX_UPLOAD_MB (25) and the types its parser reads.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
SUPPORTED_TYPES = (".docx", ".markdown", ".md", ".txt")
MEDIA_TYPES = {".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
               ".markdown": "text/markdown", ".md": "text/markdown", ".txt": "text/plain"}
MAX_DOCUMENTS = 500
STATUSES = frozenset({"queued", "processing", "ready", "failed"})
GRAPH_STATUSES = frozenset({"pending", "extracting", "done", "waiting", "failed"})


def _file_name(value: str) -> str | None:
    """The name the server keeps (its path dropped), or None when the server would refuse it."""
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not 1 <= len(name) <= 255 or any(ord(char) < 32 or ord(char) == 127 for char in name):
        return None
    return name


def _type(name: str) -> str | None:
    stem, dot, extension = name.rpartition(".")
    found = f".{extension}".lower() if dot and stem else ""
    return found if found in SUPPORTED_TYPES else None


def _number(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def _document(item: Any) -> dict[str, Any] | None:
    """One document as the app shows it (B1a's shape, and B7's ``graph_status``); None when malformed."""
    if not isinstance(item, dict):
        return None
    document_id = brain._id(item.get("id"))
    if document_id is None or item.get("status") not in STATUSES:
        return None
    graph = item.get("graph_status") if item.get("graph_status") in GRAPH_STATUSES else None
    return {
        "id": document_id,
        "title": brain._text(item.get("title"), 300) or brain._text(item.get("file_name"), 300) or "Untitled",
        "file_name": brain._text(item.get("file_name"), 255),
        "byte_count": _number(item.get("byte_count")),
        "status": item["status"],
        "status_reason": brain._text(item.get("status_reason"), 500) or None,
        "graph_status": graph,
        "graph_reason": (brain._text(item.get("graph_reason"), 500) or None) if graph else None,
        "version": max(_number(item.get("version")), 1),
        "uploaded_at": brain._text(item.get("uploaded_at"), 64),
    }


def empty(state: str, company: str | None = None, reason: str | None = None) -> dict[str, Any]:
    """A view with no documents: not joined, or why the company can't be asked now."""
    return {"schema_version": 1, "state": state, "company": company, "reason": reason, "spaces": [],
            "space_id": None, "documents": [], "max_upload_bytes": MAX_UPLOAD_BYTES,
            "supported_types": list(SUPPORTED_TYPES), "outcome": None}


def _listing(origin: str, company: str, wanted: str | None, *, outcome: str | None = None,
             limits: dict[str, Any] | None = None) -> dict[str, Any]:
    """The spaces the person owns and one of them's documents (``wanted``, or the first)."""
    owned = [space for space in brain._spaces(brain._get(origin, brain.SPACES_PATH)) if space["is_owner"]]
    ids = [space["id"] for space in owned]
    chosen = wanted if wanted in ids else (ids[0] if ids else None)
    documents: list[dict[str, Any]] = []
    if chosen is not None:
        answer = brain._get(origin, f"{brain.SPACES_PATH}/{chosen}/documents")
        documents = [found for found in map(_document, brain._items(answer, "documents")) if found]
    return {**empty("ready", company), **(limits or {}), "spaces": owned, "space_id": chosen,
            "documents": documents[:MAX_DOCUMENTS], "outcome": outcome}


def overview(space_id: str | None = None) -> dict[str, Any] | None:
    """The spaces the person owns and the documents of ``space_id`` (or the first); None when not joined."""
    record = store.read_record()
    if record is None:
        return None
    wanted = brain._id(space_id) if space_id else None
    return brain._read(record, empty, lambda origin: _listing(origin, record.company, wanted))


def _refusal(response: Any) -> tuple[str, dict[str, Any]]:
    """An upload's or replace's refusal as an outcome, with the server's limits when it says them."""
    from row_bot.company import client

    answer = client._answer(response)
    status = response.status_code
    if status == 413:
        limit = answer.get("max_bytes")
        return "too_large", ({"max_upload_bytes": limit} if type(limit) is int and limit > 0 else {})
    if status == 415:
        given = answer.get("supported")
        types = [str(item)[:16] for item in given[:16]] if isinstance(given, list) else []
        return "unsupported_type", ({"supported_types": types} if types else {})
    if status == 403:
        return "not_owner", {}
    if status == 404:
        return "not_found", {}
    if status in (400, 422):
        return "invalid_file", {}
    logger.info("The company's server answered an upload with %d", status)
    raise client.Unreachable(str(status))


def _checked(file_name: str, content: bytes) -> tuple[str | None, str | None]:
    """(the file's name, None) when it may be sent, or (None, the refusal) before anything is asked."""
    name = _file_name(file_name)
    if name is None:
        return None, "invalid_file"
    if _type(name) is None:
        return None, "unsupported_type"
    if len(content) > MAX_UPLOAD_BYTES:
        return None, "too_large"
    return name, None


def _send(origin: str, method: str, path: str, name: str, content: bytes) -> tuple[str | None, dict]:
    """Send a file (the server titles it after its name); (None, {}) when the server took it, else the refusal
    and the limits it said."""
    from row_bot.company import client

    files = {"file": (name, content, MEDIA_TYPES[_type(name) or ".txt"])}
    response = client.call(origin, method, path, files=files)
    if response.status_code in (200, 201):
        return None, {}
    return _refusal(response)


def upload(space_id: str, file_name: str, content: bytes) -> dict[str, Any] | None:
    """Add a document to a space the person owns; the space's list afterwards. None when not joined."""
    record = store.read_record()
    if record is None:
        return None
    space = brain._id(space_id)

    def ask(origin: str) -> dict[str, Any]:
        name, refused = _checked(file_name, content)
        if space is None:
            refused = "not_found"
        limits: dict[str, Any] = {}
        if refused is None and name is not None:
            refused, limits = _send(origin, "POST", f"{brain.SPACES_PATH}/{space}/documents", name, content)
        return _listing(origin, record.company, space, outcome=refused or "uploaded", limits=limits)

    return brain._read(record, empty, ask)


def _space_of(origin: str, document: str | None) -> str | None:
    """The space a document is in, or None when the person can't read it (or it's gone)."""
    if document is None:
        return None
    found = brain._get(origin, f"/api/brain/documents/{document}")
    return brain._id(found.get("space_id")) if found else None


def replace(document_id: str, file_name: str, content: bytes) -> dict[str, Any] | None:
    """Replace a document's file (the same document, its version one more); the space's list afterwards."""
    record = store.read_record()
    if record is None:
        return None
    document = brain._id(document_id)

    def ask(origin: str) -> dict[str, Any]:
        space = _space_of(origin, document)
        name, refused = _checked(file_name, content)
        if space is None:
            refused = "not_found"
        limits: dict[str, Any] = {}
        if refused is None and name is not None:
            refused, limits = _send(origin, "PUT", f"/api/brain/documents/{document}", name, content)
        return _listing(origin, record.company, space, outcome=refused or "replaced", limits=limits)

    return brain._read(record, empty, ask)


def remove(document_id: str) -> dict[str, Any] | None:
    """Delete a document (its parts go, its facts keep their text); the space's list afterwards."""
    record = store.read_record()
    if record is None:
        return None
    document = brain._id(document_id)

    def ask(origin: str) -> dict[str, Any]:
        from row_bot.company import client

        space = _space_of(origin, document)
        outcome = "not_found"
        if space is not None:
            response = client.call(origin, "DELETE", f"/api/brain/documents/{document}")
            outcome = "removed" if response.status_code in (200, 204) else _refusal(response)[0]
        return _listing(origin, record.company, space, outcome=outcome)

    return brain._read(record, empty, ask)
