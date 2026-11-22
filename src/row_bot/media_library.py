"""Library › Media (5.3.1 B3): every picture, video and sound in Row-Bot's chats, with search and tags.

Recordings, video edits, voice lines, music, pictures Row-Bot made and files the person added are all
kept with their chat (``application.attachments``). This reads them across chats: newest first,
found by name, tag, kind or chat title. Tags are kept in their own table beside the chats. An incognito chat's
files are never listed. ``bring`` copies an item into another chat's files, which is how one chat
or workflow hands media to another; ``locate`` gives a tool that sends an item on (``x_post``) its file.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

Kind = Literal["image", "video", "audio"]
KINDS: dict[str, Kind] = {"image/png": "image", "image/jpeg": "image", "image/gif": "image", "video/mp4": "video",
                          "audio/mpeg": "audio", "audio/ogg": "audio", "audio/wav": "audio",
                          "audio/flac": "audio", "audio/aac": "audio"}
MAX_TAGS = 12
REF = re.compile(r"[\w-]+:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TAG = re.compile(r"[^\W_][\w .&+-]{0,39}", re.UNICODE)
_LOCK = threading.Lock()


class MediaError(ValueError):
    """A media request that can't be done; the message says why."""


def _db() -> sqlite3.Connection:
    from row_bot import threads

    conn = sqlite3.connect(threads.DB_PATH)  # Kept with the chats the files belong to.
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS media_tags (ref TEXT PRIMARY KEY, tags TEXT NOT NULL)")
    return conn


def _tags() -> dict[str, list[str]]:
    conn = _db()
    try:
        rows = conn.execute("SELECT ref, tags FROM media_tags").fetchall()
    finally:
        conn.close()
    found: dict[str, list[str]] = {}
    for row in rows:
        try:
            value = json.loads(row["tags"])
        except ValueError:
            continue
        if isinstance(value, list):
            found[str(row["ref"])] = [str(tag) for tag in value if isinstance(tag, str)]
    return found


def clean_tags(tags: list[str]) -> list[str]:
    """Tags as kept: trimmed, lower case, each once, up to MAX_TAGS of up to 40 characters."""
    kept: list[str] = []
    for tag in tags:
        text = " ".join(str(tag).split()).lower()
        if not text:
            continue
        if not _TAG.fullmatch(text):
            raise MediaError(f"A tag is up to 40 letters, digits, spaces and . & + -: {str(tag)[:40]!r} isn't one.")
        if text not in kept:
            kept.append(text)
    if len(kept) > MAX_TAGS:
        raise MediaError(f"An item takes up to {MAX_TAGS} tags.")
    return kept


# Each chat folder's media, kept until the folder changes: a file added, renamed or removed changes the
# folder's modified time, and files are never changed in place. Keyed by the folder's path.
_LISTED: dict[str, tuple[int, list[tuple[float, dict[str, Any]]]]] = {}
# A folder changed this recently may change again within its file system's time resolution: read it afresh.
_SETTLED_SECONDS = 2.0


def _chat_media(folder: Path, changed: int) -> list[tuple[float, dict[str, Any]]]:
    """A chat's pictures, videos and sounds with each file's time, newest first; checked records, read once."""
    from row_bot.application.attachments import list_conversation_files
    from row_bot.thread_cleanup import is_thread_deleting

    chat = folder.name
    kept = _LISTED.get(str(folder))
    if kept and kept[0] == changed:
        if is_thread_deleting(chat):  # Listing the files would refuse a chat being deleted.
            raise MediaError("deleting")
        return kept[1]
    listed = []
    for entry in list_conversation_files(chat, limit=1000):
        item_kind = KINDS.get(str(entry["mime_type"]))
        if item_kind is None:
            continue
        ref = str(entry["attachment_ref"])
        try:
            at = (folder / f"attachment_{ref.rsplit(':', 1)[1]}.bin").stat().st_mtime
        except OSError:
            continue
        listed.append((at, {"ref": ref, "name": str(entry["name"]), "kind": item_kind,
                            "mime_type": str(entry["mime_type"]), "size": int(entry["size_bytes"])}))
    if time.time() - changed / 1e9 > _SETTLED_SECONDS:
        _LISTED[str(folder)] = (changed, listed)
    return listed


