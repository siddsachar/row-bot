"""Library › Media (5.3.1 B3): every picture, video and sound in Row-Bot's chats, with search and tags.

Recordings, video edits, voice lines, music, pictures Row-Bot made and files the person added are all
kept with their chat (``application.attachments``). This reads them across chats: newest first,
found by name, tag, kind or chat title. Tags are kept in their own table beside the chats. An incognito chat's
files are never listed. A saved character or style is a tag too: "character: maya" on a few pictures of Maya,
"style: noir" on a still with the look, which the image and video tools take by name (``look_pictures``).
``bring`` copies an item into another chat's files, which is how one chat or workflow hands media to
another; ``locate`` gives a tool that sends an item on (``x_post``) its file.
"""
from __future__ import annotations

import hashlib
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
_TAG = re.compile(r"[^\W_][\w .&+:-]{0,39}", re.UNICODE)
# A saved character's or style's tag, and how many of its newest pictures a shot holds to.
LOOKS = {"character": 3, "style": 2}
_LOOK = re.compile(r"(character|style): (.+)")
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


def look_of(ref: str) -> str:
    """The saved character or style one item shows, as its tag ("character: maya"), or "" (``looks``)."""
    return next((tag for tag in _tags().get(ref, []) if _LOOK.fullmatch(tag)), "")


def clean_tags(tags: list[str]) -> list[str]:
    """Tags as kept: trimmed, lower case, each once, up to MAX_TAGS of up to 40 characters."""
    kept: list[str] = []
    for tag in tags:
        text = re.sub(r"\s*:\s*", ": ", " ".join(str(tag).split()).lower())
        if not text:
            continue
        if not _TAG.fullmatch(text):
            raise MediaError(f"A tag is up to 40 letters, digits, spaces and . & + : - "
                             f"({str(tag)[:40]!r} isn't one).")
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


def _chat_files(folder: Path, changed: int) -> list[tuple[float, dict[str, Any]]]:
    """A chat's files with each one's time, role and lineage, newest first; checked records, read once."""
    from row_bot.application.attachments import list_conversation_files
    from row_bot.thread_cleanup import is_thread_deleting

    chat = folder.name
    kept = _LISTED.get(str(folder))
    if kept and kept[0] == changed:
        if is_thread_deleting(chat):  # Listing the files would refuse a chat being deleted.
            raise MediaError("deleting")
        return kept[1]
    listed = []
    for entry in list_conversation_files(chat, limit=100_000):
        ref = str(entry["attachment_ref"])
        try:
            at = (folder / f"attachment_{ref.rsplit(':', 1)[1]}.bin").stat().st_mtime
        except OSError:
            continue
        listed.append((at, {"ref": ref, "name": str(entry["name"]), "mime_type": str(entry["mime_type"]),
                            "size": int(entry["size_bytes"]), "role": str(entry["role"] or ""),
                            "output_id": str(entry["output_id"] or ""), "version": int(entry["version"] or 1)}))
    if time.time() - changed / 1e9 > _SETTLED_SECONDS:
        _LISTED[str(folder)] = (changed, listed)
    return listed


# Each chat folder's outputs, kept with the listing they were grouped from (``_group``).
_GROUPED: dict[str, tuple[list, list[tuple[float, dict[str, Any]]]]] = {}


def _chat_outputs(folder: Path, changed: int) -> list[tuple[float, dict[str, Any]]]:
    """A chat's outputs, newest first, grouped again only when its listing is read again."""
    listed = _chat_files(folder, changed)
    kept = _GROUPED.get(str(folder))
    if kept and kept[0] is listed:
        return kept[1]
    grouped = _group(folder.name, listed)
    if _LISTED.get(str(folder), (0, None))[1] is listed:
        _GROUPED[str(folder)] = (listed, grouped)
    return grouped


