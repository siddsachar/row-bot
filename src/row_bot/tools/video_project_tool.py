"""The ``video`` tool (5.3.1 B3): a video project of scenes, shared with the Video panel.

Row-Bot writes the scenes (which piece of which video, its speed, zoom, card and line), makes
the video, reads its checks and changes it on request; the person edits the same project in
the Video panel beside the chat. A new video of several scenes is a storyboard first (C1):
create makes nothing, the person approves or comments on its scenes in the panel, and make
renders it; a video of one scene, and a change to a made video, are made straight away. Every
change is a new version with a note of what changed, so undo is one step. Making the video
voices the lines in the person's chosen voice, renders it with the music ducked under the voice,
and saves it to the chat's files. The call waits for it (``MAKE_SECONDS``); a video that takes
longer carries on, and a wait wakes the chat when it's made.

Approvals: downloading ffmpeg the first time, as in edit_video. Reading the chat's files and
the workspace, and writing new files to the chat, needs none. Footage elsewhere on this computer
is asked about by the approval level when it joins a video; that project then reads that exact
file in place, and any other file elsewhere asks again.
"""

from __future__ import annotations

import threading
import time
from typing import Literal

from pydantic import BaseModel, Field

# How long make, change and export wait for the video, as long as run_command waits for a command
# (background_processes.INLINE_SECONDS): most short videos are made by then. A longer one carries on, and a wait
# starts a turn here once it's made, so nothing polls.
MAKE_SECONDS = 120.0


class _Scene(BaseModel):
    scene_id: str = Field(default="", description="Keep a scene's id when changing it (new scenes: leave empty).")
    source: str = Field(default="", description="Its footage, a video or a picture: a recording (rec-…), an edit "
                                                "(edit-…), a file in this chat, a workspace path, or a full path "
                                                "on this computer (read where it is, never copied; asks first). "
                                                "A picture is held with a slow push in. Empty: a title card (its "
                                                "card on the brand colour for `end` seconds, 3 by default), so a "
                                                "title, an end card or a whole video of cards needs no made-up "
                                                "background video.")
    start: float = Field(default=0.0, description="Seconds into that video.")
    end: float | None = Field(default=None, description="Seconds into that video (empty: to its end). A picture: "
                                                        "how long it shows (empty: its line's length, else 3.5).")
    speed: float = Field(default=1.0, description="2 = twice as fast (shows a 2× badge), 0.5 = half.")
    zoom: list[float] | None = Field(default=None, description="Leave empty: on a recording the camera follows "
                                                             "what was done. [x, y, width, height], 0-1: hold there "
                                                             "(on a picture: push in to there).")
    card: str = Field(default="", description="A title laid over its first 3 seconds.")
    line: str = Field(default="", description="The words said over it (voiced when the video is made).")
    voice: str = Field(default="", description="A voice file made with speak for this line (empty: voiced for you).")
    fit: bool | None = Field(default=None, description="true: its whole picture inset on the brand colour; false: "
                                                       "it fills the frame, cropped around the action; leave it out "
                                                       "for the brand's framing (full by default).")
    footage: Literal["clip", "picture", "ai", "card", "screen"] | None = Field(
        default=None, description="Its footage (from its source when left out). No source: footage still to make.")
    shows: str | None = Field(default=None, description="What it shows, in a few words.")
    frame: str | None = Field(default=None, description="A picture you made for its card (empty: a frame of it).")
    cue: str | None = Field(default=None, description="Its music cue.")
    speaker: str | None = Field(default=None, description="Who says its line: a character (in its own voice); "
                                                          "empty: the narrator.")
    effects: list[str] | None = Field(default=None, description="whoosh, hit, click (as it starts); rain, wind, "
                                                                "crowd, waves, footsteps (under it); or a sound file.")


