"""Video edits (F3): each edit is a job of its conversation, from getting ffmpeg to the saved result.

An edit runs in the background: getting ffmpeg first when the person agreed to download it,
then preparing (the inputs, the plan, the pictures it lays over the video), then one ffmpeg
run through the background process manager (so it is contained, listed in Monitor and
stoppable, and its output is kept), then saving. The result is saved to the conversation's
files, where the chat shows it with a preview; a frames edit saves each still and one contact
sheet of them. Writing a new file to the conversation's files never asks: nothing is
replaced. Copying a result into the workspace is a separate step in a turn (``edit_video``
with ``save``), with that folder's own rules.

Stopping an edit needs no approval. Edits stop with their conversation and when Row-Bot
quits; one a crash cut short is marked on the next start. A small record per edit is kept with
the conversation, so its card still shows after a restart.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from row_bot.video_editing import clock

logger = logging.getLogger(__name__)

EDIT_ID = re.compile(r"edit-[0-9a-f]{8}")
Kind = Literal["video", "frames"]
Status = Literal["getting_ffmpeg", "preparing", "editing", "saving", "saved", "failed", "stopped"]
StoppedBy = Literal["", "you", "agent", "conversation", "quit"]
Problem = Literal["", "ffmpeg_unavailable", "download_failed", "edit_failed", "too_large", "not_saved"]
ACTIVE: frozenset[str] = frozenset({"getting_ffmpeg", "preparing", "editing", "saving"})
KEEP_PER_CONVERSATION = 100
PROBE_SECONDS = 30
_FOLDER = "video-edits"
_LISTENERS: list[Callable[[], None]] = []


class Stopped(Exception):
    """The edit was stopped before it finished."""


@dataclass
class VideoEdit:
    edit_id: str
    conversation_id: str
    kind: Kind
    summary: str
    output_name: str
    started_at: float
    status: Status = "preparing"
    expected_seconds: float = 0.0
    process_id: str = ""
    ended_at: float | None = None
    stopped_by: str = ""
    problem: str = ""
    detail: str = ""
    media_ref: str = ""
    mime_type: str = ""
    size_bytes: int = 0
    frame_refs: list[str] = field(default_factory=list)
    frame_times: list[float] = field(default_factory=list)
    ffmpeg: str = ""
    done: threading.Event = field(default_factory=threading.Event)
    stop_event: threading.Event = field(default_factory=threading.Event)

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    def elapsed(self) -> float:
        end = self.ended_at if self.ended_at is not None else time.time()
        return max(0.0, end - self.started_at)


@dataclass
class Request:
    """What to make: an edited video from the sources and steps, or stills at these times."""

    conversation_id: str
    kind: Kind
    sources: list[Any]
    output_name: str
    steps: list[Any] = field(default_factory=list)
    count: int = 0
    times: list[float] = field(default_factory=list)
    download: bool = False


_LOCK = threading.RLock()
_RECORDS: dict[str, VideoEdit] = {}
_LOADED_ROOT: Path | None = None


def edits_root() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir() / _FOLDER


def _conversation_folder(conversation_id: str) -> Path:
    from row_bot.tool_outputs import thread_folder_name

    return edits_root() / thread_folder_name(conversation_id)


def working_folder(edit: VideoEdit) -> Path:
    return _conversation_folder(edit.conversation_id) / edit.edit_id


def _record_path(edit: VideoEdit) -> Path:
    return _conversation_folder(edit.conversation_id) / f"{edit.edit_id}.json"


def _iso(value: float | None) -> str | None:
    return None if value is None else datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


_SAVED_FIELDS = ("edit_id", "conversation_id", "kind", "summary", "output_name", "started_at", "status",
                 "expected_seconds", "process_id", "ended_at", "stopped_by", "problem", "detail", "media_ref",
                 "mime_type", "size_bytes", "frame_refs", "frame_times", "ffmpeg")


def _save(edit: VideoEdit) -> None:
    path = _record_path(edit)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.part")
        partial.write_text(json.dumps({name: getattr(edit, name) for name in _SAVED_FIELDS}), encoding="utf-8",
                           newline="\n")
        os.replace(partial, path)
    except OSError:
        logger.warning("Could not save a video edit's record", exc_info=True)


def _load(path: Path) -> VideoEdit | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not EDIT_ID.fullmatch(str(data.get("edit_id") or "")) or data.get("kind") not in {"video", "frames"}:
            return None
        edit = VideoEdit(
            edit_id=data["edit_id"], conversation_id=str(data["conversation_id"]), kind=data["kind"],
            summary=str(data.get("summary") or "")[:200], output_name=str(data.get("output_name") or "")[:120],
            started_at=float(data["started_at"]),
        )
        for name in ("status", "process_id", "stopped_by", "problem", "detail", "media_ref", "mime_type", "ffmpeg"):
            setattr(edit, name, str(data.get(name) or getattr(edit, name)))
        edit.expected_seconds = float(data.get("expected_seconds") or 0)
        edit.ended_at = float(data["ended_at"]) if data.get("ended_at") is not None else None
        edit.size_bytes = int(data.get("size_bytes") or 0)
        edit.frame_refs = [str(item) for item in data.get("frame_refs") or []][:64]
        edit.frame_times = [float(item) for item in data.get("frame_times") or []][:64]
        return edit
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def _remove(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
    except OSError:
        logger.debug("Could not remove a video edit's working files", exc_info=True)


def _ensure_loaded() -> None:
    """Read this data folder's edits once; one a crash cut short is marked, and its files go."""
    global _LOADED_ROOT
    root = edits_root()
    with _LOCK:
        if _LOADED_ROOT == root:
            return
        _RECORDS.clear()
        _LOADED_ROOT = root
        if not root.is_dir():
            return
        for path in sorted(root.glob("*/edit-*.json")):
            edit = _load(path)
            if edit is None:
                continue
            if edit.active:
                edit.status, edit.problem = "failed", "not_saved"
                edit.stopped_by = edit.stopped_by or "quit"
                edit.detail = "Row-Bot closed before the edit finished."
                edit.ended_at = edit.ended_at or edit.started_at
                _remove(working_folder(edit))
                _save(edit)
            edit.done.set()
            _RECORDS[edit.edit_id] = edit


