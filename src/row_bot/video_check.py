"""The finished check (C8): what Row-Bot reads in every video it makes before it says the video is done.

Any video: a film of AI shots, a vlog, a montage, a tutorial or a short. The check is cheap next to the render:

- **One read of the finished video** by ffmpeg (shrunk first, so it's quick) measures its loudness (EBU R128:
  integrated LUFS and true peak), black, frozen and, under a voice, quiet stretches, and sudden jumps in the picture.
- **What the render knows**, with nothing decoded: how much larger than its footage each scene is shown (soft), the
  words laid over each moment against what was being done in a recording, and words in a tall or square video's
  safe area.
- **One frame of footage per scene**: faces under words (when OpenCV is installed), and AI shots meant to show one
  character or style drifting apart (a colour histogram each).
- **One look** at the review sheet by the person's own vision model, with a fixed list of what to look for.

Each finding is one line with its scene and time and what to do. Lines starting "Check" (the heuristics, and what the
vision model saw) are worth a look: they don't stop a video being ready. Everything else does, until it's fixed or the person says it's fine.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from row_bot.video_editing import (
    SAFE_AREAS, InputVideo, Look, Plan, SceneCut, Step, TextBox, VideoInfo, clock, fit_to_lines, input_arguments,
    shape_of,
)

logger = logging.getLogger(__name__)

# Shown more than this many times larger than its footage was made, a picture is worth a look (a camera's zoom on a
# recording, a wide clip in a tall export); more than BLURRED, it's plainly soft (480p at 1080p, 720p zoomed in).
SOFT, BLURRED = 1.5, 2.0
# Loudness (LUFS, integrated) a video plays well at, and the true peak (dBTP) it stays under so it never clips.
LOUDEST, QUIETEST, PEAK = -9.0, -23.0, -1.0
# A gap this long in a narrated video, or a picture held this long, is a pause a viewer notices; a narrated video's
# picture may hold while the voice speaks over it (a viewer reads along).
QUIET_GAP_SECONDS = 6.0
LONG_STILL_SECONDS = 10.0
NARRATED_STILL_SECONDS = 20.0
# A sudden jump in the picture this large (ffmpeg's scene score, 0-100) inside a scene, away from its cuts.
JUMP_SCORE = 12.0
# Words covering this share of what's being done, or of a face, for this long, hide it.
COVERED, COVERED_SECONDS = 0.25, 0.3
# Two shots of one character or style whose colours match less than this have drifted apart.
SAME_LOOK = 0.45
# Lines kept from the vision model's look.
MAX_LOOKS = 6
WORDS = {"caption": "caption", "title": "scene's title", "badge": "badge", "card": "card's words", "logo": "logo"}


@dataclass(frozen=True)
class Footage:
    """What the check knows of a scene's footage beyond its cut: what was done while it was recorded (its
    recording's action log) and the character or style it shows (shared with other scenes)."""

    actions: tuple[tuple[Any, ...], ...] = ()
    look: str = ""


@dataclass(frozen=True)
class Finding:
    line: str
    # False: a heuristic worth a look; it doesn't stop the video being ready.
    fix: bool = True


@dataclass(frozen=True)
class Checks:
    passed: bool
    lines: list[str]


# ── One read of the finished video ──────────────────────────────────────────


def measure_arguments(path: Path | str, *, has_audio: bool, narrated: bool = False) -> list[str]:
    """One ffmpeg read of a finished video: its picture shrunk for black, frozen and jumping stretches, and its
    sound for loudness and true peak (and, narrated, quiet stretches)."""
    args = ["-hide_banner", "-nostdin", *input_arguments(path),
            "-vf", "scale=480:-2:flags=fast_bilinear,blackdetect=d=0.5:pix_th=0.10,freezedetect=n=0.003:d=3,"
                   "scdet=threshold=10"]
    if has_audio:
        quiet = f",silencedetect=n=-40dB:d={QUIET_GAP_SECONDS:g}" if narrated else ""
        args += ["-af", f"ebur128=peak=true:framelog=verbose{quiet}"]
    return args + ["-f", "null", "-"]