def _folders(conversation_id: str = "") -> tuple[list[tuple[int, Path]], dict[str, str]]:
    """The chats' file folders, newest first, and the chats' titles: every chat's but an incognito one's, or the
    one chat's (*conversation_id*, incognito or not)."""
    from row_bot import threads
    from row_bot.application.client_monitor import _conversation_titles

    root = Path(threads._MEDIA_DIR)
    if conversation_id:
        titles = _conversation_titles({conversation_id})
        if conversation_id not in titles:
            raise MediaError("There's no such chat.")
        try:
            return [((root / conversation_id).stat().st_mtime_ns, root / conversation_id)], titles
        except OSError:  # No files yet.
            return [], titles
    if not root.is_dir():
        return [], {}
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
    return folders, _conversation_titles({path.name for _, path in folders})


def _stamp(at: float) -> str:
    return datetime.fromtimestamp(at, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def items(*, query: str = "", kind: str = "", limit: int = 200) -> list[dict[str, Any]]:
    """Media across chats, newest first: each item's reference, name, kind, size, chat and tags."""
    folders, titles = _folders()
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
            listed = _chat_files(folder, changed)
        except Exception:
            continue
        for at, entry in listed:
            item_kind = KINDS.get(entry["mime_type"])
            if item_kind is None or (kind and item_kind != kind):
                continue
            tags = tagged.get(entry["ref"], [])
            text = " ".join([entry["name"], titles[chat], *tags]).casefold()
            if words and not all(word in text for word in words):
                continue
            found.append((at, {"ref": entry["ref"], "name": entry["name"], "kind": item_kind,
                               "mime_type": entry["mime_type"], "size": entry["size"], "added_at": _stamp(at),
                               "conversation_id": chat,
                               "conversation_title": titles[chat] or "Untitled conversation", "tags": tags}))
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in found[:limit]]


# ── Outputs (W3): one entry per thing made ──────────────────────────────────

Show = Literal["made", "uploads", "working"]
SHOWS: tuple[Show, ...] = ("made", "uploads", "working")
OutputKind = Literal["video", "image", "audio", "document", "file"]
OUTPUT_KINDS: tuple[OutputKind, ...] = ("video", "image", "audio", "document", "file")
# Outputs in one page across chats (Library › Media, the phone's Files); ``next`` reads the one after.
PAGE = 100
# The newest versions an output lists (``version_count`` says how many it has).
MAX_VERSIONS = 50
# Files kept before roles (W1) carry none: what each one is is told by the name its tool gave it.
_LEGACY_WORKING = re.compile(r"generated-(?:screen-)?recording(?:-\d+)?\.mp4|generated-.+-sheet(?:-\d+)?\.jpg"
                             r"|.+ review(?:-\d+)?\.jpg|.+\.srt")
_LEGACY_FRAME = re.compile(r"(.+)-\d{2}(?:-\d+)?\.jpg")
_SHOWN = {"made": frozenset({"final", "draft"}), "uploads": frozenset({"upload"}),
          "working": frozenset({"working"})}


def _output_kind(mime_type: str) -> OutputKind:
    kind = KINDS.get(mime_type)
    return kind if kind else "document" if mime_type == "application/pdf" else "file"


def _role(entry: dict[str, Any], names: set[str]) -> str:
    """What a file is to the person: ``final``, ``draft``, ``working``, or ``upload`` (one the person added).

    A file kept before roles is told by its name: "generated-…" pictures and videos were made by AI (final);
    recordings, contact sheets, an edit's frames, review sheets, voice lines, music and subtitles were working
    files; anything else is the person's own.
    """
    if entry["role"]:
        return entry["role"]
    name = entry["name"].casefold()
    if _LEGACY_WORKING.fullmatch(name) or entry["mime_type"] == "audio/wav":
        return "working"
    if name.startswith("generated-"):
        return "final" if _output_kind(entry["mime_type"]) in {"image", "video"} else "working"
    frame = _LEGACY_FRAME.fullmatch(name)
    return "working" if frame and f"generated-{frame.group(1)}-sheet.jpg" in names else "upload"


