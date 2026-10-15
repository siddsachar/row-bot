"""Vision tool — let the agent see through the user's camera or screen.

The agent calls ``analyze_image`` when it needs to look at something
the user is showing to the camera, read their screen, or answer a
visual question.  The tool captures a frame from the webcam or a
screenshot of the primary monitor, sends it to the selected Vision
model, and returns the description.

Several images (F5, decision D6): up to eight pictures go in one call,
from the chat's files, the workspace, the page in Row-Bot's browser,
recordings and video edits (a video gives frames, through ffmpeg). A
contact sheet tiles many of them into one labelled picture, shown in the
chat too. Nothing is gathered when the model can't see images.
"""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.tools.base import BaseTool
from row_bot.tools import registry
from row_bot.vision_runtime import get_vision_service


def _get_vision_service():
    return get_vision_service()


# ── Tool implementation ──────────────────────────────────────────────────────

# A capture of the person's screen or camera can show anything, so it asks every time, even in Auto, and says
# what it captures (B327). A page in Row-Bot's own browser, or a file, needs no capture.
_CAPTURES = {
    "screen": (
        "Capture your whole screen?",
        "Takes a picture of everything on your main display (every open window, not just Row-Bot's browser) "
        "and shows it to the vision model.",
    ),
    "camera": (
        "Take a picture with your camera?",
        "Takes a photo with your webcam and shows it to the vision model.",
    ),
}


def _analyze_browser_page(question: str) -> str:
    from row_bot.tools.browser_tool import _get_thread_id, get_session_manager

    image = get_session_manager().take_screenshot(_get_thread_id())
    if image is None:
        return "No page is open in Row-Bot's browser. Open it with browser_navigate first, then look at it."
    return _get_vision_service().analyze(image, f"This is the page open in Row-Bot's browser. {question}")


# Frames taken from each video: as separate images, or on a contact sheet.
DEFAULT_FRAMES = 4
DEFAULT_SHEET_FRAMES = 12
FRAMES_SECONDS = 120


