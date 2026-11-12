"""Import memory: what other assistants know about the person, reviewed, then saved to Row-Bot's memory.

One import is a *batch*. The person adds export files (sent in chunks, several parts of one export
together) or pastes an assistant's answer; a background scan turns them into candidate memories with
stable ids. They may also ask Row-Bot to look for memories in their past conversations from those
exports: that reads only their own messages, newest conversations first and bounded, with the
extraction prompt on their default chat model. A preview then decides, per chosen memory, whether it is
new, adds to a saved memory, replaces one (kept as history) or is already saved. Only Apply writes, and
it records a receipt that Undo reverses.

Nothing is written to memory before Apply. No network is used here: links in an export are never
fetched, and the only model calls go to the person's own default chat model. Imported text is data, never
instructions. Account files are never read (the parsers skip them).

State lives in the data folder under ``memory_import/<batch_id>/``: the uploaded files, pasted text,
the candidates and the preview. Uploaded files and pasted text are deleted once the batch is applied,
discarded or abandoned (idle for a day), and when the server stops. An applied batch keeps only its
receipt, so Undo works for 7 days; then it is removed too.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from row_bot.application.client_platform import ClientPlatformError

logger = logging.getLogger(__name__)

FOLDER = "memory_import"
CHUNK_BYTES = 1024 * 1024
FILE_MAX_BYTES = 8 * 1024 ** 3
FILES_PER_BATCH = 20
PASTE_MAX_CHARS = 1_000_000
TEXTS_PER_BATCH = 20
# Free space kept on the disk beyond what the files still to arrive need.
DISK_RESERVE_BYTES = 256 * 1024 ** 2
BATCHES_HELD = 4
CANDIDATE_LIMIT = 5_000
CONVERSATION_LIMIT = 200
CHARACTER_LIMIT = 400_000
TEXT_MAX_CHARS = 2_000
IDLE_SECONDS = 24 * 3600
KEEP_SECONDS = 7 * 24 * 3600
_SAFE_NAME = re.compile(r"[^\w .,()+-]")
_BATCH_ID = re.compile(r"[0-9a-f]{32}")
_now = time.time

_LOCK = threading.RLock()


@dataclass(eq=False)
class _File:
    id: str
    name: str
    size: int
    path: Path
    received: int = 0
    last: tuple[int, str] | None = None


@dataclass(eq=False)
class _Batch:
    id: str
    session_id: str
    folder: Path
    touched: float
    files: dict[str, _File] = field(default_factory=dict)
    texts: list[Path] = field(default_factory=list)
    job: dict | None = None
    cancel: threading.Event | None = None
    scanned: bool = False
    scans: list[Any] = field(default_factory=list, repr=False)
    candidates: dict[str, dict] = field(default_factory=dict)
    counts: dict[str, dict] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    conversations: dict = field(default_factory=lambda: {"ran": False, "read": 0, "characters": 0, "found": 0})
    preview: dict | None = None
    receipt: dict | None = None


_BATCHES: dict[str, _Batch] = {}


# Places ---------------------------------------------------------------------

def _root() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    root = get_row_bot_data_dir() / FOLDER
    if root.is_symlink():
        raise ClientPlatformError("import_storage_unavailable")
    root.mkdir(mode=0o700, exist_ok=True)
    return root


def _remove(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Memory import files stay until the next sweep", exc_info=True)


def _write_json(path: Path, value: Any) -> None:
    handle, temporary = tempfile.mkstemp(prefix="tmp-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as sink:
            json.dump(value, sink, ensure_ascii=False)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _drop_staged(batch: _Batch) -> None:
    """The person's files and pasted text: never kept once the batch no longer needs them."""
    batch.files.clear()
    batch.texts.clear()
    batch.scans.clear()
    for name in ("files", "texts", "candidates.json", "preview.json"):
        _remove(batch.folder / name)


def _retire(batch: _Batch) -> None:
    """Discarded, abandoned or the server stopping. Holds _LOCK."""
    if batch.cancel is not None:
        batch.cancel.set()
    _BATCHES.pop(batch.id, None)
    if batch.receipt is None:
        _remove(batch.folder)
    else:
        _drop_staged(batch)


