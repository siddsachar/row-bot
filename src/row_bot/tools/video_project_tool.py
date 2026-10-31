"""The ``video`` tool (5.3.1 B3): a video project of scenes, shared with the Video panel.

Row-Bot writes the scenes (which piece of which video, its speed, zoom, card and line), makes
the video, reads its checks and changes it on request; the person edits the same project in
the Video panel beside the chat. Every change is a new version with a note of what changed, so
undo is one step. Making the video voices the lines in the person's chosen voice, renders it
with the music ducked under the voice, and saves it to the chat's files.

Approvals: downloading ffmpeg the first time, as in edit_video. Reading the chat's files and
the workspace, and writing new files to the chat, needs none.
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field

INLINE_SECONDS = 15


class _Scene(BaseModel):
    scene_id: str = Field(default="", description="Keep a scene's id when changing it (new scenes: leave empty).")
    source: str = Field(default="", description="Its video: a recording (rec-…), an edit (edit-…), a file in this "
                                                "chat or a workspace path. Empty: a title card (its card on the "
                                                "brand colour for end seconds, 3 by default), so a title, an end "
                                                "card or a whole video of cards needs no made-up background video.")
    start: float = Field(default=0.0, description="Seconds into that video.")
    end: float | None = Field(default=None, description="Seconds into that video (empty: to its end).")
    speed: float = Field(default=1.0, description="2 = twice as fast (shows a 2× badge), 0.5 = half.")
    zoom: list[float] | None = Field(default=None, description="Leave empty: on a recording the camera follows "
                                                             "what was done. [x, y, width, height], 0-1: hold there.")
    card: str = Field(default="", description="A title laid over its first 3 seconds.")
    line: str = Field(default="", description="The words said over it (voiced when the video is made).")
    voice: str = Field(default="", description="A voice file made with speak for this line (empty: voiced for you).")


class _VideoInput(BaseModel):
    action: Literal["create", "change", "make", "status", "undo", "export"] = "status"
    project: str = Field(default="", description="change, make, status, undo: the project (vid-…).")
    title: str = Field(default="", description="create: the video's name.")
    recording: str = Field(default="", description="create: make the scenes from a browser recording's beats "
                                                   "(rec-…), each with its title and line: live as it is, work "
                                                   "sped up, cut left out.")
    scenes: list[_Scene] | None = Field(default=None, description="create, change: every scene, in order.")
    music: str | None = Field(default=None, description="A music file (make_music) under the whole video; '' none.")
    music_volume: float | None = Field(default=None, description="0-1, default 0.3 (it dips under the voice).")
    narration: str | None = Field(default=None, description="One voice file (speak a whole script) said over the "
                                                           "whole video instead of each scene's line; '' none.")
    captions: bool | None = Field(default=None, description="Show each line as a caption under its scene.")
    voice_lines: bool | None = Field(default=None, description="Say the lines (default true).")
    aspect: Literal["", "16:9", "1:1", "9:16"] | None = Field(
        default=None, description="create, change: the video's shape ('' keeps its first video's).")
    brand: bool | None = Field(default=None, description="create, change: use the brand kit (default true): "
                                                          "its colour, captions, logo, intro and outro.")
    voice: str | None = Field(default=None, description="create, change: this video's voice for its lines "
                                                        "('' the brand kit's).")
    voice_speed: float | None = Field(default=None, description="create, change: its speed, 0.9-1.25 (0 the kit's).")
    shape: Literal["16:9", "1:1", "9:16", "gif"] | None = Field(
        default=None, description="export: the same version in this shape (X, YouTube 16:9; Shorts, Reels 9:16), "
                                  "each scene reframed on its zoom, or a GIF.")
    note: str = Field(default="", description="create, change: what you changed, in a few words.")
    version: int | None = Field(default=None, description="make, status: a version (default the latest).")


def _conversation() -> str:
    from row_bot.tool_outputs import current_thread_id

    return str(current_thread_id() or "")


def _wait_for(project_id: str, conversation: str, number: int, seconds: float) -> None:
    """Wait a little for the video while the turn waits; it carries on in the background after."""
    from row_bot import video_edits, video_projects

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        project = video_projects.get(project_id, conversation)
        version = project.version(number) if project else None
        if version is None or version.status == "failed":
            return
        if version.edit_id:
            edit = video_edits.get(version.edit_id)
            if edit is not None:
                video_edits.wait(edit, max(0.0, deadline - time.monotonic()))
            return
        time.sleep(0.2)


def _make(project, number: int | None) -> str:
    from row_bot import video_projects
    from row_bot.tools.approval_gate import with_approval
    from row_bot.tools.video_editing_tool import ffmpeg_consent

    refusal, line, download = ffmpeg_consent("edit", "video")
    if refusal:
        return refusal
    target = project.version(number) if number else project.latest
    if target is not None and video_projects.needs_voice(target):
        from row_bot.tools.sound_tool import voice_consent

        refusal, voiced = voice_consent("video", "Narrating this video")
        if refusal:
            return refusal
        line = "\n".join(part for part in (line, voiced) if part)
    version = video_projects.make(project, number, download=download)
    _wait_for(project.project_id, project.conversation_id, version.number, INLINE_SECONDS)
    fresh = video_projects.get(project.project_id, project.conversation_id) or project
    text = video_projects.summary(fresh, version.number)
    return with_approval(line, text) if line else text


def _export(project, shape: str, number: int | None) -> str:
    from row_bot import video_projects
    from row_bot.tools.approval_gate import with_approval
    from row_bot.tools.video_editing_tool import ffmpeg_consent

    refusal, line, download = ffmpeg_consent("edit", "video")
    if refusal:
        return refusal
    version = video_projects.export(project, shape, number, download=download)
    fresh = project
    for _ in range(int(INLINE_SECONDS * 5)):  # Until its edit has started (it's made in the background).
        fresh = video_projects.get(project.project_id, project.conversation_id) or project
        if shape in (fresh.version(version.number) or version).exports:
            break
        time.sleep(0.2)
    text = (f"Exporting version {version.number} as {shape}: it's saved to this chat when it's made, and shows in the "
            "Video panel. " + video_projects.summary(fresh, version.number))
    return with_approval(line, text) if line else text


def _video(action: str = "status", project: str = "", title: str = "", scenes: list | None = None,
           recording: str = "",
           music: str | None = None, music_volume: float | None = None, narration: str | None = None,
           captions: bool | None = None, voice_lines: bool | None = None, note: str = "", version: int | None = None, aspect: str | None = None,
           shape: str | None = None, brand: bool | None = None, voice: str | None = None,
           voice_speed: float | None = None) -> str:
    from row_bot import video_projects
    from row_bot.video_projects import ProjectError

    conversation = _conversation()
    if not conversation:
        return "Video projects need a conversation."
    raw = [item.model_dump() if hasattr(item, "model_dump") else dict(item) for item in scenes or []]
    try:
        if action == "create":
            if recording and not raw:
                raw = video_projects.scenes_from_beats(recording.strip(), conversation)
            found = video_projects.create(conversation, title, raw, music=music or "",
                                          music_volume=0.3 if music_volume is None else music_volume,
                                          narration=narration or "",
                                          captions=bool(captions), voice_lines=voice_lines is not False,
                                          aspect=aspect or "", brand=brand is not False, by="row-bot",
                                          note=note, voice=voice or "", voice_speed=voice_speed)
            return _make(found, None)
        found = video_projects.get(project, conversation)
        if found is None:
            return f"{project or 'That'} isn't a video project in this chat: use action create."
        if action == "change":
            changed = video_projects.change(found, by="row-bot", note=note, scenes=raw if scenes is not None else None,
                                            title=title or None, music=music, music_volume=music_volume, narration=narration,
                                            captions=captions, voice_lines=voice_lines, aspect=aspect,
                                            brand=brand, voice=voice, voice_speed=voice_speed)
            return _make(found, changed.number)
        if action == "undo":
            if len(found.versions) < 2:
                return "There's no earlier version to go back to."
            back = video_projects.restore(found, found.versions[-2].number, by="row-bot")
            return video_projects.summary(found, back.number)
        if action == "make":
            return _make(found, version)
        if action == "export":
            return _export(found, shape or "", version)
        if action == "status":
            number = version or found.latest.number
            _wait_for(found.project_id, conversation, number, 60)
            fresh = video_projects.get(found.project_id, conversation) or found
            return video_projects.summary(fresh, number)
    except ProjectError as error:
        return str(error)
    return "Use action create, change, make, status, undo or export."


def langchain_tool():
    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(
        func=_video,
        name="video",
        description=(
            "Make a video as scenes the person also edits in the Video panel: each scene a piece of a video "
            "(start, end), with speed, zoom, a card and its line (voiced when made). create, then change with "
            "every scene (keep scene ids) and a note; each change is a new version and makes the video again, "
            "saved to this chat. Read the checks in the result: never say it's done until they pass. undo goes "
            "back one version; export makes the same version in another shape or a GIF. For one quick edit use "
            "edit_video. A title or end card is a scene without a video: never make background videos or other "
            "working files in the person's workspace for it."
        ),
        args_schema=_VideoInput,
    )
