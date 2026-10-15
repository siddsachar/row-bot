"""Recordings: Row-Bot's own browser, or the screen through Computer use (F2, decision D2).

Each recording belongs to the conversation that started it. Starting one goes through the
approval gate (the tools ask); stopping one never needs approval. A recording stops when it's
asked to, at its time limit, before its video outgrows what a chat can hold, when its
conversation is deleted and when Row-Bot quits. The finished video (H.264 MP4) is saved to the
conversation's files, where the chat shows it with a preview; the working files go.

The backends do the capturing: ``browser.recording`` records the conversation's page in the
managed browser, ``computer_use.recording`` records the main display through Cua Driver. A
backend is a handle with ``request_stop(reason)``; it calls back once with the finished file
(or nothing) when capture has ended. A small record per recording is kept on disk with the
conversation, so the chat can still show the result after a restart.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

logger = logging.getLogger(__name__)

RECORDING_ID = re.compile(r"rec-[0-9a-f]{8}")
Kind = Literal["browser", "screen"]
Status = Literal["recording", "saving", "saved", "failed"]
StoppedBy = Literal["", "you", "agent", "limit", "size", "conversation", "quit", "closed", "error"]
# Why a recording has no video, as the client words it.
Problem = Literal["", "not_saved", "too_large", "nothing_recorded"]

DEFAULT_MINUTES = 5
MAX_MINUTES: dict[str, int] = {"browser": 15, "screen": 10}
# A chat holds files up to 25 MB; recordings stop before their video reaches this.
SIZE_LIMIT = 20 * 1024 * 1024
# Finished recordings listed per conversation (their videos stay in its files).
KEEP_PER_CONVERSATION = 100
_FOLDER = "recordings"
_LISTENERS: list[Callable[[], None]] = []


class RecordingError(RuntimeError):
    """A recording could not start; the message says why in plain words."""


class Capture(Protocol):
    def request_stop(self, reason: str) -> None: ...


@dataclass
class Recording:
    recording_id: str
    conversation_id: str
    kind: Kind
    started_at: float
    max_seconds: int
    status: Status = "recording"
    stopped_by: StoppedBy = ""
    ended_at: float | None = None
    duration_seconds: float = 0.0
    media_ref: str = ""
    mime_type: str = ""
    size_bytes: int = 0
    problem: Problem = ""
    capture: Any = None
    done: threading.Event = field(default_factory=threading.Event)

    @property
    def active(self) -> bool:
        return self.status in {"recording", "saving"}

    def elapsed(self) -> float:
        if self.ended_at is not None:
            return self.duration_seconds
        return max(0.0, time.time() - self.started_at)


_LOCK = threading.RLock()
_RECORDS: dict[str, Recording] = {}
_LOADED_ROOT: Path | None = None


def recordings_root() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir() / _FOLDER


def _conversation_folder(conversation_id: str) -> Path:
    from row_bot.tool_outputs import thread_folder_name

    return recordings_root() / thread_folder_name(conversation_id)


def working_path(record: Recording) -> Path:
    """Where the backend writes while it records: a file (browser) or a folder (screen)."""

    folder = _conversation_folder(record.conversation_id)
    return folder / (f"{record.recording_id}.mp4" if record.kind == "browser" else record.recording_id)


def _record_path(record: Recording) -> Path:
    return _conversation_folder(record.conversation_id) / f"{record.recording_id}.json"


def _iso(value: float | None) -> str | None:
    return None if value is None else datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _data(record: Recording) -> dict[str, Any]:
    return {
        "recording_id": record.recording_id, "conversation_id": record.conversation_id, "kind": record.kind,
        "started_at": record.started_at, "max_seconds": record.max_seconds, "status": record.status,
        "stopped_by": record.stopped_by, "ended_at": record.ended_at, "duration_seconds": record.duration_seconds,
        "media_ref": record.media_ref, "mime_type": record.mime_type, "size_bytes": record.size_bytes,
        "problem": record.problem,
    }


def _save(record: Recording) -> None:
    path = _record_path(record)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.name}.{os.getpid()}.part")
        partial.write_text(json.dumps(_data(record)), encoding="utf-8", newline="\n")
        os.replace(partial, path)
    except OSError:
        logger.warning("Could not save a recording's record", exc_info=True)


def _remove(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
    except OSError:
        logger.debug("Could not remove a recording's working files", exc_info=True)


def _load(path: Path) -> Recording | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not RECORDING_ID.fullmatch(str(data.get("recording_id") or "")) or data.get("kind") not in MAX_MINUTES:
            return None
        return Recording(
            recording_id=data["recording_id"], conversation_id=str(data["conversation_id"]), kind=data["kind"],
            started_at=float(data["started_at"]), max_seconds=int(data["max_seconds"]),
            status=data.get("status") or "failed", stopped_by=data.get("stopped_by") or "",
            ended_at=data.get("ended_at"), duration_seconds=float(data.get("duration_seconds") or 0),
            media_ref=str(data.get("media_ref") or ""), mime_type=str(data.get("mime_type") or ""),
            size_bytes=int(data.get("size_bytes") or 0), problem=data.get("problem") or "",
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def _ensure_loaded() -> None:
    """Read this data folder's recordings once; one a crash cut short is marked, and its files go."""

    global _LOADED_ROOT
    root = recordings_root()
    with _LOCK:
        if _LOADED_ROOT == root:
            return
        _RECORDS.clear()
        _LOADED_ROOT = root
        if not root.is_dir():
            return
        for path in sorted(root.glob("*/rec-*.json")):
            record = _load(path)
            if record is None:
                continue
            if record.active:
                # Row-Bot closed while it recorded: the unfinished video can't be played.
                record.status, record.problem = "failed", "not_saved"
                record.stopped_by = record.stopped_by or "quit"
                record.ended_at = record.ended_at or record.started_at
                _remove(working_path(record))
                _save(record)
            record.done.set()
            _RECORDS[record.recording_id] = record


