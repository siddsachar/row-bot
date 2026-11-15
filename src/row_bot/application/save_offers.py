"""One way to save a file from Row-Bot (5.3.3): a short-lived offer the Save dialog or a download reads.

Every Download and Export button asks the server for an offer of its file (a page, a sent file, a
design's export, a workflow's webhook file, or code and a chart the chat shows), then saves it the one
way the client saves a conversation's file: the desktop app's Save dialog, a browser download, or,
while the desktop window reconnects, a copy in Exports. An offer is ``save:`` and 32 hex characters,
read once by the session that asked for it, and gone after a few minutes.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass

from row_bot.application.attachments import MAX_ATTACHMENT_BYTES, AttachmentError

TTL_SECONDS = 600
MAX_PER_SESSION = 16
REFERENCE = re.compile(r"save:[0-9a-f]{32}")


@dataclass(frozen=True)
class _Offer:
    session_id: str
    name: str
    data: bytes
    expires_at: float


_LOCK = threading.Lock()
_OFFERS: dict[str, _Offer] = {}


def _sweep(now: float) -> None:
    for reference in [key for key, offer in _OFFERS.items() if offer.expires_at <= now]:
        del _OFFERS[reference]


def plain_name(name: str) -> str:
    """A name the Save dialog and a download both take: letters, digits, space, dot, dash and underscore."""
    from row_bot.application.export_folder import safe_export_name

    plain = re.sub(r"[^A-Za-z0-9 ._-]+", "-", safe_export_name(name))
    stem, dot, extension = plain.rpartition(".")
    if not dot:
        stem, extension = plain, ""
    stem = re.sub(r"^[^A-Za-z0-9]+", "", stem)[: 119 - len(extension)].rstrip(" .-") or "export"
    return f"{stem}.{extension}" if extension else stem


def offer(session_id: str, name: str, data: bytes) -> dict[str, str]:
    """Hold one file for this session to save; its reference and plain name. It is served as bytes to save,
    never as a page (an HTML export or page included)."""
    if not session_id or not data or len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentError("payload_too_large" if data else "invalid_command")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,119}", name or ""):
        raise AttachmentError("invalid_command")
    now = time.monotonic()
    reference = f"save:{uuid.uuid4().hex}"
    with _LOCK:
        _sweep(now)
        mine = sorted((item.expires_at, key) for key, item in _OFFERS.items() if item.session_id == session_id)
        for _expires, key in mine[: max(0, len(mine) - MAX_PER_SESSION + 1)]:
            del _OFFERS[key]  # The oldest offers make room: one person saves one file at a time.
        _OFFERS[reference] = _Offer(session_id, name, data, now + TTL_SECONDS)
    return {"reference": reference, "file_name": name}


def is_offer(reference: str) -> bool:
    return bool(REFERENCE.fullmatch(reference or ""))


def take(reference: str, session_id: str) -> tuple[dict, bytes]:
    """The offered file, once, for the session that asked for it; like ``read_attachment``."""
    with _LOCK:
        _sweep(time.monotonic())
        found = _OFFERS.get(reference)
        if found is None or found.session_id != session_id:
            raise AttachmentError("not_found")
        del _OFFERS[reference]
    return {"attachment_ref": reference, "name": found.name, "mime_type": "application/octet-stream",
            "size_bytes": len(found.data), "revision": "1"}, found.data
