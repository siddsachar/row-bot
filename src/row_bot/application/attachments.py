"""Opaque attachment references over the existing conversation media owner."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import threading
import tempfile
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from functools import lru_cache
from typing import BinaryIO
from uuid import UUID, uuid4

# A file in a chat, at most: a phone video or a screen recording fits (5.3.1 A4). Files are held in
# memory while they are copied, so this stays well below the computer's memory.
MAX_ATTACHMENT_BYTES = 200 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024
UPLOAD_BATCH_BYTES = 1024 * 1024 * 1024
UPLOAD_TTL_SECONDS = 1800
_LOCK = threading.RLock()
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
PUBLIC_MIME_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "video/mp4",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/flac",
        "audio/aac",
        "application/pdf",
        "application/octet-stream",
    }
)


class AttachmentError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# What a file a tool made is to the person (finals first): a final they're shown, a draft they're asked to look
# at, or a working file made on the way (a recording, frames, a contact sheet, a voice line). A file the person
# brought has none. With it, a light lineage: the output it is a version of, which version, and what it was made
# from.
ROLES = frozenset({"final", "draft", "working"})
_RECORD_KEYS = frozenset({"attachment_ref", "name", "mime_type", "size_bytes", "revision", "sha256"})
_LINEAGE_KEYS = frozenset({"role", "output_id", "version", "made_from"})
_OUTPUT_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
MAX_MADE_FROM = 8


def _lineage(role: str, output_id: str, version: int, made_from: tuple[str, ...] | list[str]) -> dict:
    """The role and lineage a record keeps; nothing for a file without a role."""
    if not role:
        return {}
    sources = [str(item)[:80] for item in made_from if str(item or "").strip()][:MAX_MADE_FROM]
    if (role not in ROLES or not _OUTPUT_ID.fullmatch(output_id) or type(version) is not int
            or not 1 <= version <= 100_000):
        raise AttachmentError("invalid_command")
    return {"role": role, "output_id": output_id, "version": version, "made_from": sources}


def _valid_lineage(metadata: dict) -> bool:
    keys = set(metadata) - _RECORD_KEYS
    if not keys:
        return True
    if keys != _LINEAGE_KEYS or not isinstance(metadata["output_id"], str) or not isinstance(
            metadata["made_from"], list) or not all(isinstance(item, str) for item in metadata["made_from"]):
        return False
    try:
        return _lineage(metadata["role"], metadata["output_id"], metadata["version"],
                        metadata["made_from"]) == {key: metadata[key] for key in _LINEAGE_KEYS}
    except AttachmentError:
        return False


def _conversation(conversation_id: str) -> None:
    from row_bot import threads
    from row_bot.runtime import admissions
    import sqlite3
    if not _ID.fullmatch(conversation_id):
        raise AttachmentError("not_found")
    threads._ensure_thread_db()
    with closing(sqlite3.connect(threads.DB_PATH)) as conn, conn:
        if not conn.execute("SELECT 1 FROM thread_meta WHERE thread_id=?", (conversation_id,)).fetchone():
            raise AttachmentError("not_found")
    if threads._thread_write_blocked(conversation_id) or admissions.deletion_state(conversation_id) != "active":
        raise AttachmentError("action_denied")


def _safe_path(root: Path, path: Path) -> Path:
    root = root.absolute()
    if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
        raise AttachmentError("action_denied")
    try:
        relative = path.absolute().relative_to(root)
    except ValueError:
        raise AttachmentError("action_denied") from None
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise AttachmentError("action_denied")
    if not path.resolve().is_relative_to(root.resolve()):
        raise AttachmentError("action_denied")
    return path


def _managed_root(path: Path) -> Path:
    """Canonicalize trusted ancestry while rejecting a replaced managed root."""
    root = path.absolute()
    _safe_path(root, root)
    canonical = root.parent.resolve() / root.name
    return _safe_path(canonical, canonical)


def _open(root: Path, path: Path) -> BinaryIO:
    _safe_path(root, path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0))
    try:
        _safe_path(root, path)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(opened, path.stat()):
            raise AttachmentError("action_denied")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _write(root: Path, path: Path, data: bytes) -> None:
    """Create immutable media bytes only after checking the opened file identity."""
    _safe_path(root, path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        _safe_path(root, path)
        if not os.path.samestat(os.fstat(descriptor), path.stat()):
            raise AttachmentError("action_denied")
        with os.fdopen(descriptor, "wb") as destination:
            descriptor = -1
            destination.write(data)
            destination.flush()
            os.fsync(destination.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _numbered(name: str, taken: set[str]) -> str:
    """*name*, or the first of name-2, name-3, … (before its type) that isn't in *taken* (case-folded names)."""
    if name.casefold() not in taken:
        return name
    stem, suffix = os.path.splitext(name)
    if len(suffix.encode("utf-8")) > 40:  # Not a file type: number the whole name.
        stem, suffix = name, ""
    number = 2
    while True:
        tail, head = f"-{number}{suffix}", stem
        while len((head + tail).encode("utf-8")) > 240:
            head = head[:-1]
        if (head + tail).casefold() not in taken:
            return head + tail
        number += 1