def sweep_leftovers() -> int:
    """At start-up: recordings a crash cut short are marked, and their working files removed."""

    global _LOADED_ROOT
    with _LOCK:
        _LOADED_ROOT = None
        _ensure_loaded()
        return sum(1 for record in _RECORDS.values() if record.problem == "not_saved" and record.stopped_by == "quit")


def add_listener(callback: Callable[[], None]) -> Callable[[], None]:
    """Called when a recording starts or ends (Buddy follows them)."""

    _LISTENERS.append(callback)
    return lambda: _LISTENERS.remove(callback) if callback in _LISTENERS else None


def _changed() -> None:
    for callback in list(_LISTENERS):
        try:
            callback()
        except Exception:
            logger.debug("A recording listener failed", exc_info=True)


# ── Starting and stopping ───────────────────────────────────────────────────


def minutes(value: object, kind: Kind) -> int:
    """The time limit asked for, kept within the kind's maximum (5 minutes by default)."""

    try:
        chosen = int(value) if value not in (None, "", 0) else DEFAULT_MINUTES
    except (TypeError, ValueError):
        chosen = DEFAULT_MINUTES
    return max(1, min(chosen, MAX_MINUTES[kind]))


def start(
    conversation_id: str,
    kind: Kind,
    *,
    max_minutes: int,
    begin: Callable[[Recording, Path], Capture],
) -> Recording:
    """Start one; *begin* starts the backend and returns its capture (or raises RecordingError)."""

    conversation = str(conversation_id or "").strip()
    if not conversation:
        raise RecordingError("Recordings belong to a conversation.")
    _ensure_loaded()
    with _LOCK:
        for record in _RECORDS.values():
            if record.status != "recording":
                continue
            if record.kind == kind and record.conversation_id == conversation:
                what = "Row-Bot's browser" if kind == "browser" else "the screen"
                raise RecordingError(f"{what.capitalize()} is already being recorded in this chat ({record.recording_id}).")
            if kind == "screen" and record.kind == "screen":
                raise RecordingError("The screen is already being recorded in another chat; stop that recording first.")
        record = Recording(
            recording_id=f"rec-{uuid.uuid4().hex[:8]}",
            conversation_id=conversation,
            kind=kind,
            started_at=time.time(),
            max_seconds=minutes(max_minutes, kind) * 60,
        )
        _RECORDS[record.recording_id] = record
    path = working_path(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        record.capture = begin(record, path)
    except BaseException:
        with _LOCK:
            _RECORDS.pop(record.recording_id, None)
        _remove(path)
        raise
    _save(record)
    _forget_old(conversation)
    _changed()
    logger.info("Recording started (%s)", kind)
    return record


def finished(record: Recording, reason: str, video: Path | None) -> None:
    """The backend's call when capture has ended: save the video to the conversation's files."""

    with _LOCK:
        if record.status != "recording":
            return  # Already being saved (a second end, from a stop that failed and its capture).
        record.status = "saving"
        record.stopped_by = record.stopped_by or _stopped_by(reason)
        record.ended_at = time.time()
        record.duration_seconds = max(0.0, record.ended_at - record.started_at)
    problem: Problem = ""
    try:
        if video is None or not video.is_file() or video.stat().st_size == 0:
            problem = "nothing_recorded" if reason != "error" else "not_saved"
        else:
            from row_bot.application.attachments import MAX_ATTACHMENT_BYTES, AttachmentError, register_attachment

            size = video.stat().st_size
            if size > MAX_ATTACHMENT_BYTES:
                problem = "too_large"
            else:
                data = video.read_bytes()
                name = "generated-recording.mp4" if record.kind == "browser" else "generated-screen-recording.mp4"
                try:
                    saved = register_attachment(record.conversation_id, name, data)
                except AttachmentError:
                    problem = "not_saved"  # The conversation is gone or can't take files now.
                else:
                    if saved.get("mime_type") != "video/mp4":
                        problem = "not_saved"
                    else:
                        record.media_ref = str(saved["attachment_ref"])
                        record.mime_type = "video/mp4"
                        record.size_bytes = size
    except OSError:
        logger.warning("Could not save a finished recording", exc_info=True)
        problem = "not_saved"
    finally:
        _remove(working_path(record))
        with _LOCK:
            record.problem = problem
            record.status = "failed" if problem else "saved"
            record.capture = None
        _save(record)
        record.done.set()
        _changed()
        logger.info("Recording ended (%s, %s)", record.kind, record.status)


def _stopped_by(reason: str) -> StoppedBy:
    return reason if reason in {"you", "agent", "limit", "size", "conversation", "quit", "closed", "error"} else "error"  # type: ignore[return-value]


def stop(recording_id: str, *, by: StoppedBy) -> Recording | None:
    """Ask a recording to stop; its video is saved shortly after (``wait``)."""

    record = get(recording_id)
    if record is None:
        return None
    with _LOCK:
        if record.status != "recording":
            return record
        record.stopped_by = by
        capture = record.capture
    if capture is not None:
        try:
            capture.request_stop(by)
        except Exception:
            logger.warning("A recording did not stop cleanly", exc_info=True)
            finished(record, "error", None)
    return record


def wait(record: Recording, timeout: float | None) -> bool:
    """Wait until the recording's video is saved (or it failed); True when it has."""

    return record.done.wait(timeout)


def stop_conversation(conversation_id: str, *, by: StoppedBy = "conversation", timeout: float = 15.0) -> int:
    records = [record for record in list_recordings(conversation_id) if record.status == "recording"]
    for record in records:
        stop(record.recording_id, by=by)
    deadline = time.monotonic() + timeout
    for record in records:
        wait(record, max(0.0, deadline - time.monotonic()))
    return len(records)


def stop_all(*, by: StoppedBy = "quit", timeout: float = 15.0) -> int:
    """Stop every recording and wait (bounded) for their videos to be saved."""

    with _LOCK:
        records = [record for record in _RECORDS.values() if record.status == "recording"]
    for record in records:
        stop(record.recording_id, by=by)
    deadline = time.monotonic() + timeout
    for record in records:
        wait(record, max(0.0, deadline - time.monotonic()))
    return len(records)


# ── Reading ─────────────────────────────────────────────────────────────────


def get(recording_id: str) -> Recording | None:
    if not RECORDING_ID.fullmatch(str(recording_id or "")):
        return None
    _ensure_loaded()
    with _LOCK:
        return _RECORDS.get(recording_id)


def list_recordings(conversation_id: str | None = None) -> list[Recording]:
    """Recordings now running first, then finished ones, newest first."""

    _ensure_loaded()
    with _LOCK:
        records = [record for record in _RECORDS.values()
                   if conversation_id is None or record.conversation_id == conversation_id]
    return sorted(records, key=lambda record: (not record.active, -record.started_at))


def active_recordings() -> list[Recording]:
    with _LOCK:
        return [record for record in _RECORDS.values() if record.status == "recording"]


def active_for(conversation_id: str, kind: Kind) -> Recording | None:
    _ensure_loaded()
    with _LOCK:
        return next((record for record in _RECORDS.values() if record.status == "recording"
                     and record.conversation_id == conversation_id and record.kind == kind), None)


def latest_for(conversation_id: str, kind: Kind) -> Recording | None:
    return next((record for record in list_recordings(conversation_id) if record.kind == kind), None)


def _forget_old(conversation_id: str) -> None:
    finished_records = [record for record in list_recordings(conversation_id) if not record.active]
    for record in finished_records[KEEP_PER_CONVERSATION:]:
        with _LOCK:
            _RECORDS.pop(record.recording_id, None)
        _remove(_record_path(record))


def delete_conversation(conversation_id: str) -> int:
    """Forget a deleted conversation's recordings and remove their records (its files go with it)."""

    _ensure_loaded()
    with _LOCK:
        for recording_id in [key for key, record in _RECORDS.items() if record.conversation_id == conversation_id]:
            _RECORDS.pop(recording_id, None)
    folder = _conversation_folder(conversation_id)
    if not folder.exists():
        return 0
    _remove(folder)
    return 1


def view(record: Recording) -> dict[str, Any]:
    """The client's view of one recording."""

    return {
        "recording_id": record.recording_id,
        "conversation_id": record.conversation_id,
        "kind": record.kind,
        "status": record.status,
        "started_at": _iso(record.started_at),
        "ended_at": _iso(record.ended_at),
        "seconds": round(record.elapsed(), 1),
        "max_seconds": record.max_seconds,
        "stopped_by": record.stopped_by,
        "media_ref": record.media_ref or None,
        "mime_type": record.mime_type or None,
        "size_bytes": record.size_bytes or None,
        "problem": record.problem,
    }


def clock_words(seconds: float) -> str:
    """0:42, 4:05, 1:02:10."""

    total = max(0, int(round(seconds)))
    hours, rest = divmod(total, 3600)
    mins, secs = divmod(rest, 60)
    return f"{hours}:{mins:02d}:{secs:02d}" if hours else f"{mins}:{secs:02d}"


def size_words(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{max(1, round(size / 1024))} KB"


def summary(record: Recording) -> str:
    """The tool result's first line: ``Recording rec-…: …`` (the chat's card follows it by that id)."""

    what = "Row-Bot's browser" if record.kind == "browser" else "the screen"
    if record.status == "recording":
        return (f"Recording {record.recording_id}: recording {what} in this chat "
                f"(stops by itself after {record.max_seconds // 60} minutes).")
    if record.status == "saving":
        return f"Recording {record.recording_id}: stopped; the video of {what} is being saved."
    if record.status == "saved":
        return (f"Recording {record.recording_id}: saved {clock_words(record.duration_seconds)} of {what} "
                f"({size_words(record.size_bytes)}) to this chat's files; the person sees it in the chat.")
    reason = {
        "too_large": "the video was too large to save to the chat",
        "nothing_recorded": "nothing was recorded",
    }.get(record.problem, "the video couldn't be saved")
    return f"Recording {record.recording_id}: stopped, but {reason}."
