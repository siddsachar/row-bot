"""Video editing tool (F3): one ``edit_video`` tool over ffmpeg.

An edit (trim, join, speed with an optional "2×" label, text badges and titles, zoom,
crop, burned-in captions, or stills) is one H.264 MP4 encode that runs in the background:
the tool waits a little, then the chat's card shows its progress with Stop and, once it is
saved, the video with a player. Results go to the conversation's files (new files, so nothing
is replaced and nothing asks); ``save`` copies a finished one into the workspace, where
replacing a file asks first and a code folder's files are never replaced.

Approvals (one at most per call): downloading ffmpeg the first time (a network download, by
the approval level) and replacing a workspace file. Reading the chat's files and the
workspace, and running ffmpeg on them, needs none.
"""

from __future__ import annotations

import mimetypes
import os
import re
import uuid
from pathlib import Path
from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.tools import registry
from row_bot.tools.base import BaseTool
from row_bot.tools.video_project_tool import langchain_tool as video_project_tool

# How long a call waits for its edit before it carries on in the background.
INLINE_SECONDS = 15
STATUS_WAIT_SECONDS = 60
DEFAULT_FRAMES = 6


def _conversation() -> str:
    from row_bot.tool_outputs import current_thread_id

    return str(current_thread_id() or "")


def output_name(value: str, *, default: str, suffix: str = ".mp4") -> str:
    """A plain file name for a result: letters, digits, spaces, dots, dashes and brackets, ending in *suffix*.

    Only a real file type comes off the end: "Row-Bot 5.3.1 demo" keeps its whole name."""
    text = Path(str(value or "").replace("\\", "/")).name
    if mimetypes.guess_type("name" + Path(text).suffix)[0]:
        text = Path(text).stem
    stem = re.sub(r"[^A-Za-z0-9 ._()\-]+", "-", text).strip(" .-")[:80]
    return (stem or default) + suffix


def _wait(edit, seconds: float) -> None:
    """Wait for an edit while the turn waits; the turn's Stop stops it."""
    from row_bot import video_edits
    from row_bot.cancellation import current_cancellation_scope

    scope = current_cancellation_scope()
    unregister = (scope.register(lambda: video_edits.stop(edit.edit_id, by="you"), "video_edit.stop")
                  if scope is not None else None)
    try:
        video_edits.wait(edit, seconds)
    finally:
        if unregister is not None:
            unregister()


def _resolve_videos(inputs: list[str], conversation: str):
    from row_bot.media_sources import SourceError, resolve

    sources = []
    for value in inputs:
        for media in resolve(value, conversation):
            if media.kind != "video":
                raise SourceError(f"{media.label} isn't a video.")
            sources.append(media)
    return sources


def _sounds(steps) -> dict:
    """The voice and music files the sound steps name: name -> (path, bytes, suffix)."""
    from row_bot.tools.sound_tool import _source
    from row_bot.video_editing import sound_sources

    found = {}
    for name in sound_sources(steps):
        _label, path, data, suffix = _source(name)
        found[name] = (path, data or b"", suffix)
    return found


def _check(inputs: list[str]) -> str:
    """Read a finished video: sound level, black and still stretches (the review card's checks)."""
    from row_bot import ffmpeg_runtime, video_edits
    from row_bot.media_sources import SourceError
    from row_bot.video_editing import EditError

    if len(inputs) != 1:
        return "Check one video at a time: name it in inputs (edit-…, rec-…, a file in this chat or a path)."
    conversation = _conversation()
    try:
        source = _resolve_videos(inputs, conversation)[0]
    except SourceError as exc:
        return str(exc)
    ffmpeg = ffmpeg_runtime.find()
    if ffmpeg is None:
        return "Checking a video needs ffmpeg: make an edit first (it offers to download it)."
    import tempfile

    with tempfile.TemporaryDirectory(prefix="row-bot-check-") as folder:
        path = Path(folder) / "video.mp4"
        if source.ref:
            path.write_bytes(source.read_bytes(limit=2 * 1024 ** 3))
        else:
            path = Path(source.path)
        try:
            checks = video_edits.check_file(ffmpeg, path, Path(folder))
        except EditError as error:
            return str(error)
    return f"Checks for {source.label}:\n" + "\n".join(f"- {line}" for line in checks.lines)