def _version(at: float, entry: dict[str, Any], role: str) -> dict[str, Any]:
    return {"ref": entry["ref"], "version": entry["version"] if entry["role"] else 1, "role": role,
            "file_name": entry["name"], "mime_type": entry["mime_type"], "size": entry["size"],
            "created_at": _stamp(at)}


def _group(chat: str, listed: list[tuple[float, dict[str, Any]]]) -> list[tuple[float, dict[str, Any]]]:
    """A chat's files as outputs, each with its newest file's time, newest first.

    The versions of an output share its output and its name. A made output (a version final or draft) shows
    its final, else its latest draft, with those versions inside; its working files (review sheets, frames,
    checks) are counted. A working file of nothing made, and each file the person added, is one of its own.
    """
    from row_bot.media_sources import output_name

    names = {entry["name"].casefold() for _, entry in listed}
    groups: dict[tuple[str, str], list[tuple[float, dict[str, Any], str]]] = {}
    for at, entry in listed:
        output_id = (entry["output_id"] if entry["role"] else "") or entry["ref"]
        groups.setdefault((output_id, entry["name"].casefold()), []).append((at, entry, _role(entry, names)))
    outputs: list[tuple[str, float, dict[str, Any]]] = []
    made: dict[str, dict[str, Any]] = {}
    for (output_id, name), members in groups.items():
        shown = [member for member in members if member[2] in _SHOWN["made"]]
        versions = shown or members
        role = "final" if any(member[2] == "final" for member in shown) else versions[0][2]
        at, entry, _ = next(member for member in versions if member[2] == role)
        output = {"id": hashlib.sha256(f"{chat}\0{output_id}\0{name}".encode()).hexdigest()[:24],
                  "conversation_id": chat, "name": output_name(entry["name"]) or entry["name"],
                  "kind": _output_kind(entry["mime_type"]), **_version(at, entry, role),
                  "versions": [_version(*member) for member in versions[:MAX_VERSIONS]],
                  "version_count": len(versions), "working_count": len(members) - len(shown) if shown else 0,
                  "updated_at": _stamp(members[0][0])}
        outputs.append((output_id, members[0][0], output))
        if shown:
            made.setdefault(output_id, output)
    for output_id, _at, output in outputs:  # Its own working files: a video's review sheets, an edit's frames.
        owner = made.get(output_id) if output["role"] == "working" else None
        if owner is not None:
            owner["working_count"] += output["version_count"]
    return [(at, output) for _output_id, at, output in outputs]


CURSOR = re.compile(r"(\d{1,12}\.\d{6}):([0-9a-f]{24})")


