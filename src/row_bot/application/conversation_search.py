"""Bounded discovery over authoritative metadata and public checkpoints.

No second transcript database or provider calls. Continuations pin query, library
metadata and each checkpoint; a changed cut requires a fresh search.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from contextlib import closing
from typing import Any

from row_bot.application.client_platform import ClientPlatformError


def _cursor(value: list) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode()


def _decode(value: str) -> list:
    try:
        result = json.loads(base64.urlsafe_b64decode(value))
        if not isinstance(result, list):
            raise ValueError()
        return result
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ClientPlatformError("cursor_expired") from exc


def _library_revision(conn: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for row in conn.execute("SELECT thread_id,name,client_revision,updated_at,pinned_at FROM thread_meta ORDER BY thread_id"):
        digest.update(json.dumps(tuple(row), separators=(",", ":")).encode())
    return digest.hexdigest()


def _require_readable(service: Any, conversation_id: str) -> None:
    from row_bot.runtime import admissions
    service._metadata(conversation_id)
    if admissions.deletion_state(conversation_id) != "active":
        raise ClientPlatformError("conversation_deleting")


def _readable(service: Any, conversation_id: str) -> bool:
    try:
        _require_readable(service, conversation_id)
        return True
    except ClientPlatformError as exc:
        if exc.code not in {"not_found", "conversation_deleting"}:
            raise
        return False


def search(service: Any, query: str, *, conversation_id: str | None = None,
           cursor: str | None = None, limit: int = 25) -> dict:
    """Search all public history, returning bounded results and scan continuation."""
    from row_bot import threads
    from row_bot.runtime.checkpoint_reader import open_checkpoint
    query = query.strip()
    if not query or len(query) > 200:
        raise ClientPlatformError("invalid_command")
    limit = min(50, max(1, limit))
    threads._ensure_thread_db()
    if conversation_id:
        _require_readable(service, conversation_id)
    with closing(sqlite3.connect(threads.DB_PATH)) as conn:
        revision = _library_revision(conn)
        signature = hashlib.sha256(json.dumps([query.casefold(), conversation_id, revision]).encode()).hexdigest()
        after, offset, checkpoint = "", -1, ""
        if cursor:
            values = _decode(cursor)
            if len(values) != 5 or values[0] != signature:
                raise ClientPlatformError("cursor_expired")
            _, after, offset, checkpoint, _version = values
            if not isinstance(after, str) or not isinstance(offset, int) or offset < -1 or _version != 1:
                raise ClientPlatformError("cursor_expired")
        rows = conn.execute(
            "SELECT thread_id,name FROM thread_meta WHERE thread_id>=? "
            + ("AND thread_id=? " if conversation_id else "") + "ORDER BY thread_id LIMIT 33",
            (after, conversation_id) if conversation_id else (after,),
        ).fetchall()
    items: list[dict] = []
    scanned = 0
    continuation = None
    needle = query.casefold()
    for row_number, (identity, title) in enumerate(rows):
        if row_number >= 32:
            continuation = _cursor([signature, identity, -1, "", 1])
            break
        if not _readable(service, identity):
            continue
        start = offset if identity == after else -1
        if start == -1 and needle in str(title).casefold():
            items.append({"conversation_id": identity, "title": title,
                          "message_id": None, "row_id": None, "excerpt": title[:400],
                          "checkpoint_revision": ""})
            if len(items) >= limit:
                continuation = _cursor([signature, identity, 0, "", 1])
                break
        threads.migrate_checkpoint_message_ids(identity)
        with open_checkpoint(identity) as reader:
            if reader:
                if identity == after and checkpoint and checkpoint != reader.revision:
                    raise ClientPlatformError("cursor_expired")
                for index, record in reader.records(start=max(0, start)):
                    # Work is bounded even for a zero-hit page. Complete traversal
                    # remains available to callers through its continuation.
                    if scanned >= 500 or len(items) >= limit:
                        continuation = _cursor([signature, identity, index, reader.revision, 1])
                        break
                    scanned += 1
                    hit = _find_public_text(reader, record, needle)
                    if hit is not None:
                        public = reader.public_row(record, maximum=1024)
                        items.append({"conversation_id": identity, "title": title,
                                      "message_id": record["message_id"], "row_id": public["id"],
                                      "excerpt": hit, "checkpoint_revision": reader.revision})
        if continuation:
            break
    # Deletion/revocation during the scan must not return a stale result.
    if conversation_id:
        _require_readable(service, conversation_id)
    items = [item for item in items if _readable(service, item["conversation_id"])]
    with closing(sqlite3.connect(threads.DB_PATH)) as conn:
        if revision != _library_revision(conn):
            raise ClientPlatformError("cursor_expired")
    return {"items": items, "has_more": continuation is not None,
            "next_cursor": continuation, "scanned_messages": scanned, "revision": revision}


def _find_public_text(reader: Any, record: dict, needle: str) -> str | None:
    """Stream escaped public text with bounded memory, including very long rows."""
    # The checkpoint JSON chunks are safely escaped public text only. Decode
    # each string fragment; rolling overlap finds matches across chunk edges.
    overlap = ""
    for fragment in reader.public_text_chunks(record):
        text = overlap + fragment
        at = text.casefold().find(needle)
        if at >= 0:
            return text[max(0, at - 80):at + len(needle) + 240][:400]
        overlap = text[-(len(needle) + 80):]
    return None


def history_window(service: Any, conversation_id: str, *, message_id: str | None = None,
                   cursor: str | None = None, limit: int = 100) -> dict:
    """Latest/previous/next windows or an exact stable-ID jump, at most 100 rows."""
    from row_bot.runtime.checkpoint_reader import open_checkpoint
    _require_readable(service, conversation_id)
    snapshot = service.snapshot(conversation_id)
    limit = min(100, max(1, limit))
    revision = snapshot["checkpoint_revision"]
    start = -limit
    if cursor:
        values = _decode(cursor)
        if len(values) != 3 or values[:2] != [conversation_id, revision] or not isinstance(values[2], int):
            raise ClientPlatformError("cursor_expired")
        start = max(0, values[2])
    rows, indices, more = [], [], False
    with open_checkpoint(conversation_id, revision) as reader:
        if reader:
            if message_id:
                match = next((index for index, record in reader.records() if record["message_id"] == message_id), None)
                if match is None:
                    raise ClientPlatformError("not_found")
                start = max(0, match - limit // 2)
            size = 0
            for index, record in reader.records(start=start):
                public = reader.public_row(record)
                encoded = len(json.dumps(public, ensure_ascii=False).encode())
                if len(rows) >= limit or (rows and size + encoded > 256 * 1024):
                    more = True
                    break
                rows.append(public)
                indices.append(index)
                size += encoded
    _require_readable(service, conversation_id)
    return {**snapshot, "rows": rows, "has_more": more,
            "previous_cursor": _cursor([conversation_id, revision, max(0, indices[0] - limit)]) if indices and indices[0] else None,
            "next_cursor": _cursor([conversation_id, revision, indices[-1] + 1]) if more else None}