@dataclass(frozen=True)
class Measures:
    """What the read found: loudness and true peak (None: nothing to hear), and the stretches."""

    loudness: float | None
    peak: float | None
    black: list[tuple[float, float]] = field(default_factory=list)
    frozen: list[tuple[float, float]] = field(default_factory=list)
    quiet: list[tuple[float, float]] = field(default_factory=list)
    jumps: list[tuple[float, float]] = field(default_factory=list)


_NUMBER = r"(-?[0-9.]+|-inf|nan)"
_INTEGRATED = re.compile(r"Integrated loudness:\s*I:\s*" + _NUMBER + r" LUFS")
_TRUE_PEAK = re.compile(r"True peak:\s*Peak:\s*" + _NUMBER + r" dBFS")
_BLACK = re.compile(r"black_start:\s*([0-9.]+)\s+black_end:\s*([0-9.]+)")
_FROZEN = re.compile(r"freeze_start:\s*([0-9.]+)[\s\S]*?freeze_duration:\s*([0-9.]+)")
_SILENCE_START = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([0-9.]+)")
_JUMP = re.compile(r"lavfi\.scd\.score:\s*([0-9.]+),\s*lavfi\.scd\.time:\s*([0-9.]+)")


def _level(text: str) -> float:
    return float("-inf") if text in {"-inf", "nan"} else float(text)


def read_measures(output: str, info: VideoInfo, *, narrated: bool = False) -> Measures:
    """ffmpeg's words from ``measure_arguments`` as numbers and stretches."""
    integrated, peak = _INTEGRATED.search(output), _TRUE_PEAK.search(output)
    heard = info.has_audio and integrated is not None
    quiet: list[tuple[float, float]] = []
    if narrated and heard:
        starts = [max(0.0, float(a)) for a in _SILENCE_START.findall(output)]
        ends = [float(b) for b in _SILENCE_END.findall(output)] + [info.duration] * len(starts)
        quiet = [(a, b) for a, b in zip(starts, ends, strict=False) if b - a >= QUIET_GAP_SECONDS]
    return Measures(
        loudness=_level(integrated.group(1)) if heard else None,
        peak=_level(peak.group(1)) if heard and peak else None,
        black=[(float(a), float(b)) for a, b in _BLACK.findall(output)],
        frozen=[(float(a), float(d)) for a, d in _FROZEN.findall(output)],
        quiet=quiet,
        jumps=[(float(at), float(score)) for score, at in _JUMP.findall(output)],
    )


# ── What the render knows ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Render:
    """A made video as its render planned it; for a project, its scenes as they were asked for (before the studio
    pass drew them), their inputs, what the check knows of each one's footage, and the folder they're read from."""

    plan: Plan
    inputs: list[InputVideo] = field(default_factory=list)
    step: Step | None = None
    footage: list[Footage] = field(default_factory=list)
    folder: Path | None = None


@dataclass(frozen=True)
class _Shot:
    """One scene with footage, as it shows on the finished video."""

    number: int
    begin: float
    end: float
    cut: SceneCut
    source: InputVideo
    keys: tuple[tuple[float, float, float, float], ...]
    frame: Any
    footage: Footage

    def at(self, moment: float) -> float:
        """Seconds into its footage at this moment of the finished video."""
        return self.cut.start + (moment - self.begin) * self.cut.speed

    def on_video(self, seconds: float) -> float:
        return self.begin + (seconds - self.cut.start) / self.cut.speed

    def where(self, box: tuple[float, float, float, float], seconds: float) -> tuple[float, float, float, float]:
        """A box on its footage (x, y, width, height, 0-1 across) where it shows in the finished frame, in pixels."""
        from row_bot.video_studio import placement, view

        picture = (self.source.info.width, self.source.info.height)
        (left, top, right, bottom), (x0, y0, x1, y1) = placement(picture, self.frame,
                                                                 view(self.keys, seconds - self.cut.start))
        across, down = (x1 - x0) / (right - left), (y1 - y0) / (bottom - top)
        x, y, width, height = box
        return (x0 + (x * picture[0] - left) * across, y0 + (y * picture[1] - top) * down,
                width * picture[0] * across, height * picture[1] * down)