def items(*, query: str = "", kind: str = "", limit: int = 200) -> list[dict[str, Any]]:
    """Media across chats, newest first: each item's reference, name, kind, size, chat and tags."""
    from row_bot import threads
    from row_bot.application.client_monitor import _conversation_titles

    root = Path(threads._MEDIA_DIR)
    if not root.is_dir():
        return []
    hidden = threads.incognito_thread_ids()
    folders = []
    for path in root.iterdir():
        try:
            if path.name not in hidden and path.is_dir():
                folders.append((path.stat().st_mtime_ns, path))
        except OSError:
            continue
    # Newest folder first: a folder changes whenever a file is added, so once `limit` items are found, a
    # folder older than the oldest of them holds nothing newer, and neither does any after it.
    folders.sort(reverse=True)
    titles = _conversation_titles({path.name for _, path in folders})
    tagged = _tags()
    words = [word for word in query.casefold().split() if word]
    found: list[tuple[float, dict[str, Any]]] = []
    for changed, folder in folders:
        if len(found) >= limit and changed / 1e9 < sorted(at for at, _ in found)[-limit]:
            break
        chat = folder.name
        if chat not in titles:
            continue  # A chat being removed, or files left from one already gone.
        try:
            listed = _chat_media(folder, changed)
        except Exception:
            continue
        for at, entry in listed:
            if kind and entry["kind"] != kind:
                continue
            tags = tagged.get(entry["ref"], [])
            text = " ".join([entry["name"], titles[chat], *tags]).casefold()
            if words and not all(word in text for word in words):
                continue
            added = datetime.fromtimestamp(at, tz=timezone.utc)
            found.append((at, {**entry, "added_at": added.isoformat(timespec="seconds").replace("+00:00", "Z"),
                               "conversation_id": chat,
                               "conversation_title": titles[chat] or "Untitled conversation", "tags": tags}))
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in found[:limit]]


def _known(ref: str) -> None:
    from row_bot import threads
    from row_bot.application.attachments import AttachmentError, inspect_attachment

    try:
        conversation_id, value = ref.rsplit(":", 1)
        UUID(value)
        if conversation_id in threads.incognito_thread_ids():
            raise ValueError
        inspect_attachment(ref)
    except (ValueError, AttachmentError):
        raise MediaError("There's no such item in the media library.") from None


def set_tags(ref: str, tags: list[str]) -> list[str]:
    """Replace an item's tags; the tags kept."""
    kept = clean_tags(tags)
    _known(ref)
    with _LOCK:
        conn = _db()
        try:
            with conn:
                if kept:
                    conn.execute("INSERT INTO media_tags (ref, tags) VALUES (?, ?) ON CONFLICT(ref) DO UPDATE SET "
                                 "tags = excluded.tags", (ref, json.dumps(kept)))
                else:
                    conn.execute("DELETE FROM media_tags WHERE ref = ?", (ref,))
        finally:
            conn.close()
    return kept


def locate(ref: str) -> tuple[dict[str, Any], Path]:
    """An item's facts (name, MIME type, size) and its stored file, read in place."""
    from row_bot import threads
    from row_bot.application.attachments import inspect_attachment

    _known(ref)
    conversation_id, value = ref.rsplit(":", 1)
    return inspect_attachment(ref), Path(threads._MEDIA_DIR) / conversation_id / f"attachment_{UUID(value)}.bin"


def bring(ref: str, conversation_id: str) -> dict[str, Any]:
    """Copy an item into a chat's files (its own copy, so the chat keeps it if the first one goes)."""
    from row_bot.application.attachments import (
        AttachmentError, list_conversation_files, read_attachment, register_attachment,
    )

    _known(ref)
    if ref.startswith(conversation_id + ":"):
        meta, _ = read_attachment(ref)
        # The name the chat knows it by, which tells it from an older file kept under the same name.
        named = {entry["attachment_ref"]: entry["name"] for entry in list_conversation_files(conversation_id,
                                                                                           limit=100_000)}
        return {**meta, "name": named.get(ref, meta["name"])}
    try:
        meta, data = read_attachment(ref)
        return register_attachment(conversation_id, meta["name"], data, meta["mime_type"])
    except AttachmentError as exc:
        raise MediaError(f"That item couldn't be copied into this chat ({exc}).") from None