def sweep_leftovers() -> int:
    """At start-up: edits a crash cut short are marked, and their working files removed."""
    global _LOADED_ROOT
    with _LOCK:
        _LOADED_ROOT = None
        _ensure_loaded()
        return sum(1 for edit in _RECORDS.values() if edit.problem == "not_saved" and edit.stopped_by == "quit")


def add_listener(callback: Callable[[], None]) -> Callable[[], None]:
    _LISTENERS.append(callback)
    return lambda: _LISTENERS.remove(callback) if callback in _LISTENERS else None


def _changed() -> None:
    for callback in list(_LISTENERS):
        try:
            callback()
        except Exception:
            logger.debug("A video edit listener failed", exc_info=True)


# ── Running one ─────────────────────────────────────────────────────────────


def start(request: Request) -> VideoEdit:
    """Start an edit in the background; its card follows it by its id."""
    conversation = str(request.conversation_id or "").strip()
    if not conversation:
        raise ValueError("Video edits belong to a conversation.")
    _ensure_loaded()
    with _LOCK:
        edit = VideoEdit(
            edit_id=f"edit-{uuid.uuid4().hex[:8]}",
            conversation_id=conversation,
            kind=request.kind,
            summary=_summary(request),
            output_name=request.output_name,
            started_at=time.time(),
            status="getting_ffmpeg" if request.download else "preparing",
        )
        _RECORDS[edit.edit_id] = edit
    _save(edit)
    _forget_old(conversation)
    thread = threading.Thread(target=_run, args=(edit, request), daemon=True, name=f"row-bot-{edit.edit_id}")
    thread.start()
    _changed()
    logger.info("Video edit started (%s)", request.kind)
    return edit


def _summary(request: Request) -> str:
    if request.kind == "frames":
        count = len(request.times) if request.times else max(1, request.count)
        return f"{count} still{'s' if count != 1 else ''} from {request.sources[0].label}"
    from row_bot.video_editing import describe

    return describe(list(request.steps), len(request.sources))


def _set(edit: VideoEdit, **changes: Any) -> None:
    with _LOCK:
        for name, value in changes.items():
            setattr(edit, name, value)
    _save(edit)
    _changed()