def register_attachment(conversation_id: str, name: str, data: bytes,
                        mime_type: str = "application/octet-stream", *, role: str = "", output_id: str = "",
                        version: int = 1, made_from: tuple[str, ...] | list[str] = ()) -> dict:
    """Persist one bounded immutable file and its reference in thread media.

    Each output in a chat has its own name: a file whose name another output holds is kept as name-2,
    name-3, … (the returned ``name`` says which), so five generated videos are five files a tool can name.

    A file a tool made has a *role* (``ROLES``) and is a *version* of an output (*output_id*, its own id
    when none is given), *made_from* the files it names. A new version of an output keeps its output's
    name: versions are told apart by their number, never by their names.
    """
    from row_bot import threads
    if not data or len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentError("payload_too_large")
    if (not name or len(name.encode("utf-8")) > 240 or name in {".", ".."}
            or any(c in name for c in '/\\:\x00\r\n')):
        raise AttachmentError("invalid_command")
    _lineage(role, output_id or "file", version, made_from)  # Checked before anything is written.
    with _LOCK:
        _conversation(conversation_id)
        media_root = threads._MEDIA_DIR
        folder = media_root / conversation_id
        _safe_path(media_root, folder)
        folder.mkdir(parents=True, exist_ok=True)
        _safe_path(media_root, folder)
        # Under the lock, so two tools finishing at once in one chat never take the same name.
        name = _numbered(name, {str(item["name"]).casefold() for item in _named_files(conversation_id)
                                if not output_id or item["output_id"] != output_id})
        attachment_id = str(uuid4())
        ref = f"{conversation_id}:{attachment_id}"
        filename = f"attachment_{attachment_id}.bin"
        metadata_name = f"attachment_{attachment_id}.json"
        # Active formats are always downloaded with attachment disposition;
        # content type is sniffed, never a client's authority claim.
        detected = (
            "image/png"
            if data.startswith(b"\x89PNG\r\n\x1a\n")
            else "image/jpeg"
            if data.startswith(b"\xff\xd8\xff")
            else "image/gif"
            if data.startswith((b"GIF87a", b"GIF89a"))
            else "video/mp4"
            if len(data) >= 12 and data[4:8] == b"ftyp"
            else "audio/wav"
            if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WAVE"
            else "audio/ogg"
            if data.startswith(b"OggS")
            else "audio/flac"
            if data.startswith(b"fLaC")
            else "audio/aac"
            if len(data) >= 2 and data[0] == 0xFF and data[1] & 0xF6 == 0xF0
            else "audio/mpeg"
            if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)
            else "application/pdf"
            if data.startswith(b"%PDF-")
            else "application/octet-stream"
        )
        metadata = {"attachment_ref": ref, "name": name, "mime_type": detected,
                    "size_bytes": len(data), "revision": "1",
                    "sha256": hashlib.sha256(data).hexdigest(),
                    **_lineage(role, output_id or attachment_id, version, made_from)}
        _safe_path(media_root, folder / filename)
        _write(media_root, folder / filename, data)
        _conversation(conversation_id)
        _safe_path(media_root, folder / metadata_name)
        _write(media_root, folder / metadata_name, json.dumps(metadata).encode())
        return {key: value for key, value in metadata.items() if key != "sha256"}


