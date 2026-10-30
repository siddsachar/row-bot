"""Video projects (5.3.1 B3): a video as scenes, versioned, shared by the Video panel and Row-Bot.

A project is its scenes in order. Each scene is a piece of a source (a recording, a video edit,
or a video in the chat's files or the workspace) from its start to its end, with its speed, a
zoom on a region, a card laid over its first seconds, and its line: the words said over it,
voiced in the person's chosen voice (or a voice file made with ``speak``) and, with captions on,
shown under it. Music (``make_music``, or the person's own file) plays under the whole video
and dips while a line is said.

A version is made in one shape (16:9, 1:1 or 9:16; by default its first video's), and the same
version can be exported in the other shapes, each scene reframed on its zoom, or as a GIF.

Every change is a new version, by the person or by Row-Bot with a note of what changed. Going
back is a new version too, so nothing is lost. Making a version's video voices each line whose
voice is missing or says other words, then renders the whole version as one video edit that
also reads the checks (sound, black and still stretches) for the review card. The video is
saved to the conversation's files like any edit.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

PROJECT_ID = re.compile(r"vid-[0-9a-f]{8}")
SCENE_ID = re.compile(r"s[0-9a-f]{6}")
By = Literal["you", "row-bot"]
MAX_SCENES = 60
MAX_VERSIONS = 50
MAX_TITLE = 80
MAX_CARD = 120
MAX_LINE = 300
MAX_NOTE = 200
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
    # Use the brand kit (its colour, captions, logo, intro and outro).
    brand: bool = True
    # Making its video: "" (not yet), "voicing", "rendering" (edit_id follows the edit) or "failed".
    status: str = ""
    edit_id: str = ""
    problem: str = ""


@dataclass
class Project:
    project_id: str
    conversation_id: str
    title: str
    created_at: float
    versions: list[Version] = field(default_factory=list)

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
                exports={str(key): str(value) for key, value in (item.get("exports") or {}).items()
                         if key in EXPORTS},
                edit_id=str(item.get("edit_id") or ""), problem=str(item.get("problem") or ""),
            ))
        if not versions:
            return None
        return Project(project_id=data["project_id"], conversation_id=str(data["conversation_id"]),
                       title=str(data.get("title") or "Video"), created_at=float(data["created_at"]),
                       versions=versions)
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


def _check_source(value: str, conversation_id: str, number: int) -> None:
    from row_bot.media_sources import SourceError, resolve

    try:
        found = resolve(value, conversation_id)
    except SourceError as error:
        raise ProjectError(f"Scene {number}: {error}") from None
    if len(found) != 1 or found[0].kind != "video":
        raise ProjectError(f"Scene {number}: {value} isn't a video.")


def _clean_scenes(raw: list[Any], conversation_id: str, before: list[Scene]) -> list[Scene]:
    """The scenes as given, checked; a scene that keeps its id keeps its voice while its line is the same."""
    if not raw:
        raise ProjectError("A video needs at least one scene.")
    if len(raw) > MAX_SCENES:
        raise ProjectError(f"At most {MAX_SCENES} scenes fit in one video: join some.")
    from row_bot.video_editing import CARD_SECONDS, MAX_INPUTS

    known = {scene.scene_id: scene for scene in before}
    checked: dict[str, None] = {}
    scenes: list[Scene] = []
    for number, item in enumerate(raw, start=1):
        data = item if isinstance(item, dict) else dict(item)
        scene_id = str(data.get("scene_id") or "")
        if not SCENE_ID.fullmatch(scene_id) or any(scene.scene_id == scene_id for scene in scenes):
            scene_id = f"s{uuid.uuid4().hex[:6]}"
        source = _text(data.get("source"), 512)
        card = _text(data.get("card"), MAX_CARD)
        if not source and not card:
            raise ProjectError(f"Scene {number}: name its video (a recording rec-…, an edit edit-…, a file in "
                               "this chat or a workspace path), or give it a card to make it a title card.")
        if source and source not in checked:
            _check_source(source, conversation_id, number)
            checked[source] = None
        start = _number(data.get("start"), "start", number) or 0.0
        end = _number(data.get("end"), "end", number)
        if not source:  # A title card: its card on the brand's colour, end seconds long.
            start, end = 0.0, CARD_SECONDS if end is None else end
        if end is not None and end - start < 0.1:
            raise ProjectError(f"Scene {number}: it ends before it starts.")
        speed = float(data.get("speed") or 1)
        if not SPEEDS[0] <= speed <= SPEEDS[1]:
            raise ProjectError(f"Scene {number}: speed is {SPEEDS[0]:g} to {SPEEDS[1]:g} (2 = twice as fast).")
        zoom = data.get("zoom")
        if zoom:
            try:
                x, y, width, height = (min(1.0, max(0.0, float(value))) for value in zoom)
            except (TypeError, ValueError):
                raise ProjectError(f"Scene {number}: zoom is [x, y, width, height], each 0 to 1.") from None
            zoom = None if width >= 0.999 and height >= 0.999 else [x, y, max(width, 0.05), max(height, 0.05)]
        line = _text(data.get("line"), MAX_LINE)
        voice = _text(data.get("voice"), 200)
        old = known.get(scene_id)
        if voice and (old is None or voice != old.voice):
            voiced = line  # A voice file Row-Bot made for this line with speak.
        elif old is not None and old.voice and old.voiced == line:
            voice, voiced = old.voice, old.voiced
        else:
            voice, voiced = "", ""
        scenes.append(Scene(scene_id=scene_id, source=source, start=start, end=end,
                            speed=round(speed, 3) if source else 1.0,
                            zoom=(zoom or None) if source else None, card=card, line=line, voice=voice,
                            voiced=voiced))
    if len({scene.source for scene in scenes if scene.source}) > MAX_INPUTS:
        raise ProjectError(f"A video can use at most {MAX_INPUTS} different sources: join some first.")
    return scenes


def _aspect(value: Any) -> str:
    text = str(value or "").strip()
    if text and text not in SHAPES:
        raise ProjectError(f"A video's shape is {', '.join(SHAPES)}, or empty for its first video's.")
    return text


WORK_SPEED = 4.0


def scenes_from_beats(recording_id: str, conversation_id: str) -> list[dict[str, Any]]:
    """A recording's scenes from the beats marked while it recorded (5.3.1 B4): ``live`` stretches as they
    happened, ``work`` stretches sped up (with their badge), ``cut`` stretches left out; before the first
    beat counts as live."""
    from row_bot import recordings

    record = recordings.get(recording_id)
    if record is None or record.conversation_id != conversation_id:
        raise ProjectError(f"{recording_id} isn't a recording in this chat.")
    if record.status != "saved":
        raise ProjectError(f"{recording_id} has no saved video yet.")
    marks = sorted((float(at), str(kind), str(note)) for at, kind, note in record.beats)
    if not marks or marks[0][0] > 0.05:
        marks.insert(0, (0.0, "live", ""))
    scenes: list[dict[str, Any]] = []
    for index, (start, kind, _note) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else None
        if kind == "cut" or (end is not None and end - start < 0.1):
            continue
        scenes.append({"source": recording_id, "start": round(start, 2), "end": None if end is None else round(end, 2),
                       "speed": WORK_SPEED if kind == "work" else 1.0, "card": "", "line": ""})
    if not scenes:
        raise ProjectError(f"Every part of {recording_id} is marked cut: nothing is left to show.")
    return scenes


def _add_version(project: Project, version: Version) -> Version:
    project.versions.append(version)
    del project.versions[:-MAX_VERSIONS]
    _save(project)
    return version


def create(conversation_id: str, title: str, scenes: list[Any], *, music: str = "", music_volume: float = 0.3,
           narration: str = "", captions: bool = False, voice_lines: bool = True, aspect: str = "", brand: bool = True,
           by: By = "row-bot", note: str = "") -> Project:
    """A new project with its first version."""
    conversation = str(conversation_id or "").strip()
    if not conversation:
        raise ProjectError("Video projects belong to a conversation.")
    with _LOCK:
        project = Project(project_id=f"vid-{uuid.uuid4().hex[:8]}", conversation_id=conversation,
                          title=_text(title, MAX_TITLE) or "Video", created_at=time.time())
        version = Version(number=1, at=time.time(), by=by, note=_text(note, MAX_NOTE) or "First version",
                          scenes=_clean_scenes(scenes, conversation, []), music=_text(music, 200),
                          music_volume=_volume(music_volume), narration=_text(narration, 200), captions=bool(captions),
                          voice_lines=bool(voice_lines), aspect=_aspect(aspect), brand=bool(brand))
        _add_version(project, version)
    logger.info("Video project created (%d scenes)", len(version.scenes))
    return project


def _volume(value: Any) -> float:
    try:
        return round(min(1.0, max(0.0, float(value))), 3)
    except (TypeError, ValueError):
        return 0.3


def change(project: Project, *, by: By, note: str = "", base: int | None = None, scenes: list[Any] | None = None,
           title: str | None = None, music: str | None = None, music_volume: float | None = None,
           narration: str | None = None, captions: bool | None = None, voice_lines: bool | None = None, aspect: str | None = None,
           brand: bool | None = None) -> Version:
    """A new version with these changes; ``base`` is the version they were made on (refused when it isn't the
    latest, so two editors never overwrite each other)."""
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
        version = Version(
            number=latest.number + 1, at=time.time(), by=by, note=_text(note, MAX_NOTE) or "Changed",
            scenes=(_clean_scenes(scenes, fresh.conversation_id, latest.scenes) if scenes is not None
                    else [Scene(**asdict(scene)) for scene in latest.scenes]),
            music=latest.music if music is None else _text(music, 200),
            music_volume=latest.music_volume if music_volume is None else _volume(music_volume),
            narration=latest.narration if narration is None else _text(narration, 200),
            captions=latest.captions if captions is None else bool(captions),
            voice_lines=latest.voice_lines if voice_lines is None else bool(voice_lines),
            aspect=latest.aspect if aspect is None else _aspect(aspect),
            brand=latest.brand if brand is None else bool(brand),
        )
        _add_version(fresh, version)
        project.title, project.versions = fresh.title, fresh.versions
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
                          aspect=old.aspect, exports=dict(old.exports), brand=old.brand,
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
    keys = ("source", "start", "end", "speed", "zoom", "card", "line")
    return [scene.scene_id for scene in version.scenes
            if scene.scene_id not in old or any(getattr(scene, key) != getattr(old[scene.scene_id], key)
                                                for key in keys)]


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


def needs_voice(version: Version) -> list[Scene]:
    return [scene for scene in version.scenes
            if _says_lines(version) and scene.line and (not scene.voice or scene.voiced != scene.line)]


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
    if shape == "gif":
        from row_bot.media_sources import resolve

        edit = video_edits.start(video_edits.Request(
            conversation_id=project.conversation_id, kind="video", sources=resolve(made.edit_id,
                                                                               project.conversation_id),
            output_name=_file_name(project.title, ".gif", f"v{version.number}"), download=download, gif=True))
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
    _update(project, version.number, status="voicing" if needs_voice(version) else "rendering", edit_id="",
            problem="")
    thread = threading.Thread(target=_make, args=(project.project_id, project.conversation_id, version.number,
                                                  download), daemon=True, name=f"row-bot-{project.project_id}")
    thread.start()
    return project.version(version.number) or version


def _file_name(title: str, suffix: str, tail: str = "") -> str:
    """The title, shortened so *tail* (a version or a scene) always stays: no two files share a name."""
    from row_bot.tools.video_editing_tool import output_name

    return output_name(f"{title.strip()[:60]} {tail}".strip(), default="video", suffix=suffix)


def _look() -> Any:
    """The brand kit as the renderer draws it."""
    from row_bot import video_brand
    from row_bot.video_editing import Look

    kit = video_brand.load()
    return Look(colour=video_brand.rgb(kit.colour), captions=kit.captions, logo=video_brand.logo_png(kit),
                logo_corner=kit.logo_corner, intro=kit.intro, outro=kit.outro)


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


def _make(project_id: str, conversation_id: str, number: int, download: bool, shape: str = "") -> None:
    """Voice what needs it, then make the version's video (or, with ``shape``, the same in that shape)."""
    from row_bot import sound, video_edits
    from row_bot.media_sources import SourceError, resolve
    from row_bot.tools.sound_tool import _save as save_sound
    from row_bot.tools.sound_tool import _source as sound_source
    from row_bot.video_editing import SceneCut, Step

    project = get(project_id, conversation_id)
    version = project.version(number) if project is not None else None
    if project is None or version is None:
        return
    try:
        for scene in needs_voice(version):
            speech = sound.speak(scene.line)
            scene.voice = save_sound(_file_name(project.title, ".wav", scene.scene_id), speech.wav,
                                     conversation_id)
            scene.voiced = scene.line
        if _says_lines(version) and not shape:
            _update(project, number, scenes=version.scenes, status="rendering")
        sources = list(dict.fromkeys(scene.source for scene in version.scenes if scene.source))
        media = []
        for value in sources:
            found = resolve(value, conversation_id)
            if len(found) != 1 or found[0].kind != "video":
                raise SourceError(f"{value} isn't a video any more.")
            media.append(found[0])
        cuts = [SceneCut(input=sources.index(scene.source) if scene.source else None, start=scene.start,
                         end=scene.end, speed=scene.speed,
                         zoom=tuple(scene.zoom) if scene.zoom else None, card=scene.card, line=scene.line,
                         voice=scene.voice if _says_lines(version) else "",
                         focus=None if scene.zoom or not scene.source
                         else pointer_focus(scene.source, scene.start, scene.end))
                for scene in version.scenes]
        steps = [Step(op="scenes", scenes=cuts, style="caption" if version.captions else "",
                      aspect=shape or version.aspect, look=_look() if version.brand else None)]
        sounds: dict[str, tuple[Any, bytes, str]] = {}
        extra = [name for name in (version.narration, version.music) if name]
        for name in [cut.voice for cut in cuts if cut.voice] + extra:
            if name not in sounds:
                _label, path, data, suffix = sound_source(name, conversation_id)
                sounds[name] = (path, data or b"", suffix)
        if version.narration:
            steps.append(Step(op="sound", source=version.narration, role="voice", start=0.0))
        if version.music:
            steps.append(Step(op="sound", source=version.music, role="music", volume=version.music_volume))
        edit = video_edits.start(video_edits.Request(
            conversation_id=conversation_id, kind="video", sources=media, steps=steps,
            output_name=_file_name(project.title, ".mp4", f"v{number} {shape.replace(':', 'x')}".strip()),
            download=download, sounds=sounds, check=True,
        ))
        if shape:
            fresh = get(project_id, conversation_id)
            current = fresh.version(number) if fresh else None
            _update(project, number, exports={**(current.exports if current else {}), shape: edit.edit_id})
        else:
            _update(project, number, status="rendering", edit_id=edit.edit_id)
    except (SourceError, ValueError) as error:
        if not shape:
            _update(project, number, status="failed", problem=str(error)[:300])
        logger.info("A video project's video couldn't be made: %s", error)
    except Exception:
        logger.warning("A video project's video couldn't be made", exc_info=True)
        if not shape:
            _update(project, number, status="failed", problem="Something went wrong while making the video.")


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
                found = resolve(value, self.conversation_id)
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