def _check(edit: VideoEdit) -> None:
    if edit.stop_event.is_set():
        raise Stopped()


def _ffmpeg(edit: VideoEdit, request: Request):
    from row_bot import ffmpeg_runtime

    found = ffmpeg_runtime.find()
    if found is not None:
        return found
    if not request.download:
        raise ffmpeg_runtime.FfmpegError(
            "ffmpeg isn't installed any more. Ask again to download it, or install it yourself.")

    def check() -> None:
        if edit.stop_event.is_set():
            raise ffmpeg_runtime.FfmpegStopped("Stopped.")

    _set(edit, status="getting_ffmpeg")
    try:
        return ffmpeg_runtime.install(check=check)
    except ffmpeg_runtime.FfmpegStopped:
        raise Stopped() from None


def probe_video(ffmpeg: Any, path: str, folder: Path) -> Any:
    """What ffmpeg says about one input (a short run of its own)."""
    from row_bot.video_editing import EditError, parse_probe, probe_command

    try:
        result = subprocess.run(  # noqa: S603 - the resolved ffmpeg, arguments built by video_editing
            ffmpeg.command(*probe_command(path)), cwd=folder, capture_output=True, text=True,
            timeout=PROBE_SECONDS, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        raise EditError("ffmpeg took too long to read a video.") from None
    except OSError:
        raise EditError("ffmpeg couldn't be started on this computer.") from None
    return parse_probe((result.stderr or "") + (result.stdout or ""))


def _inputs(edit: VideoEdit, request: Request, ffmpeg: Any, folder: Path) -> list[Any]:
    """Each source as a file ffmpeg reads: chat files are copied in (checked), workspace files read in place."""
    from row_bot.video_editing import InputVideo

    inputs = []
    for index, source in enumerate(request.sources, start=1):
        _check(edit)
        if source.ref:
            name = f"input-{index}.mp4"
            (folder / name).write_bytes(source.read_bytes())
            path = name
        else:
            path = str(source.path)
        inputs.append(InputVideo(path=path, info=probe_video(ffmpeg, path, folder), label=source.label))
    return inputs


def _command_words(ffmpeg: Any, args: list[str]) -> str:
    command = ffmpeg.command(*args)
    text = subprocess.list2cmdline(command) if os.name == "nt" else " ".join(command)
    return text[:4096]


def _run_process(edit: VideoEdit, ffmpeg: Any, args: list[str], folder: Path, name: str) -> str:
    """Run ffmpeg through the background process manager until it ends; its last output."""
    from row_bot import background_processes
    from row_bot.video_editing import EditError

    try:
        record = background_processes.start(edit.conversation_id, ffmpeg.command(*args),
                                            command=_command_words(ffmpeg, args), folder=str(folder),
                                            kind="command", name=name)
    except (background_processes.ProcessLimitError, background_processes.ProcessStartError) as exc:
        raise EditError(str(exc)) from None
    _set(edit, process_id=record.process_id, status="editing")
    while not background_processes.wait(record, 0.5):
        if edit.stop_event.is_set():
            background_processes.stop(record.process_id, by="you" if edit.stopped_by == "you" else "agent")
    text, _dropped = record.log.tail(8192)
    if edit.stop_event.is_set() or record.status == "stopped":
        edit.stopped_by = edit.stopped_by or "you"
        raise Stopped()
    if record.exit_code != 0:
        raise EditError(_failure_words(text))
    return text


def _failure_words(output: str) -> str:
    """ffmpeg's own last complaint, for the agent to fix its steps."""
    lines = [line.strip() for line in output.splitlines()
             if line.strip() and "=" not in line.split(" ", 1)[0] and not line.startswith("frame=")]
    useful = [line for line in lines if re.search(r"error|invalid|no such|not found|unable|cannot|failed", line,
                                                  re.IGNORECASE)]
    last = (useful or lines or ["no details"])[-1]
    return f"ffmpeg couldn't make it: {last[:300]}"


def _measure(edit: VideoEdit, ffmpeg: Any, folder: Path) -> None:
    """How long the edited video really is (the plan's length is what it expected)."""
    from row_bot.media_sources import SourceError
    from row_bot.video_editing import EditError

    try:
        info = probe_video(ffmpeg, "output.mp4", folder)
    except (EditError, SourceError, OSError):
        return
    _set(edit, expected_seconds=round(info.duration, 2))


def _save_video(edit: VideoEdit, output: Path) -> None:
    from row_bot.application.attachments import MAX_ATTACHMENT_BYTES, AttachmentError, register_attachment
    from row_bot.video_editing import EditError

    _set(edit, status="saving")
    try:
        size = output.stat().st_size
    except OSError:
        raise EditError("ffmpeg finished but made no video.") from None
    if size == 0:
        raise EditError("ffmpeg finished but made no video.")
    if size > MAX_ATTACHMENT_BYTES:
        mb = size / (1024 * 1024)
        edit.problem = "too_large"
        raise EditError(f"The edited video came out at {mb:.0f} MB, more than a chat can hold (25 MB). Make it "
                        "shorter or smaller (trim or crop it) and try again.")
    try:
        saved = register_attachment(edit.conversation_id, edit.output_name, output.read_bytes())
    except AttachmentError:
        edit.problem = "not_saved"
        raise EditError("The video couldn't be saved to this chat.") from None
    if saved.get("mime_type") != "video/mp4":
        edit.problem = "not_saved"
        raise EditError("ffmpeg didn't make an MP4 video.")
    _set(edit, media_ref=str(saved["attachment_ref"]), mime_type="video/mp4", size_bytes=size)


def _save_frames(edit: VideoEdit, folder: Path, names: list[str], times: list[float]) -> None:
    from row_bot.application.attachments import AttachmentError, register_attachment
    from row_bot.contact_sheet import SheetError, make_sheet
    from row_bot.video_editing import EditError

    _set(edit, status="saving")
    pictures: list[tuple[bytes, str]] = []
    refs: list[str] = []
    stem = Path(edit.output_name).stem or "frame"
    for index, (name, moment) in enumerate(zip(names, times), start=1):
        path = folder / name
        if not path.is_file() or path.stat().st_size == 0:
            continue
        data = path.read_bytes()
        try:
            saved = register_attachment(edit.conversation_id, f"{stem}-{index:02d}.jpg", data)
        except AttachmentError:
            raise EditError("The frames couldn't be saved to this chat.") from None
        refs.append(str(saved["attachment_ref"]))
        pictures.append((data, clock(moment)))
    if not refs:
        raise EditError("ffmpeg made no frames (the times may be past the end of the video).")
    try:
        sheet, _tiles = make_sheet(pictures)
        saved = register_attachment(edit.conversation_id, f"generated-{stem}-sheet.jpg", sheet)
    except (SheetError, AttachmentError):
        raise EditError("The frames were saved, but their contact sheet couldn't be made.") from None
    _set(edit, frame_refs=refs, frame_times=list(times[:len(refs)]), media_ref=str(saved["attachment_ref"]),
         mime_type=str(saved["mime_type"]), size_bytes=len(sheet))


def _run(edit: VideoEdit, request: Request) -> None:
    from row_bot import ffmpeg_runtime
    from row_bot.media_sources import SourceError
    from row_bot.video_editing import (
        EditError, build_plan, frame_times, frames_arguments, graph_option_for,
    )

    folder = working_folder(edit)
    try:
        ffmpeg = _ffmpeg(edit, request)
        _check(edit)
        _set(edit, ffmpeg=ffmpeg.describe(), status="preparing")
        folder.mkdir(parents=True, exist_ok=True)
        inputs = _inputs(edit, request, ffmpeg, folder)
        _check(edit)
        if request.kind == "video":
            plan = build_plan(inputs, list(request.steps), "output.mp4",
                              graph_option=graph_option_for(ffmpeg.version),
                              encoder=ffmpeg_runtime.h264_encoder(ffmpeg))
            (folder / "graph.txt").write_text(plan.graph, encoding="utf-8", newline="\n")
            for name, data in plan.overlays:
                (folder / name).write_bytes(data)
            _set(edit, expected_seconds=round(plan.duration, 2))
            _run_process(edit, ffmpeg, plan.args, folder, "Video edit")
            _measure(edit, ffmpeg, folder)
            _save_video(edit, folder / "output.mp4")
        else:
            info = inputs[0].info
            times = frame_times(info.duration, request.count, request.times)
            names = [f"frame-{index:02d}.jpg" for index in range(1, len(times) + 1)]
            _set(edit, expected_seconds=0.0)
            _run_process(edit, ffmpeg, frames_arguments(inputs[0].path, times, names), folder, "Video frames")
            _save_frames(edit, folder, names, times)
        _set(edit, status="saved", ended_at=time.time())
    except Stopped:
        _set(edit, status="stopped", stopped_by=edit.stopped_by or "you", ended_at=time.time())
    except ffmpeg_runtime.FfmpegError as exc:
        _set(edit, status="failed", problem="download_failed" if request.download else "ffmpeg_unavailable",
             detail=str(exc), ended_at=time.time())
    except (EditError, SourceError) as exc:
        _set(edit, status="failed", problem=edit.problem or "edit_failed", detail=str(exc), ended_at=time.time())
    except Exception:
        logger.warning("A video edit failed", exc_info=True)
        _set(edit, status="failed", problem=edit.problem or "edit_failed",
             detail="Something went wrong while editing the video.", ended_at=time.time())
    finally:
        _remove(folder)
        edit.done.set()
        _changed()
        logger.info("Video edit ended (%s)", edit.status)


# ── Stopping, waiting, reading ──────────────────────────────────────────────


def stop(edit_id: str, *, by: StoppedBy) -> VideoEdit | None:
    """Ask an edit to stop; it ends shortly after (``wait``). No approval."""
    edit = get(edit_id)
    if edit is None:
        return None
    with _LOCK:
        if not edit.active:
            return edit
        edit.stopped_by = edit.stopped_by or by
    edit.stop_event.set()
    if edit.process_id:
        from row_bot import background_processes

        background_processes.stop(edit.process_id, by="you" if by == "you" else "agent")
    return edit


def wait(edit: VideoEdit, timeout: float | None) -> bool:
    return edit.done.wait(timeout)


def stop_conversation(conversation_id: str, *, by: StoppedBy = "conversation", timeout: float = 15.0) -> int:
    edits = [edit for edit in list_edits(conversation_id) if edit.active]
    for edit in edits:
        stop(edit.edit_id, by=by)
    deadline = time.monotonic() + timeout
    for edit in edits:
        wait(edit, max(0.0, deadline - time.monotonic()))
    return len(edits)


def stop_all(*, by: StoppedBy = "quit", timeout: float = 10.0) -> int:
    with _LOCK:
        edits = [edit for edit in _RECORDS.values() if edit.active]
    for edit in edits:
        stop(edit.edit_id, by=by)
    deadline = time.monotonic() + timeout
    for edit in edits:
        wait(edit, max(0.0, deadline - time.monotonic()))
    return len(edits)


def get(edit_id: str) -> VideoEdit | None:
    if not EDIT_ID.fullmatch(str(edit_id or "")):
        return None
    _ensure_loaded()
    with _LOCK:
        return _RECORDS.get(edit_id)


def list_edits(conversation_id: str | None = None) -> list[VideoEdit]:
    """Edits running now first, then finished ones, newest first."""
    _ensure_loaded()
    with _LOCK:
        edits = [edit for edit in _RECORDS.values()
                 if conversation_id is None or edit.conversation_id == conversation_id]
    return sorted(edits, key=lambda edit: (not edit.active, -edit.started_at))


def active_edits() -> list[VideoEdit]:
    with _LOCK:
        return [edit for edit in _RECORDS.values() if edit.active]


def _forget_old(conversation_id: str) -> None:
    finished = [edit for edit in list_edits(conversation_id) if not edit.active]
    for edit in finished[KEEP_PER_CONVERSATION:]:
        with _LOCK:
            _RECORDS.pop(edit.edit_id, None)
        _remove(_record_path(edit))


def delete_conversation(conversation_id: str) -> int:
    """Forget a deleted conversation's edits (their results are its files and go with it)."""
    _ensure_loaded()
    with _LOCK:
        for edit_id in [key for key, edit in _RECORDS.items() if edit.conversation_id == conversation_id]:
            _RECORDS.pop(edit_id, None)
    folder = _conversation_folder(conversation_id)
    if not folder.exists():
        return 0
    _remove(folder)
    return 1


def progress(edit: VideoEdit) -> tuple[float | None, int, int]:
    """(How far it is, 0–1, when that is known; bytes of ffmpeg downloaded; the download's size)."""
    if edit.status == "getting_ffmpeg":
        from row_bot import ffmpeg_runtime

        state = ffmpeg_runtime.download_state()
        total = int(state["total"] or 0)
        received = int(state["received"] or 0)
        return (min(1.0, received / total) if total else None), received, total
    if edit.status == "editing" and edit.kind == "video" and edit.process_id and edit.expected_seconds > 0:
        from row_bot import background_processes
        from row_bot.video_editing import progress_seconds

        record = background_processes.get(edit.process_id)
        if record is None:
            return None, 0, 0
        text, _dropped = record.log.tail(4096)
        done = progress_seconds(text)
        if done is None:
            return 0.0, 0, 0
        return max(0.0, min(0.99, done / edit.expected_seconds)), 0, 0
    if edit.status in {"saving", "saved"}:
        return 1.0, 0, 0
    return None, 0, 0


def status_words(edit: VideoEdit) -> str:
    fraction, received, total = progress(edit)
    if edit.status == "getting_ffmpeg":
        from row_bot.ffmpeg_runtime import size_words

        return (f"getting ffmpeg, {size_words(received)} of {size_words(total)}" if total
                else "getting ffmpeg")
    if edit.status == "editing":
        return f"editing, {round((fraction or 0) * 100)}% done" if edit.kind == "video" else "taking the frames"
    return {"preparing": "preparing", "saving": "saving", "saved": "saved", "failed": "failed",
            "stopped": "stopped"}[edit.status]


def view(edit: VideoEdit) -> dict[str, Any]:
    """The client's view of one edit."""
    fraction, received, total = progress(edit)
    return {
        "edit_id": edit.edit_id,
        "conversation_id": edit.conversation_id,
        "kind": edit.kind,
        "summary": edit.summary,
        "status": edit.status,
        "progress": None if fraction is None else round(fraction, 3),
        "download_received": received if edit.status == "getting_ffmpeg" else None,
        "download_total": total if edit.status == "getting_ffmpeg" else None,
        "started_at": _iso(edit.started_at),
        "ended_at": _iso(edit.ended_at),
        "seconds": round(edit.elapsed(), 1),
        "duration_seconds": edit.expected_seconds or None,
        "output_name": edit.output_name,
        "media_ref": edit.media_ref or None,
        "mime_type": edit.mime_type or None,
        "size_bytes": edit.size_bytes or None,
        "frame_count": len(edit.frame_refs),
        "stopped_by": edit.stopped_by if edit.stopped_by in {"you", "agent", "conversation", "quit"} else "",
        "problem": edit.problem,
        "detail": edit.detail[:400],
        "ffmpeg": edit.ffmpeg or None,
    }


def summary(edit: VideoEdit) -> str:
    """The tool result's first line: ``Video edit edit-…: …`` (the chat's card follows it by that id)."""
    head = f"Video edit {edit.edit_id}: "
    if edit.status == "saved" and edit.kind == "frames":
        moments = ", ".join(clock(moment) for moment in edit.frame_times[:12])
        return (head + f"saved {len(edit.frame_refs)} frames (at {moments}) to this chat's files; the person sees "
                "them on one contact sheet. Look at them with analyze_image, files=['" + edit.edit_id + "'].")
    if edit.status == "saved":
        from row_bot.recordings import size_words

        return (head + f"saved {clock(edit.expected_seconds)} of video ({size_words(edit.size_bytes)}) to this "
                f"chat's files as {edit.output_name}; the person sees it in the chat. {edit.summary}.")
    if edit.status == "failed":
        return head + f"failed. {edit.detail or 'The edit did not work.'}"
    if edit.status == "stopped":
        who = {"you": "by the person", "agent": "by you", "conversation": "with its conversation",
               "quit": "when Row-Bot closed"}.get(edit.stopped_by, "")
        return head + f"stopped{(' ' + who) if who else ''}; nothing was saved."
    return (head + f"{status_words(edit)}. It carries on in the background and the chat shows its progress with "
            f"Stop. To wait for it, call edit_video with action status and edit_id {edit.edit_id}.")