def list_conversation_files(conversation_id: str, *, limit: int = 200) -> list[dict]:
    """The conversation's files (name, type, size and reference), newest first, from their checked records.

    Each has its own name in the chat, the one tools find it by (see ``_named_files``).
    """
    return _named_files(conversation_id)[:limit]


def output_versions(conversation_id: str, output_id: str) -> list[dict]:
    """The chat's files that are versions of one output, newest first, each with its ``version`` and ``sha256``."""
    from row_bot import threads

    found = []
    for item in _named_files(conversation_id):
        if item["output_id"] != output_id:
            continue
        attachment_id = str(item["attachment_ref"]).rsplit(":", 1)[1]
        metadata = _metadata(threads._MEDIA_DIR, threads._MEDIA_DIR / conversation_id, attachment_id,
                             str(item["attachment_ref"]))
        found.append({**item, "sha256": metadata["sha256"]})
    return found


def _named_files(conversation_id: str) -> list[dict]:
    """All the conversation's files, newest first, each under a name no other output in the chat has (the
    versions of one output share its name).

    ``register_attachment`` gives every new output a free name. Files kept before that may share one (every
    generated video was "generated-video.mp4"): the newest keeps the name, so it still finds what it found
    before, and the older ones are told apart as name-2, name-3, … from newest to oldest. Their records stay
    as they are, and a file that never shared its name keeps it.
    """
    from row_bot import threads

    _conversation(conversation_id)
    root = _managed_root(threads._MEDIA_DIR)
    folder = root / conversation_id
    _safe_path(root, folder)
    if not folder.is_dir():
        return []
    candidates = []
    for path in folder.glob("attachment_*.json"):
        try:
            info = path.stat()
        except OSError:
            continue
        candidates.append((info.st_mtime_ns, path, (info.st_ino, info.st_size, info.st_mtime_ns)))
    candidates.sort(reverse=True)
    files: list[dict] = []
    for _, path, signature in candidates:
        attachment_id = path.stem.removeprefix("attachment_")
        try:
            record = _listed_record(root, folder, str(UUID(attachment_id)), f"{conversation_id}:{UUID(attachment_id)}",
                                    signature)
        except (AttachmentError, ValueError):
            continue
        files.append(dict(zip(("attachment_ref", "name", "mime_type", "size_bytes", "role", "output_id", "version"),
                              record)))
    taken = {str(item["name"]).casefold() for item in files}
    kept: dict[str, str] = {}
    for item in files:
        key = str(item["name"]).casefold()
        if key not in kept:
            kept[key] = item["output_id"]  # The newest with a name keeps it, and so do its output's versions.
            continue
        if item["output_id"] and item["output_id"] == kept[key]:
            continue
        item["name"] = _numbered(str(item["name"]), taken)
        taken.add(item["name"].casefold())
    return files


@lru_cache(maxsize=16384)
def _listed_record(root: Path, folder: Path, attachment_id: str, reference: str,
                   signature: tuple[int, int, int]) -> tuple[str, str, str, int, str, str, int]:
    """A file's checked record (reference, name, type, size, role, output, version), checked again only when its
    record file changes.

    Records change only when a file's role does (``set_role``), so each new file costs one check rather than
    every file on every listing (each new file reads the chat's names). Reading a file checks it in full every
    time.
    """
    metadata = _metadata(root, folder, attachment_id, reference)
    return (metadata["attachment_ref"], metadata["name"], metadata["mime_type"], metadata["size_bytes"],
            metadata.get("role", ""), metadata.get("output_id", ""), metadata.get("version", 0))