def sweep() -> None:
    """Abandoned batches, an earlier run's leftovers, and receipts past their 7 days."""
    try:
        with _LOCK:
            now = _now()
            for batch in list(_BATCHES.values()):
                running = batch.job is not None and batch.job.get("status") == "running"
                if batch.receipt is None and not running and now - batch.touched > IDLE_SECONDS:
                    _retire(batch)
            root = _root()
            for entry in root.iterdir():
                if not _BATCH_ID.fullmatch(entry.name) or entry.is_symlink() or not entry.is_dir():
                    continue
                if entry.name in _BATCHES:
                    continue
                receipt = _read_json(entry / "receipt.json")
                if not isinstance(receipt, dict):
                    _remove(entry)  # never applied, and its session is gone
                    continue
                for name in ("files", "texts", "candidates.json", "preview.json"):
                    _remove(entry / name)
                if now - float(receipt.get("applied_ts") or 0) > KEEP_SECONDS:
                    _remove(entry)
    except OSError:
        logger.warning("Memory imports could not be swept", exc_info=True)


def close_batches() -> None:
    """At shutdown: unapplied batches never outlive the server, and neither do their files."""
    with _LOCK:
        for batch in list(_BATCHES.values()):
            _retire(batch)


# Views ----------------------------------------------------------------------

def _model() -> dict | None:
    """The default chat model, named before anything is sent to it."""
    try:
        from row_bot.providers.background import background_model_ref
        from row_bot.providers.selection import parse_model_ref, provider_display_label

        reference = background_model_ref("extraction")
        parsed = parse_model_ref(reference) if reference else None
    except Exception:
        logger.debug("Memory import: the default chat model could not be read", exc_info=True)
        return None
    if not parsed:
        return None
    provider, model_id = parsed
    return {"name": model_id[:200], "provider": provider_display_label(provider)[:120],
            "on_this_computer": provider in {"ollama", "local"}}


def sources() -> dict:
    """Where memories can come from, the prompt to paste, and the bounds shown before anything runs."""
    from row_bot.memory_import import CONTINUE_PROMPT, SOURCES, UNIVERSAL_PROMPT

    return {
        "sources": [{"id": item.id, "name": item.name, "export": item.export, "prompt_route": item.prompt_route,
                     "how_to_get": item.how_to_get} for item in SOURCES],
        "prompt": UNIVERSAL_PROMPT,
        "continue_prompt": CONTINUE_PROMPT,
        "file_max_bytes": FILE_MAX_BYTES,
        "conversation_limit": CONVERSATION_LIMIT,
        "character_limit": CHARACTER_LIMIT,
        "model": _model(),
        "recent": recent(),
    }


def _file_view(item: _File) -> dict:
    return {"file_id": item.id, "name": item.name, "size_bytes": item.size, "received_bytes": item.received,
            "chunk_bytes": CHUNK_BYTES}


def _view(batch: _Batch) -> dict:
    available = sum(int(count.get("conversations") or 0) for count in batch.counts.values())
    return {
        "batch_id": batch.id,
        "status": "applied" if batch.receipt is not None else "open",
        "files": [_file_view(item) for item in batch.files.values()],
        "texts": len(batch.texts),
        "job": dict(batch.job) if batch.job else None,
        "scanned": batch.scanned,
        "found": len(batch.candidates),
        "sources": list(batch.counts.values()),
        "warnings": batch.warnings[:50],
        "skipped": batch.skipped[:100],
        "skipped_count": len(batch.skipped),
        "conversations": {**batch.conversations, "available": available,
                          "limit": min(available, CONVERSATION_LIMIT), "character_limit": CHARACTER_LIMIT},
        "model": _model(),
        "preview_revision": batch.preview["revision"] if batch.preview else None,
        "receipt": _receipt_view(batch.receipt) if batch.receipt else None,
    }


def _batch(session_id: str, batch_id: str) -> _Batch:
    batch = _BATCHES.get(batch_id)
    if batch is None:
        raise ClientPlatformError("import_expired")
    if batch.session_id != session_id:
        raise ClientPlatformError("not_found")
    batch.touched = _now()
    return batch


def _idle(batch: _Batch) -> None:
    if batch.job is not None and batch.job.get("status") == "running":
        raise ClientPlatformError("import_busy")
    if batch.receipt is not None:
        raise ClientPlatformError("import_applied")


# Batches and files ------------------------------------------------------------

def begin(session_id: str) -> dict:
    """A new import. A window has one at a time: starting again lets its earlier one go."""
    with _LOCK:
        for batch in [item for item in _BATCHES.values() if item.session_id == session_id]:
            _retire(batch)
        sweep()
        if sum(1 for item in _BATCHES.values() if item.receipt is None) >= BATCHES_HELD:
            raise ClientPlatformError("rate_limited")
        batch_id = uuid4().hex
        folder = _root() / batch_id
        folder.mkdir(mode=0o700)
        batch = _Batch(batch_id, session_id, folder, _now())
        _BATCHES[batch_id] = batch
        return _view(batch)