def ffmpeg_consent(purpose: str, tool: str) -> tuple[str, str, bool]:
    """(refusal, approval line, download): asks to download ffmpeg the first time, by the approval level."""
    from row_bot import ffmpeg_runtime
    from row_bot.tools.approval_gate import approval_check

    if ffmpeg_runtime.find() is not None:
        return "", "", False
    wheel = ffmpeg_runtime.current_wheel()
    if wheel is None:
        return ffmpeg_runtime.unavailable_reason(), "", False
    refusal, line = approval_check(
        ffmpeg_runtime.consent_request(purpose, tool, wheel=wheel),
        risks=ffmpeg_runtime.consent_risks(wheel),
        blocked_message=("BLOCKED: Video editing needs ffmpeg, and downloading it needs approval, but this chat "
                         "is on Block. Install ffmpeg yourself or change the approval level."),
        cancelled_message="The person didn't allow downloading ffmpeg; nothing was downloaded or edited.",
    )
    return refusal, line, not refusal


def _start(action: str, inputs: list[str], steps: list, name: str, count: int, times: list[float]) -> str:
    from row_bot import video_edits
    from row_bot.media_sources import SourceError
    from row_bot.tools.approval_gate import with_approval
    from row_bot.video_editing import MAX_FRAMES, MAX_INPUTS, EditError, parse_steps

    conversation = _conversation()
    if not conversation:
        return "Video editing needs a conversation."
    if not inputs:
        return "Name the video to edit in inputs: a recording (rec-…), an earlier edit (edit-…), a file in this chat or a workspace path."
    try:
        if action == "frames":
            if len(inputs) != 1:
                return "Frames come from one video at a time."
            if count and not 1 <= count <= MAX_FRAMES:
                return f"Take from 1 to {MAX_FRAMES} frames."
            parsed = []
        else:
            if len(inputs) > MAX_INPUTS:
                return f"At most {MAX_INPUTS} videos can be joined in one edit."
            parsed = parse_steps(steps)
        sources = _resolve_videos(inputs, conversation)
        sounds = _sounds(parsed)
    except (EditError, SourceError) as exc:
        return str(exc)
    refusal, line, download = ffmpeg_consent("frames" if action == "frames" else "edit", "edit_video")
    if refusal:
        return refusal
    default = "frame" if action == "frames" else (Path(sources[0].label).stem or "video") + "-edited"
    request = video_edits.Request(
        conversation_id=conversation,
        kind="frames" if action == "frames" else "video",
        sources=sources,
        steps=parsed,
        output_name=output_name(name, default=default, suffix="" if action == "frames" else ".mp4"),
        count=count or (0 if times else DEFAULT_FRAMES),
        times=[float(value) for value in times or []],
        download=download,
        sounds=sounds,
    )
    edit = video_edits.start(request)
    _wait(edit, INLINE_SECONDS)
    return with_approval(line, video_edits.summary(edit)) if line else video_edits.summary(edit)


def _own_edit(edit_id: str):
    from row_bot import video_edits

    edit = video_edits.get(str(edit_id or "").strip())
    if edit is None or edit.conversation_id != _conversation():
        return None
    return edit


def _status(edit_id: str) -> str:
    edit = _own_edit(edit_id)
    if edit is None:
        return f"{edit_id or 'That'} isn't a video edit in this chat."
    if edit.active:
        _wait(edit, STATUS_WAIT_SECONDS)
    from row_bot import video_edits

    return video_edits.summary(edit)


def _stop(edit_id: str) -> str:
    from row_bot import video_edits

    edit = _own_edit(edit_id)
    if edit is None:
        return f"{edit_id or 'That'} isn't a video edit in this chat."
    video_edits.stop(edit.edit_id, by="agent")
    video_edits.wait(edit, 15)
    return video_edits.summary(edit)


# ── Saving into the workspace ───────────────────────────────────────────────


def _replace_approval(relative: str, count: int, size: str) -> dict:
    what = relative if count == 1 else f"{count} files in {relative}"
    return {
        "tool": "edit_video",
        "label": "Replace a file",
        "question": f"Replace {what} in the workspace?",
        "description": (f"Row-Bot will overwrite {what} in the workspace with the edited "
                        f"{'video' if count == 1 else 'frames'} ({size}). What is there now is replaced."),
        "risk_class": "medium",
        "args": {"path": relative},
    }