def outputs(*, conversation_id: str = "", show: Show = "made", query: str = "", kind: str = "",
            limit: int = 200, before: str = "") -> dict[str, Any]:
    """The outputs list (W3): one chat's (its Outputs pane) or every chat's but an incognito one's (Library ›
    Media, the phone's Files), newest first, read from the files' stored records.

    *show*: what Row-Bot made (finals, else the latest draft; AI-made media is final), the files the person
    added, or the working files made on the way. One chat's page also counts each. A page holds *limit*
    outputs: chats are read newest first and the reading stops once the page is full, and ``next`` (pass it as
    *before*) reads the page after it.
    """
    cursor = CURSOR.fullmatch(before)
    if before and not cursor:
        raise MediaError("That page of outputs can't be found.")
    after = (float(cursor.group(1)), cursor.group(2)) if cursor else None
    folders, titles = _folders(conversation_id)
    tagged = _tags()
    words = [word for word in query.casefold().split() if word]
    counts = dict.fromkeys(SHOWS, 0)
    found: list[tuple[float, dict[str, Any]]] = []
    for changed, folder in folders:
        # One more than a page tells whether another page follows; a folder older than that many outputs
        # holds nothing newer, and neither does any after it.
        if (not conversation_id and len(found) > limit
                and changed / 1e9 < sorted(at for at, _ in found)[-limit - 1]):
            break
        chat = folder.name
        if chat not in titles:
            continue  # A chat being removed, or files left from one already gone.
        try:
            grouped = _chat_outputs(folder, changed)
        except Exception:
            if conversation_id:
                raise MediaError("This chat's files can't be read now.") from None
            continue
        for at, output in grouped:
            for name, roles in _SHOWN.items():
                counts[name] += output["role"] in roles
            tags = tagged.get(output["ref"], [])
            text = " ".join([output["name"], output["file_name"], titles[chat], *tags]).casefold()
            if (output["role"] not in _SHOWN[show] or (kind and output["kind"] != kind)
                    or (words and not all(word in text for word in words))
                    or (after and (round(at, 6), output["id"]) >= after)):
                continue
            found.append((round(at, 6), {**output, "conversation_title": titles[chat] or "Untitled conversation",
                                         "tags": tags}))
    found.sort(key=lambda pair: (pair[0], pair[1]["id"]), reverse=True)
    last = found[limit - 1] if len(found) > limit else None
    return {"items": [item for _, item in found[:limit]], "counts": counts if conversation_id else None,
            "next": f"{last[0]:.6f}:{last[1]['id']}" if last else None}


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
        return register_attachment(conversation_id, meta["name"], data, meta["mime_type"], role="working",
                                   made_from=[meta["name"]])
    except AttachmentError as exc:
        raise MediaError(f"That item couldn't be copied into this chat ({exc}).") from None


_VOICE = re.compile(r"voice: (\S+)")


def _character_refs(name: str) -> list[tuple[str, list[str]]]:
    wanted = f"character: {' '.join(str(name).split()).lower()}"
    return [(ref, tags) for ref, tags in _tags().items() if wanted in tags]


def character_voice(name: str) -> str:
    """The voice a saved character speaks in (C6): a "voice: …" tag kept on its pictures, or "" (none saved, or no
    such character)."""
    for _ref, tags in _character_refs(name):
        for tag in tags:
            if found := _VOICE.fullmatch(tag):
                return found.group(1)
    return ""


def keep_character_voice(name: str, voice: str) -> bool:
    """Keep a voice with a saved character, on its pictures, so its lines in every video use it. False when there's
    no such character in Library › Media."""
    kept = False
    for ref, tags in _character_refs(name):
        try:
            set_tags(ref, [tag for tag in tags if not _VOICE.fullmatch(tag)] + [f"voice: {voice}"])
            kept = True
        except MediaError:  # A picture since deleted, or one with all its tags taken.
            continue
    return kept


def looks(kind: str, name: str = "") -> dict[str, list[dict[str, Any]]]:
    """The saved characters or styles (*kind*), or the one named: each name's pictures, newest first."""
    wanted = " ".join(name.split()).lower()
    found: dict[str, list[dict[str, Any]]] = {}
    for item in items(kind="image", limit=100_000):
        for tag in item["tags"]:
            look = _LOOK.fullmatch(tag)
            if look and look.group(1) == kind and (not wanted or look.group(2) == wanted):
                found.setdefault(look.group(2), []).append(item)
    return found


def look_pictures(kind: str, name: str) -> list[bytes]:
    """A saved character's or style's newest pictures, read in place, for a shot or a picture to hold to."""
    wanted = " ".join(name.split()).lower()
    pictures = looks(kind, wanted).get(wanted)
    if not pictures:
        raise MediaError(f"There's no {kind} named {name.strip()!r} in Library › Media. Save one by tagging a few "
                         f"of its pictures '{kind}: {wanted}' with media_tag.")
    return [locate(item["ref"])[1].read_bytes() for item in pictures[:LOOKS[kind]]]