def view(project: Project, number: int | None = None) -> dict[str, Any]:
    """The client's view of a project at one version (the latest by default)."""
    from row_bot import video_edits

    version = project.version(number) or project.latest
    edit = video_edits.get(version.edit_id) if version.edit_id else None
    marks = list(edit.marks) if edit is not None and len(edit.marks) == len(version.scenes) + 1 else []
    refs = _Refs(project.conversation_id)
    return {
        "project_id": project.project_id,
        "conversation_id": project.conversation_id,
        "title": project.title,
        "latest": project.latest.number,
        "versions": [{"number": item.number, "at": _iso(item.at), "by": item.by if item.by in {"you", "row-bot"}
                      else "row-bot", "note": item.note} for item in reversed(project.versions)],
        "number": version.number,
        "scenes": [{"scene_id": scene.scene_id, "source": scene.source, "start": scene.start, "end": scene.end,
                    "speed": scene.speed, "zoom": scene.zoom, "card": scene.card, "line": scene.line,
                    "voiced": bool(scene.voice) and scene.voiced == scene.line,
                    "at": marks[index] if marks else None,
                    "length": round(marks[index + 1] - marks[index], 3) if marks else None,
                    "source_ref": refs.source(scene.source) if scene.source else None,
                    "voice_ref": refs.voice(scene.voice) if scene.voice and scene.voiced == scene.line else None}
                   for index, scene in enumerate(version.scenes)],
        "changed": changed_scenes(project, version),
        "music": version.music,
        "music_volume": version.music_volume,
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
        "exports": [{"shape": shape, "edit": video_edits.view(found)} for shape in EXPORTS
                    if (found := video_edits.get(version.exports.get(shape, ""))) is not None],
    }


