"""Video projects (5.3.1 B3): a video as scenes, versioned, shared by the Video panel and Row-Bot.

A project is its scenes in order. Each scene's footage is a piece of a video from its start to its
end (a recording, a video edit, a clip in the chat's files, the workspace or anywhere on this
computer, an AI shot) or a picture held with a slow push in (a photo, a screenshot, generated
art), with its speed, a zoom on a region, a card laid over its first seconds, and its line: the
words said over it, voiced in the person's chosen voice (or a voice file made with ``speak``) and, with captions on,
shown under it. Music (``make_music``, the person's own file, or music made to fit the cut: as long as the video,
its sections changing where the music cue does) plays under the whole video and dips while anyone speaks; a
montage's cuts can land on its beat. A scene's line may be a character's (``speaker``), said in that character's
voice (``Version.cast``, kept with the character in Library › Media), and a scene may carry effects and ambience
(``video_sound``) (C6).

A version is made in one shape (16:9, 1:1 or 9:16; by default its first video's), and the same
version can be exported in the other shapes, each scene reframed on its zoom, or as a GIF. Every
scene fills the frame: a video of another shape is cropped around where the action is, never shown
with bars. A scene's ``fit`` (or the brand kit's framed look) sets its picture inset instead.

A new video of several scenes starts as a storyboard (C1): a logline, then each scene's footage type
(a clip, a picture, an AI shot, a card or a screen recording), what it shows, its frame, length, line and
music cue, with nothing made. A scene can be planned before its footage exists (an AI shot still to make);
the person approves, changes or comments on each scene, and making it renders the storyboard. Making a
version draws again only the scenes whose inputs changed: each scene's drawn footage is kept, keyed by a
hash of what it was drawn from (``video_studio.prepare``), and an unchanged scene is reused.

Every change is a new version, by the person or by Row-Bot with a note of what changed. Going
back is a new version too, so nothing is lost; approving a scene marks it on its version and makes
none. Making a version's video voices each line whose voice is missing or says other words, then
renders the whole version as one video edit that also gives it the finished check (``video_check``)
for the review card. The video is saved to the conversation's files like any edit.

Footage is used where it is: a workspace file, or a file elsewhere on this computer once the
person allows reading it by the approval level, is read in place when the video is made, never
copied into the chat, however large or long. The person is asked when such a file joins the
video; the project remembers that exact file and who allowed it (``Project.outside``), for this
project only, and any other file elsewhere asks again. Footage moved or deleted later is named by its scene.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
import wave
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

PROJECT_ID = re.compile(r"vid-[0-9a-f]{8}")
SCENE_ID = re.compile(r"s[0-9a-f]{6}")
By = Literal["you", "row-bot"]
MAX_SCENES = 200
# Different videos and pictures one video uses (each is read in place, one at a time, into one clip).
MAX_SOURCES = 100
MAX_VERSIONS = 50
MAX_TITLE = 80
MAX_CARD = 120
MAX_LINE = 300
MAX_NOTE = 200
MAX_SHOWS = 200
MAX_CUE = 80
MAX_LOGLINE = 160
MAX_SPEAKER = 40
MAX_EFFECTS = 4
# Music made to fit a version's cut, instead of a music file.
FIT = "fit"
# A scene's footage: the person's own clip, a picture, an AI shot, a title card or a screen recording.
FOOTAGE = ("clip", "picture", "ai", "card", "screen")
FOOTAGE_WORDS = {"clip": "clip", "picture": "picture", "ai": "AI shot", "card": "title card",
                 "screen": "screen recording"}
SPEEDS = (0.25, 4.0)
SHAPES = ("16:9", "1:1", "9:16")
EXPORTS = SHAPES + ("gif",)
_FOLDER = "video-projects"
_LOCK = threading.RLock()


class ProjectError(ValueError):
    """The change can't be made as asked; the message says why in plain words."""

    def __init__(self, message: str, *, stale: bool = False) -> None:
        super().__init__(message)
        self.stale = stale


@dataclass
class Scene:
    scene_id: str
    source: str
    start: float = 0.0
    end: float | None = None
    speed: float = 1.0
    zoom: list[float] | None = None
    card: str = ""
    line: str = ""
    # The voice file said over it (a file in the chat's files) and the words that file says.
    voice: str = ""
    voiced: str = ""
    # Who said it: the voice and speed Row-Bot voiced it in ("af_heart@1"), or "given" for a file made for it.
    voiced_as: str = ""
    # True: its whole picture inset on the brand colour; False: it fills the frame, cropped around the action; None:
    # the brand kit's framing (full by default).
    fit: bool | None = None
    # The storyboard (C1): its footage type (``FOOTAGE``), what it shows, a still for its card (a picture; empty:
    # a frame of its footage), its music cue, and whether the person approved it as it is.
    footage: str = ""
    shows: str = ""
    frame: str = ""
    cue: str = ""
    approved: bool = False
    # Its sound (C6): who says its line (a character, in its own voice; "" the narrator), and its effects and
    # ambience (``video_sound.EFFECTS``, or sound files in the chat by name).
    speaker: str = ""
    effects: list[str] = field(default_factory=list)


@dataclass
class Version:
    number: int
    at: float
    by: str
    note: str
    scenes: list[Scene]
    music: str = ""
    music_volume: float = 0.3
    # One voice file said over the whole video from its start (speak a script); its scenes' lines then
    # aren't said one by one.
    narration: str = ""
    captions: bool = False
    voice_lines: bool = True
    # Its shape ("" keeps its first video's) and its other shapes or GIF, each an edit: shape -> edit id.
    aspect: str = ""
    exports: dict[str, str] = field(default_factory=dict)
    # Why an export in a shape didn't come out (shape -> words): it used to fail and say nothing (R5).
    export_problems: dict[str, str] = field(default_factory=dict)
    # Use the brand kit (its colour, captions, logo, intro and outro).
    brand: bool = True
    # This video's own voice and speed for its lines ("" and 0: the brand kit's).
    voice: str = ""
    voice_speed: float = 0.0
    # Making its video: "" (not yet), "voicing", "rendering" (edit_id follows the edit) or "failed".
    status: str = ""
    edit_id: str = ""
    problem: str = ""
    # The storyboard's one line: what the video is.
    logline: str = ""
    # Its sound (C6): the voice each character speaks in (name -> voice), the mood of music made to fit its cut
    # (``music`` "fit"; "" the brand kit's), and its cuts on the music's beat.
    cast: dict[str, str] = field(default_factory=dict)
    music_mood: str = ""
    on_beat: bool = False


@dataclass
class Project:
    project_id: str
    conversation_id: str
    title: str
    created_at: float
    versions: list[Version] = field(default_factory=list)
    # The files outside the workspace this project may read, each allowed when it joined: {"path": its full path,
    # "by": who allowed it, "person", "reviewer" (Auto), "hands_off", or the person's "rule" or "chat_grant"}.
    outside: list[dict[str, str]] = field(default_factory=list)

    @property
    def latest(self) -> Version:
        return self.versions[-1]

    def version(self, number: int | None) -> Version | None:
        if number is None:
            return self.latest
        return next((item for item in self.versions if item.number == number), None)


def projects_root() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir() / _FOLDER


def _path(conversation_id: str, project_id: str) -> Path:
    from row_bot.tool_outputs import thread_folder_name

    return projects_root() / thread_folder_name(conversation_id) / f"{project_id}.json"