class _VideoInput(BaseModel):
    action: Literal["create", "change", "make", "status", "undo", "export", "brand"] = "status"
    project: str = Field(default="", description="change, make, status, undo: the project (vid-…).")
    title: str = Field(default="", description="create: the video's name.")
    logline: str | None = Field(default=None, description="create, change: what the video is, in one line.")
    recording: str = Field(default="", description="create: make the scenes from a browser recording's beats "
                                                   "(rec-…), each with its title and line: live as it is, work "
                                                   "sped up, cut left out.")
    scenes: list[_Scene] | None = Field(default=None, description="create, change: every scene, in order.")
    music: str | None = Field(default=None, description="Music under the video: 'fit' (made to its length, "
                                                         "changing with the music cues), a music file, or '' none.")
    music_volume: float | None = Field(default=None, description="0-1, default 0.3 (it dips under the voice).")
    narration: str | None = Field(default=None, description="One voice file (speak a whole script) said over the "
                                                           "whole video instead of each scene's line; '' none.")
    captions: bool | None = Field(default=None, description="Caption what's said, word by word (a short has them "
                                                             "unless false).")
    voice_lines: bool | None = Field(default=None, description="Say the lines (default true).")
    aspect: Literal["", "16:9", "1:1", "9:16"] | None = Field(
        default=None, description="create, change: the video's shape ('' keeps its first video's).")
    brand: bool | None = Field(default=None, description="create, change: use the brand kit (default true): "
                                                          "its colour, captions, logo, intro and outro.")
    voice: str | None = Field(default=None, description="create, change: this video's voice for its lines "
                                                        "('' the brand kit's).")
    voice_speed: float | None = Field(default=None, description="create, change: its speed, 0.9-1.25 (0 the kit's).")
    cast: dict[str, str] | None = Field(default=None, description="create, change: a character's voice the person "
                                                                  'chose, e.g. {"Maya": "bf_emma"}; a saved character '
                                                                  "keeps it in every video.")
    on_beat: bool | None = Field(default=None, description="create, change: cut scenes on the music's beat.")
    recipe: Literal["", "film", "youtube", "vlog", "montage", "explainer", "demo", "short"] | None = Field(
        default=None, description="create, change: the kind of video; it sets the look below and the caption style "
                                  "(a short is 9:16 with big word captions).")
    grade: Literal["", "natural", "warm", "cinematic", "vivid", "bw"] | None = Field(
        default=None, description="create, change: the colour grade ('' the recipe's). Clips and AI shots are "
                                  "matched to one look first.")
    grain: bool | None = Field(default=None, description="create, change: film grain.")
    letterbox: bool | None = Field(default=None, description="create, change: 2.39 letterbox bars (16:9).")
    transitions: Literal["", "cut", "crossfade"] | None = Field(
        default=None, description="create, change: between scenes ('' the recipe's: crossfades for a montage, "
                                  "else cuts).")
    shape: Literal["16:9", "1:1", "9:16", "gif"] | None = Field(
        default=None, description="export: the same version in this shape (X, YouTube 16:9; Shorts, Reels 9:16), "
                                  "each scene reframed on its zoom, or a GIF.")
    framing: Literal["full", "framed"] | None = Field(
        default=None, description="brand: footage filling the frame (full, default) or inset on the brand colour "
                                  "(framed, only when asked).")
    caption_style: Literal["", "box", "clean", "bold", "title", "word"] | None = Field(
        default=None, description="How captions look (word: a short's big words, one lit as it's said). brand: "
                                  "every video's; create, change: this video's ('' the recipe's or the kit's).")
    font: Literal["sans", "modern", "serif", "display"] | None = Field(
        default=None, description="brand: the typeface of every title and caption.")
    title_style: Literal["studio", "minimal", "bold"] | None = Field(
        default=None, description="brand: titles, lower thirds and the end card: on a card of the brand colour, "
                                  "white words (title cards on black), or large on solid colour.")
    music_mood: Literal["calm", "upbeat", "focused", "warm"] | None = Field(
        default=None, description="create, change: the mood of 'fit' music (default the kit's); brand: the mood of "
                                  "music made for a video.")
    loudness: float | None = Field(default=None, description="brand: loudness in LUFS (-14 social, default; -16 "
                                                             "podcasts; -23 broadcast).")
    note: str = Field(default="", description="create, change: what you changed, in a few words.")
    version: int | None = Field(default=None, description="make, status: a version (default the latest).")


def _conversation() -> str:
    from row_bot.tool_outputs import current_thread_id

    return str(current_thread_id() or "")


def _workflow_run(conversation: str) -> bool:
    """True while a workflow runs in this chat (F31): its message is the person's instruction, so a video is made."""
    from row_bot import tasks

    return tasks.active_run_for_conversation(conversation) is not None