def _shots(render: Render) -> list[_Shot]:
    """A project's scenes with footage, each as the studio pass frames it (``video_studio.prepare``)."""
    from dataclasses import replace

    from row_bot.video_studio import CAPTION_ROOM, Frame, zoom_keys

    plan, step = render.plan, render.step
    if step is None or step.op != "scenes" or len(plan.marks) != len(step.scenes) + 1:
        return []
    look = step.look or Look()
    size = (plan.width, plan.height)
    fill = Frame(size, look.framing if shape_of(*size) == "16:9" else "full", look.colour,
                 captions=CAPTION_ROOM if step.style == "caption" else 0.0)
    fitted = [scene for scene, _hold in fit_to_lines(step.scenes, [item.info.duration for item in render.inputs])]
    shots = []
    for index, scene in enumerate(fitted):
        if scene.input is None or not 0 <= scene.input < len(render.inputs):
            continue
        source = render.inputs[scene.input]
        if scene.zoom:
            keys = zoom_keys(scene.zoom)
        elif look.camera == "follow" and scene.camera:
            keys = scene.camera
        elif scene.focus and abs(source.info.width / max(1, source.info.height) - size[0] / size[1]) > 0.01:
            keys = ((0.0, scene.focus[0], scene.focus[1], 1.0),)
        else:
            keys = ()
        frame = {True: replace(fill, framing="framed"), False: replace(fill, framing="full")}.get(scene.fit, fill)
        footage = render.footage[index] if index < len(render.footage) else Footage()
        shots.append(_Shot(index + 1, plan.marks[index], plan.marks[index + 1], scene, source, keys, frame, footage))
    return shots


def _where(number: int | None, begin: float, end: float | None = None) -> str:
    span = clock(begin) if end is None or end - begin < 0.5 else f"{clock(begin)}–{clock(end)}"
    return f"Scene {number}, {span}" if number else span


def _scene_at(plan: Plan, moment: float) -> int | None:
    marks = plan.marks
    return next((index + 1 for index in range(len(marks) - 1) if marks[index] <= moment < marks[index + 1]), None)


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """How much of box *a* box *b* covers, 0-1."""
    width = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    height = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(0.0, width) * max(0.0, height) / max(1e-6, a[2] * a[3])


def _text_box(text: TextBox) -> tuple[float, float, float, float]:
    return float(text.x), float(text.y), float(text.width), float(text.height)


def _shown(text: TextBox, plan: Plan, begin: float, end: float) -> float:
    """How long these words show between two moments."""
    return min(end, plan.duration if text.end is None else text.end) - max(begin, text.start)


def _sharpness(render: Render, shots: list[_Shot]) -> list[Finding]:
    """Scenes shown so much larger than their footage was made that they look soft."""
    from row_bot.video_studio import placement

    found: list[Finding] = []
    if render.step is not None and render.step.op == "scenes":
        for shot in shots:
            info = shot.source.info
            zooms = [key[3] for key in shot.keys] or [1.0]
            tightest = shot.keys[zooms.index(max(zooms))][1:] if shot.keys else (0.5, 0.5, 1.0)
            (left, _top, right, _bottom), (x0, _y0, x1, _y1) = placement((info.width, info.height), shot.frame,
                                                                       tightest)
            larger = (x1 - x0) / max(1e-6, right - left)
            if larger > SOFT:
                zoomed = max(zooms) > 1.01
                # Cropped to fill a frame of another shape, it may be sharp shown whole (fit).
                whole = min(shot.frame.size[0] / info.width, shot.frame.size[1] / info.height) <= SOFT
                fix = (", or zoom in less" if zoomed else ", or show its whole picture (fit)"
                       if whole and shot.frame.framing == "full" else "")
                found.append(_soft(_where(shot.number, shot.begin, shot.end),
                                   f"Its {info.width}×{info.height} footage is shown {larger:.1f}× larger"
                                   f"{f', zoomed in {max(zooms):.1f}×' if zoomed else ''}. Use a sharper or larger "
                                   f"copy{fix}.", larger))
        return found
    for begin, end, larger in render.plan.enlarged:
        if larger > SOFT:
            found.append(_soft(_where(None, begin, end), f"It's shown {larger:.1f}× larger than it was made. Use a "
                               "sharper or larger copy, or zoom in less.", larger))
    return found