def _video_frames(media, count: int, ffmpeg, folder) -> list[tuple[bytes, str]]:
    """Evenly spread stills from one video, each captioned with its time."""
    import subprocess
    import uuid

    from row_bot.media_sources import SourceError
    from row_bot.video_editing import EditError, clock, frame_times, frames_arguments
    from row_bot.video_edits import probe_video

    prefix = uuid.uuid4().hex[:8]
    if media.ref:
        source = folder / f"video-{prefix}.mp4"
        source.write_bytes(media.read_bytes())
        path = source.name
    else:
        path = str(media.path)
    try:
        info = probe_video(ffmpeg, path, folder)
        times = frame_times(info.duration, count)
        names = [f"still-{prefix}-{index:02d}.jpg" for index in range(len(times))]
        subprocess.run(  # noqa: S603 - the resolved ffmpeg, arguments built by video_editing
            ffmpeg.command(*frames_arguments(path, times, names, max_width=1280)), cwd=folder, capture_output=True,
            timeout=FRAMES_SECONDS, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        raise SourceError(f"Taking frames from {media.label} took too long.") from None
    except EditError as exc:
        raise SourceError(f"{media.label}: {exc}") from None
    frames = [((folder / name).read_bytes(), f"{media.label} at {clock(moment)}")
              for name, moment in zip(names, times) if (folder / name).is_file()]
    if not frames:
        raise SourceError(f"No frames could be taken from {media.label}.")
    return frames


def _gather(files: list[str], conversation: str, *, sheet: bool, frames: int):
    """The pictures the files name, videos as frames (asking to download ffmpeg the first time).

    Returns (pictures, approval line) or raises SourceError with plain words.
    """
    import shutil
    import uuid

    from row_bot import ffmpeg_runtime
    from row_bot.data_paths import get_row_bot_data_dir
    from row_bot.media_sources import SourceError, resolve

    entries: list = []
    for value in files:
        if str(value).strip().casefold() == "browser":
            entries.append("browser")
            continue
        entries.extend(resolve(value, conversation))
    videos = [entry for entry in entries if entry != "browser" and entry.kind == "video"]
    per_video = frames or (DEFAULT_SHEET_FRAMES if sheet else DEFAULT_FRAMES)
    from row_bot.contact_sheet import MAX_TILES
    from row_bot.vision import MAX_IMAGES

    total = len(entries) - len(videos) + per_video * len(videos)
    limit = MAX_TILES if sheet else MAX_IMAGES
    if total > limit:
        if sheet:
            raise SourceError(f"A contact sheet holds at most {MAX_TILES} pictures (this would be {total}).")
        raise SourceError(
            f"At most {MAX_IMAGES} images go in one look (this would be {total}). Look at them in smaller groups, "
            "or set contact_sheet to tile them onto one labelled picture.")
    line = ""
    ffmpeg = None
    if videos:
        ffmpeg, line = ffmpeg_runtime.ensure("frames", "analyze_image")
        if ffmpeg is None:
            raise SourceError(line)
    pictures: list[tuple[bytes, str]] = []
    folder = get_row_bot_data_dir() / "vision-frames" / uuid.uuid4().hex
    try:
        for entry in entries:
            if entry == "browser":
                from row_bot.tools.browser_tool import _get_thread_id, get_session_manager

                image = get_session_manager().take_screenshot(_get_thread_id())
                if image is None:
                    raise SourceError("No page is open in Row-Bot's browser.")
                pictures.append((image, "The page in Row-Bot's browser"))
            elif entry.kind == "video":
                folder.mkdir(parents=True, exist_ok=True)
                pictures.extend(_video_frames(entry, per_video, ffmpeg, folder))
            else:
                pictures.append((entry.read_bytes(), entry.label))
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return pictures, line


def _analyze_files(question: str, files: list[str], contact_sheet: bool, frames: int) -> str:
    """Look at several pictures (or videos' frames) in one call, or at one contact sheet of them."""
    from row_bot.contact_sheet import SheetError, make_sheet
    from row_bot.media_sources import SourceError
    from row_bot.tool_outputs import current_thread_id
    from row_bot.tools.approval_gate import with_approval

    conversation = str(current_thread_id() or "")
    service = _get_vision_service()
    problem = service.cannot_see()
    if problem:  # Say so before gathering anything (and before any ffmpeg download).
        return f"Couldn't look at the images: {problem}"
    try:
        pictures, line = _gather(files, conversation, sheet=contact_sheet, frames=max(0, int(frames or 0)))
    except SourceError as exc:
        return str(exc)
    if contact_sheet:
        try:
            sheet, tiles = make_sheet(pictures)
        except SheetError as exc:
            return with_approval(line, str(exc)) if line else str(exc)
        shown = _show_sheet(sheet)
        answer = service.analyze(
            sheet,
            f"This is a contact sheet of {len(tiles)} pictures, numbered 1 to {len(tiles)} in reading order; "
            f"each caption under a picture gives its number and name (or its time in a video). {question}",
        )
        note = ("The contact sheet is shown to the person in the chat. " if shown else "")
        result = f"Looked at a contact sheet of {len(tiles)} pictures. {note}\n{answer}"
    else:
        answer = service.analyze_many(pictures, question)
        result = f"Looked at {len(pictures)} image{'s' if len(pictures) != 1 else ''}.\n{answer}"
    return with_approval(line, result) if line else result


def _show_sheet(sheet: bytes) -> bool:
    """Show the contact sheet in the chat, as a generated picture (saved to the chat's files)."""
    import base64

    try:
        from row_bot.tools.image_gen_tool import _set_pending_image

        _set_pending_image(base64.b64encode(sheet).decode("ascii"))
        return True
    except Exception:
        return False


def _analyze_image(
    question: str, source: str | None = None, file_path: str = "", files: list[str] | None = None,
    contact_sheet: bool = False, frames: int = 0,
) -> str:
    """Analyze the page in Row-Bot's browser, an image file, or (after asking) the person's screen or camera."""
    from row_bot.approval_policy import normalize_approval_mode
    from row_bot.tools.approval_gate import (
        APPROVAL_DENIED,
        current_approval_mode,
        resolve_approval_line,
        with_approval,
    )

    chosen = source is not None
    source = str(source or "").strip().casefold()
    if files:
        if source in _CAPTURES:
            # A capture keeps its own question (B327): never mixed with other pictures.
            return "Look at the screen or the camera in a call of its own; it can't be mixed with files."
        return _analyze_files(question, [str(item) for item in files], bool(contact_sheet), frames)
    if contact_sheet:
        return "A contact sheet needs files: the pictures or videos to tile."
    if not chosen:
        source = "camera"  # The default, as before; a blank or unknown source is refused below.
    if source == "browser":
        return _analyze_browser_page(question)
    if source == "file":
        return _get_vision_service().capture_and_analyze(question, source="file", file_path=file_path)
    if source not in _CAPTURES:
        # Any other value used to reach the webcam capture without the approval below.
        return f"Unknown source {source!r}: use 'camera', 'screen', 'file' or 'browser'."
    label, description = _CAPTURES[source]
    mode = normalize_approval_mode(current_approval_mode())
    if mode == "block":
        return f"BLOCKED: {label[:-1]} is unavailable while this conversation is in Block approval mode."
    # Asked under Ask (B327); under Auto the reviewer approves it only when the person clearly asked to
    # be looked at; Hands-off allows it.
    outcome, line = resolve_approval_line(
        {"tool": "analyze_image", "label": label, "description": description,
         "args": {"source": source, "question": question}},
        approval_mode=mode,
        risks=(f"captures the person's {'screen' if source == 'screen' else 'camera'}",),
    )
    if outcome != "allow":
        return with_approval(line or APPROVAL_DENIED, "Capture cancelled by user.")
    return with_approval(line, _get_vision_service().capture_and_analyze(question, source=source))


# ── Registration ─────────────────────────────────────────────────────────────

class VisionTool(BaseTool):

    @property
    def name(self) -> str:
        return "vision"

    @property
    def display_name(self) -> str:
        return "👁️ Vision"

    @property
    def description(self) -> str:
        return (
            "Look at the page open in Row-Bot's browser, an image file, or (after "
            "asking the person) their screen or camera, to describe what's visible, "
            "read text and answer visual questions."
        )

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def untrusted_tool_names(self) -> set[str]:
        # Answers read text off web pages, screens and image files.
        return {"analyze_image"}

    def as_langchain_tools(self) -> list:

        class _AnalyzeInput(BaseModel):
            question: str = Field(
                description="A specific visual question, e.g. 'What error is on the screen?'."
            )
            # When to use each source is in the tool description (and the vision guide).
            source: Literal["camera", "screen", "file", "browser"] | None = Field(
                default=None, description="Where the image comes from (camera when nothing else is given)."
            )
            file_path: str = Field(
                default="",
                description="Image path for source='file', workspace-relative or absolute.",
            )
            files: list[str] = Field(
                default_factory=list,
                description=("Up to 8 pictures in one look: chat files by name, workspace paths, 'browser', "
                             "rec-…, edit-… (a video gives frames)."),
            )
            contact_sheet: bool = Field(
                default=False,
                description="Tile files (up to 48 pictures or frames) into one labelled picture.",
            )
            frames: int = Field(default=0, description="Frames per video (default 4; 12 on a sheet).")

        return [
            StructuredTool.from_function(
                func=_analyze_image,
                name="analyze_image",
                description=(
                    "Look at an image and answer a visual question. "
                    "source='browser' looks at the page open in Row-Bot's "
                    "browser: use it for any screenshot of a web page. "
                    "source='file' with file_path reads a workspace image. "
                    "source='screen' captures the person's whole desktop and "
                    "source='camera' their webcam: use those only when they "
                    "ask about their screen or something in front of the "
                    "camera; each asks the person first. files looks at several "
                    "pictures or videos at once."
                ),
                args_schema=_AnalyzeInput,
            ),
        ]

    def execute(self, query: str) -> str:
        return _analyze_image(query, source="camera", file_path="")


registry.register(VisionTool())