def _wait_for(project_id: str, conversation: str, number: int, seconds: float, shape: str = "") -> bool:
    """Wait for a version's video (or its export in *shape*) while the turn waits; the turn's Stop stops it. True
    once it's made, failed or stopped, False while it's still being made."""
    from row_bot import video_edits, video_projects
    from row_bot.cancellation import current_cancellation_scope

    stopped = threading.Event()
    scope = current_cancellation_scope()
    unregister = scope.register(stopped.set, "video.stop") if scope is not None else None
    deadline = time.monotonic() + seconds
    try:
        while True:
            project = video_projects.get(project_id, conversation)
            version = project.version(number) if project else None
            if version is None:
                return True
            edit, done = video_projects.render_of(version, shape)
            if stopped.is_set() and edit is not None:
                video_edits.stop(edit.edit_id, by="you")
            left = deadline - time.monotonic()
            if done or stopped.is_set():
                return True
            if left <= 0:
                return False
            # Before its edit starts (voicing, or an export setting out) there's nothing to wait on but the clock.
            (edit.done if edit is not None else stopped).wait(min(left, 0.5 if edit is not None else 0.2))
    finally:
        if unregister is not None:
            unregister()


def _check_back(project, number: int, shape: str = "") -> str:
    """A video still being made: a wait that starts a turn here once it's made (one per video), and about how long
    that will be when its edit says how far it is."""
    from row_bot import video_edits, video_projects, waits

    spec = {"project_id": project.project_id, "number": number, "shape": shape}
    waiting = waits.waiting(project.conversation_id)
    if not any(record["kind"] == "video" and record["spec"] == spec for record in waiting):
        try:
            waits.create(project.conversation_id, "video", f"“{project.title}” version {number}"
                         f"{' in ' + shape if shape else ''} is made", spec=spec, hours=6)
        except waits.WaitError as error:
            return (f"Still being made in the background, and Row-Bot can't check back for it: {error} Tell the "
                    "person it's still being made; look with action status in a later turn, or when they ask.")
    fresh = video_projects.get(project.project_id, project.conversation_id) or project
    version = fresh.version(number)
    edit = video_projects.render_of(version, shape)[0] if version is not None else None
    fraction = video_edits.progress(edit)[0] if edit is not None else None
    minutes = max(1, round(edit.elapsed() * (1 - fraction) / fraction / 60)) if fraction and fraction >= 0.05 else 0
    left = f", about {minutes} more minute{'s' if minutes != 1 else ''}" if minutes else ""
    return (f"Still being made in the background{left}. Row-Bot checks back here when it's made, with its status "
            "and checks. Don't call status meanwhile: tell the person in a line, finish anything else they asked "
            "for, and end the turn.")


def _both_downloads_consent() -> tuple[str, str] | None:
    """(refusal, approval line) for a narrated video that needs ffmpeg and Row-Bot's voice: one card for both,
    asked once before making it, not two in a row (U25). None when they aren't both missing."""
    from row_bot import ffmpeg_runtime, tts
    from row_bot.tools.approval_gate import approval_check

    if ffmpeg_runtime.find() is not None or tts.TTSService().is_installed():
        return None
    wheel = ffmpeg_runtime.current_wheel()
    if wheel is None:
        return None
    voice = f"about {round(tts.VOICE_DOWNLOAD_BYTES / 1_000_000)} MB"
    request = ffmpeg_runtime.consent_request(
        "edit", "video", wheel=wheel,
        also=(f"It also downloads Row-Bot's voice (Kokoro and its voices, {voice}) from {tts.VOICE_DOWNLOAD_HOST}, "
              "checked against the fingerprints saved in Row-Bot, to read the lines aloud on this computer."))
    request["label"] = "Download ffmpeg and Row-Bot's voice"
    request["question"] = (f"Making this video needs ffmpeg (about {ffmpeg_runtime.size_words(wheel.size)}) and "
                           f"Row-Bot's voice (Kokoro, {voice}). Download both now?")
    request["args"] = {**request["args"], "voice": f"Kokoro voice ({voice}) from {tts.VOICE_DOWNLOAD_HOST}"}
    refusal, line = approval_check(
        request,
        risks=(*ffmpeg_runtime.consent_risks(wheel),
               f"downloads a voice model ({voice}) from GitHub and keeps it in Row-Bot's data folder"),
        blocked_message=("BLOCKED: making this video needs ffmpeg and Row-Bot's voice, and downloading them needs "
                         "approval, but this chat is on Block. Install the voice in Settings › Voice, or change "
                         "the approval level."),
        cancelled_message="The person didn't allow the downloads; nothing was downloaded or made.",
    )
    if refusal:
        return refusal, ""
    try:
        tts.TTSService().download_model()
    except Exception as error:  # A network or fingerprint problem: said in words, nothing kept.
        return f"Row-Bot's voice couldn't be downloaded: {error}", ""
    return "", line