def _soft(where: str, words: str, larger: float) -> Finding:
    """Past BLURRED a fix; from SOFT to BLURRED a line worth a look, which doesn't hold the video back."""
    if larger > BLURRED:
        return Finding(f"{where}: soft. {words}")
    return Finding(f"Check {where[0].lower()}{where[1:]}: a little soft. {words}", fix=False)


def _over_the_action(plan: Plan, shots: list[_Shot]) -> list[Finding]:
    """Words laid over what was being done in a recording (a button clicked, a field typed into)."""
    found: dict[tuple[int, str], Finding] = {}
    for shot in shots:
        for action in shot.footage.actions:
            if len(action) < 7 or str(action[2]) in {"open", "scroll"} or float(action[5]) * float(action[6]) > 0.5:
                continue
            begin, until = max(float(action[0]), shot.cut.start), float(action[1])
            if shot.cut.end is not None:
                until = min(until, shot.cut.end)
            if until <= begin:
                continue
            on, off = shot.on_video(begin), min(shot.end, shot.on_video(until))
            if off - on < 0.05:
                continue
            box = shot.where(tuple(float(value) for value in action[3:7]), (begin + until) / 2)
            for text in plan.texts:
                if text.kind == "card" or (shot.number, text.kind) in found:
                    continue
                if _shown(text, plan, on, off) >= min(COVERED_SECONDS, off - on) and \
                        _overlap(box, _text_box(text)) >= COVERED:
                    note = f" ({str(action[7])[:40]})" if len(action) > 7 and action[7] else ""
                    found[(shot.number, text.kind)] = Finding(
                        f"{_where(shot.number, max(on, text.start))}: the {WORDS[text.kind]} covers what's being "
                        f"done{note}. Frame the scene so it shows higher (its zoom), or move or shorten the words.")
    return list(found.values())


def _safe_area(plan: Plan) -> list[Finding]:
    """Words a phone app or a feed would cover on a tall or square video."""
    shape = shape_of(plan.width, plan.height)
    if shape not in SAFE_AREAS:
        return []
    left, top, right, bottom = SAFE_AREAS[shape]
    width, height = plan.width, plan.height
    found: dict[tuple[int | None, str], Finding] = {}
    for text in plan.texts:
        edges = [name for name, outside in (
            ("left", text.x < left * width - 1), ("top", text.y < top * height - 1),
            ("right", text.x + text.width > width * (1 - right) + 1),
            ("bottom", text.y + text.height > height * (1 - bottom) + 1)) if outside]
        number = _scene_at(plan, text.start)
        if edges and (number, text.kind) not in found:
            found[(number, text.kind)] = Finding(
                f"{_where(number, text.start)}: the {WORDS[text.kind]} reaches into the {shape} video's "
                f"{' and '.join(edges)} edge, where apps lay their own buttons and captions. Shorten it.")
    return list(found.values())


def _jumps(plan: Plan, shots: list[_Shot], measures: Measures) -> list[Finding]:
    """Sudden jumps in the picture inside a scene, away from its cuts: a jump cut nobody meant. A recording's
    pages change at once, so its scenes aren't read."""
    found: list[Finding] = []
    for moment, score in measures.jumps:
        if score < JUMP_SCORE or any(abs(moment - mark) < 0.3 for mark in plan.marks):
            continue
        shot = next((item for item in shots if item.begin < moment < item.end), None)
        if shot is None or shot.footage.actions or any(item.line.startswith(f"Check scene {shot.number},")
                                                       for item in found):
            continue
        found.append(Finding(f"Check scene {shot.number}, {clock(moment)}: the picture jumps there, with no cut "
                             "meant (a jump cut). If it isn't wanted, start or end the scene away from it.", fix=False))
    return found[:3]


