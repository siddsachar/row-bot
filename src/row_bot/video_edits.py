"""Video edits (F3): each edit is a job of its conversation, from getting ffmpeg to the saved result.

An edit runs in the background: getting ffmpeg first when the person agreed to download it,
then preparing (the inputs, the plan, the pictures it lays over the video), then one ffmpeg
run through the background process manager (so it is contained, listed in Monitor and
stoppable, and its output is kept), then saving. The result is saved to the conversation's
files as a working file (``present`` shows it to the person; a video project's render is a draft its
card shows); a frames edit saves each still and one contact sheet of them, working files too. Writing a new file to the conversation's files never asks: nothing is
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

from row_bot.video_editing import ASPECTS, clock, shape_of

logger = logging.getLogger(__name__)

EDIT_ID = re.compile(r"edit-[0-9a-f]{8}")
Kind = Literal["video", "frames"]
Status = Literal["getting_ffmpeg", "preparing", "editing", "saving", "saved", "failed", "stopped"]
StoppedBy = Literal["", "you", "agent", "conversation", "quit"]
Problem = Literal["", "ffmpeg_unavailable", "download_failed", "edit_failed", "too_large", "not_saved"]
ACTIVE: frozenset[str] = frozenset({"getting_ffmpeg", "preparing", "editing", "saving"})
KEEP_PER_CONVERSATION = 100
PROBE_SECONDS = 30
FRAMES_PER_OPEN = 8  # Frames taken by opening the video at each time; more are picked in one read.
MAX_WORDS = 50_000  # A video's words kept for its subtitles: a few hours of speech.
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
    # The names the stills were saved under in the chat (S3): a taken name is kept as name-2, …
    frame_names: list[str] = field(default_factory=list)
    ffmpeg: str = ""
    checks: list[str] = field(default_factory=list)
    checks_passed: bool | None = None
    # A finished video with nothing to hear (cut only from screen recordings, which have no sound).
    silent: bool = False
    # A video project's scene starts on the video, then its end (5.3.1 B3).
    marks: list[float] = field(default_factory=list)
    # A video project's review sheet: frames of each scene, labelled, in the chat's files (its name).
    sheet: str = ""
    # How each of a video project's scenes was made: "new" (drawn now), "reused" (unchanged, drawn before) or ""
    # (a title card).
    scene_makes: list[str] = field(default_factory=list)
    # What a video project's video says, word by word in time (``Plan.spoken``), and its shape ("16:9", …; "" another
    # or not known): its subtitles and publish kit (C10).
    words: list[tuple[float, float, str]] = field(default_factory=list)
    shape: str = ""
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
    # Voice and music files a sound step names (5.3.1 B): name -> (path, or the chat file's bytes and suffix).
    sounds: dict[str, tuple[Any, bytes, str]] = field(default_factory=dict)
    # Give the finished video the whole finished check (``video_check``) and its review sheet (a video project's).
    check: bool = False
    # A video project's scenes: what the check knows of each one's footage (``video_check.Footage``), in order.
    footage: list[Any] = field(default_factory=list)
    # Make a looping GIF of the one source video instead (a video project's export).
    gif: bool = False
    # What the made video is to the person (``attachments.ROLES``) and the output it is a version of: a video
    # project's render is a draft of that project; any other edit is a working file of its own.
    role: str = "working"
    output_id: str = ""
    version: int = 1
    # Where a video project keeps its scenes' drawn clips, so making it again draws only the scenes that changed; its
    # made picture, music and effects are kept there too, so a change to its sound mixes only the sound again.
    scene_cache: Path | None = None
    # A video project's sound (C6): music made to fit its cut in this mood ("" none), its cuts on the music's beat,
    # and the loudness its mix is brought to (LUFS; None: -14).
    score: str = ""
    on_beat: bool = False
    loudness: float | None = None


def _lineage(edit: "VideoEdit", request: Request, role: str = "") -> dict[str, Any]:
    """The role and lineage of a file this edit saves (*role* for one of its working files)."""
    return {"role": role or request.role, "output_id": request.output_id or edit.edit_id,
            "version": request.version if not role else 1,
            "made_from": [Path(str(getattr(source, "label", "") or "")).name for source in request.sources]}


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
                 "mime_type", "size_bytes", "frame_refs", "frame_times", "frame_names", "ffmpeg", "checks", "checks_passed", "marks",
                 "sheet", "silent", "scene_makes", "words", "shape")


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
        for name in ("status", "process_id", "stopped_by", "problem", "detail", "media_ref", "mime_type", "ffmpeg",
                     "sheet"):
            setattr(edit, name, str(data.get(name) or getattr(edit, name)))
        edit.expected_seconds = float(data.get("expected_seconds") or 0)
        edit.ended_at = float(data["ended_at"]) if data.get("ended_at") is not None else None
        edit.size_bytes = int(data.get("size_bytes") or 0)
        edit.frame_refs = [str(item) for item in data.get("frame_refs") or []][:64]
        edit.frame_times = [float(item) for item in data.get("frame_times") or []][:64]
        edit.frame_names = [str(item)[:120] for item in data.get("frame_names") or []][:64]
        edit.checks = [str(item)[:400] for item in data.get("checks") or []][:40]
        edit.checks_passed = None if data.get("checks_passed") is None else bool(data["checks_passed"])
        from row_bot.video_projects import MAX_SCENES

        edit.marks = [float(item) for item in data.get("marks") or []][:MAX_SCENES + 1]
        edit.silent = bool(data.get("silent"))
        edit.scene_makes = [str(item) if item in ("new", "reused") else ""
                            for item in data.get("scene_makes") or []][:MAX_SCENES]
        edit.words = [(float(start), float(end), str(word)[:80]) for start, end, word in data.get("words") or []
                      ][:MAX_WORDS]
        edit.shape = str(data.get("shape") or "") if data.get("shape") in ASPECTS else ""
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
    if request.gif:
        return f"A GIF of {request.sources[0].label}"
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
    """Each source as a file the edit reads: chat files are copied in (checked); workspace files and files elsewhere
    on this computer are read where they are, never copied, however large. A picture (a project's still scene) is
    read for its size only."""
    from row_bot.video_editing import EditError, InputVideo
    from row_bot.video_studio import still_info

    inputs = []
    for index, source in enumerate(request.sources, start=1):
        _check(edit)
        if source.ref:
            name = f"input-{index}{'.png' if source.kind == 'image' else '.mp4'}"
            (folder / name).write_bytes(source.read_bytes())
            path, identity = name, f"ref:{source.ref}"
        else:
            path = str(source.path)
            try:
                found = source.path.stat()
            except OSError:
                raise EditError(f"{source.label} can't be read.") from None
            identity = f"{source.path}:{found.st_size}:{found.st_mtime_ns}"
        info = still_info(folder / path) if source.kind == "image" else probe_video(ffmpeg, path, folder)
        inputs.append(InputVideo(path=path, info=info, label=source.label, identity=identity))
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
    """How long the edited video really is (the plan's length is what it expected), and whether it has anything
    to hear."""
    from row_bot.media_sources import SourceError
    from row_bot.sound import has_sound
    from row_bot.video_editing import EditError

    _set(edit, silent=has_sound(folder / "output.mp4") is False)
    try:
        info = probe_video(ffmpeg, "output.mp4", folder)
    except (EditError, SourceError, OSError):
        return
    _set(edit, expected_seconds=round(info.duration, 2))


def _save_gif(edit: VideoEdit, output: Path, lineage: dict[str, Any]) -> None:
    from row_bot.application.attachments import AttachmentError, register_attachment
    from row_bot.video_editing import EditError

    _set(edit, status="saving")
    data = output.read_bytes() if output.is_file() else b""
    if not data:
        raise EditError("ffmpeg finished but made no GIF.")
    try:
        saved = register_attachment(edit.conversation_id, edit.output_name, data, **lineage)
    except AttachmentError:
        edit.problem = "not_saved"
        raise EditError("The GIF couldn't be saved to this chat: make the video shorter first.") from None
    if saved.get("mime_type") != "image/gif":
        edit.problem = "not_saved"
        raise EditError("ffmpeg didn't make a GIF.")
    _set(edit, media_ref=str(saved["attachment_ref"]), mime_type="image/gif", size_bytes=len(data),
         output_name=str(saved["name"]))  # A taken name is kept as name-2, …: say the one it has.


def _save_video(edit: VideoEdit, output: Path, lineage: dict[str, Any]) -> None:
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
        raise EditError(f"The edited video came out at {mb:.0f} MB, more than a chat can hold "
                        f"({MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB). Make it shorter or smaller (trim or crop it) "
                        "and try again.")
    try:
        saved = register_attachment(edit.conversation_id, edit.output_name, output.read_bytes(), **lineage)
    except AttachmentError:
        edit.problem = "not_saved"
        raise EditError("The video couldn't be saved to this chat.") from None
    if saved.get("mime_type") != "video/mp4":
        edit.problem = "not_saved"
        raise EditError("ffmpeg didn't make an MP4 video.")
    _set(edit, media_ref=str(saved["attachment_ref"]), mime_type="video/mp4", size_bytes=size,
         output_name=str(saved["name"]))  # A taken name is kept as name-2, …: say the one it has.


def _save_frames(edit: VideoEdit, folder: Path, names: list[str], times: list[float],
                 lineage: dict[str, Any]) -> None:
    from row_bot.application.attachments import AttachmentError, register_attachment
    from row_bot.contact_sheet import SheetError, make_sheet
    from row_bot.video_editing import EditError

    _set(edit, status="saving")
    pictures: list[tuple[bytes, str]] = []
    refs: list[str] = []
    saved_names: list[str] = []
    stem = Path(edit.output_name).stem or "frame"
    for index, (name, moment) in enumerate(zip(names, times), start=1):
        path = folder / name
        if not path.is_file() or path.stat().st_size == 0:
            continue
        data = path.read_bytes()
        try:
            saved = register_attachment(edit.conversation_id, f"{stem}-{index:02d}.jpg", data, **lineage)
        except AttachmentError:
            raise EditError("The frames couldn't be saved to this chat.") from None
        refs.append(str(saved["attachment_ref"]))
        saved_names.append(str(saved["name"]))
        pictures.append((data, clock(moment)))
    if not refs:
        raise EditError("ffmpeg made no frames (the times may be past the end of the video).")
    try:
        sheet, _tiles = make_sheet(pictures)
        saved = register_attachment(edit.conversation_id, f"generated-{stem}-sheet.jpg", sheet, **lineage)
    except (SheetError, AttachmentError):
        raise EditError("The frames were saved, but their contact sheet couldn't be made.") from None
    _set(edit, frame_refs=refs, frame_times=list(times[:len(refs)]), frame_names=saved_names, media_ref=str(saved["attachment_ref"]),
         mime_type=str(saved["mime_type"]), size_bytes=len(sheet))


def measure_file(ffmpeg: Any, path: Path, folder: Path, *, narrated: bool = False) -> tuple[Any, Any]:
    """One read of a finished video by ffmpeg (``video_check.measure_arguments``): what it is, and what was found."""
    from row_bot.video_check import measure_arguments, read_measures
    from row_bot.video_editing import EditError

    info = probe_video(ffmpeg, str(path), folder)
    try:
        done = subprocess.run(  # noqa: S603 - the resolved ffmpeg, arguments built by video_check
            ffmpeg.command(*measure_arguments(path, has_audio=info.has_audio, narrated=narrated)), cwd=folder,
            capture_output=True, text=True, timeout=1800, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        raise EditError("Checking took too long: check a shorter part (trim it first).") from None
    return info, read_measures(done.stderr or "", info, narrated=narrated)


def check_file(ffmpeg: Any, path: Path, folder: Path, *, conversation_id: str, name: str,
               label: str = "") -> tuple[Any, str]:
    """The finished check of any video (edit_video's check): one read for its sound and stretches, and one look at
    a review sheet of frames across it, saved to the chat as *name*, a working file made from the video (*label*).
    The checks, and the sheet's name ("" none)."""
    from row_bot import video_check
    from row_bot.application.attachments import AttachmentError, register_attachment
    from row_bot.contact_sheet import SheetError, make_sheet
    from row_bot.video_editing import EditError, frame_times, stills_arguments

    info, measures = measure_file(ffmpeg, path, folder)
    times = frame_times(info.duration, REVIEW_TILES // 2)
    found: list[Any] = []
    saved = ""
    try:
        subprocess.run(  # noqa: S603 - the resolved ffmpeg, arguments built by video_editing
            ffmpeg.command(*stills_arguments(path, times, "review-%02d.jpg", max_width=960)), cwd=folder,
            capture_output=True, timeout=600, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        pictures = [((folder / f"review-{index:02d}.jpg").read_bytes(), clock(at))
                    for index, at in enumerate(times, start=1) if (folder / f"review-{index:02d}.jpg").is_file()]
        sheet, _tiles = make_sheet(pictures)
        saved = str(register_attachment(conversation_id, name, sheet, role="working",
                                        output_id=f"check-{uuid.uuid4().hex[:8]}",
                                        made_from=[Path(label).name] if label else [])["name"])
        found = video_check.look(sheet)
    except (EditError, OSError, SheetError, AttachmentError, subprocess.SubprocessError):
        logger.info("A checked video's review sheet couldn't be made", exc_info=True)
    return video_check.checks(info, measures, found), saved


def _check_video(edit: VideoEdit, ffmpeg: Any, folder: Path, plan: Any, request: Request,
                 originals: tuple[list[Any], Any]) -> None:
    """The finished check of a made video (``video_check``): one read by ffmpeg, what its render knows (each
    scene's sharpness, words over the action or faces, safe areas, drift between shots), what its plan does that a
    good video doesn't (``Plan.review``), and, for a project's video, one look at its review sheet by the person's
    vision model."""
    from row_bot import video_check

    _check(edit)
    _set(edit, detail="Checking the video")
    info, measures = measure_file(ffmpeg, folder / "output.mp4", folder, narrated=plan.narrated)
    inputs, step = originals
    render = video_check.Render(plan=plan, inputs=inputs, step=step, footage=list(request.footage), folder=folder)
    found = video_check.findings(render, measures)
    if request.check and len(plan.marks) > 1:  # A project's video: frames of each scene, looked at once.
        sheet = _review_sheet(edit, ffmpeg, folder, plan.marks, _lineage(edit, request, "working"))
        if sheet:
            _check(edit)
            found += video_check.look(sheet, [scene.line or scene.card for scene in step.scenes]
                                      if step is not None and step.op == "scenes" else [])
    checks = video_check.checks(info, measures, found, narrated=plan.narrated, review=plan.review,
                                target=request.loudness if plan.mix is not None else None)
    _set(edit, checks=checks.lines, checks_passed=checks.passed, detail="")


REVIEW_TILES = 24


def review_times(marks: list[float]) -> list[tuple[int, float]]:
    """When to look at each scene of a made video: a second in and near its end (once for a short scene), as
    (scene, seconds); at most ``REVIEW_TILES``."""
    looks: list[tuple[int, float]] = []
    for number, (start, end) in enumerate(zip(marks, marks[1:]), start=1):
        if end - start >= 3.0:
            looks += [(number, start + 1.0), (number, end - 0.6)]
        elif end - start > 0.2:
            looks.append((number, (start + end) / 2))
    if len(looks) > REVIEW_TILES:
        step = len(looks) / REVIEW_TILES
        looks = [looks[int(index * step)] for index in range(REVIEW_TILES)]
    return [(number, round(at, 2)) for number, at in looks]


def _review_sheet(edit: VideoEdit, ffmpeg: Any, folder: Path, marks: list[float], lineage: dict[str, Any]) -> bytes:
    """The made video's review sheet: frames of each scene, labelled "scene · time", saved to the chat as a working
    file (the person can open it) for the check's one look; the sheet, or nothing when it couldn't be made."""
    from row_bot.application.attachments import AttachmentError, register_attachment
    from row_bot.contact_sheet import SheetError, make_sheet
    from row_bot.video_editing import EditError, stills_arguments

    looks = review_times(marks)
    if not looks:
        return b""
    names = [f"review-{index:02d}.jpg" for index in range(1, len(looks) + 1)]
    _check(edit)
    try:
        _run_process(edit, ffmpeg, stills_arguments("output.mp4", [at for _scene, at in looks], "review-%02d.jpg",
                                                   max_width=960), folder, "Video review")
        pictures = [((folder / name).read_bytes(), f"scene {scene} · {clock(at)}")
                    for name, (scene, at) in zip(names, looks) if (folder / name).is_file()]
        sheet, _tiles = make_sheet(pictures)
        saved = register_attachment(edit.conversation_id, f"{Path(edit.output_name).stem} review.jpg", sheet,
                                    **lineage)
    except (EditError, OSError, SheetError, AttachmentError):
        logger.info("A video's review sheet couldn't be made", exc_info=True)
        return b""
    _set(edit, sheet=str(saved["name"]))
    return sheet


# What a project keeps besides its scenes' clips, and how long one no make used is kept (as its clips are).
_KEPT = ("picture-", "score-", "effect-")


def _key(*parts: object) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:32]


def _kept(cache: Path | None, name: str) -> Path | None:
    """A file a project made before and keeps (marked used now), or None."""
    if cache is None:
        return None
    path = cache / name
    try:
        os.utime(path)
    except OSError:
        return None
    return path


def _keep(cache: Path | None, made: Path, name: str) -> Path:
    """Keep a file just made with the project (where a later make finds it), or leave it in the edit's folder."""
    if cache is None:
        return made
    cache.mkdir(parents=True, exist_ok=True)
    os.replace(made, cache / name)
    return cache / name


def _prune_kept(cache: Path | None, used: set[str]) -> None:
    from row_bot.video_studio import KEEP_SECONDS

    if cache is None:
        return
    old = time.time() - KEEP_SECONDS
    for path in cache.iterdir() if cache.is_dir() else []:
        try:
            if path.name.startswith(_KEPT) and path.name not in used and path.stat().st_mtime < old:
                path.unlink()
        except OSError:
            pass


def _on_beat(step: Any, inputs: list[Any], request: Request, sounds: dict[str, str], folder: Path) -> Any:
    """A project's scenes cut on the beat of its music: the beat of the music made to fit (its mood's tempo), else
    of its music file. Unchanged when it has no music."""
    from dataclasses import replace

    from row_bot import music, video_sound
    from row_bot.video_editing import BOOKEND_SECONDS, CROSSFADE, on_the_beat

    if request.score:
        beats = video_sound.grid(music.STYLES[request.score].bpm, music.MAX_SECONDS)
    else:
        track = next((item.source for item in request.steps if item.op == "sound" and item.role == "music"), "")
        if not track:
            return step
        path = Path(sounds[track])
        beats = video_sound.file_beats(path if path.is_absolute() else folder / path)
    offset = BOOKEND_SECONDS if step.look is not None and step.look.intro else 0.0
    fading = step.finish is not None and step.finish.transition == "crossfade" and len(step.scenes) > 1
    cut = on_the_beat(step.scenes, [item.info.duration for item in inputs], [item.info.still for item in inputs],
                      beats, offset=offset, overlap=CROSSFADE if fading else 0.0)
    return replace(step, scenes=cut)


def _picture(edit: VideoEdit, ffmpeg: Any, plan: Any, folder: Path, cache: Path | None) -> Path:
    """A video's picture with its footage's own sound (``PICTURE``): made by the plan's run, or, for a project whose
    picture didn't change (only its sound did), the one it made before."""
    import hashlib

    from row_bot.video_editing import PICTURE

    listed = folder / "scenes.txt"
    name = "picture-" + _key(plan.args, plan.graph, hashlib.sha256(plan.overlay_sheet).hexdigest(),
                             listed.read_text(encoding="utf-8") if listed.is_file() else "") + ".mkv"
    kept = _kept(cache, name)
    if kept is not None:
        _set(edit, detail="Picture unchanged: mixing the sound")
        return kept
    _run_process(edit, ffmpeg, plan.args, folder, "Video edit")
    _check(edit)
    return _keep(cache, folder / PICTURE, name)


def _mix(edit: VideoEdit, plan: Any, folder: Path, request: Request, picture: Path) -> None:
    """A video's sound (``video_sound.mix_down``): its music made to fit and its effects first (each kept with the
    project, made again only when it changes), then every sound mixed at its loudness into ``MIXED``."""
    from dataclasses import replace

    from row_bot import music, video_sound
    from row_bot.video_editing import MIXED, PICTURE, SCORE, EditError

    _check(edit)
    _set(edit, detail="Mixing the sound")
    actual = {PICTURE: str(picture)}
    used = {picture.name}
    if request.score:
        seconds = round(max(1.0, plan.duration), 3)
        name = f"score-{_key(request.score, seconds, plan.sections)}.wav"
        kept = _kept(request.scene_cache, name)
        if kept is None:
            (folder / SCORE).write_bytes(music.wav_bytes(music.compose(seconds, mood=request.score,
                                                                       beats=list(plan.sections))))
            kept = _keep(request.scene_cache, folder / SCORE, name)
        actual[SCORE], used = str(kept), used | {kept.name}
    for path, effect, _at, seconds in plan.effects:
        name = f"effect-{_key(effect, round(seconds, 3))}.wav"
        kept = _kept(request.scene_cache, name)
        if kept is None:
            video_sound.write_wav(folder / path, video_sound.effect(effect, seconds))
            kept = _keep(request.scene_cache, folder / path, name)
        actual[path], used = str(kept), used | {kept.name}
    _check(edit)
    mix = replace(plan.mix, tracks=tuple(replace(track, path=actual.get(track.path, track.path))
                                         for track in plan.mix.tracks))
    try:
        video_sound.mix_down(mix, folder, folder / MIXED, stopped=edit.stop_event.is_set)
    except video_sound.SoundError as error:
        _check(edit)
        raise EditError(str(error)) from None
    _prune_kept(request.scene_cache, used)
    _set(edit, detail="")


def _run(edit: VideoEdit, request: Request) -> None:
    from row_bot import ffmpeg_runtime
    from row_bot.media_sources import SourceError
    from row_bot.video_editing import (
        MIXED, NARRATION, OVERLAYS, SCORE, EditError, build_plan, final_arguments, frame_times, frames_arguments,
        graph_option_for, stills_arguments,
    )

    folder = working_folder(edit)
    try:
        ffmpeg = _ffmpeg(edit, request)
        _check(edit)
        _set(edit, ffmpeg=ffmpeg.describe(), status="preparing")
        folder.mkdir(parents=True, exist_ok=True)
        inputs = _inputs(edit, request, ffmpeg, folder)
        _check(edit)
        if request.gif:
            from row_bot.video_editing import gif_arguments

            _set(edit, expected_seconds=round(inputs[0].info.duration, 2))
            _run_process(edit, ffmpeg, gif_arguments(inputs[0].path, "output.gif"), folder, "Video GIF")
            _save_gif(edit, folder / "output.gif", _lineage(edit, request))
        elif request.kind == "video":
            sounds: dict[str, str] = {}
            for number, (name, (path, data, suffix)) in enumerate(request.sounds.items(), start=1):
                if path is None:  # A chat file: copied in, so ffmpeg reads only the edit's own folder.
                    copied = f"sound-{number}{suffix or '.bin'}"
                    (folder / copied).write_bytes(data)
                    sounds[name] = copied
                else:
                    sounds[name] = str(path)
            if request.score:
                sounds[SCORE] = SCORE
            steps = list(request.steps)
            # The check reads a project's scenes as they were asked for, before the studio pass draws them.
            originals = (list(inputs), steps[0] if steps else None)
            if steps and steps[0].op == "scenes":  # A video project: each recorded scene's camera and look.
                from row_bot import video_studio

                def drawing(done: int, total: int) -> None:
                    _set(edit, detail=f"Drawing scene {done + 1} of {total}")

                if request.on_beat:
                    steps[0] = _on_beat(steps[0], inputs, request, sounds, folder)
                inputs, steps[0], made = video_studio.prepare(inputs, steps[0], folder,
                                                              stopped=edit.stop_event.is_set, progress=drawing,
                                                              cache=request.scene_cache)
                _check(edit)
                _set(edit, detail="", scene_makes=made)
            plan = build_plan(inputs, steps, "output.mp4", sounds=sounds,
                              graph_option=graph_option_for(ffmpeg.version),
                              encoder=ffmpeg_runtime.h264_encoder(ffmpeg), loudness=request.loudness)
            (folder / "graph.txt").write_text(plan.graph, encoding="utf-8", newline="\n")
            if plan.overlay_sheet:
                (folder / OVERLAYS).write_bytes(plan.overlay_sheet)
            if plan.voices:
                from row_bot import video_studio

                video_studio.narration([(at, folder / path) for at, path in plan.voices], plan.duration,
                                       folder / NARRATION)
            _set(edit, expected_seconds=round(plan.duration, 2), marks=list(plan.marks),
                 words=list(plan.spoken[:MAX_WORDS]), shape=shape_of(plan.width, plan.height))
            if plan.mix is None:
                _run_process(edit, ffmpeg, plan.args, folder, "Video edit")
            else:  # Its picture (made, or as made before), then its sound mixed and laid beside it.
                picture = _picture(edit, ffmpeg, plan, folder, request.scene_cache)
                _mix(edit, plan, folder, request, picture)
                _run_process(edit, ffmpeg, final_arguments(str(picture), MIXED, "output.mp4"), folder, "Video sound")
            _measure(edit, ffmpeg, folder)
            if request.check or plan.narrated:  # A video given a voice is checked for gaps in it.
                _check_video(edit, ffmpeg, folder, plan, request, originals)
            _save_video(edit, folder / "output.mp4", _lineage(edit, request))
        else:
            info = inputs[0].info
            times = frame_times(info.duration, request.count, request.times)
            names = [f"frame-{index:02d}.jpg" for index in range(1, len(times) + 1)]
            _set(edit, expected_seconds=0.0)
            # Each time opens the video once: past a few, one read picks them all (18 to 34 times failed, R5).
            arguments = (frames_arguments(inputs[0].path, times, names) if len(times) <= FRAMES_PER_OPEN
                         else stills_arguments(inputs[0].path, times, "frame-%02d.jpg"))
            _run_process(edit, ffmpeg, arguments, folder, "Video frames")
            _save_frames(edit, folder, names, times, _lineage(edit, request, "working"))
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
    """The tool result's first line: ``Video edit edit-…: …`` (the chat's card follows it by that id), and a saved
    video's output (``media_sources.identity``)."""
    head = f"Video edit {edit.edit_id}: "
    if edit.status == "saved" and edit.kind == "frames":
        moments = ", ".join(clock(moment) for moment in edit.frame_times[:12])
        stem = Path(edit.output_name).stem or "frame"
        first, last = ((edit.frame_names[0], edit.frame_names[-1]) if edit.frame_names
                       else (f"{stem}-01.jpg", f"{stem}-{len(edit.frame_refs):02d}.jpg"))
        return (head + f"saved {len(edit.frame_refs)} frames (at {moments}) to this chat's files as working files "
                "(the person doesn't see them). Look at them with analyze_image, files=['" + edit.edit_id + "']. "
                f"Each is also a full-size still ({first} to {last}, or {edit.edit_id}/{first}): name one to read "
                "small text.")
    if edit.status == "saved":
        from row_bot.recordings import size_words

        # A silent video went out to X as a demo twice before anyone noticed: say it where it's made.
        silent = (" It has no sound: whoever watches it hears nothing. A video for people to watch needs a voice "
                  "(make it with the video tool) or music; if it's meant to be silent, say so when you share it."
                  if edit.silent else "")
        from row_bot.media_sources import identity_of

        # Its output first (W4): history masking keeps a result's first line.
        named = identity_of(edit.media_ref) or edit.output_name
        return (head + f"{named}\nSaved {clock(edit.expected_seconds)} of video ({size_words(edit.size_bytes)}) "
                f"to this chat's files: present shows it to the person. {edit.summary}.{silent}")
    if edit.status == "failed":
        return head + f"failed. {edit.detail or 'The edit did not work.'}"
    if edit.status == "stopped":
        who = {"you": "by the person", "agent": "by you", "conversation": "with its conversation",
               "quit": "when Row-Bot closed"}.get(edit.stopped_by, "")
        return head + f"stopped{(' ' + who) if who else ''}; nothing was saved."
    return (head + f"{status_words(edit)}. It carries on in the background and the chat shows its progress with "
            f"Stop. To wait for it, call edit_video with action status and edit_id {edit.edit_id}.")