def _make(project, number: int | None, approved: list[str] | None = None) -> str:
    """Make a version's video and wait for it; the result leads with every approval given for it (*approved*:
    reading footage outside the workspace, so the chat's step shows who allowed it)."""
    from row_bot import video_projects
    from row_bot.tools.approval_gate import with_approval
    from row_bot.tools.video_editing_tool import ffmpeg_consent

    target = project.version(number) if number else project.latest
    if target is not None and video_projects.planned(target):  # Its footage first: nothing to download yet.
        return video_projects.planned_words(video_projects.planned(target))
    narrated = target is not None and video_projects.needs_voice(target)
    both = _both_downloads_consent() if narrated else None
    if both is not None:
        refusal, line = both
        if refusal:
            return refusal
        download = True
    else:
        refusal, line, download = ffmpeg_consent("edit", "video")
        if refusal:
            return refusal
        if narrated:
            from row_bot.tools.sound_tool import voice_consent

            refusal, voiced = voice_consent("video", "Narrating this video")
            if refusal:
                return refusal
            line = "\n".join(part for part in (line, voiced) if part)
    version = video_projects.make(project, number, download=download)
    made = _wait_for(project.project_id, project.conversation_id, version.number, MAKE_SECONDS)
    fresh = video_projects.get(project.project_id, project.conversation_id) or project
    text = video_projects.summary(fresh, version.number)
    if not made:
        text += "\n" + _check_back(fresh, version.number)
    line = "\n".join(part for part in (*(approved or []), line) if part)
    return with_approval(line, text) if line else text


def _with(approved: list[str], text: str) -> str:
    """A result that leads with the approvals given for it (reading footage outside the workspace)."""
    from row_bot.tools.approval_gate import with_approval

    return with_approval("\n".join(approved), text) if approved else text


def _export(project, shape: str, number: int | None) -> str:
    from row_bot import video_projects
    from row_bot.tools.approval_gate import with_approval
    from row_bot.tools.video_editing_tool import ffmpeg_consent

    refusal, line, download = ffmpeg_consent("edit", "video")
    if refusal:
        return refusal
    version = video_projects.export(project, shape, number, download=download)
    made = _wait_for(project.project_id, project.conversation_id, version.number, MAKE_SECONDS, shape)
    fresh = video_projects.get(project.project_id, project.conversation_id) or project
    text = video_projects.summary(fresh, version.number)
    if not made:
        text += "\n" + _check_back(fresh, version.number, shape)
    return with_approval(line, text) if line else text