def _key_frames(render: Render, shots: list[_Shot]) -> dict[int, tuple[float, Any]]:
    """One frame of each scene's footage, read from its video (seconds into it, the picture): where words first show
    over it, else its middle. Nothing when PyAV can't read them."""
    try:
        import av
    except ImportError:
        return {}
    from row_bot.video_editing import INPUT_FORMATS

    plan = render.plan
    wanted: dict[str, list[tuple[float, int]]] = {}
    for shot in shots:
        starts = [text.start for text in plan.texts if text.kind not in {"card", "logo"}
                  and shot.begin <= text.start < shot.end]
        moment = min(shot.end - 0.05, min(starts) + 1.0) if starts else (shot.begin + shot.end) / 2
        seconds = min(max(0.0, shot.at(moment)), max(0.0, shot.source.info.duration - 0.05))
        path = Path(shot.source.path)
        if not path.is_absolute() and render.folder is not None:
            path = render.folder / path
        wanted.setdefault(str(path), []).append((seconds, shot.number))
    frames: dict[int, tuple[float, Any]] = {}
    for path, moments in wanted.items():
        try:
            with av.open(path, options={"protocol_whitelist": "file", "format_whitelist": INPUT_FORMATS}) as reader:
                video = reader.streams.video[0]
                video.thread_type = "AUTO"
                if not video.time_base:
                    continue
                for seconds, number in sorted(moments):
                    reader.seek(int(seconds / video.time_base), stream=video, backward=True)
                    for decoded in reader.decode(video):
                        if decoded.time is None or decoded.time >= seconds - 0.05:
                            picture = decoded.to_image()
                            picture.thumbnail((640, 640))
                            frames[number] = (seconds, picture)
                            break
        except (av.FFmpegError, OSError, IndexError, ValueError):
            logger.info("A scene's footage couldn't be read for the check", exc_info=True)
    return frames


@lru_cache(maxsize=1)
def _face_finder() -> Any:
    """OpenCV's frontal face finder when OpenCV is installed (the media extras), else None."""
    try:
        import cv2
    except Exception:  # OpenCV can raise OSError for missing native libraries.
        return None
    # On the CPU: a graphics driver's OpenCL, once started from a background thread, can crash the app as it quits.
    cv2.ocl.setUseOpenCL(False)
    finder = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))
    return None if finder.empty() else finder