def read_native_selection(path: Path) -> tuple[str, bytes]:
    """Read one explicitly selected native file without following replacements.

    The path is supplied only by the trusted native host callback.  Renderer
    payloads never reach this helper.  Identity is checked before and after the
    bounded read so a chooser result cannot be swapped for a link, directory,
    device, or a different file while it is being admitted.
    """
    selected = Path(path).absolute()
    try:
        if selected.is_symlink() or (
            hasattr(selected, "is_junction") and selected.is_junction()
        ):
            raise AttachmentError("action_denied")
        before = selected.stat()
        if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= MAX_ATTACHMENT_BYTES:
            raise AttachmentError("payload_too_large")
        descriptor = os.open(
            selected,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(opened, before):
                raise AttachmentError("action_denied")
            with os.fdopen(descriptor, "rb") as source:
                descriptor = -1
                data = source.read(MAX_ATTACHMENT_BYTES + 1)
            after = selected.stat()
            if (
                len(data) > MAX_ATTACHMENT_BYTES
                or not os.path.samestat(opened, after)
                or len(data) != opened.st_size
            ):
                raise AttachmentError("revision_conflict")
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except AttachmentError:
        raise
    except (OSError, ValueError):
        raise AttachmentError("resource_unavailable") from None
    return selected.name, data


def _metadata(root: Path, folder: Path, attachment_id: str, reference: str) -> dict:
    try:
        with _open(root, folder / f"attachment_{attachment_id}.json") as source:
            raw = source.read(8193)
        if len(raw) > 8192:
            raise ValueError()
        metadata = json.loads(raw)
        if (not isinstance(metadata, dict)
                or not _RECORD_KEYS <= set(metadata) or not _valid_lineage(metadata)
                or metadata["attachment_ref"] != reference
                or type(metadata["size_bytes"]) is not int
                or not 1 <= metadata["size_bytes"] <= MAX_ATTACHMENT_BYTES
                or metadata["revision"] != "1"
                or not isinstance(metadata["name"], str)
                or not metadata["name"] or len(metadata["name"].encode("utf-8")) > 240
                or metadata["name"] in {".", ".."}
                or any(c in metadata["name"] for c in '/\\:\x00\r\n')
                or not isinstance(metadata["mime_type"], str)
                or metadata["mime_type"] not in PUBLIC_MIME_TYPES
                or not isinstance(metadata["sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", metadata["sha256"])):
            raise ValueError()
        return metadata
    except (OSError, ValueError, TypeError):
        raise AttachmentError("not_found") from None


def _reference_parts(reference: str) -> tuple[str, str]:
    try:
        conversation_id, value = reference.rsplit(":", 1)
        return conversation_id, str(UUID(value))
    except (ValueError, AttributeError):
        raise AttachmentError("not_found") from None


def attachment_lineage(reference: str) -> dict:
    """A file's role and lineage (``role``, ``output_id``, ``version``, ``made_from``); empty when it has none."""
    from row_bot import threads
    conversation_id, attachment_id = _reference_parts(reference)
    with _LOCK:
        _conversation(conversation_id)
        metadata = _metadata(threads._MEDIA_DIR, threads._MEDIA_DIR / conversation_id, attachment_id, reference)
    return {key: metadata[key] for key in _LINEAGE_KEYS if key in metadata}


def set_role(reference: str, role: str, *, output_id: str = "", version: int = 0) -> dict:
    """Give a file of this chat a new role (a version the agent presents becomes ``final``); its new lineage.

    Its record is written again in place, its time kept, so the chat's files keep their order and names.
    """
    from row_bot import threads
    conversation_id, attachment_id = _reference_parts(reference)
    with _LOCK:
        _conversation(conversation_id)
        root = threads._MEDIA_DIR
        folder = root / conversation_id
        metadata = _metadata(root, folder, attachment_id, reference)
        lineage = _lineage(role, output_id or metadata.get("output_id") or attachment_id,
                           version or metadata.get("version") or 1, metadata.get("made_from") or ())
        record = {**{key: metadata[key] for key in _RECORD_KEYS}, **lineage}
        if record != metadata:
            path = folder / f"attachment_{attachment_id}.json"
            _safe_path(root, path)
            before = path.stat()
            staged = folder / f"attachment_{attachment_id}.{uuid4().hex}.part"
            _write(root, staged, json.dumps(record).encode())
            for attempt in range(5):  # On Windows a record open for reading refuses the replace for a moment.
                try:
                    os.replace(staged, path)
                    break
                except PermissionError:
                    time.sleep(0.05 * (attempt + 1))
                except OSError:
                    break
            if staged.exists():
                staged.unlink(missing_ok=True)
                raise AttachmentError("resource_unavailable")
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        return lineage


def read_attachment(reference: str) -> tuple[dict, bytes]:
    """Authorize existence and validate the file at open, without exposing paths."""
    from row_bot import threads
    try:
        conversation_id, value = reference.rsplit(":", 1)
        attachment_id = str(UUID(value))
    except (ValueError, AttributeError):
        raise AttachmentError("not_found") from None
    with _LOCK:
        _conversation(conversation_id)
        root = threads._MEDIA_DIR
        folder = root / conversation_id
        metadata = _metadata(root, folder, attachment_id, reference)
        try:
            with _open(root, folder / f"attachment_{attachment_id}.bin") as source:
                data = source.read(MAX_ATTACHMENT_BYTES + 1)
        except (OSError, ValueError):
            raise AttachmentError("not_found") from None
        if (metadata.get("attachment_ref") != reference or len(data) > MAX_ATTACHMENT_BYTES
                or metadata.get("size_bytes") != len(data)
                or metadata.get("sha256") != hashlib.sha256(data).hexdigest()):
            raise AttachmentError("revision_conflict")
        return {k: metadata[k] for k in ("attachment_ref", "name", "mime_type", "size_bytes", "revision")}, data


def inspect_attachment(reference: str) -> dict:
    """Bounded metadata/size preflight; full consumption still checks the hash."""
    from row_bot import threads
    try:
        conversation_id, value = reference.rsplit(":", 1)
        attachment_id = str(UUID(value))
    except (ValueError, AttributeError):
        raise AttachmentError("not_found") from None
    with _LOCK:
        _conversation(conversation_id)
        root = threads._MEDIA_DIR
        folder = root / conversation_id
        metadata = _metadata(root, folder, attachment_id, reference)
        try:
            with _open(root, folder / f"attachment_{attachment_id}.bin") as source:
                size = os.fstat(source.fileno()).st_size
            if metadata["size_bytes"] != size:
                raise AttachmentError("revision_conflict")
            return {k: metadata[k] for k in ("attachment_ref", "name", "mime_type", "size_bytes", "revision")}
        except OSError:
            raise AttachmentError("not_found") from None


def copy_attachment(reference: str, conversation_id: str) -> dict:
    """Copy one checked file into another conversation, under the same id (a fork's history names it).

    The copy's reference is ``conversation_id:<id>``: the other conversation reads its own copy, never this
    one's files, and deleting either conversation leaves the other's copy.
    """
    from row_bot import threads
    metadata, data = read_attachment(reference)
    attachment_id = reference.rsplit(":", 1)[1]
    copied = f"{conversation_id}:{attachment_id}"
    with _LOCK:
        _conversation(conversation_id)
        media_root = threads._MEDIA_DIR
        folder = media_root / conversation_id
        _safe_path(media_root, folder)
        folder.mkdir(parents=True, exist_ok=True)
        _safe_path(media_root, folder)
        if not (folder / f"attachment_{attachment_id}.json").exists():
            record = {**metadata, **attachment_lineage(reference), "attachment_ref": copied,
                      "sha256": hashlib.sha256(data).hexdigest()}
            _safe_path(media_root, folder / f"attachment_{attachment_id}.bin")
            _write(media_root, folder / f"attachment_{attachment_id}.bin", data)
            _safe_path(media_root, folder / f"attachment_{attachment_id}.json")
            _write(media_root, folder / f"attachment_{attachment_id}.json", json.dumps(record).encode())
    return inspect_attachment(copied)


THUMBNAIL_PIXELS = 160
THUMBNAIL_SOURCE_PIXELS = 64_000_000
VIDEO_THUMBNAIL_TYPES = frozenset({"video/mp4", "video/webm", "video/quicktime"})
# A generated video often opens on black, so its poster is the frame half a second in.
POSTER_SECONDS = 0.5


def _video_poster(data: bytes):
    """The frame at POSTER_SECONDS (or the last before it), decoded in-process by PyAV."""
    import av

    try:
        with av.open(io.BytesIO(data), metadata_errors="ignore") as container:
            if not container.streams.video:
                raise AttachmentError("not_found")
            stream = container.streams.video[0]
            if stream.codec_context.width * stream.codec_context.height > THUMBNAIL_SOURCE_PIXELS:
                raise AttachmentError("payload_too_large")
            poster = None
            for frame in container.decode(stream):
                poster = frame
                if frame.time is None or frame.time >= POSTER_SECONDS:
                    break
            if poster is None:
                raise AttachmentError("not_found")
            return poster.to_image()
    except av.error.FFmpegError:
        raise AttachmentError("not_found") from None


def read_attachment_thumbnail(reference: str) -> bytes:
    """A PNG of at most 160 px of an image attachment, or of a video's poster frame.

    The picture is decoded and drawn again upright, so no original bytes or
    metadata are served. Anything but a PNG, JPEG or common video attachment,
    or one that does not decode, is not_found; one over 64 megapixels is never
    decoded. A video's frame needs PyAV (the media extra); without it, not_found.
    """
    from PIL import Image, ImageOps
    metadata, data = read_attachment(reference)
    video = metadata["mime_type"] in VIDEO_THUMBNAIL_TYPES
    if not video and metadata["mime_type"] not in {"image/png", "image/jpeg"}:
        raise AttachmentError("not_found")
    try:
        if video:
            image = _video_poster(data)
        else:
            with Image.open(io.BytesIO(data)) as source:
                if source.width * source.height > THUMBNAIL_SOURCE_PIXELS:
                    raise AttachmentError("payload_too_large")
                # A JPEG decodes at a fraction of its size (no-op for a PNG).
                source.draft(None, (THUMBNAIL_PIXELS * 2, THUMBNAIL_PIXELS * 2))
                image = ImageOps.exif_transpose(source)
        image.thumbnail((THUMBNAIL_PIXELS, THUMBNAIL_PIXELS))
        output = io.BytesIO()
        image.convert("RGBA").save(output, format="PNG")
    except AttachmentError:
        raise
    except ImportError:  # No PyAV: the tile keeps its play mark.
        raise AttachmentError("not_found") from None
    except (OSError, ValueError, Image.DecompressionBombError):
        raise AttachmentError("not_found") from None
    return output.getvalue()


@dataclass
class _Upload:
    id: str
    session_id: str
    conversation_id: str
    batch_id: str
    name: str
    size: int
    digest: str
    source: BinaryIO
    expires: float
    chunks: dict[int, str] = field(default_factory=dict)
    received: int = 0


class AttachmentUploads:
    """Ephemeral staging in the media owner's root, never a second durable store.

    TemporaryFile owns unlink-on-close/process-exit cleanup. Restart deliberately
    loses the upload IDs; committed references remain in the existing media owner.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._lock = threading.RLock()
        # Routes count transfers in and out on the event loop, while a worker may
        # hold _lock across a commit that checks the session on that same loop:
        # sharing _lock deadlocked them until the check's 5-second timeout (503).
        self._transfer_lock = threading.Lock()
        self._uploads: dict[str, _Upload] = {}
        self._inflight: dict[str, int] = {}

    def _prune(self) -> None:
        for identifier, upload in tuple(self._uploads.items()):
            if upload.expires <= self.clock():
                upload.source.close()
                del self._uploads[identifier]

    def _get(self, session_id: str, upload_id: str) -> _Upload:
        self._prune()
        upload = self._uploads.get(upload_id)
        if upload is None:
            raise AttachmentError("upload_expired")
        if upload.session_id != session_id:
            raise AttachmentError("not_found")
        _conversation(upload.conversation_id)
        return upload

    def _view(self, upload: _Upload) -> dict:
        return {"upload_id": upload.id, "size_bytes": upload.size,
                "received_bytes": upload.received, "chunk_bytes": UPLOAD_CHUNK_BYTES,
                "expires_in_seconds": max(0, int(upload.expires - self.clock()))}

    def create(self, session_id: str, *, conversation_id: str, batch_id: str,
               name: str, size_bytes: int, sha256: str) -> dict:
        from row_bot import threads
        with self._lock:
            self._prune()
            _conversation(conversation_id)
            if (not 1 <= size_bytes <= MAX_ATTACHMENT_BYTES or not re.fullmatch(r"[a-f0-9]{64}", sha256)
                    or not name or len(name.encode()) > 240 or any(c in name for c in '/\\:\x00\r\n')):
                raise AttachmentError("invalid_command")
            batch_size = sum(u.size for u in self._uploads.values()
                             if u.session_id == session_id and u.batch_id == batch_id)
            if batch_size + size_bytes > UPLOAD_BATCH_BYTES:
                raise AttachmentError("payload_too_large")
            # Bounded reservations stop unfilled uploads exhausting local storage.
            if len(self._uploads) >= 128 or sum(u.size for u in self._uploads.values()) + size_bytes > 1024 ** 3:
                raise AttachmentError("rate_limited")
            folder = threads._MEDIA_DIR / conversation_id
            _safe_path(threads._MEDIA_DIR, folder)
            folder.mkdir(parents=True, exist_ok=True)
            _safe_path(threads._MEDIA_DIR, folder)
            source = tempfile.TemporaryFile(mode="w+b", dir=folder, prefix="upload_")
            upload = _Upload(str(uuid4()), session_id, conversation_id, batch_id, name,
                             size_bytes, sha256, source, self.clock() + UPLOAD_TTL_SECONDS)
            self._uploads[upload.id] = upload
            return self._view(upload)

    def status(self, session_id: str, upload_id: str) -> dict:
        with self._lock:
            return self._view(self._get(session_id, upload_id))

    def expire(self) -> None:
        with self._lock:
            self._prune()

    def enter_chunk(self, session_id: str, upload_id: str) -> None:
        with self._lock:
            self._get(session_id, upload_id)
            self.enter_transfer(session_id)

    def enter_transfer(self, session_id: str) -> None:
        with self._transfer_lock:
            if self._inflight.get(session_id, 0) >= 4 or sum(self._inflight.values()) >= 32:
                raise AttachmentError("rate_limited")
            self._inflight[session_id] = self._inflight.get(session_id, 0) + 1

    def leave_chunk(self, session_id: str) -> None:
        with self._transfer_lock:
            count = self._inflight.get(session_id, 0) - 1
            if count > 0:
                self._inflight[session_id] = count
            else:
                self._inflight.pop(session_id, None)

    def write(self, session_id: str, upload_id: str, offset: int, data: bytes) -> dict:
        with self._lock:
            upload = self._get(session_id, upload_id)
            if (offset < 0 or offset % UPLOAD_CHUNK_BYTES or offset >= upload.size
                    or len(data) != min(UPLOAD_CHUNK_BYTES, upload.size - offset)):
                raise AttachmentError("invalid_command")
            digest = hashlib.sha256(data).hexdigest()
            old = upload.chunks.get(offset)
            if old is not None and old != digest:
                raise AttachmentError("revision_conflict")
            if old is None:
                upload.source.seek(offset)
                upload.source.write(data)
                upload.chunks[offset] = digest
                upload.received += len(data)
            upload.expires = self.clock() + UPLOAD_TTL_SECONDS
            return self._view(upload)

    def complete(self, session_id: str, upload_id: str, commit: Callable[..., dict]) -> dict:
        with self._lock:
            upload = self._get(session_id, upload_id)
            if upload.received != upload.size:
                raise AttachmentError("upload_incomplete")
            upload.source.seek(0)
            data = upload.source.read(MAX_ATTACHMENT_BYTES + 1)
            if len(data) != upload.size or hashlib.sha256(data).hexdigest() != upload.digest:
                raise AttachmentError("revision_conflict")
            result = commit(conversation_id=upload.conversation_id, name=upload.name,
                            data=data, mime_type="application/octet-stream")
            # Retain immutable staging until TTL for safe response-loss retries.
            upload.expires = self.clock() + UPLOAD_TTL_SECONDS
            return result

    def read_staged(self, session_id: str, upload_id: str, *, conversation_id: str,
                    name: str, validate: Callable[[], None]) -> bytes:
        """Read exact scoped staging for a different canonical resource owner.

        The existing TTL owns cleanup; reading never creates an attachment or
        chooses an asset destination. The consumer must check size and digest.
        """
        with self._lock:
            validate()
            upload = self._get(session_id, upload_id)
            if upload.conversation_id != conversation_id or upload.name != name:
                raise AttachmentError("not_found")
            if upload.received != upload.size:
                raise AttachmentError("upload_incomplete")
            upload.source.seek(0)
            data = upload.source.read(MAX_ATTACHMENT_BYTES + 1)
            if len(data) != upload.size or hashlib.sha256(data).hexdigest() != upload.digest:
                raise AttachmentError("revision_conflict")
            validate()
            return data

    def cancel(self, session_id: str, upload_id: str) -> None:
        with self._lock:
            upload = self._get(session_id, upload_id)
            upload.source.close()
            del self._uploads[upload.id]

    def close(self) -> None:
        with self._lock:
            for upload in self._uploads.values():
                upload.source.close()
            self._uploads.clear()
            with self._transfer_lock:
                self._inflight.clear()