def _video(action: str = "status", project: str = "", title: str = "", scenes: list | None = None,
           recording: str = "",
           music: str | None = None, music_volume: float | None = None, narration: str | None = None,
           captions: bool | None = None, voice_lines: bool | None = None, note: str = "", version: int | None = None, aspect: str | None = None,
           shape: str | None = None, brand: bool | None = None, voice: str | None = None,
           voice_speed: float | None = None, framing: str | None = None, caption_style: str | None = None,
           music_mood: str | None = None, logline: str | None = None, cast: dict | None = None,
           on_beat: bool | None = None, loudness: float | None = None, recipe: str | None = None,
           grade: str | None = None, grain: bool | None = None, letterbox: bool | None = None,
           transitions: str | None = None, font: str | None = None, title_style: str | None = None) -> str:
    from row_bot import video_projects
    from row_bot.video_projects import ProjectError

    if action == "brand":
        return _brand(framing=framing, captions=caption_style or None, music_mood=music_mood, voice=voice,
                      voice_speed=voice_speed, loudness=loudness, font=font, titles=title_style)
    look = {"recipe": recipe, "grade": grade, "grain": grain, "letterbox": letterbox, "transitions": transitions,
            "caption_style": caption_style}
    conversation = _conversation()
    if not conversation:
        return "Video projects need a conversation."
    # A storyboard field left out keeps the scene's own.
    raw = [{key: value for key, value in (item.model_dump() if hasattr(item, "model_dump") else dict(item)).items()
            if value is not None or key in ("end", "zoom", "fit")} for item in scenes or []]
    try:
        if action == "create":
            if recording and not raw:
                raw = video_projects.scenes_from_beats(recording.strip(), conversation)
            found = video_projects.create(conversation, title, raw, music=music or "",
                                          music_volume=0.3 if music_volume is None else music_volume,
                                          narration=narration or "",
                                          captions=captions, voice_lines=voice_lines is not False,
                                          aspect=aspect or "", brand=brand is not False, by="row-bot",
                                          note=note, voice=voice or "", voice_speed=voice_speed,
                                          logline=logline or "", approvals=(approved := []), cast=cast,
                                          music_mood=music_mood or "", on_beat=bool(on_beat), kept=(kept := []),
                                          look=look)
            # A new video of several scenes: its storyboard first (G6), but a workflow's run has nobody to approve it.
            if len(found.latest.scenes) > 1 and not _workflow_run(conversation):
                return _with(approved, video_projects.summary(found) + _kept(kept))
            return _make(found, None, approved) + _kept(kept)
        found = video_projects.get(project, conversation)
        if found is None:
            return f"{project or 'That'} isn't a video project in this chat: use action create."
        if action == "change":
            storyboard = video_projects.storyboard(found.latest)
            changed = video_projects.change(found, by="row-bot", note=note, scenes=raw if scenes is not None else None,
                                            title=title or None, music=music, music_volume=music_volume, narration=narration,
                                            captions=captions, voice_lines=voice_lines, aspect=aspect,
                                            brand=brand, voice=voice, voice_speed=voice_speed, logline=logline,
                                            approvals=(approved := []), cast=cast, music_mood=music_mood,
                                            on_beat=on_beat, kept=(kept := []), look=look)
            if storyboard:  # A storyboard stays one until it's made.
                return _with(approved, video_projects.summary(found, changed.number) + _kept(kept))
            return _make(found, changed.number, approved) + _kept(kept)
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
            text = video_projects.summary(found, number)
            shown = found.version(number)
            # Whatever of it is still being made checks back by itself.
            making = [shape for shape in ("", *video_projects.EXPORTS) if shown is not None
                      and (not shape or shape in shown.exports) and not video_projects.render_of(shown, shape)[1]]
            said = [_check_back(found, number, shape) for shape in making]
            return text + ("\n" + said[-1] if said else "")
    except ProjectError as error:
        return str(error)
    return "Use action create, change, make, status, undo, export or brand."


def _kept(names: list[str]) -> str:
    """The result's line for voices kept with saved characters ("" none)."""
    return (f"\nKept each voice with {', '.join(names)} in Library › Media: their lines in every video use it."
            if names else "")


def _brand(**changes: object) -> str:
    """Save how every video should look, as the person said it (R3): the kit the Video settings show."""
    from dataclasses import asdict

    from row_bot import video_brand

    try:
        kit = video_brand.update(changes)
    except video_brand.BrandError as error:
        return str(error)
    shown = {key: value for key, value in asdict(kit).items() if key in {"framing", "captions", "music_mood",
                                                                      "voice", "voice_speed", "loudness", "font",
                                                                      "titles"}}
    return ("Saved in the brand kit (the Video panel’s Brand kit); every new video starts this way: "
            + ", ".join(f"{key} {value or 'default'}" for key, value in shown.items()) + ".")


def langchain_tool():
    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(
        func=_video,
        name="video",
        description=(
            "Make a video as scenes the person also edits in the Video panel: each scene a piece of a video "
            "(start, end) or a picture, with speed, zoom, a card and its line (voiced when made). create with a "
            "logline and every scene's footage type, shows and line: a video of several scenes is a storyboard "
            "first, nothing made, which the person approves or comments on; make it when they approve, or at once when "
            "they said to make the video. A video of "
            "one scene is made at once. change with every scene (keep scene ids) and a note: each change is a new "
            "version, made again once the video was made. make, change and "
            "export wait for the video; a longer one checks back here when it's made: never poll status. Each made "
            "video comes with its checks, by scene and time: fix what they list and make it again; it's ready to "
            "share only when they pass (no analyze_image passes of your own). undo goes back one version; export "
            "makes the same version in another shape or a GIF; brand saves how every video should look (framing, "
            "caption style, font, title style, music mood, voice, loudness). For one quick edit use edit_video. A "
            "title or end card is a "
            "scene without a video: never make background videos or other working files in the person's workspace "
            "for it."
        ),
        args_schema=_VideoInput,
    )