def faces(picture: Any) -> list[tuple[float, float, float, float]]:
    """The faces in a picture (x, y, width, height, 0-1 across); none without OpenCV."""
    finder = _face_finder()
    if finder is None:
        return []
    import cv2
    import numpy

    grey = numpy.asarray(picture.convert("L"))
    side = max(24, grey.shape[0] // 12)
    try:
        found = finder.detectMultiScale(grey, scaleFactor=1.1, minNeighbors=6, minSize=(side, side))
    except cv2.error:
        logger.info("Faces couldn't be looked for in a scene's frame", exc_info=True)
        return []
    return [(x / grey.shape[1], y / grey.shape[0], w / grey.shape[1], h / grey.shape[0]) for x, y, w, h in found]


def _over_faces(plan: Plan, shots: list[_Shot], frames: dict[int, tuple[float, Any]]) -> list[Finding]:
    """Words over a face, in footage that isn't a recording (a recording says what was done instead)."""
    found: list[Finding] = []
    for shot in shots:
        if shot.footage.actions or shot.number not in frames:
            continue
        seconds, picture = frames[shot.number]
        moment = shot.on_video(seconds)
        boxes = [shot.where(face, seconds) for face in faces(picture)]
        for text in plan.texts:
            if text.kind == "card" or not text.start <= moment <= (plan.duration if text.end is None else text.end):
                continue
            if any(_overlap(box, _text_box(text)) >= COVERED for box in boxes):
                found.append(Finding(f"{_where(shot.number, moment)}: the {WORDS[text.kind]} covers a face. Frame the "
                                     "scene so the face shows higher (its zoom), or move or shorten the words."))
                break
    return found


def _colours(picture: Any) -> Any:
    import numpy

    hsv = numpy.asarray(picture.convert("RGB").resize((64, 36)).convert("HSV")).reshape(-1, 3)
    counts, _edges = numpy.histogramdd(hsv, bins=(8, 3, 3), range=((0, 256), (0, 256), (0, 256)))
    return counts.ravel() / max(1, hsv.shape[0])


def _continuity(shots: list[_Shot], frames: dict[int, tuple[float, Any]]) -> list[Finding]:
    """Scenes meant to show one character or style (their footage shares it) whose colours drifted apart."""
    found: list[Finding] = []
    first: dict[str, tuple[int, Any]] = {}
    for shot in shots:
        if not shot.footage.look or shot.number not in frames:
            continue
        colours = _colours(frames[shot.number][1])
        if shot.footage.look not in first:
            first[shot.footage.look] = (shot.number, colours)
            continue
        number, earlier = first[shot.footage.look]
        if float(sum(min(a, b) for a, b in zip(earlier, colours, strict=True))) < SAME_LOOK:
            found.append(Finding(f"Check scene {shot.number}, {clock(shot.begin)}: it may not look like scene {number} "
                                 f"any more ({shot.footage.look}): its colours have drifted. If the character or style "
                                 "changed, make that shot again from the same references.", fix=False))
    return found[:3]


def findings(render: Render, measures: Measures) -> list[Finding]:
    """What the render and one frame per scene show that a good video doesn't."""
    plan = render.plan
    shots = _shots(render)
    found = _sharpness(render, shots) + _over_the_action(plan, shots) + _safe_area(plan)
    frames = _key_frames(render, shots) if shots else {}
    return found + _over_faces(plan, shots, frames) + _jumps(plan, shots, measures) + _continuity(shots, frames)


# ── One look by the person's vision model ───────────────────────────────────

RUBRIC = (
    "Look at it as a viewer would, and check only these:\n"
    "1. Is every word readable, and inside the frame?\n"
    "2. Does each zoom or highlight land on something worth seeing?\n"
    "3. Is anything important cut off at an edge?\n"
    "4. Does each scene's picture fit what's said over it?\n"
    "5. Any visible AI artefacts: warped hands or faces, melting objects, garbled text?\n"
    "Reply with one line for each clear problem a viewer would notice, starting with the label under its frame, "
    "for example \"scene 3 · 0:12: the caption runs off the right edge\". At most 6 lines. If there's none, reply "
    "OK. Don't describe what's fine."
)
_LABELLED = re.compile(r"(scene\s+\d+|\d+:\d{2})", re.IGNORECASE)


def look(sheet: bytes, lines: list[str] | None = None) -> list[Finding]:
    """One look at a made video's review sheet by the person's own vision model, with a fixed list of what to look
    for: each clear problem it sees, or why it couldn't look."""
    from row_bot.vision_runtime import get_vision_service

    service = get_vision_service()
    problem = service.cannot_see()
    if problem:
        return [Finding(f"Look: not done. {problem}", fix=False)]
    said = "\n".join(f"scene {number}: {line[:160]}" for number, line in enumerate(lines or [], start=1) if line)
    question = ("This is the review sheet of a finished video: frames of it in reading order, each labelled under "
                "it with its scene and time." + (f"\nWhat each scene says:\n{said}" if said else "") + "\n\n" + RUBRIC)
    try:
        answer = str(service.analyze(sheet, question) or "").strip()
    except Exception as error:  # A provider's own failure: the rest of the check still stands.
        logger.info("The vision model couldn't look at a review sheet", exc_info=True)
        answer = f"Vision analysis failed: {type(error).__name__}"
    if answer.startswith("Vision analysis failed") or not answer:
        return [Finding(f"Look: the vision model couldn't look at the review sheet ({answer[:160] or 'no answer'}). "
                        "Tell the person the frames weren't looked at, so they can open the review sheet and check by "
                        "eye.", fix=False)]
    seen = []
    for raw in answer.splitlines():
        text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", raw).strip().strip("*")
        if _LABELLED.match(text):
            # A fresh look each make can see something new: worth a look, never a reason to hold the video back.
            seen.append(Finding(f"Check {text[0].lower()}{text[1:200]}", fix=False))
    if seen:
        return seen[:MAX_LOOKS]
    if answer.upper().rstrip(".!").startswith("OK"):
        return [Finding("Look: the vision model saw nothing wrong on the review sheet.", fix=False)]
    return [Finding(f"Check the review sheet: the vision model answered \"{' '.join(answer.split())[:200]}\".",
                    fix=False)]


# ── The lines ───────────────────────────────────────────────────────────────


def _sound(info: VideoInfo, measures: Measures) -> Finding | None:
    """The sound line: None when it's fine."""
    loudness, peak = measures.loudness, measures.peak
    if loudness is None:
        return Finding("Sound: none. Add a voice or music if this video should have sound.")
    if loudness < QUIETEST:
        level = "silent" if loudness == float("-inf") or loudness <= -70 else f"{loudness:.1f} LUFS"
        return Finding(f"Sound: too quiet ({level}; keep it between {QUIETEST:g} and {LOUDEST:g} LUFS, -14 suits "
                       "most videos). Turn the voice or music up.")
    if loudness > LOUDEST:
        return Finding(f"Sound: too loud ({loudness:.1f} LUFS; keep it between {QUIETEST:g} and {LOUDEST:g} LUFS). "
                       "Turn it down.")
    if peak is not None and peak > PEAK:
        return Finding(f"Sound: its peaks reach {peak:.1f} dBTP, enough to crackle on phones (keep them under "
                       f"{PEAK:g}). Turn it down a little.")
    return None


def checks(info: VideoInfo, measures: Measures, found: list[Finding], *, narrated: bool = False,
           review: list[str] | None = None) -> Checks:
    """The check's lines: what needs fixing (with scenes and times), what passed, then what's worth a look. A video
    passes when nothing needs fixing."""
    lines = [f"Length {clock(info.duration)}, {info.width}×{info.height}."]
    sound = _sound(info, measures)
    fixes = [sound] if sound is not None else []
    # A fade at the very start or end isn't a problem.
    inside = [(a, b) for a, b in measures.black if not (a <= 1.0 and b - a <= 1.5)
              and not (b >= info.duration - 1.0 and b - a <= 1.5)]
    if inside:
        fixes.append(Finding("Black: " + ", ".join(f"{clock(a)}–{clock(b)}" for a, b in inside[:5])
                             + ". Cut it, or put a picture there."))
    fixes += list(found) + [Finding(line) for line in review or []]
    if narrated and measures.quiet:
        fixes.append(Finding("Quiet: " + ", ".join(f"{clock(a)}–{clock(b)}" for a, b in measures.quiet[:5])
                             + " with no voice. Give those parts a line, or cut them shorter."))
    limit = NARRATED_STILL_SECONDS if narrated and info.has_audio else LONG_STILL_SECONDS
    held = [(a, d) for a, d in measures.frozen if d >= limit]
    if held:
        fixes.append(Finding("Still: " + ", ".join(f"{clock(a)} for {d:.0f} s" for a, d in held[:5])
                             + ": too long to watch. Cut those parts shorter or speed them up."))
    lines += [item.line for item in fixes if item.fix]
    if sound is None:
        lines.append(f"Sound: {measures.loudness:.1f} LUFS, peaks {measures.peak:.1f} dBTP." if measures.peak is not None
                     and measures.peak != float("-inf") else f"Sound: {measures.loudness:.1f} LUFS.")
    if not inside:
        lines.append("Black: none.")
    shorter = [(a, d) for a, d in measures.frozen if d < limit]
    if not held:
        lines.append("Still: " + (", ".join(f"{clock(a)} for {d:.0f} s" for a, d in shorter[:5])
                                  + " (fine if nothing should move there)." if shorter else "none."))
    lines += [item.line for item in fixes if not item.fix]
    passed = not any(item.fix for item in fixes)
    lines.append("Result: passed." if passed else "Result: needs attention before it's done.")
    return Checks(passed, lines)