def _save(project: Project) -> None:
    path = _path(project.conversation_id, project.project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.part")
    partial.write_text(json.dumps(asdict(project)), encoding="utf-8", newline="\n")
    os.replace(partial, path)


def _scene_from(data: dict[str, Any]) -> Scene:
    zoom = data.get("zoom")
    return Scene(
        scene_id=str(data["scene_id"]), source=str(data["source"]), start=float(data.get("start") or 0),
        end=None if data.get("end") is None else float(data["end"]), speed=float(data.get("speed") or 1),
        zoom=[float(value) for value in zoom][:4] if zoom else None, card=str(data.get("card") or ""),
        line=str(data.get("line") or ""), voice=str(data.get("voice") or ""), voiced=str(data.get("voiced") or ""),
        voiced_as=str(data.get("voiced_as") or ""), fit=None if data.get("fit") is None else bool(data["fit"]),
        footage=str(data.get("footage") or "") if data.get("footage") in FOOTAGE else "",
        shows=str(data.get("shows") or ""), frame=str(data.get("frame") or ""), cue=str(data.get("cue") or ""),
        approved=bool(data.get("approved")), speaker=str(data.get("speaker") or ""),
        effects=[str(item) for item in data.get("effects") or []][:MAX_EFFECTS],
    )


def _load(path: Path) -> Project | None:
    try:
        # Read under the lock saves take: on Windows a file open for reading refuses the save's replace.
        with _LOCK:
            data = json.loads(path.read_text(encoding="utf-8"))
        if not PROJECT_ID.fullmatch(str(data.get("project_id") or "")):
            return None
        versions = []
        for item in data.get("versions") or []:
            scenes = [_scene_from(scene) for scene in item.get("scenes") or []]
            versions.append(Version(
                number=int(item["number"]), at=float(item["at"]), by=str(item.get("by") or "row-bot"),
                note=str(item.get("note") or ""), scenes=scenes, music=str(item.get("music") or ""),
                music_volume=float(item.get("music_volume", 0.3)), narration=str(item.get("narration") or ""),
                captions=bool(item.get("captions")),
                voice_lines=bool(item.get("voice_lines", True)), status=str(item.get("status") or ""),
                aspect=str(item.get("aspect") or ""), brand=bool(item.get("brand", True)),
                voice=str(item.get("voice") or ""), voice_speed=float(item.get("voice_speed") or 0),
                exports={str(key): str(value) for key, value in (item.get("exports") or {}).items()
                         if key in EXPORTS},
                export_problems={str(key): str(value)[:300]
                                 for key, value in (item.get("export_problems") or {}).items() if key in EXPORTS},
                edit_id=str(item.get("edit_id") or ""), problem=str(item.get("problem") or ""),
                logline=str(item.get("logline") or ""),
                cast={str(key): str(value) for key, value in (item.get("cast") or {}).items()},
                music_mood=str(item.get("music_mood") or ""), on_beat=bool(item.get("on_beat")),
            ))
        if not versions:
            return None
        return Project(project_id=data["project_id"], conversation_id=str(data["conversation_id"]),
                       title=str(data.get("title") or "Video"), created_at=float(data["created_at"]),
                       versions=versions, outside=[{"path": str(item["path"]), "by": str(item.get("by") or "")}
                                                  for item in data.get("outside") or [] if isinstance(item, dict)])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def get(project_id: str, conversation_id: str | None = None) -> Project | None:
    """A project by id (in that conversation when one is named)."""
    if not PROJECT_ID.fullmatch(str(project_id or "")):
        return None
    root = projects_root()
    if conversation_id is not None:
        candidates = [_path(conversation_id, project_id)]
    else:
        candidates = list(root.glob(f"*/{project_id}.json")) if root.is_dir() else []
    for path in candidates:
        if path.is_file():
            project = _load(path)
            if project is not None and (conversation_id is None or project.conversation_id == conversation_id):
                return project
    return None


def list_projects(conversation_id: str) -> list[Project]:
    """A conversation's projects, the most recently changed first."""
    folder = _path(conversation_id, "x").parent
    found = [project for path in folder.glob("vid-*.json") if (project := _load(path)) is not None] \
        if folder.is_dir() else []
    return sorted(found, key=lambda project: -project.latest.at)


def delete_conversation(conversation_id: str) -> int:
    """Forget a deleted conversation's projects (their videos are its files and go with it)."""
    import shutil

    folder = _path(conversation_id, "x").parent
    if not folder.is_dir():
        return 0
    shutil.rmtree(folder, ignore_errors=True)
    return 1


# ── Changing a project ──────────────────────────────────────────────────────


def _text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _number(value: Any, name: str, number: int) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ProjectError(f"Scene {number}: {name} must be a number of seconds.") from None
    if result != result or result < 0:
        raise ProjectError(f"Scene {number}: {name} must be 0 or more.")
    return round(result, 3)


def _kinds(sources: dict[str, int], conversation_id: str, allowed: dict[str, str],
           approvals: list[str] | None = None) -> dict[str, str]:
    """Each source's kind ("video" or "image"), checked (source -> its first scene's number). Files outside the
    workspace that this project wasn't allowed to read yet are asked about by the approval level, all in one
    question; once allowed, those exact files join *allowed* (the project's own: path -> who allowed it), and the
    approval line joins *approvals* for the tool's result (the chat's step shows who approved)."""
    from row_bot.media_sources import SourceError, outside_path, resolve

    asking: list[Path] = []
    for value, number in sources.items():
        try:
            path = outside_path(value, conversation_id)
        except SourceError as error:
            raise ProjectError(f"Scene {number}: {error}") from None
        if path is not None and str(path) not in allowed and path not in asking:
            asking.append(path)
    if asking:
        from row_bot.tools.filesystem_tool import outside_workspace_refusal

        from row_bot.tools.approval_gate import decided_by

        refusal, line = outside_workspace_refusal("read_file", asking, tool="video")
        if refusal:
            raise ProjectError(refusal)
        allowed.update({str(path): decided_by() or "person" for path in asking})
        if line and approvals is not None:
            approvals.append(line)
    kinds: dict[str, str] = {}
    for value, number in sources.items():
        try:
            found = resolve(value, conversation_id, allowed=frozenset(Path(item) for item in allowed))
        except SourceError as error:
            raise ProjectError(f"Scene {number}: {error}") from None
        if len(found) != 1:
            raise ProjectError(f"Scene {number}: {value} is several pictures: name one of them.")
        kinds[value] = found[0].kind
    return kinds


def _clean_scenes(raw: list[Any], conversation_id: str, before: list[Scene], allowed: dict[str, str],
                  approvals: list[str] | None = None) -> list[Scene]:
    """The scenes as given, checked; a scene that keeps its id keeps its voice while its line is the same, and its
    approval while nothing in it changed. A storyboard field left out (``None``) keeps the scene's own. A file
    outside the workspace the person allows reading joins *allowed*."""
    if not raw:
        raise ProjectError("A video needs at least one scene.")
    if len(raw) > MAX_SCENES:
        raise ProjectError(f"At most {MAX_SCENES} scenes fit in one video: join some.")
    from row_bot.video_editing import CARD_SECONDS
    from row_bot.video_sound import effect_words

    named: dict[str, int] = {}
    for number, item in enumerate(raw, start=1):
        data = item if isinstance(item, dict) else dict(item)
        for value in (_text(data.get("source"), 512), _text(data.get("frame"), 512)):
            if value:
                named.setdefault(value, number)
    if len(named) > MAX_SOURCES:
        raise ProjectError(f"A video can use at most {MAX_SOURCES} different videos and pictures: join some first.")
    kinds = _kinds(named, conversation_id, allowed, approvals)
    known = {scene.scene_id: scene for scene in before}
    scenes: list[Scene] = []
    for number, item in enumerate(raw, start=1):
        data = item if isinstance(item, dict) else dict(item)
        scene_id = str(data.get("scene_id") or "")
        if not SCENE_ID.fullmatch(scene_id) or any(scene.scene_id == scene_id for scene in scenes):
            scene_id = f"s{uuid.uuid4().hex[:6]}"
        source = _text(data.get("source"), 512)
        card = _text(data.get("card"), MAX_CARD)
        old = known.get(scene_id)

        def kept(key: str, limit: int) -> str:
            return _text(data[key], limit) if data.get(key) is not None else getattr(old, key) if old else ""

        shows, cue, frame = kept("shows", MAX_SHOWS), kept("cue", MAX_CUE), kept("frame", 512)
        speaker = kept("speaker", MAX_SPEAKER)
        # Effects left out keep the scene's own; a new scene's are those its music cue names ("rain, then thunder").
        effects = (_effects(data["effects"], number) if data.get("effects") is not None
                   else list(old.effects) if old else effect_words(cue))
        given = kept("footage", 16)
        if given and given not in FOOTAGE:
            raise ProjectError(f"Scene {number}: its footage is one of clip, picture, ai, card or screen.")
        if frame and kinds.get(frame, "image") != "image":
            raise ProjectError(f"Scene {number}: its frame is a picture, not {frame}.")
        picture = kinds.get(source) == "image"
        if source:
            footage = ("picture" if picture else given if given in ("clip", "ai", "screen")
                       else "screen" if source.startswith("rec-") else "clip")
        else:  # A title card, or footage still to make (planned on a storyboard).
            footage = given or "card"
        if not source and footage == "card" and not card:
            raise ProjectError(f"Scene {number}: name its footage (a video or picture: a recording rec-…, an edit "
                               "edit-…, a file in this chat, a workspace path or a full path), give it a card to "
                               "make it a title card, or plan it: its footage type and what it shows.")
        start = _number(data.get("start"), "start", number) or 0.0
        end = _number(data.get("end"), "end", number)
        line = _text(data.get("line"), MAX_LINE)
        if not source and footage != "card" and not (shows or line):
            raise ProjectError(f"Scene {number}: say what it shows, so its {FOOTAGE_WORDS[footage]} can be made.")
        if not source:  # A title card (its card on the brand's colour, end seconds long), or planned footage.
            start, end = 0.0, CARD_SECONDS if end is None and footage == "card" else end
        elif picture:  # A picture: held end seconds (by default its line's length), pushed in slowly.
            start = 0.0
        if end is not None and end - start < 0.1:
            raise ProjectError(f"Scene {number}: it ends before it starts.")
        speed = 1.0 if picture else float(data.get("speed") or 1)
        if not SPEEDS[0] <= speed <= SPEEDS[1]:
            raise ProjectError(f"Scene {number}: speed is {SPEEDS[0]:g} to {SPEEDS[1]:g} (2 = twice as fast).")
        zoom = data.get("zoom")
        if zoom:
            try:
                x, y, width, height = (min(1.0, max(0.0, float(value))) for value in zoom)
            except (TypeError, ValueError):
                raise ProjectError(f"Scene {number}: zoom is [x, y, width, height], each 0 to 1.") from None
            zoom = None if width >= 0.999 and height >= 0.999 else [x, y, max(width, 0.05), max(height, 0.05)]
        voice = _text(data.get("voice"), 200)
        if voice and (old is None or voice != old.voice):
            voiced, voiced_as = line, "given"  # A voice file made for this line with speak.
        elif old is not None and old.voice and old.voiced == line:
            voice, voiced, voiced_as = old.voice, old.voiced, old.voiced_as
        else:
            voice, voiced, voiced_as = "", "", ""
        # The Video panel doesn't send fit, so a scene it edits keeps its own; the video tool always sends it.
        fit = (None if data["fit"] is None else bool(data["fit"])) if "fit" in data else old.fit if old else None
        scene = Scene(scene_id=scene_id, source=source, start=start, end=end,
                      speed=round(speed, 3) if source else 1.0,
                      zoom=(zoom or None) if source else None, card=card, line=line, voice=voice,
                      voiced=voiced, voiced_as=voiced_as, fit=fit if source else None, footage=footage,
                      shows=shows, frame=frame, cue=cue, speaker=speaker, effects=effects)
        scene.approved = old is not None and old.approved and _same(scene, old)
        scenes.append(scene)
    return scenes


def _effects(given: Any, number: int) -> list[str]:
    """A scene's effects as given, checked: built-in effects by name, or sound files in the chat."""
    from row_bot.video_sound import EFFECTS, SOUND_SUFFIXES

    found: list[str] = []
    for item in given or []:
        name = _text(item, 120)
        if name.casefold() in EFFECTS:
            name = name.casefold()
        elif not name.casefold().endswith(SOUND_SUFFIXES):
            raise ProjectError(f"Scene {number}: its effects are {', '.join(EFFECTS)}, or sound files in this chat "
                               f"by name ({name} is neither).")
        if name and name not in found:
            found.append(name)
    if len(found) > MAX_EFFECTS:
        raise ProjectError(f"Scene {number}: at most {MAX_EFFECTS} effects fit in one scene.")
    return found


# What a scene is: a change to any of these is a change to the scene (its approval goes, and the panel marks it).
CONTENT = ("source", "start", "end", "speed", "zoom", "card", "line", "fit", "footage", "shows", "frame", "cue",
           "speaker", "effects")


def _same(scene: Scene, other: Scene) -> bool:
    return all(getattr(scene, key) == getattr(other, key) for key in CONTENT)


def _grants(allowed: dict[str, str]) -> list[dict[str, str]]:
    return [{"path": path, "by": by} for path, by in sorted(allowed.items())]


def _aspect(value: Any) -> str:
    text = str(value or "").strip()
    if text and text not in SHAPES:
        raise ProjectError(f"A video's shape is {', '.join(SHAPES)}, or empty for its first video's.")
    return text


WORK_SPEED = 4.0


def scenes_from_beats(recording_id: str, conversation_id: str) -> list[dict[str, Any]]:
    """A recording's scenes from the beats marked while it recorded (5.3.1 B4): ``live`` stretches as they
    happened, ``work`` stretches sped up (with their badge), ``cut`` stretches left out; before the first
    beat counts as live. Each scene's title is its beat's note, and its line the beat's line."""
    from row_bot import recordings

    record = recordings.get(recording_id)
    if record is None or record.conversation_id != conversation_id:
        raise ProjectError(f"{recording_id} isn't a recording in this chat.")
    if record.status != "saved":
        raise ProjectError(f"{recording_id} has no saved video yet.")
    marks = sorted((float(at), str(kind), str(note), str(line[0]) if line else "")
                   for at, kind, note, *line in record.beats)
    if not marks or marks[0][0] > 0.05:
        marks.insert(0, (0.0, "live", "", ""))
    scenes: list[dict[str, Any]] = []
    for index, (start, kind, note, line) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else None
        if kind == "cut" or (end is not None and end - start < 0.1):
            continue
        scenes.append({"source": recording_id, "start": round(start, 2), "end": None if end is None else round(end, 2),
                       "speed": WORK_SPEED if kind == "work" else 1.0, "card": note[:MAX_CARD],
                       "line": line[:MAX_LINE]})
    if not scenes:
        raise ProjectError(f"Every part of {recording_id} is marked cut: nothing is left to show.")
    return scenes


def _add_version(project: Project, version: Version) -> Version:
    project.versions.append(version)
    del project.versions[:-MAX_VERSIONS]
    _save(project)
    return version


def _music(value: Any) -> str:
    """A version's music: a music file by name, ``FIT`` (music made to fit its cut), or "" for none."""
    text = _text(value, 200)
    return FIT if text.casefold() == FIT else text


def _mood(value: Any) -> str:
    """The mood of a version's music made to fit, or "" for the brand kit's."""
    from row_bot import video_brand

    text = str(value or "").strip()
    if text and text not in video_brand.MOODS:
        raise ProjectError(f"The music's mood is {', '.join(video_brand.MOODS)}, or empty for the brand kit's.")
    return text


def _named(table: dict[str, str], name: str) -> str:
    return next((value for key, value in table.items() if key.casefold() == name.casefold()), "")


def _cast(scenes: list[Scene], given: dict[str, Any] | None, before: dict[str, str], narrator: str,
          kept: list[str] | None = None) -> dict[str, str]:
    """The voice each character speaks in (G4): the one given now (kept with the character in Library › Media, so
    its lines in every video use it; its name joins *kept*), else the one this video gave it, else its saved one, else
    one picked for it, unlike the narrator's (the version's own *narrator* voice, else the kit's or Settings') and
    every other character's. A voice per name, as written."""
    import hashlib

    from row_bot import media_library
    from row_bot.tts import VOICE_CATALOG

    chosen: dict[str, str] = {}
    for name, value in (given or {}).items():
        name, value = _text(name, MAX_SPEAKER), str(value or "").strip()
        if name and value:
            if value not in VOICE_CATALOG:
                raise ProjectError(f"There's no voice called {value} for {name}. Voices: "
                                   f"{', '.join(sorted(VOICE_CATALOG))}.")
            chosen[name] = value
    named: dict[str, str] = {}  # One voice per character, however its name is written: the first spelling.
    for name in [*(scene.speaker for scene in scenes if scene.speaker), *chosen]:
        named.setdefault(name.casefold(), name)
    speakers = list(named.values())
    if not speakers:
        return {}
    cast: dict[str, str] = {}
    for name in speakers:
        if found := _named(chosen, name):
            if media_library.keep_character_voice(name, found) and kept is not None:
                kept.append(name)
        else:
            found = _named(before, name) or media_library.character_voice(name)
        cast[name] = found if found in VOICE_CATALOG else ""
    voices = sorted(VOICE_CATALOG)
    picking = [name for name, found in cast.items() if not found]
    said = _speaking_voice(narrator) if picking else ""
    for name in picking:
        taken = {said, *cast.values()}
        first = int(hashlib.sha256(name.casefold().encode()).hexdigest()[:8], 16) % len(voices)
        cast[name] = next((voices[(first + step) % len(voices)] for step in range(len(voices))
                           if voices[(first + step) % len(voices)] not in taken), voices[first])
    return cast


def _speaking_voice(voice: str) -> str:
    """The voice that says a version's lines (its own, else the brand kit's, else the one chosen in Settings)."""
    from row_bot import tts, video_brand

    return voice or video_brand.load().voice or tts.TTSService().voice


def create(conversation_id: str, title: str, scenes: list[Any], *, music: str = "", music_volume: float = 0.3,
           narration: str = "", captions: bool = False, voice_lines: bool = True, aspect: str = "", brand: bool = True,
           by: By = "row-bot", note: str = "", voice: str = "", voice_speed: float | None = None,
           logline: str = "", approvals: list[str] | None = None, cast: dict[str, Any] | None = None,
           music_mood: str = "", on_beat: bool = False, kept: list[str] | None = None) -> Project:
    """A new project with its first version; the approval lines of reading files outside the workspace join
    *approvals*, and the characters whose voice was kept with them in Library › Media join *kept*."""
    conversation = str(conversation_id or "").strip()
    if not conversation:
        raise ProjectError("Video projects belong to a conversation.")
    with _LOCK:
        allowed: dict[str, str] = {}
        cleaned = _clean_scenes(scenes, conversation, [], allowed, approvals)
        chosen = _voice(voice)
        project = Project(project_id=f"vid-{uuid.uuid4().hex[:8]}", conversation_id=conversation,
                          title=_text(title, MAX_TITLE) or "Video", created_at=time.time(), outside=_grants(allowed))
        version = Version(number=1, at=time.time(), by=by, note=_text(note, MAX_NOTE) or "First version",
                          scenes=cleaned, music=_music(music),
                          music_volume=_volume(music_volume), narration=_text(narration, 200), captions=bool(captions),
                          voice_lines=bool(voice_lines), aspect=_aspect(aspect), brand=bool(brand),
                          voice=chosen, voice_speed=_voice_speed(voice_speed),
                          logline=_text(logline, MAX_LOGLINE), music_mood=_mood(music_mood), on_beat=bool(on_beat),
                          cast=_cast(cleaned, cast, {}, chosen, kept))
        _add_version(project, version)
    logger.info("Video project created (%d scenes)", len(version.scenes))
    return project


def _voice(value: Any) -> str:
    from row_bot import video_brand

    try:
        return video_brand.narration_voice(value)
    except video_brand.BrandError as error:
        raise ProjectError(str(error)) from None


def _voice_speed(value: Any) -> float:
    """A video's own speed for its lines, or 0 for the brand kit's."""
    from row_bot import video_brand

    if value in (None, "", 0):
        return 0.0
    try:
        return video_brand.narration_speed(value)
    except video_brand.BrandError as error:
        raise ProjectError(str(error)) from None


def _volume(value: Any) -> float:
    try:
        return round(min(1.0, max(0.0, float(value))), 3)
    except (TypeError, ValueError):
        return 0.3


def change(project: Project, *, by: By, note: str = "", base: int | None = None, scenes: list[Any] | None = None,
           title: str | None = None, music: str | None = None, music_volume: float | None = None,
           narration: str | None = None, captions: bool | None = None, voice_lines: bool | None = None, aspect: str | None = None,
           brand: bool | None = None, voice: str | None = None, voice_speed: float | None = None,
           logline: str | None = None, approvals: list[str] | None = None, cast: dict[str, Any] | None = None,
           music_mood: str | None = None, on_beat: bool | None = None, kept: list[str] | None = None) -> Version:
    """A new version with these changes; ``base`` is the version they were made on (refused when it isn't the
    latest, so two editors never overwrite each other). Approval lines and kept voices join *approvals* and *kept*,
    as in ``create``."""
    with _LOCK:
        fresh = get(project.project_id, project.conversation_id)
        if fresh is None:
            raise ProjectError("This video project is no longer here.")
        latest = fresh.latest
        if base is not None and base != latest.number:
            raise ProjectError(f"The video changed meanwhile (now version {latest.number}): look again, then change "
                               "it.", stale=True)
        if title is not None and _text(title, MAX_TITLE):
            fresh.title = _text(title, MAX_TITLE)
        allowed = {item["path"]: item["by"] for item in fresh.outside}
        version = Version(
            number=latest.number + 1, at=time.time(), by=by, note=_text(note, MAX_NOTE) or "Changed",
            scenes=(_clean_scenes(scenes, fresh.conversation_id, latest.scenes, allowed, approvals)
                    if scenes is not None
                    else [Scene(**asdict(scene)) for scene in latest.scenes]),
            music=latest.music if music is None else _music(music),
            music_volume=latest.music_volume if music_volume is None else _volume(music_volume),
            narration=latest.narration if narration is None else _text(narration, 200),
            captions=latest.captions if captions is None else bool(captions),
            voice_lines=latest.voice_lines if voice_lines is None else bool(voice_lines),
            aspect=latest.aspect if aspect is None else _aspect(aspect),
            brand=latest.brand if brand is None else bool(brand),
            voice=latest.voice if voice is None else _voice(voice),
            voice_speed=latest.voice_speed if voice_speed is None else _voice_speed(voice_speed),
            logline=latest.logline if logline is None else _text(logline, MAX_LOGLINE),
            music_mood=latest.music_mood if music_mood is None else _mood(music_mood),
            on_beat=latest.on_beat if on_beat is None else bool(on_beat),
        )
        version.cast = _cast(version.scenes, cast, latest.cast, version.voice, kept)
        fresh.outside = _grants(allowed)
        _add_version(fresh, version)
        project.title, project.versions, project.outside = fresh.title, fresh.versions, fresh.outside
    return version


def restore(project: Project, number: int, *, by: By) -> Version:
    """Back to an earlier version, as a new version (so going back can be undone too)."""
    old = project.version(number)
    if old is None:
        raise ProjectError(f"Version {number} isn't kept any more.")
    with _LOCK:
        fresh = get(project.project_id, project.conversation_id) or project
        version = Version(number=fresh.latest.number + 1, at=time.time(), by=by, note=f"Back to version {number}",
                          scenes=[Scene(**asdict(scene)) for scene in old.scenes], music=old.music,
                          music_volume=old.music_volume, narration=old.narration, captions=old.captions,
                          voice_lines=old.voice_lines,
                          aspect=old.aspect, exports=dict(old.exports), brand=old.brand, voice=old.voice,
                          voice_speed=old.voice_speed, logline=old.logline, cast=dict(old.cast),
                          music_mood=old.music_mood, on_beat=old.on_beat,
                          status="rendering" if old.edit_id else "", edit_id=old.edit_id)
        _add_version(fresh, version)
        project.versions = fresh.versions
    return version


def changed_scenes(project: Project, version: Version) -> list[str]:
    """The scenes this version added or changed against the one before it (the panel highlights them)."""
    before = project.version(version.number - 1)
    if before is None:
        return []
    old = {scene.scene_id: scene for scene in before.scenes}
    return [scene.scene_id for scene in version.scenes
            if scene.scene_id not in old or not _same(scene, old[scene.scene_id])]


def storyboard(version: Version) -> bool:
    """A version not made, nor being made: a storyboard (C1). A new video of several scenes starts as one."""
    return not version.status and not version.edit_id


def approve(project: Project, scene_ids: list[str], approved: bool, *, base: int) -> Version:
    """The person approves scenes of the latest version as they are (or takes it back): marked on that version,
    which stays the same version (nothing in the video changed). Refused when it isn't the latest."""
    with _LOCK:
        fresh = get(project.project_id, project.conversation_id)
        if fresh is None:
            raise ProjectError("This video project is no longer here.")
        latest = fresh.latest
        if base != latest.number:
            raise ProjectError(f"The video changed meanwhile (now version {latest.number}): look again.", stale=True)
        chosen = set(scene_ids)
        if not chosen <= {scene.scene_id for scene in latest.scenes}:
            raise ProjectError("Those scenes aren't in this version.")
        for scene in latest.scenes:
            if scene.scene_id in chosen:
                scene.approved = bool(approved)
        _save(fresh)
        project.versions = fresh.versions
    return latest


def footage_of(scene: Scene) -> str:
    """A scene's footage type: its own, else (a project saved before storyboards) told from its source."""
    from row_bot.media_sources import IMAGE_SUFFIXES

    if scene.footage or not scene.source:
        return scene.footage or "card"
    return ("screen" if scene.source.startswith("rec-")
            else "picture" if Path(scene.source).suffix.casefold() in IMAGE_SUFFIXES else "clip")


def planned(version: Version) -> list[tuple[int, Scene]]:
    """The scenes whose footage is still to make (an AI shot, a picture, a clip or a recording planned on the
    storyboard): (scene number, scene)."""
    return [(number, scene) for number, scene in enumerate(version.scenes, start=1)
            if not scene.source and scene.footage not in ("", "card")]


def planned_words(missing: list[tuple[int, Scene]]) -> str:
    shown = ", ".join(f"scene {number} ({FOOTAGE_WORDS[scene.footage]}: {scene.shows or scene.line})"
                      for number, scene in missing[:5])
    more = f" and {len(missing) - 5} more" if len(missing) > 5 else ""
    return (f"No footage yet: {shown}{more}. Make it first (generate_video, generate_image, browser_record, or the "
            "person's own file), then change those scenes with its source.")


# ── Making the video ────────────────────────────────────────────────────────


def _update(project: Project, number: int, **changes: Any) -> None:
    with _LOCK:
        fresh = get(project.project_id, project.conversation_id)
        if fresh is None:
            return
        version = fresh.version(number)
        if version is None:
            return
        for name, value in changes.items():
            setattr(version, name, value)
        _save(fresh)


def narrator(version: Version) -> tuple[str, float]:
    """The voice and speed a version's lines are said in: its own, else the brand kit's."""
    from row_bot import video_brand

    kit = video_brand.load()
    return version.voice or kit.voice, version.voice_speed or kit.voice_speed


def _said_as(voice: str, speed: float) -> str:
    return f"{voice or 'settings'}@{speed:g}"


def voice_of(version: Version, scene: Scene, narrated: tuple[str, float] | None = None) -> tuple[str, float]:
    """The voice and speed a scene's line is said in: its character's (``Version.cast``), else the narrator's
    (*narrated*, else ``narrator``)."""
    voice, speed = narrated or narrator(version)
    return (_named(version.cast, scene.speaker) or voice) if scene.speaker else voice, speed


def needs_voice(version: Version) -> list[Scene]:
    """The scenes whose line must be voiced: none yet, its words changed, or another voice or speed chosen (the
    narrator's, or its character's)."""
    if not _says_lines(version):
        return []
    narrated = narrator(version)
    return [scene for scene in version.scenes
            if scene.line and (not scene.voice or scene.voiced != scene.line
                               or scene.voiced_as not in ("given", _said_as(*voice_of(version, scene, narrated))))]


def render_of(version: Version, shape: str = "") -> tuple[Any, bool]:
    """The edit making a version's video (or its export in *shape*), or None before it starts; and whether that's
    over: made, failed or stopped."""
    from row_bot import video_edits

    if shape:
        edit = video_edits.get(version.exports.get(shape, ""))
        return edit, shape in version.export_problems or (edit is not None and not edit.active)
    edit = video_edits.get(version.edit_id) if version.edit_id else None
    if edit is None:
        return None, version.status not in ("voicing", "rendering")
    return edit, not edit.active


def _says_lines(version: Version) -> bool:
    """Its scenes' lines are said one by one: voice lines on, and no narration said over the whole video."""
    return version.voice_lines and not version.narration


def export(project: Project, shape: str, number: int | None = None, *, download: bool = False) -> Version:
    """The same version in another shape, or as a GIF, in the background (a made version's export)."""
    project = get(project.project_id, project.conversation_id) or project
    version = project.version(number)
    if version is None:
        raise ProjectError("That version isn't kept any more.")
    if shape not in EXPORTS:
        raise ProjectError(f"Export as {', '.join(SHAPES)} or gif.")
    from row_bot import video_edits

    made = video_edits.get(version.edit_id) if version.edit_id else None
    if made is None or made.status != "saved":
        raise ProjectError("Make this version first: its exports come from it.")
    if shape in version.export_problems or shape in version.exports:  # Made again: the last one's gone.
        _update(project, version.number,
                exports={key: value for key, value in version.exports.items() if key != shape},
                export_problems={key: value for key, value in version.export_problems.items() if key != shape})
        version = (get(project.project_id, project.conversation_id) or project).version(version.number) or version
    if shape == "gif":
        from row_bot.media_sources import resolve

        edit = video_edits.start(video_edits.Request(
            conversation_id=project.conversation_id, kind="video", sources=resolve(made.edit_id,
                                                                               project.conversation_id),
            output_name=_file_name(project.title, ".gif"), download=download, gif=True,
            role="draft", output_id=project.project_id, version=version.number))
        _update(project, version.number, exports={**version.exports, shape: edit.edit_id})
    else:
        thread = threading.Thread(target=_make, args=(project.project_id, project.conversation_id, version.number,
                                                      download, shape), daemon=True,
                                  name=f"row-bot-{project.project_id}-{shape}")
        thread.start()
    return project.version(version.number) or version


def make(project: Project, number: int | None = None, *, download: bool = False) -> Version:
    """Make a version's video in the background: voice the lines that need it, then one edit with the checks."""
    version = project.version(number)
    if version is None:
        raise ProjectError("That version isn't kept any more.")
    missing = planned(version)
    if missing:
        raise ProjectError(planned_words(missing))
    _update(project, version.number, status="voicing" if needs_voice(version) else "rendering", edit_id="",
            problem="")
    thread = threading.Thread(target=_make, args=(project.project_id, project.conversation_id, version.number,
                                                  download), daemon=True, name=f"row-bot-{project.project_id}")
    thread.start()
    return project.version(version.number) or version


def _file_name(title: str, suffix: str, tail: str = "") -> str:
    """The title, shortened so *tail* (a shape or a scene) always stays. A version is no part of it: the versions
    of a project's video share its name, so "Living Buddy v2" is never "Living Buddy v2 v1" (W4)."""
    from row_bot.tools.video_editing_tool import output_name

    return output_name(f"{title.strip()[:60]} {tail}".strip(), default="video", suffix=suffix)


def _look() -> Any:
    """The brand kit as the renderer draws it."""
    from row_bot import video_brand
    from row_bot.video_editing import Look

    kit = video_brand.load()
    return Look(colour=video_brand.rgb(kit.colour), captions=kit.captions, logo=video_brand.logo_png(kit),
                logo_corner=kit.logo_corner, intro=kit.intro, outro=kit.outro, framing=kit.framing,
                camera=kit.camera)


def pointer_focus(source: str, start: float, end: float | None) -> tuple[float, float] | None:
    """Where a recording's pointer mostly was during a scene (the median of its moves), or None: no recording, or
    no moves then. A square or tall cut is centred there."""
    from statistics import median

    from row_bot import recordings

    record = recordings.get(source) if str(source).startswith("rec-") else None
    moves = [(x, y) for at, x, y in (record.trail if record else [])
             if at >= start and (end is None or at <= end)]
    if not moves:
        return None
    return (round(median(x for x, _y in moves), 4), round(median(y for _x, y in moves), 4))


def _voice_seconds(path: Path | None, data: bytes | None, suffix: str) -> float:
    """How long a voice file lasts; 0 when it isn't a WAV (a scene then keeps the length it was given)."""
    from row_bot.sound import wav_seconds

    if suffix.casefold() != ".wav":
        return 0.0
    try:
        return wav_seconds(data if data else path.read_bytes() if path else b"")
    except (OSError, EOFError, wave.Error):
        return 0.0


def scene_camera(source: str, start: float, end: float | None, speed: float) -> tuple[Any, ...]:
    """A scene's camera from what was done while its recording ran (``video_studio.camera``); none for a video
    that isn't a recording."""
    from row_bot import recordings, video_studio

    record = recordings.get(source) if str(source).startswith("rec-") else None
    if record is None or not record.actions:
        return ()
    return video_studio.camera(record.actions, start, record.duration_seconds if end is None else end, speed)


def scene_waits(source: str, start: float, end: float | None) -> tuple[tuple[float, float], ...]:
    """Where nothing was clicked, typed or scrolled in a recording scene, from what its recording logged; none
    for a video that isn't a recording (nothing says what was done)."""
    from row_bot import recordings

    record = recordings.get(source) if str(source).startswith("rec-") else None
    if record is None or not record.actions:
        return ()
    end = record.duration_seconds if end is None else end
    waits, quiet = [], start
    for begin, finish in sorted((float(action[0]), float(action[1])) for action in record.actions):
        if finish < start or begin > end:
            continue
        if begin > quiet:
            waits.append((quiet, begin))
        quiet = max(quiet, finish)
    if end > quiet:
        waits.append((quiet, end))
    return tuple(waits)


def footage(project: Project, version: Version) -> tuple[dict[str, Any], list[tuple[int, str]], list[tuple[int, str]]]:
    """Each source of a version's scenes as the edit reads it, in order (source -> media); the scenes whose source
    can't be found any more, moved, renamed or deleted since it joined the video; and those whose source is a file
    outside the workspace this project wasn't allowed to read: (scene number, source). Nothing is asked."""
    from row_bot.media_sources import SourceError, outside_path, resolve

    allowed = frozenset(Path(item["path"]) for item in project.outside)
    found: dict[str, Any] = {}  # source -> its media; None: not found; "": not allowed.
    missing: list[tuple[int, str]] = []
    refused: list[tuple[int, str]] = []
    for number, scene in enumerate(version.scenes, start=1):
        if not scene.source:
            continue
        if scene.source not in found:
            try:
                path = outside_path(scene.source, project.conversation_id)
                if path is not None and path not in allowed:
                    found[scene.source] = ""
                else:
                    media = resolve(scene.source, project.conversation_id, allowed=allowed)
                    found[scene.source] = media[0] if len(media) == 1 else None
            except (SourceError, OSError, ValueError):
                found[scene.source] = None
        if found[scene.source] == "":
            refused.append((number, scene.source))
        elif found[scene.source] is None:
            missing.append((number, scene.source))
    return {source: media for source, media in found.items() if media}, missing, refused


def _scenes_words(scenes: list[tuple[int, str]]) -> str:
    shown = ", ".join(f"scene {number} ({source})" for number, source in scenes[:5])
    return shown + (f" and {len(scenes) - 5} more" if len(scenes) > 5 else "")


def missing_words(missing: list[tuple[int, str]]) -> str:
    return (f"Footage missing: {_scenes_words(missing)}: moved, renamed or deleted since it joined the video. Ask "
            "the person to put it back, or give those scenes other footage.")


def refused_words(refused: list[tuple[int, str]]) -> str:
    return (f"Footage not allowed: {_scenes_words(refused)}: outside the workspace, and the person hasn't allowed "
            "this video to read it. To ask the person, change the video again with those scenes.")


def scene_footage(source: str, ref: str) -> Any:
    """What the finished check knows of a scene's footage beyond its cut (``video_check.Footage``): what its
    recording logged being done, and the character or style its file is tagged with in Library › Media."""
    from row_bot import media_library, recordings
    from row_bot.video_check import Footage

    record = recordings.get(source) if str(source).startswith("rec-") else None
    return Footage(actions=tuple(tuple(action) for action in record.actions) if record else (),
                   look=media_library.look_of(ref) if ref else "")


def _make(project_id: str, conversation_id: str, number: int, download: bool, shape: str = "") -> None:
    """Voice what needs it, then make the version's video (or, with ``shape``, the same in that shape)."""
    from row_bot import sound, video_brand, video_edits
    from row_bot.media_sources import SourceError
    from row_bot.tools.sound_tool import _save as save_sound
    from row_bot.tools.sound_tool import _source as sound_source
    from row_bot.video_check import Footage
    from row_bot.video_editing import SCORE, SceneCut, Step
    from row_bot.video_sound import EFFECTS

    project = get(project_id, conversation_id)
    version = project.version(number) if project is not None else None
    if project is None or version is None:
        return
    try:
        narrated = narrator(version)
        for scene in needs_voice(version):  # Each line in its speaker's voice, with the person's pronunciations.
            voice, speed = voice_of(version, scene, narrated)
            speech = sound.speak(scene.line, voice=voice, speed=speed)
            scene.voice = save_sound(_file_name(project.title, ".wav", scene.scene_id), speech.wav,
                                     conversation_id)
            scene.voiced, scene.voiced_as = scene.line, _said_as(voice, speed)
        lasts: dict[str, float] = {}
        sounds: dict[str, tuple[Any, bytes, str]] = {}
        for scene in version.scenes if _says_lines(version) else []:
            if not scene.voice:
                continue
            _label, path, data, suffix = sound_source(scene.voice, conversation_id)
            sounds[scene.voice] = (path, data or b"", suffix)
            lasts[scene.voice] = _voice_seconds(path, data, suffix)
        if _says_lines(version) and not shape:
            _update(project, number, scenes=version.scenes, status="rendering")
        found, missing, refused = footage(project, version)
        if missing or refused:
            raise SourceError(" ".join(words for words in (missing and missing_words(missing),
                                                           refused and refused_words(refused)) if words))
        sources, media = list(found), list(found.values())
        cuts = [SceneCut(input=sources.index(scene.source) if scene.source else None, start=scene.start,
                         end=scene.end, speed=scene.speed,
                         zoom=tuple(scene.zoom) if scene.zoom else None, card=scene.card, line=scene.line,
                         voice=scene.voice if _says_lines(version) else "",
                         voice_seconds=lasts.get(scene.voice, 0.0) if _says_lines(version) else 0.0,
                         focus=None if scene.zoom or not scene.source
                         else pointer_focus(scene.source, scene.start, scene.end), fit=scene.fit,
                         camera=scene_camera(scene.source, scene.start, scene.end, scene.speed)
                         if scene.source and not scene.zoom else (),
                         waits=scene_waits(scene.source, scene.start, scene.end) if scene.source else (),
                         effects=tuple(scene.effects), cue=scene.cue)
                for scene in version.scenes]
        steps = [Step(op="scenes", scenes=cuts, style="caption" if version.captions else "",
                      aspect=shape or version.aspect, look=_look() if version.brand else None)]
        fitted = version.music == FIT
        extra = [name for name in (version.narration, "" if fitted else version.music) if name]
        files = [name for cut in cuts for name in cut.effects if name not in EFFECTS]
        for name in [cut.voice for cut in cuts if cut.voice] + files + extra:
            if name not in sounds:
                _label, path, data, suffix = sound_source(name, conversation_id)
                sounds[name] = (path, data or b"", suffix)
        if version.narration:
            steps.append(Step(op="sound", source=version.narration, role="voice", start=0.0))
        if version.music:  # A music file, or music made to fit the cut (``SCORE``, made with the video).
            steps.append(Step(op="sound", source=SCORE if fitted else version.music, role="music",
                              volume=version.music_volume))
        kit = video_brand.load()
        known = {value: scene_footage(value, item.ref or "") for value, item in zip(sources, media, strict=True)}
        edit = video_edits.start(video_edits.Request(
            conversation_id=conversation_id, kind="video", sources=media, steps=steps,
            output_name=_file_name(project.title, ".mp4", shape.replace(":", "x")),
            download=download, sounds=sounds, check=True,
            footage=[known.get(scene.source, Footage()) for scene in version.scenes],
            role="draft", output_id=project.project_id, version=number, scene_cache=scene_cache(project),
            score=(version.music_mood or kit.music_mood) if fitted else "", on_beat=version.on_beat,
            loudness=kit.loudness if version.brand else None,
        ))
        if shape:
            fresh = get(project_id, conversation_id)
            current = fresh.version(number) if fresh else None
            _update(project, number, exports={**(current.exports if current else {}), shape: edit.edit_id})
        else:
            _update(project, number, status="rendering", edit_id=edit.edit_id)
    except (SourceError, ValueError) as error:
        _record_failure(project_id, conversation_id, number, shape, str(error)[:300])
        logger.info("A video project's video couldn't be made: %s", error)
    except Exception:
        logger.warning("A video project's video couldn't be made", exc_info=True)
        _record_failure(project_id, conversation_id, number, shape, "Something went wrong while making the video.")


def scene_cache(project: Project) -> Path:
    """Where a project keeps its scenes' drawn clips (with its conversation's projects, so they go with it)."""
    return _path(project.conversation_id, project.project_id).with_name(f"{project.project_id}-scenes")


def _record_failure(project_id: str, conversation_id: str, number: int, shape: str, words: str) -> None:
    """A version's video that didn't come out says why; so does an export in another shape (R5)."""
    project = get(project_id, conversation_id)
    if project is None:
        return
    if not shape:
        _update(project, number, status="failed", problem=words)
        return
    current = project.version(number)
    _update(project, number, export_problems={**(current.export_problems if current else {}), shape: words})


# ── Reading ─────────────────────────────────────────────────────────────────


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


class _Refs:
    """The files a version's live preview plays (5.3.1 B3): each scene's video and its voice line, by reference.
    A video in the workspace has none, so its scene previews as a still card until the version is made."""

    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        self._sources: dict[str, str | None] = {}
        self._files: dict[str, str] | None = None

    def source(self, value: str) -> str | None:
        if value not in self._sources:
            from row_bot.media_sources import SourceError, resolve

            try:
                found = resolve(value, self.conversation_id, allowed=frozenset())  # Chat files: nothing else plays.
            except (SourceError, ValueError, OSError):
                found = []
            self._sources[value] = found[0].ref or None if len(found) == 1 and found[0].kind == "video" else None
        return self._sources[value]

    def voice(self, name: str) -> str | None:
        if self._files is None:
            from row_bot.application.attachments import AttachmentError, list_conversation_files

            try:
                files = list_conversation_files(self.conversation_id)
            except (AttachmentError, OSError, ValueError):
                files = []
            self._files = {}
            for item in reversed(files):  # The newest of a name wins.
                self._files[str(item["name"]).casefold()] = str(item["attachment_ref"])
        return self._files.get(name.casefold())


def _kit_mood() -> str:
    from row_bot import video_brand

    return video_brand.load().music_mood


def view(project: Project, number: int | None = None) -> dict[str, Any]:
    """The client's view of a project at one version (the latest by default)."""
    from row_bot import video_edits

    version = project.version(number) or project.latest
    edit = video_edits.get(version.edit_id) if version.edit_id else None
    marks = list(edit.marks) if edit is not None and len(edit.marks) == len(version.scenes) + 1 else []
    makes = list(edit.scene_makes) if edit is not None and len(edit.scene_makes) == len(version.scenes) else []
    refs = _Refs(project.conversation_id)
    return {
        "project_id": project.project_id,
        "conversation_id": project.conversation_id,
        "title": project.title,
        "latest": project.latest.number,
        "versions": [{"number": item.number, "at": _iso(item.at), "by": item.by if item.by in {"you", "row-bot"}
                      else "row-bot", "note": item.note} for item in reversed(project.versions)],
        "number": version.number,
        "logline": version.logline,
        "scenes": [{"scene_id": scene.scene_id, "source": scene.source, "start": scene.start, "end": scene.end,
                    "speed": scene.speed, "zoom": scene.zoom, "card": scene.card, "line": scene.line,
                    "voiced": bool(scene.voice) and scene.voiced == scene.line,
                    "at": marks[index] if marks else None,
                    "length": round(marks[index + 1] - marks[index], 3) if marks else None,
                    "source_ref": refs.source(scene.source) if scene.source else None,
                    "voice_ref": refs.voice(scene.voice) if scene.voice and scene.voiced == scene.line else None,
                    "footage": footage_of(scene), "shows": scene.shows,
                    "cue": scene.cue, "approved": scene.approved,
                    "has_frame": bool(scene.frame or scene.source),
                    "made": makes[index] if makes else "", "speaker": scene.speaker,
                    "effects": list(scene.effects)}
                   for index, scene in enumerate(version.scenes)],
        "changed": changed_scenes(project, version),
        "music": version.music,
        "music_volume": version.music_volume,
        "music_mood": version.music_mood or (_kit_mood() if version.music == FIT else ""),
        "on_beat": version.on_beat,
        "cast": [{"name": name, "voice": voice} for name, voice in version.cast.items()],
        "narration": version.narration,
        "captions": version.captions,
        "voice_lines": version.voice_lines,
        "status": _status(version, edit),
        "problem": version.problem or (edit.detail if edit is not None and edit.status == "failed" else ""),
        "edit": None if edit is None else video_edits.view(edit),
        "checks": list(edit.checks) if edit is not None else [],
        "checks_passed": edit.checks_passed if edit is not None else None,
        "aspect": version.aspect,
        "brand": version.brand,
        "voice": version.voice,
        "voice_speed": version.voice_speed,
        "review_sheet": edit.sheet if edit is not None and _status(version, edit) == "made" else "",
        "exports": [{"shape": shape, "edit": video_edits.view(found)} for shape in EXPORTS
                    if (found := video_edits.get(version.exports.get(shape, ""))) is not None],
    }


def scene_frame(project: Project, scene_id: str, number: int | None = None) -> bytes | None:
    """A storyboard card's frame (``video_studio.card_frame``): the scene's own still when it has one, else its
    picture, else its video half a second into the scene. None for a title card, footage still to make, or footage
    that can't be read here. A frame is read once, then kept with the project."""
    import hashlib

    from row_bot.media_sources import SourceError, resolve
    from row_bot.video_studio import card_frame

    version = project.version(number)
    scene = next((item for item in version.scenes if item.scene_id == scene_id), None) if version else None
    target = (scene.frame or scene.source) if scene is not None else ""
    if not target:
        return None
    try:
        found = resolve(target, project.conversation_id,
                        allowed=frozenset(Path(item["path"]) for item in project.outside))
        if len(found) != 1:
            return None
        media = found[0]
        picture = media.kind == "image"
        at = 0.0 if picture else scene.start + min(0.5, ((scene.end or scene.start + 1) - scene.start) / 2)
        if media.ref:
            identity = media.ref
        else:
            stat = media.path.stat()
            identity = f"{media.path}:{stat.st_size}:{stat.st_mtime_ns}"
        kept = scene_cache(project).with_name(f"{project.project_id}-frames") / (
            hashlib.sha256(f"{identity}|{at:.3f}".encode()).hexdigest()[:32] + ".jpg")
        if kept.is_file():
            return kept.read_bytes()
        frame = card_frame(media.path if media.path and not media.ref else media.read_bytes(1 << 31),
                           picture=picture, at=at)
    except ImportError:  # No PyAV (the media extra): the card shows its footage type instead.
        return None
    except (SourceError, OSError, ValueError):  # Footage that doesn't decode: the same.
        logger.debug("A storyboard frame couldn't be read", exc_info=True)
        return None
    kept.parent.mkdir(parents=True, exist_ok=True)
    partial = kept.with_name(f"{kept.name}.{uuid.uuid4().hex[:6]}.part")
    partial.write_bytes(frame)
    os.replace(partial, kept)
    return frame


def _status(version: Version, edit: Any) -> str:
    if version.status in {"voicing", "failed"}:
        return version.status
    if edit is None:
        return "rendering" if version.status == "rendering" and not version.edit_id else "draft"
    return {"saved": "made", "failed": "failed", "stopped": "stopped"}.get(edit.status, "rendering")


# A made video's checks are the finished check (C8): its sound, picture and words were read and its review sheet
# looked at once by the vision model, so Row-Bot fixes what they list instead of looking again itself.
REVIEW = ("Review sheet: {sheet} (frames of each scene, for the person to open). The checks above already "
          "include one look at it: don't look at it again with analyze_image. Fix what they list and make the video "
          "again; it's ready to share only when they pass. A line starting \"Check\" is worth a look but doesn't "
          "stop it. If something can't be fixed, tell the person. If a problem is still listed after two makes, stop, "
          "tell the person what's left, and ask whether to present it as it is.")


def summary(project: Project, number: int | None = None) -> str:
    """The tool result's first line, ``Video project vid-…: Output "…" v… (…)`` (the chat's card follows it; history
    masking keeps it), then the scenes."""
    data = view(project, number)
    words = {"draft": "nothing made yet", "voicing": "voicing the lines", "rendering": "making the video",
             "made": "made", "failed": "not made", "stopped": "stopped"}[data["status"]]
    made = data["edit"] if data["status"] == "made" and data["edit"] else None
    from row_bot.media_sources import identity, identity_of

    # A made version leads with its output (W4): history masking keeps a result's first line.
    named = (identity_of(str(made.get("media_ref") or ""))
             or identity({"name": made["output_name"], "role": "draft", "version": data["number"]})) if made else ""
    role = "storyboard" if data["status"] == "draft" else "draft"
    named = named or f'Output "{project.title}" v{data["number"]} ({role})'
    lines = [f"Video project {project.project_id}: {named}; version {data['number']} of {data['latest']}, "
             f"{len(data['scenes'])} scenes, {words}."]
    if data["logline"]:
        lines.append(f"Logline: {data['logline']}")
    if data["problem"]:
        lines.append(f"Why: {data['problem']}")
    shown = project.version(data["number"])
    found, missing, refused = footage(project, shown) if shown is not None else ({}, [], [])
    for words in (missing and missing_words(missing), refused and refused_words(refused)):
        if words and words not in data["problem"]:
            lines.append(words)
    fits = [scene.fit for scene in shown.scenes] if shown is not None else []
    for index, scene in enumerate(data["scenes"], start=1):
        span = f"{scene['start']:g}–{'end' if scene['end'] is None else format(scene['end'], 'g')} s"
        if getattr(found.get(scene["source"]), "kind", "") == "image":
            span = (f"picture, {scene['end']:g} s" if scene["end"] is not None
                    else "picture, as long as its line (else 3.5 s)")
        fit = fits[index - 1] if index <= len(fits) else None
        what = scene["source"] or "title card"
        if not scene["source"] and scene["footage"] != "card":  # Planned: its footage is still to make.
            what = f"{FOOTAGE_WORDS[scene['footage']]} to make,"
            span = f"{scene['end']:g} s" if scene["end"] is not None else "as long as its line"
        extras = ["AI shot" if scene["source"] and scene["footage"] == "ai" else "",
                  f"{scene['speed']:g}×" if scene["speed"] != 1 else "", "zoom" if scene["zoom"] else "",
                  {True: "fit", False: "fill"}.get(fit, ""),
                  f"card \"{scene['card']}\"" if scene["card"] else "",
                  f"shows \"{scene['shows']}\"" if scene["shows"] else "",
                  f"music \"{scene['cue']}\"" if scene["cue"] else "",
                  f"said by {scene['speaker']}" if scene["speaker"] else "",
                  f"effects {', '.join(scene['effects'])}" if scene["effects"] else "",
                  "approved" if scene["approved"] else ""]
        extra = ", ".join(item for item in extras if item)
        line = f" — \"{scene['line']}\"" if scene["line"] else ""
        lines.append(f"{index}. [{scene['scene_id']}] {what} {span}{', ' + extra if extra else ''}{line}")
    if data["status"] == "draft" and shown is not None:
        lines.append("Storyboard, nothing made yet: show it to the person (the Video panel shows each scene as a "
                     "card to approve, change or comment on). Make it with action make once they approve. Make it at "
                     "once only when they said to skip the storyboard.")
        if planned(shown):
            lines.append(planned_words(planned(shown)))
    makes = [scene["made"] for scene in data["scenes"]]
    if made and "reused" in makes:
        drawn = makes.count("new")
        lines.append(f"Drew {drawn} changed scene{'s' if drawn != 1 else ''} again and reused "
                     f"{makes.count('reused')} unchanged.")
    if data["narration"]:
        lines.append(f"Narration: {data['narration']}, said over the whole video (the lines above aren't said "
                     "one by one).")
    from row_bot import video_brand

    if data["cast"]:
        lines.append("Voices: " + ", ".join(f"{item['name']} {item['voice']}" for item in data["cast"])
                     + " (each character's lines in its own voice).")
    if data["music"] == FIT:
        lines.append(f"Music: made to fit the cut, {data['music_mood']}, its sections changing where the music cue does"
                     + (", with the cuts on its beat." if data["on_beat"] else "."))
    elif data["music"] and data["on_beat"]:
        lines.append(f"Cuts on the beat of {data['music']}.")
    elif data["on_beat"]:
        lines.append("No music, so the cuts aren't on a beat.")

    if shown is not None and shown.brand and video_brand.load().framing == "framed":
        lines.append("Framed: the brand kit sets 16:9 footage inset on its colour. To fill the frame, video action "
                     "brand with framing full.")
    if made:
        lines.append(f"Its edit: {made['edit_id']}.")
    if data["checks"]:
        verdict = "passed: ready to share" if data["checks_passed"] else "NOT passed: fix these, then make it again"
        lines.append(f"Checks {verdict}:\n" + "\n".join(f"- {line}" for line in data["checks"]))
    if data["review_sheet"]:
        lines.append(REVIEW.format(sheet=data["review_sheet"]))
    for item in data["exports"]:
        made = item["edit"]
        state = {"saved": f"made: {made['output_name']}", "failed": f"not made: {made.get('detail') or 'it failed'}",
                 "stopped": "stopped"}.get(made["status"], "being made")
        lines.append(f"Export {item['shape']}: {state}.")
    for shape, words in (shown.export_problems if shown is not None else {}).items():
        lines.append(f"Export {shape}: not made: {words}")
    return "\n".join(lines)