def status(session_id: str, batch_id: str) -> dict:
    with _LOCK:
        return _view(_batch(session_id, batch_id))


def discard(session_id: str, batch_id: str) -> None:
    """Closed without importing: its files and everything found go."""
    with _LOCK:
        _retire(_batch(session_id, batch_id))


def _safe_name(name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", name).strip(" .") or "export"
    return cleaned[:120]


def add_file(session_id: str, batch_id: str, *, name: str, size_bytes: int) -> dict:
    """Make room for one file of an export, sent in chunks next."""
    if (not name or len(name) > 200 or any(char in name for char in "/\\:")
            or any(ord(char) < 32 for char in name) or size_bytes < 1):
        raise ClientPlatformError("invalid_command")
    if size_bytes > FILE_MAX_BYTES:
        raise ClientPlatformError("import_file_too_large")
    with _LOCK:
        batch = _batch(session_id, batch_id)
        _idle(batch)
        if len(batch.files) >= FILES_PER_BATCH:
            raise ClientPlatformError("import_too_many_files")
        waiting = sum(item.size - item.received for held in _BATCHES.values() for item in held.files.values())
        if shutil.disk_usage(batch.folder).free < size_bytes + waiting + DISK_RESERVE_BYTES:
            raise ClientPlatformError("import_storage_full")
        file_id = uuid4().hex
        place = batch.folder / "files" / file_id
        place.mkdir(parents=True, mode=0o700)
        path = place / _safe_name(name)
        path.touch()
        item = _File(file_id, name, size_bytes, path)
        batch.files[file_id] = item
        _forget_scan(batch)
        return _file_view(item)


def _file(batch: _Batch, file_id: str) -> _File:
    item = batch.files.get(file_id)
    if item is None:
        raise ClientPlatformError("not_found")
    return item


def file_status(session_id: str, batch_id: str, file_id: str) -> dict:
    with _LOCK:
        return _file_view(_file(_batch(session_id, batch_id), file_id))


def write_chunk(session_id: str, batch_id: str, file_id: str, offset: int, data: bytes) -> dict:
    """Write the next chunk, in order; a retried chunk whose answer was lost is answered, not refused."""
    digest = hashlib.sha256(data).hexdigest()
    with _LOCK:
        batch = _batch(session_id, batch_id)
        _idle(batch)
        item = _file(batch, file_id)
        if item.last is not None and item.last[0] == offset and offset + len(data) == item.received:
            if item.last[1] != digest:
                raise ClientPlatformError("revision_conflict")
        elif offset != item.received or offset >= item.size or len(data) != min(CHUNK_BYTES, item.size - offset):
            raise ClientPlatformError("invalid_command")
        else:
            with open(item.path, "r+b") as sink:
                sink.seek(offset)
                sink.write(data)
            item.received += len(data)
            item.last = (offset, digest)
        return _file_view(item)


def remove_file(session_id: str, batch_id: str, file_id: str) -> None:
    with _LOCK:
        batch = _batch(session_id, batch_id)
        _idle(batch)
        item = batch.files.pop(file_id, None)
        if item is None:
            raise ClientPlatformError("not_found")
        _remove(item.path.parent)
        _forget_scan(batch)


def add_text(session_id: str, batch_id: str, text: str) -> dict:
    """A pasted answer from the old assistant (or pasted export JSON)."""
    if not text.strip() or len(text) > PASTE_MAX_CHARS:
        raise ClientPlatformError("invalid_command")
    with _LOCK:
        batch = _batch(session_id, batch_id)
        _idle(batch)
        if len(batch.texts) >= TEXTS_PER_BATCH:
            raise ClientPlatformError("import_too_many_files")
        folder = batch.folder / "texts"
        folder.mkdir(mode=0o700, exist_ok=True)
        path = folder / f"{len(batch.texts) + 1:02d}.txt"
        path.write_text(text, encoding="utf-8")
        batch.texts.append(path)
        _forget_scan(batch)
        return _view(batch)


def _forget_scan(batch: _Batch) -> None:
    """What was found no longer matches the files: scan again."""
    batch.scanned = False
    batch.scans.clear()
    batch.candidates.clear()
    batch.counts.clear()
    batch.warnings.clear()
    batch.skipped.clear()
    batch.conversations = {"ran": False, "read": 0, "characters": 0, "found": 0}
    batch.preview = None


# Jobs -----------------------------------------------------------------------

class _Cancelled(Exception):
    pass


def _start(batch: _Batch, kind: str, work: Callable[[threading.Event, Callable[[int, int | None], None]], None],
           total: int | None = None) -> None:
    """Run ``work`` on its own thread. Holds _LOCK."""
    _idle(batch)
    cancel = threading.Event()
    batch.cancel = cancel
    batch.job = {"kind": kind, "status": "running", "done": 0, "total": total, "code": None}

    def progress(done: int, total: int | None) -> None:
        if cancel.is_set():
            raise _Cancelled
        with _LOCK:
            if batch.job is not None and batch.cancel is cancel:
                batch.job.update(done=done, total=total)

    def run() -> None:
        outcome: dict[str, Any] = {"status": "completed"}
        try:
            work(cancel, progress)
            if cancel.is_set():
                outcome = {"status": "cancelled"}
        except _Cancelled:
            outcome = {"status": "cancelled"}
        except ClientPlatformError as exc:
            outcome = {"status": "failed", "code": exc.code}
        except Exception:
            logger.error("Memory import %s failed", kind, exc_info=True)
            outcome = {"status": "failed", "code": f"import_{kind}_failed"}
        with _LOCK:
            if batch.job is not None and batch.cancel is cancel:
                batch.job.update(outcome)
                batch.touched = _now()

    threading.Thread(target=run, name=f"row-bot-memory-import-{kind}", daemon=True).start()


def cancel_job(session_id: str, batch_id: str) -> dict:
    """Stop the running job: the page moves on at once; what it found is let go."""
    with _LOCK:
        batch = _batch(session_id, batch_id)
        if batch.job is not None and batch.job.get("status") == "running" and batch.cancel is not None:
            batch.cancel.set()
            batch.job.update(status="cancelled")
            batch.cancel = None
        return _view(batch)


# Scan -----------------------------------------------------------------------

def _candidate_id(text: str) -> str:
    return hashlib.sha256(" ".join(text.lower().split()).encode("utf-8")).hexdigest()[:20]


def _candidate(text: str, category: str, *, source: str, label: str, date: str | None, origin: str,
               subject: str | None = None, tags: tuple[str, ...] | list[str] = ()) -> dict:
    return {"candidate_id": _candidate_id(text), "text": text[:TEXT_MAX_CHARS], "category": category,
            "source": source, "source_label": label[:200], "date": date, "origin": origin,
            "subject": (subject or "")[:120], "tags": list(tags)[:8]}


def _merge(batch: _Batch, scan: Any) -> None:
    from row_bot.memory_import.models import ENTITY_TYPES

    for fact in scan.facts:
        if len(batch.candidates) >= CANDIDATE_LIMIT:
            batch.warnings.append(f"Only the first {CANDIDATE_LIMIT:,} memories were kept.")
            break
        category = fact.category if fact.category in ENTITY_TYPES else "fact"
        item = _candidate(fact.text, category, source=fact.source, label=fact.source_label, date=fact.date,
                          origin="export", subject=fact.subject_hint, tags=fact.tags)
        batch.candidates.setdefault(item["candidate_id"], item)
    for source, count in scan.counts.items():
        held = batch.counts.setdefault(source, {"source": source, "label": count.label, "facts": 0,
                                                "conversations": 0})
        held["facts"] += count.facts
        held["conversations"] += count.conversations
    for warning in scan.warnings:
        if warning not in batch.warnings:
            batch.warnings.append(warning)
    batch.skipped.extend({"name": item.name[:300], "reason": item.reason[:300]} for item in scan.skipped)


def _scans(batch: _Batch) -> list[Any]:
    """Read the batch's files and texts again (nothing is written)."""
    from row_bot.memory_import import scan_files, scan_text

    found = []
    paths = [item.path for item in batch.files.values()]
    if paths:
        found.append(scan_files(paths))
    for path in list(batch.texts):
        found.append(scan_text(path.read_text(encoding="utf-8")))
    return found


def scan(session_id: str, batch_id: str) -> dict:
    """Find what the files and pasted text hold, in the background."""
    with _LOCK:
        batch = _batch(session_id, batch_id)
        _idle(batch)
        if not batch.files and not batch.texts:
            raise ClientPlatformError("invalid_command")
        if any(item.received != item.size for item in batch.files.values()):
            raise ClientPlatformError("upload_incomplete")
        _forget_scan(batch)
        parts = len(batch.files) + len(batch.texts)

        def work(cancel: threading.Event, progress: Callable[[int, int | None], None]) -> None:
            progress(0, parts)
            found = _scans(batch)
            progress(parts, parts)
            with _LOCK:
                if cancel.is_set() or batch.cancel is not cancel:
                    return
                for item in found:
                    _merge(batch, item)
                batch.scans = found
                batch.scanned = True
                _write_json(batch.folder / "candidates.json", list(batch.candidates.values()))

        _start(batch, "scan", work, parts)
        return _view(batch)


def candidates(session_id: str, batch_id: str) -> dict:
    with _LOCK:
        batch = _batch(session_id, batch_id)
        public = ("candidate_id", "text", "category", "source", "source_label", "date", "origin")
        return {"batch_id": batch.id,
                "items": [{key: item[key] for key in public} for item in batch.candidates.values()]}


# Find memories in past conversations ---------------------------------------------

def _stamp(conversation: Any) -> float:
    when = conversation.updated_at or conversation.created_at
    try:
        return when.timestamp() if when else 0.0
    except (OSError, OverflowError, ValueError):
        return 0.0


def _conversations(scans: list[Any]) -> Iterator[Any]:
    for item in scans:
        yield from item.iter_conversations()


def _newest(scans: list[Any]) -> list[tuple[str, str]]:
    """The newest conversations' keys, at most CONVERSATION_LIMIT, newest first."""
    stamps = sorted(((_stamp(item), item.provider, item.id) for item in _conversations(scans)), reverse=True)
    return [(provider, conversation_id) for _, provider, conversation_id in stamps[:CONVERSATION_LIMIT]]


def find_in_conversations(session_id: str, batch_id: str) -> dict:
    """Look for memories in the person's own messages, newest conversations first and bounded."""
    from row_bot.memory_import.sink import PROVIDER_NAMES

    with _LOCK:
        batch = _batch(session_id, batch_id)
        _idle(batch)
        if not batch.scanned:
            raise ClientPlatformError("invalid_command")
        if _model() is None:
            raise ClientPlatformError("model_selection_required")
        scans = list(batch.scans)

        def work(cancel: threading.Event, progress: Callable[[int, int | None], None]) -> None:
            import row_bot.memory_extraction as extraction
            from row_bot.memory import VALID_CATEGORIES

            nonlocal scans
            if not scans:  # after a restart of this batch's scan cache
                scans = _scans(batch)
            chosen = _newest(scans)
            order = {key: index for index, key in enumerate(chosen)}
            picked: dict[tuple[str, str], Any] = {}
            for conversation in _conversations(scans):
                key = (conversation.provider, conversation.id)
                if key in order and key not in picked:
                    picked[key] = conversation
            progress(0, len(chosen))
            used = read = calls = failed = 0
            found: dict[str, dict] = {}
            for done, key in enumerate(sorted(picked, key=order.__getitem__), 1):
                conversation = picked[key]
                messages = []
                for message in conversation.messages:
                    if message.role != "user" or used >= CHARACTER_LIMIT:
                        continue
                    text = message.text[:CHARACTER_LIMIT - used]
                    used += len(text)
                    messages.append({"role": "user", "content": text})
                if not messages:
                    if used >= CHARACTER_LIMIT:
                        break
                    continue
                read += 1
                label = f"{PROVIDER_NAMES.get(conversation.provider, conversation.provider)} · {conversation.title}"
                when = conversation.updated_at or conversation.created_at
                for window, _end in extraction._conversation_windows(messages, 0):
                    if not window:
                        continue
                    if cancel.is_set():
                        raise _Cancelled
                    calls += 1
                    entries = extraction._extract_from_conversation(window)
                    if isinstance(entries, extraction._NoReply):
                        failed += 1
                        if failed == calls and calls >= 2:
                            raise ClientPlatformError("import_model_unavailable")
                        continue
                    for entry in entries:
                        category = str(entry.get("category") or "").lower().strip()
                        content = " ".join(str(entry.get("content") or "").split())
                        if category not in VALID_CATEGORIES or category == "self_knowledge" or not content:
                            continue
                        item = _candidate(content, category, source=conversation.provider, label=label,
                                          date=when.date().isoformat() if when else None, origin="conversation",
                                          subject=str(entry.get("subject") or "").strip())
                        found.setdefault(item["candidate_id"], item)
                progress(done, len(chosen))
                if used >= CHARACTER_LIMIT:
                    break
            if calls and failed == calls:
                raise ClientPlatformError("import_model_unavailable")
            with _LOCK:
                if cancel.is_set() or batch.cancel is not cancel:
                    return
                added = 0
                for candidate_id, item in found.items():
                    if candidate_id not in batch.candidates and len(batch.candidates) < CANDIDATE_LIMIT:
                        batch.candidates[candidate_id] = item
                        added += 1
                if failed:
                    batch.warnings.append(f"{failed} part{'s' if failed != 1 else ''} of your conversations "
                                          "could not be read by the model; the rest were.")
                batch.conversations = {"ran": True, "read": read, "characters": used, "found": added}
                batch.preview = None
                _write_json(batch.folder / "candidates.json", list(batch.candidates.values()))

        _start(batch, "conversations", work, None)
        return _view(batch)


# Preview --------------------------------------------------------------------

_LEADING = re.compile(r"^(?:the user|user|they|he|she|i)\s+(?:is|are|has|have|was)?\s*", re.IGNORECASE)


def _subject(item: dict) -> str:
    """A subject for a memory with none: the sentence itself when short, else its opening words."""
    if item.get("subject"):
        return item["subject"]
    text = _LEADING.sub("", item["text"]).strip().rstrip(".!?;:,")
    if len(text) > 60:
        text = text[:60].rsplit(" ", 1)[0].rstrip(".,;:!?")
    return (text[:1].upper() + text[1:]) or "Imported memory"


def _new_memory(batch: _Batch, item: dict) -> Any:
    import row_bot.memory_evolution as memory_evo
    from row_bot.memory_reconcile import NewMemory

    source = f"import:{item['source']}"
    properties = {
        "extraction_method": "import",
        "import_batch": batch.id,
        "source_context": {"kind": "import", "display_name": item["source_label"], "batch_id": batch.id},
    }
    if item.get("date"):
        properties["source_date"] = item["date"]
    return NewMemory(item["category"], _subject(item), item["text"], tags=", ".join(item.get("tags") or []),
                     source=source,
                     properties=memory_evo.normalize_properties(properties, source=source,
                                                                entity_type=item["category"]))


def _target_view(target: dict | None) -> dict | None:
    if target is None:
        return None
    return {"id": target["id"], "subject": target.get("subject") or "", "text": target.get("description") or "",
            "updated_at": str(target.get("updated_at") or "")}


def preview(session_id: str, batch_id: str, items: list[dict]) -> dict:
    """Decide, per chosen memory, what saving it would do, in the background. Nothing is written."""
    from row_bot.memory import VALID_CATEGORIES

    with _LOCK:
        batch = _batch(session_id, batch_id)
        _idle(batch)
        chosen: list[dict] = []
        seen: set[str] = set()
        for edit in items:
            item = batch.candidates.get(str(edit.get("candidate_id") or ""))
            if item is None or item["candidate_id"] in seen:
                raise ClientPlatformError("invalid_command")
            seen.add(item["candidate_id"])
            text = " ".join(str(edit.get("text") or item["text"]).split())
            category = str(edit.get("category") or item["category"])
            if not text or len(text) > TEXT_MAX_CHARS or category not in VALID_CATEGORIES \
                    or category == "self_knowledge":
                raise ClientPlatformError("invalid_command")
            chosen.append({**item, "text": text, "category": category})
        if not chosen:
            raise ClientPlatformError("invalid_command")
        batch.preview = None

        def work(cancel: threading.Event, progress: Callable[[int, int | None], None]) -> None:
            import row_bot.memory_reconcile as reconcile

            planned: list[dict] = []
            progress(0, len(chosen))
            for start in range(0, len(chosen), reconcile.RECONCILE_BATCH):
                part = chosen[start:start + reconcile.RECONCILE_BATCH]
                memories = [_new_memory(batch, item) for item in part]
                by_memory = {id(memory): item for memory, item in zip(memories, part)}
                try:
                    decided = reconcile.plan(memories, purpose="extraction")
                except reconcile.ReconcileUnavailable:
                    raise ClientPlatformError("import_model_unavailable") from None
                for action, memory, target, value in decided:
                    item = by_memory[id(memory)]
                    planned.append({"candidate_id": item["candidate_id"], "action": action, "value": value,
                                    "text": item["text"], "category": item["category"], "source": item["source"],
                                    "source_label": item["source_label"], "date": item.get("date"),
                                    "subject": memory.subject, "tags": item.get("tags") or [],
                                    "target": _target_view(target)})
                progress(min(start + len(part), len(chosen)), len(chosen))
            order = {item["candidate_id"]: index for index, item in enumerate(chosen)}
            planned.sort(key=lambda entry: order[entry["candidate_id"]])
            revision = hashlib.sha256(json.dumps(planned, sort_keys=True).encode("utf-8")).hexdigest()
            with _LOCK:
                if cancel.is_set() or batch.cancel is not cancel:
                    return
                batch.preview = {"revision": revision, "items": planned}
                _write_json(batch.folder / "preview.json", batch.preview)

        _start(batch, "preview", work, len(chosen))
        return _view(batch)


def _preview_item(entry: dict) -> dict:
    target = entry.get("target")
    subject = entry["value"] if entry["action"] in {"add", "supersede"} else (target or {}).get("subject", "")
    return {"candidate_id": entry["candidate_id"], "action": entry["action"], "text": entry["text"],
            "category": entry["category"], "source_label": entry["source_label"], "subject": subject or "",
            "saved_subject": target["subject"] if target else None,
            "saved_text": target["text"][:TEXT_MAX_CHARS] if target else None,
            "new_text": entry["value"][:TEXT_MAX_CHARS * 2] if entry["action"] == "update" else None}


def read_preview(session_id: str, batch_id: str) -> dict:
    with _LOCK:
        batch = _batch(session_id, batch_id)
        if batch.preview is None:
            raise ClientPlatformError("import_preview_stale")
        return {"batch_id": batch.id, "revision": batch.preview["revision"],
                "items": [_preview_item(entry) for entry in batch.preview["items"]]}


# Apply and Undo ----------------------------------------------------------------

def _snapshot(entity: dict | None) -> dict | None:
    if entity is None:
        return None
    from row_bot.memory_reconcile import kg_props

    return {"id": entity["id"], "description": entity.get("description") or "", "aliases": entity.get("aliases") or "",
            "properties": kg_props(entity), "updated_at": str(entity.get("updated_at") or "")}


def _receipt_view(receipt: dict) -> dict:
    return {key: receipt.get(key) for key in (
        "batch_id", "command_id", "status", "applied_at", "undone_at", "sources", "added", "updated",
        "superseded", "skipped", "failed", "undo_until")}


def _source_words(source: str) -> str:
    from row_bot.memory_import.sink import PROVIDER_NAMES

    return "a pasted answer" if source == "pasted" else PROVIDER_NAMES.get(source, source)


def apply(session_id: str, batch_id: str, *, command_id: str, revision: str, exclude: list[str],
          validate: Callable[[], None] | None = None) -> dict:
    """Write what was previewed. Applying a batch twice changes nothing the second time."""
    import row_bot.knowledge_graph as kg
    import row_bot.memory_evolution as memory_evo
    import row_bot.memory_reconcile as reconcile

    with _LOCK:
        batch = _batch(session_id, batch_id)
        if batch.receipt is not None:
            return _receipt_view(batch.receipt)
        if batch.job is not None and batch.job.get("status") == "running":
            raise ClientPlatformError("import_busy")
        if batch.preview is None or batch.preview["revision"] != revision:
            raise ClientPlatformError("import_preview_stale")
        left_out = set(exclude)
        planned = [entry for entry in batch.preview["items"] if entry["candidate_id"] not in left_out]
        if not planned:
            raise ClientPlatformError("invalid_command")
        # A saved memory changed since the preview: preview again rather than overwrite that change.
        targets: dict[str, dict] = {}
        for entry in planned:
            target = entry.get("target")
            if target is None:
                continue
            current = kg.get_entity(target["id"])
            if current is None or str(current.get("updated_at") or "") != target["updated_at"]:
                batch.preview = None
                raise ClientPlatformError("import_preview_stale")
            targets[target["id"]] = current
        now = _now()
        receipt: dict[str, Any] = {
            "batch_id": batch.id, "command_id": command_id, "status": "applied",
            "applied_at": datetime.fromtimestamp(now).isoformat(timespec="seconds"), "applied_ts": now,
            "undo_until": datetime.fromtimestamp(now + KEEP_SECONDS).isoformat(timespec="seconds"),
            "undone_at": None,
            "sources": sorted({_source_words(entry["source"]) for entry in planned}),
            "added": 0, "updated": 0, "superseded": 0, "skipped": 0, "failed": 0, "changes": [],
        }
        for entry in planned:
            if entry["action"] == "skip":
                receipt["skipped"] += 1
                continue
            item = {**batch.candidates.get(entry["candidate_id"], {}), **entry}
            memory = _new_memory(batch, {**item, "subject": entry["subject"]})
            target = targets.get((entry.get("target") or {}).get("id", ""))
            user = None
            if entry["action"] in {"add", "supersede"} and kg._normalize_subject(entry["value"]) == "user":
                # Saving under the person's own name adds to their record: Undo restores it, never deletes it.
                user = _snapshot(kg.find_by_subject(None, "User"))
            previous = _snapshot(target)
            try:
                [outcome] = reconcile.apply([(entry["action"], memory, target, entry["value"])], actor="import",
                                            validate=validate)
            except ClientPlatformError:
                raise
            except Exception:
                logger.warning("Memory import: one memory could not be saved", exc_info=True)
                receipt["failed"] += 1
                continue
            if entry["action"] == "update":
                receipt["changes"].append({"kind": "restore", "id": outcome.entity_id, "previous": previous})
                receipt["updated"] += 1
                continue
            if user is not None and outcome.entity_id == user["id"]:
                receipt["changes"].append({"kind": "restore", "id": outcome.entity_id, "previous": user})
                receipt["updated"] += 1
            else:
                receipt["changes"].append({"kind": "delete", "id": outcome.entity_id})
                receipt["added" if entry["action"] == "add" else "superseded"] += 1
            if entry["action"] == "supersede" and previous is not None:
                receipt["changes"].append({"kind": "restore", "id": previous["id"], "previous": previous})
        batch.receipt = receipt
        _write_json(batch.folder / "receipt.json", receipt)
        _drop_staged(batch)
        changed = [change["id"] for change in receipt["changes"]]
        memory_evo.append_journal(
            "import", entity_ids=changed[:200], actor="import",
            source=", ".join(f"import:{source}" for source in sorted({e["source"] for e in planned})),
            reason=f"Imported from {', '.join(receipt['sources'])}",
            details={"batch_id": batch.id, **{key: receipt[key] for key in (
                "added", "updated", "superseded", "skipped", "failed")}})
        return _receipt_view(receipt)


def _stored(batch_id: str) -> tuple[_Batch | None, Path, dict]:
    if not _BATCH_ID.fullmatch(batch_id):
        raise ClientPlatformError("not_found")
    batch = _BATCHES.get(batch_id)
    folder = _root() / batch_id
    receipt = batch.receipt if batch is not None else _read_json(folder / "receipt.json")
    if not isinstance(receipt, dict):
        raise ClientPlatformError("not_found")
    return batch, folder, receipt


def undo(batch_id: str, *, validate: Callable[[], None] | None = None) -> dict:
    """Reverse an import from any signed-in device: delete what it added, restore what it changed."""
    import row_bot.knowledge_graph as kg
    import row_bot.memory_evolution as memory_evo

    with _LOCK:
        batch, folder, receipt = _stored(batch_id)
        if receipt.get("status") == "undone":
            return _receipt_view(receipt)
        if _now() - float(receipt.get("applied_ts") or 0) > KEEP_SECONDS:
            raise ClientPlatformError("import_expired")
        for change in reversed(receipt.get("changes") or []):
            if validate is not None:
                validate()
            if change["kind"] == "delete":
                if kg.get_entity(change["id"]) is not None:
                    kg.delete_entity(change["id"])
                continue
            previous = change.get("previous") or {}
            if kg.get_entity(change["id"]) is None:
                continue
            kg.update_entity(change["id"], previous.get("description", ""), aliases=previous.get("aliases", ""),
                             properties=previous.get("properties") or {})
        receipt["status"] = "undone"
        receipt["undone_at"] = datetime.fromtimestamp(_now()).isoformat(timespec="seconds")
        _write_json(folder / "receipt.json", receipt)
        if batch is not None:
            batch.receipt = receipt
        memory_evo.append_journal(
            "import_undo", entity_ids=[change["id"] for change in receipt.get("changes") or []][:200],
            actor="import", reason="The import was undone", details={"batch_id": batch_id})
        return _receipt_view(receipt)


def recent() -> list[dict]:
    """Imports applied in the last 7 days, newest first: each can still be undone."""
    with _LOCK:
        sweep()
        found = []
        root = _root()
        for entry in root.iterdir():
            if _BATCH_ID.fullmatch(entry.name) and entry.is_dir() and not entry.is_symlink():
                batch = _BATCHES.get(entry.name)
                receipt = batch.receipt if batch is not None else _read_json(entry / "receipt.json")
                if isinstance(receipt, dict):
                    found.append(receipt)
        found.sort(key=lambda receipt: float(receipt.get("applied_ts") or 0), reverse=True)
        return [_receipt_view(receipt) for receipt in found[:10]]