def _write_file(target: Path, data: bytes, *, replace: bool) -> None:
    """Write the whole file, never through a link; a new one is created only if nothing is there."""
    if not replace:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
                             | getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        return
    partial = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
    try:
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
                             | getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)


def _save_to_code_folder(place, edit, files: list[tuple[str, str]]) -> str:
    """Through the Developer import: new files only, each recorded as a change that can be undone."""
    from row_bot.developer import edits as developer_edits
    from row_bot.tools.approval_gate import current_approval_mode

    saved: list[str] = []
    for relative, ref in files:
        try:
            change_set, decision = developer_edits.import_conversation_media(
                workspace_id=place.code_folder_id, thread_id=edit.conversation_id, media_ref=ref, path=relative,
                approval_mode=current_approval_mode(), confirmed=False)
        except ValueError as exc:
            reason = {
                "media_destination_conflict": (f"{relative} already exists in the code folder. Files there are never "
                                               "replaced from here: choose another name."),
                "media_destination_unavailable": f"The folder for {relative} doesn't exist in the code folder.",
                "media_type_conflict": f"{relative} must end with the file's type (.mp4 or .jpg).",
                "media_too_large": "The file is too large to import.",
            }.get(str(exc), f"{relative} couldn't be saved in the code folder.")
            prefix = f"Saved {', '.join(saved)}; " if saved else ""
            return prefix + reason
        if change_set is None and decision.decision != "allow":
            # A contained new file never asks (Developer's own rule); Block refuses it.
            return decision.reason
        saved.append(relative)  # Added now, or the same file was there already.
    return f"Saved {', '.join(saved)} in the code folder; each can be undone in its changes."


def _save(edit_id: str, path: str) -> str:
    from row_bot.application.attachments import AttachmentError, read_attachment
    from row_bot.media_sources import SourceError, workspace, workspace_target
    from row_bot.recordings import size_words
    from row_bot.tools.approval_gate import approval_check, with_approval

    edit = _own_edit(edit_id)
    if edit is None:
        return f"{edit_id or 'That'} isn't a video edit in this chat."
    if edit.status != "saved":
        return f"{edit.edit_id} isn't finished, so there's nothing to save yet."
    if not str(path or "").strip():
        return "Say where to save it in the workspace with path, for example videos/demo.mp4."
    place = workspace(edit.conversation_id)
    if place is None:
        return "There's no workspace folder to save into. Choose one in Settings, or use a code folder."
    stem = Path(edit.output_name).stem or "frame"
    if edit.kind == "frames":
        folder = str(path).rstrip("/\\")
        files = [(f"{folder}/{stem}-{index:02d}.jpg", ref) for index, ref in enumerate(edit.frame_refs, start=1)]
    else:
        target_name = str(path) if str(path).casefold().endswith(".mp4") else str(path).rstrip("/\\") + "/" + edit.output_name
        files = [(target_name, edit.media_ref)]
    if place.code_folder_id:
        return _save_to_code_folder(place, edit, files)
    try:
        targets = [(workspace_target(place.root, relative), ref) for relative, ref in files]
    except SourceError as exc:
        return str(exc)
    existing = [relative for (_target, relative, exists), _ref in targets if exists]
    line = ""
    if existing:
        total = edit.size_bytes if edit.kind == "video" else 0
        refusal, line = approval_check(
            _replace_approval(existing[0] if len(existing) == 1 else str(path), len(existing),
                              size_words(total) if total else f"{len(existing)} pictures"),
            risks=("overwrites an existing file in the workspace",),
            blocked_message="BLOCKED: Replacing a file needs approval, and this chat is on Block. Choose a new name.",
            cancelled_message="The person didn't allow replacing the file; nothing was saved.",
        )
        if refusal:
            return refusal
    written: list[str] = []
    for (target, relative, exists), ref in targets:
        try:
            data = read_attachment(ref)[1]
            target.parent.mkdir(parents=True, exist_ok=True)
            # Checked again just before writing: nothing new is replaced without asking.
            _target, _relative, now_exists = workspace_target(place.root, relative)
            _write_file(target, data, replace=exists and now_exists)
        except FileExistsError:
            return with_approval(line, f"{relative} appeared while saving, so it wasn't replaced. Choose a new name.")
        except (AttachmentError, SourceError, OSError):
            return with_approval(line, f"{relative} couldn't be saved in the workspace.")
        written.append(relative)
    return with_approval(line, f"Saved {', '.join(written)} in the workspace.") if line else \
        f"Saved {', '.join(written)} in the workspace."