def _status(version: Version, edit: Any) -> str:
    if version.status in {"voicing", "failed"}:
        return version.status
    if edit is None:
        return "rendering" if version.status == "rendering" and not version.edit_id else "draft"
    return {"saved": "made", "failed": "failed", "stopped": "stopped"}.get(edit.status, "rendering")


def summary(project: Project, number: int | None = None) -> str:
    """The tool result's first line, ``Video project vid-…: …`` (the chat's card follows it), then the scenes."""
    data = view(project, number)
    words = {"draft": "not made yet", "voicing": "voicing the lines", "rendering": "making the video",
             "made": "made", "failed": "not made", "stopped": "stopped"}[data["status"]]
    lines = [f"Video project {project.project_id}: \"{project.title}\", version {data['number']} of "
             f"{data['latest']}, {len(data['scenes'])} scenes, {words}."]
    if data["problem"]:
        lines.append(f"Why: {data['problem']}")
    for index, scene in enumerate(data["scenes"], start=1):
        span = f"{scene['start']:g}–{'end' if scene['end'] is None else format(scene['end'], 'g')} s"
        extras = [f"{scene['speed']:g}×" if scene["speed"] != 1 else "", "zoom" if scene["zoom"] else "",
                  f"card \"{scene['card']}\"" if scene["card"] else ""]
        extra = ", ".join(item for item in extras if item)
        line = f" — \"{scene['line']}\"" if scene["line"] else ""
        what = scene["source"] or "title card"
        lines.append(f"{index}. [{scene['scene_id']}] {what} {span}{', ' + extra if extra else ''}{line}")
    if data["narration"]:
        lines.append(f"Narration: {data['narration']}, said over the whole video (the lines above aren't said "
                     "one by one).")
    if data["edit"] and data["status"] == "made":
        lines.append(f"Video: {data['edit']['output_name']} ({data['edit']['edit_id']}) in this chat's files.")
    if data["checks"]:
        verdict = "passed" if data["checks_passed"] else "NOT passed: fix these before saying it's done"
        lines.append(f"Checks {verdict}:\n" + "\n".join(f"- {line}" for line in data["checks"]))
    return "\n".join(lines)