# ── The tool ────────────────────────────────────────────────────────────────


class _Step(BaseModel):
    op: Literal["trim", "speed", "text", "zoom", "crop", "captions", "sound"]
    start: float | None = Field(default=None, description="Seconds.")
    end: float | None = Field(default=None, description="Seconds.")
    factor: float | None = Field(default=None, description="speed: 2 = twice as fast, 0.5 = half.")
    label: bool = Field(default=True, description="speed: show the speed, e.g. 2×.")
    text: str = Field(default="", description="text: the words. captions: SRT.")
    position: Literal["top-left", "top", "top-right", "center", "bottom-left", "bottom", "bottom-right"] | None = None
    style: Literal["badge", "title", "caption"] | None = None
    region: list[float] | None = Field(default=None, description="zoom, crop: [x, y, width, height], 0-1.")
    source: str = Field(default="", description="sound: a voice or music file by name (speak, make_music).")
    role: Literal["voice", "music", "original"] | None = Field(
        default=None, description="sound: voice at start; music under it (ducked); original = the video's own.")
    volume: float | None = Field(default=None, description="sound: 1 as it is, 0.3 music default, 0 silent.")


class _EditInput(BaseModel):
    action: Literal["edit", "frames", "check", "status", "stop", "save"] = "edit"
    inputs: list[str] = Field(default_factory=list, description=(
        "Videos, in order (several are joined): rec-…, edit-…, a file in this chat by name, or a workspace path."))
    steps: list[_Step] = Field(default_factory=list, description=(
        "Applied in order; times are on the video as it is after earlier steps. A region is fractions of the "
        "picture; zoom fills the picture with it for a while."))
    name: str = Field(default="", description="File name for the result, e.g. demo.mp4.")
    edit_id: str = Field(default="", description="status, stop, save: the edit (edit-…).")
    path: str = Field(default="", description="save: where in the workspace, e.g. videos/demo.mp4.")
    count: int = Field(default=0, description="frames: how many, evenly spread (default 6).")
    times: list[float] = Field(default_factory=list, description="frames: at these seconds instead.")


def _edit_video(action: str = "edit", inputs: list[str] | None = None, steps: list | None = None,
                name: str = "", edit_id: str = "", path: str = "", count: int = 0,
                times: list[float] | None = None) -> str:
    action = str(action or "edit").strip().casefold()
    if action in {"edit", "frames"}:
        return _start(action, list(inputs or []), list(steps or []), name, int(count or 0), list(times or []))
    if action == "check":
        return _check(list(inputs or []))
    if action == "status":
        return _status(edit_id)
    if action == "stop":
        return _stop(edit_id)
    if action == "save":
        return _save(edit_id, path)
    return "Use action edit, frames, check, status, stop or save."


class VideoEditingTool(BaseTool):

    @property
    def name(self) -> str:
        return "video_editing"

    @property
    def display_name(self) -> str:
        return "🎬 Video editing"

    @property
    def description(self) -> str:
        return ("Edit videos in the chat: trim, join, speed up with a label, add badges, titles and captions, zoom "
                "in, crop, and take stills; ffmpeg is downloaded once, after asking.")

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def untrusted_tool_names(self) -> set[str]:
        # A failed edit quotes ffmpeg's own words, which can carry a file's metadata.
        return {"edit_video", "video"}

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_edit_video,
                name="edit_video",
                description=(
                    "Edit videos with ffmpeg into one H.264 MP4 saved to this chat (shown with a player): trim, "
                    "join, speed (with a 2× label), text badges or titles, zoom (punch-in), crop, captions (SRT), "
                    "sound (voice lines at times, music ducked under them, one loudness). action=frames saves "
                    "stills; check reads a finished video's sound, black and still stretches: never say a video "
                    "is done before it passes. status/stop follow an edit in the background; save copies it into "
                    "the workspace. The first use may ask to download ffmpeg."
                ),
                args_schema=_EditInput,
            ),
            video_project_tool(),
        ]

    def execute(self, query: str) -> str:
        return "Use edit_video with its fields."


registry.register(VideoEditingTool())
