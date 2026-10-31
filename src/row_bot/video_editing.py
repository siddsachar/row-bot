"""Building ffmpeg commands for video edits (F3): trim, join, speed, text, zoom, crop, captions, frames;
voice and music laid on the finished timeline, and the checks a video passes before it's done (5.3.1 B).

Everything here is pure: it reads what ffmpeg says about a video (``probe``) and turns the
agent's steps into one ffmpeg command and one filter graph, so a whole edit is one encode to
H.264 MP4. The steps apply in order; each step's times are seconds on the video as it is
after the steps before it.

Text never goes into the filter graph. Badges, titles, speed labels and captions are drawn
with Pillow into small transparent pictures that ffmpeg lays over the video at the right
times, so no font or escaping rules of a particular ffmpeg build matter (the Linux builds have
no ``drawtext`` at all), and nothing the model writes can change the graph. Every input file is
opened through ffmpeg's ``file:`` protocol only, with the network protocols refused.
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

Op = Literal["trim", "speed", "text", "zoom", "crop", "captions", "sound"]
OPS: tuple[str, ...] = ("trim", "speed", "text", "zoom", "crop", "captions", "sound")
# sound: a voice line or music laid on the finished video, or the video's own sound turned up or down.
ROLES = ("voice", "music", "original")
MAX_SOUNDS = 40
MAX_VOLUME = 4.0
# Loudness the finished mix is brought to: speech-forward, as video platforms play it.
LOUDNESS = "loudnorm=I=-16:TP=-1.5:LRA=11"
POSITIONS = ("top-left", "top", "top-right", "center", "bottom-left", "bottom", "bottom-right")
STYLES = ("badge", "title", "caption")
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".gif"})

MAX_INPUTS = 10
MAX_STEPS = 24
MAX_CAPTIONS = 500
MAX_TEXT_CHARS = 200
MAX_CAPTION_CHARS = 300
MIN_SPEED, MAX_SPEED = 0.25, 16.0
MAX_FRAMES = 48
# Containers a person's video may be read as: never playlists or other demuxers that open more files.
INPUT_FORMATS = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi,mpegts,flv,gif,ogg,asf"
# A voice line or music: the same, plus plain audio files.
SOUND_FORMATS = INPUT_FORMATS + ",wav,mp3,flac,aac"


class EditError(ValueError):
    """The edit can't be made as asked; the message says why in plain words."""


# ── What ffmpeg says about a video ──────────────────────────────────────────


@dataclass(frozen=True)
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


_DURATION = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_VIDEO = re.compile(r"Stream #\S+.*?: Video: (.*)")
_SIZE = re.compile(r"\b(\d{2,5})x(\d{2,5})\b")
_FPS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:fps|tbr)\b")
_ROTATION = re.compile(r"rotation of (-?\d+(?:\.\d+)?) degrees")


def parse_probe(text: str) -> VideoInfo:
    """Read ``ffmpeg -i file``'s description of a video: its length, picture size, frame rate and sound."""
    duration_match = _DURATION.search(text or "")
    video = _VIDEO.search(text or "")
    if not video:
        raise EditError("That file has no video in it.")
    size = _SIZE.search(video.group(1))
    if not size:
        raise EditError("Row-Bot couldn't read that video's picture size.")
    width, height = int(size.group(1)), int(size.group(2))
    rotation = _ROTATION.search(text or "")
    if rotation and round(abs(float(rotation.group(1)))) % 180 == 90:
        width, height = height, width  # ffmpeg turns it upright when it decodes it.
    fps_match = _FPS.search(video.group(1))
    fps = float(fps_match.group(1)) if fps_match else 30.0
    if not duration_match:
        raise EditError("Row-Bot couldn't tell how long that video is.")
    hours, minutes, seconds = duration_match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if duration <= 0:
        raise EditError("That video is empty.")
    has_audio = bool(re.search(r"Stream #\S+.*?: Audio: ", text or ""))
    return VideoInfo(duration=duration, width=width, height=height, fps=max(1.0, min(fps, 120.0)),
                     has_audio=has_audio)


def file_argument(path: Path | str) -> str:
    """An input or output file for ffmpeg, so a name can never be read as another protocol."""
    return f"file:{path}"


def input_arguments(path: Path | str) -> list[str]:
    """Open a person's video: local files only, as a video container (never a playlist)."""
    return ["-protocol_whitelist", "file", "-format_whitelist", INPUT_FORMATS, "-i", file_argument(path)]


def sound_input_arguments(path: Path | str) -> list[str]:
    """Open a voice line or music file: local files only, as an audio or video container."""
    return ["-protocol_whitelist", "file", "-format_whitelist", SOUND_FORMATS, "-i", file_argument(path)]


def probe_command(path: Path | str) -> list[str]:
    return ["-hide_banner", "-nostdin", *input_arguments(path)]


# ── Steps ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Step:
    op: str
    start: float | None = None
    end: float | None = None
    factor: float | None = None
    label: bool = True
    text: str = ""
    position: str = ""
    style: str = ""
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None
    # sound: the file (as the agent named it), its role, its loudness and whether music ducks under voices.
    source: str = ""
    role: str = ""
    volume: float | None = None
    duck: bool = True
    # scenes (a video project's, 5.3.1 B3; never the agent's own step): the pieces, in order, and the shape
    # the video is reframed to ("16:9", "1:1", "9:16"; "" keeps the first input's).
    scenes: list["SceneCut"] = field(default_factory=list)
    aspect: str = ""
    # scenes: the brand kit's look (cards, badges, captions, intro, outro, logo); text: a card's fill colour
    # and whether it's drawn without its box (a bold caption, or a title on a coloured card).
    look: "Look | None" = None
    fill: tuple[int, int, int, int] | None = None
    plain: bool = False


@dataclass(frozen=True)
class SceneCut:
    """One scene of a video project: a piece of an input, its speed, a zoom, a card, its line and voice."""

    # None: a title card, its card on the brand's colour for end - start seconds (no video needed).
    input: int | None
    start: float = 0.0
    end: float | None = None
    speed: float = 1.0
    zoom: tuple[float, float, float, float] | None = None
    card: str = ""
    line: str = ""
    voice: str = ""
    # Where the action is when there's no zoom (the recording's pointer, 0–1 across): a square or tall cut
    # is centred there, so what's being used stays in frame.
    focus: tuple[float, float] | None = None


CARD_SECONDS = 3.0
BOOKEND_SECONDS = 2.0


@dataclass(frozen=True)
class Look:
    """The brand kit as the renderer uses it (5.3.1 B3)."""

    colour: tuple[int, int, int] = (31, 41, 55)
    # How lines show under their scenes: "box" (a dark box), "bold" (large, outlined) or "title" (centred).
    captions: str = "box"
    logo: bytes | None = None
    logo_corner: str = "top-left"
    intro: str = ""
    outro: str = ""
ASPECTS = {"16:9": 16 / 9, "1:1": 1.0, "9:16": 9 / 16}
# A video of title cards only has no picture to take its size from.
CARD_SIZES = {"16:9": (1920, 1080), "1:1": (1080, 1080), "9:16": (1080, 1920)}


def _number(value: Any, name: str) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise EditError(f"{name} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise EditError(f"{name} must be a number.") from None
    if not math.isfinite(number):
        raise EditError(f"{name} must be a number.")
    return number


def _clean_text(value: Any, limit: int) -> str:
    text = "".join(character if character == "\n" or ord(character) >= 32 else " " for character in str(value or ""))
    text = "\n".join(" ".join(line.split()) for line in text.strip().splitlines())
    return text[:limit]


def parse_steps(raw: Iterable[Any] | None) -> list[Step]:
    """The agent's steps, checked: each names a known operation with sensible values."""
    steps: list[Step] = []
    for index, item in enumerate(list(raw or []), start=1):
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, dict):
            raise EditError(f"Step {index} isn't an operation.")
        op = str(item.get("op") or "").strip().casefold()
        if op not in OPS:
            raise EditError(f"Step {index}: {op or 'nothing'} isn't an operation; use one of {', '.join(OPS)}.")
        start = _number(item.get("start"), f"Step {index}'s start")
        end = _number(item.get("end"), f"Step {index}'s end")
        if start is not None and start < 0:
            raise EditError(f"Step {index}: start can't be before 0 seconds.")
        if start is not None and end is not None and end <= start:
            raise EditError(f"Step {index}: end must be after start.")
        factor = _number(item.get("factor"), f"Step {index}'s factor")
        given = item.get("region")
        if given is not None and (not isinstance(given, (list, tuple)) or len(given) != 4):
            raise EditError(f"Step {index}: region is [x, y, width, height], as fractions of the picture (0 to 1).")
        values = dict(zip(("x", "y", "width", "height"), given)) if given is not None else item
        region = {name: _number(values.get(name), f"Step {index}'s {name}") for name in ("x", "y", "width", "height")}
        position = str(item.get("position") or "").strip().casefold()
        style = str(item.get("style") or "").strip().casefold()
        if position and position not in POSITIONS:
            raise EditError(f"Step {index}: position must be one of {', '.join(POSITIONS)}.")
        if style and style not in STYLES:
            raise EditError(f"Step {index}: style must be one of {', '.join(STYLES)}.")
        text = _clean_text(item.get("text"), 200_000 if op == "captions" else MAX_TEXT_CHARS)
        if op == "speed":
            if factor is None or not MIN_SPEED <= factor <= MAX_SPEED or factor == 1:
                raise EditError(f"Step {index}: speed needs a factor from {MIN_SPEED:g} to {MAX_SPEED:g} "
                                "(2 is twice as fast, 0.5 half speed).")
        if op == "text" and not text:
            raise EditError(f"Step {index}: text needs the words to show.")
        if op == "captions" and not text:
            raise EditError(f"Step {index}: captions need the captions as SRT text.")
        if op in {"zoom", "crop"}:
            if any(region[name] is None for name in ("x", "y", "width", "height")):
                raise EditError(f"Step {index}: {op} needs a region: [x, y, width, height], as fractions of the "
                                "picture (0 to 1).")
            x, y, width, height = (region["x"], region["y"], region["width"], region["height"])
            if not (0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1 and 0 < height <= 1):  # type: ignore[operator]
                raise EditError(f"Step {index}: a region's x, y, width and height are fractions of the picture, "
                                "from 0 to 1.")
            if op == "crop" and (start is not None or end is not None):
                raise EditError(f"Step {index}: crop applies to the whole video; use zoom for part of it.")
        label = item.get("label")
        sound: dict[str, Any] = {}
        if op == "sound":
            role = str(item.get("role") or "voice").strip().casefold()
            if role not in ROLES:
                raise EditError(f"Step {index}: a sound's role is voice, music or original (the video's own sound).")
            source = _clean_text(item.get("source"), 300)
            if role != "original" and not source:
                raise EditError(f"Step {index}: sound needs its source: a voice or music file by name.")
            volume = _number(item.get("volume"), f"Step {index}'s volume")
            if volume is not None and not 0 <= volume <= MAX_VOLUME:
                raise EditError(f"Step {index}: volume is from 0 (silent) to {MAX_VOLUME:g} (1 keeps it as it is).")
            duck = item.get("duck")
            sound = {"source": source if role != "original" else "", "role": role, "volume": volume,
                     "duck": True if duck is None else bool(duck)}
        steps.append(Step(op=op, start=start, end=end, factor=factor, label=True if label is None else bool(label),
                          text=text, position=position, style=style, **region, **sound))
    if len([step for step in steps if step.op != "sound"]) > MAX_STEPS:
        raise EditError(f"At most {MAX_STEPS} steps fit in one edit.")
    if len([step for step in steps if step.op == "sound"]) > MAX_SOUNDS:
        raise EditError(f"At most {MAX_SOUNDS} sounds fit in one edit: join voice lines into fewer files.")
    return steps


def sound_sources(steps: list[Step]) -> list[str]:
    """The sound files an edit lays, each once, in the order the steps name them."""
    names = [step.source for step in steps if step.op == "sound" and step.source]
    names += [scene.voice for step in steps if step.op == "scenes" for scene in step.scenes if scene.voice]
    return list(dict.fromkeys(names))


def speed_words(factor: float) -> str:
    """``2×``, ``0.5×``, ``1.5×``: a speed as the label on the video shows it."""
    return f"{factor:g}×"


def describe(steps: list[Step], inputs: int = 1) -> str:
    """What an edit does, in a few plain words: "Joined 3 videos, trimmed, sped up 2× and captioned"."""
    words: list[str] = []
    if inputs > 1:
        words.append(f"joined {inputs} videos")
    for step in steps:
        word = {
            "trim": "trimmed",
            "speed": f"sped up {speed_words(step.factor or 1)}" if (step.factor or 1) > 1
            else f"slowed to {speed_words(step.factor or 1)}",
            "text": "added a badge" if (step.style or "badge") == "badge" else "added a title"
            if step.style == "title" else "added a caption",
            "zoom": "zoomed in",
            "crop": "cropped",
            "captions": "captioned",
            "sound": {"voice": "added a voice", "music": "added music"}.get(step.role, "set the sound level"),
            "scenes": f"cut {len(step.scenes)} scene{'s' if len(step.scenes) != 1 else ''}",
        }[step.op]
        if word not in words:
            words.append(word)
    if not words:
        return "Converted to MP4"
    text = words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]
    return text[0].upper() + text[1:]


# ── Captions (SRT) ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


_CUE_TIME = re.compile(
    r"(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{1,3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{1,3})")
_TAGS = re.compile(r"<[^>]{0,40}>|\{\\[^}]{0,40}\}")


def _seconds(hours: str | None, minutes: str, seconds: str, millis: str) -> float:
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, "0")) / 1000


def parse_srt(text: str) -> list[Cue]:
    """Captions from SRT (WebVTT-style times read too); styling tags are dropped."""
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()):
        lines = [line for line in block.split("\n") if line.strip()]
        for index, line in enumerate(lines):
            match = _CUE_TIME.search(line)
            if not match:
                continue
            groups = match.groups()
            start, end = _seconds(*groups[:4]), _seconds(*groups[4:])
            words = _clean_text(_TAGS.sub("", "\n".join(lines[index + 1:])), MAX_CAPTION_CHARS)
            if end > start and words:
                cues.append(Cue(start=start, end=end, text=words))
            break
    if not cues:
        raise EditError("No captions were found: write them as SRT, for example\n"
                        "1\n00:00:01,000 --> 00:00:04,000\nHello")
    if len(cues) > MAX_CAPTIONS:
        raise EditError(f"At most {MAX_CAPTIONS} captions fit in one edit.")
    return sorted(cues, key=lambda cue: cue.start)


# ── Drawing text ────────────────────────────────────────────────────────────

_FONTS = (
    "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)
_FONT_CACHE: dict[int, Any] = {}


def font(size: int) -> Any:
    """A bold sans-serif font of this size: the system's, else Pillow's own."""
    from PIL import ImageFont

    size = max(8, int(size))
    if size not in _FONT_CACHE:
        chosen = None
        for candidate in _FONTS:
            try:
                if Path(candidate).is_file():
                    chosen = ImageFont.truetype(candidate, size)
                    break
            except OSError:
                continue
        _FONT_CACHE[size] = chosen or ImageFont.load_default(size=size)
    return _FONT_CACHE[size]


def _wrap(draw: Any, text: str, typeface: Any, max_width: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            trial = f"{current} {word}".strip()
            if not current or draw.textlength(trial, font=typeface) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" .,;:") + "…"
    return [line for line in lines if line]


@dataclass(frozen=True)
class Overlay:
    """A transparent picture laid over the video, where and when."""

    png: bytes
    width: int
    height: int
    x: int
    y: int
    start: float | None = None
    end: float | None = None


_STYLE_SIZES = {"badge": 0.05, "title": 0.08, "caption": 0.052}


def render_text(text: str, style: str, frame_width: int, frame_height: int, *,
                fill: tuple[int, int, int, int] | None = None, plain: bool = False,
                shadow: bool = False) -> tuple[bytes, int, int]:
    """Draw text as a picture: a rounded dark badge, a large title, or a caption box; in a brand's colour
    (``fill``), or ``plain``: no box, the letters outlined so they read on any picture, or with a soft
    ``shadow`` instead (a title card's own backdrop)."""
    from PIL import Image, ImageDraw, ImageFilter

    style = style or "badge"
    # Sized as on a 16:9 picture of this width, so a square or tall cut fits the same words on a line.
    reference = min(frame_height, frame_width * 9 / 16)
    size = max(12, round(reference * _STYLE_SIZES[style] * (1.3 if plain and style == "caption" else 1)))
    typeface = font(size)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    max_width = int(frame_width * (0.86 if style != "badge" else 0.6))
    lines = _wrap(probe, text, typeface, max_width, 3 if style != "badge" else 2) or [" "]
    pad_x, pad_y = round(size * 0.7), round(size * 0.42)
    line_height = round(size * 1.22)
    text_width = max(math.ceil(probe.textlength(line, font=typeface)) for line in lines)
    width = min(frame_width, text_width + 2 * pad_x)
    height = min(frame_height, line_height * len(lines) + 2 * pad_y)
    picture = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(picture)
    radius = height // 2 if style == "badge" and len(lines) == 1 else round(size * 0.45)
    if not plain:
        box = fill or ((17, 24, 39, 214) if style != "caption" else (0, 0, 0, 178))
        draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=box)
    outline = max(2, round(size * 0.08)) if plain and not shadow else 0
    places = []
    for index, line in enumerate(lines):
        line_width = probe.textlength(line, font=typeface)
        places.append(((width - line_width) / 2, pad_y + index * line_height + (line_height - size) / 2 - size * 0.1))
    if shadow:
        soft = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shade = ImageDraw.Draw(soft)
        for line, (left, top) in zip(lines, places, strict=True):
            shade.text((left, top + size * 0.06), line, font=typeface, fill=(0, 0, 0, 150))
        picture.alpha_composite(soft.filter(ImageFilter.GaussianBlur(max(2, size * 0.1))))
    for line, (left, top) in zip(lines, places, strict=True):
        draw.text((left, top), line, font=typeface, fill=(255, 255, 255, 255), stroke_width=outline,
                  stroke_fill=(0, 0, 0, 230))
    output = io.BytesIO()
    picture.save(output, format="PNG", optimize=True)
    return output.getvalue(), width, height


def card_backdrop(colour: tuple[int, int, int], width: int, height: int) -> bytes:
    """A title card's backdrop: its colour lit softly from the top left, darker towards the edges, so a card
    reads as a designed slide rather than a flat fill."""
    from PIL import Image

    small_width, small_height = 64, 36
    picture = Image.new("RGB", (small_width, small_height))
    pixels = picture.load()
    for y in range(small_height):
        for x in range(small_width):
            across = 0.55 * x / (small_width - 1) + 0.45 * y / (small_height - 1)
            edge = math.hypot(x / (small_width - 1) - 0.5, y / (small_height - 1) - 0.5) / math.hypot(0.5, 0.5)
            light = 1.18 - 0.36 * across - 0.14 * edge * edge
            pixels[x, y] = tuple(max(0, min(255, round(value * light + 10 * (1 - across)))) for value in colour)
    output = io.BytesIO()
    picture.resize((width, height), Image.Resampling.BICUBIC).save(output, format="PNG", optimize=True)
    return output.getvalue()


def place(position: str, width: int, height: int, frame_width: int, frame_height: int) -> tuple[int, int]:
    """Where a picture of this size goes for a position preset, with a margin from the edges."""
    margin = round(frame_height * 0.045)
    vertical, _, horizontal = (position or "top-right").partition("-")
    if position in {"top", "bottom", "center"}:
        vertical, horizontal = position, "center"
    x = {"left": margin, "right": frame_width - width - margin}.get(horizontal, (frame_width - width) // 2)
    y = {"top": margin, "bottom": frame_height - height - margin}.get(vertical, (frame_height - height) // 2)
    return max(0, x), max(0, y)


def text_overlay(step: Step, frame_width: int, frame_height: int) -> Overlay:
    style = step.style or "badge"
    png, width, height = render_text(step.text, style, frame_width, frame_height, fill=step.fill, plain=step.plain)
    default = {"badge": "top-right", "title": "center", "caption": "bottom"}[style]
    x, y = place(step.position or default, width, height, frame_width, frame_height)
    return Overlay(png, width, height, x, y, step.start, step.end)


def logo_overlay(png: bytes, corner: str, frame_width: int, frame_height: int) -> Overlay:
    """A brand's logo, a tenth of the picture's height, in its corner for the whole video."""
    from PIL import Image

    picture = Image.open(io.BytesIO(png)).convert("RGBA")
    height = max(16, round(frame_height * 0.1))
    width = max(16, round(picture.width * height / max(1, picture.height)))
    if width > frame_width // 3:
        width, height = frame_width // 3, max(16, round(picture.height * (frame_width // 3) / max(1, picture.width)))
    output = io.BytesIO()
    picture.resize((width, height), Image.LANCZOS).save(output, format="PNG", optimize=True)
    x, y = place(corner, width, height, frame_width, frame_height)
    return Overlay(output.getvalue(), width, height, x, y, None, None)


# ── The plan ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InputVideo:
    """A video to edit: the file ffmpeg reads (relative to the working folder, or absolute) and what it holds."""

    path: str
    info: VideoInfo
    label: str = ""


@dataclass
class Plan:
    """One ffmpeg run: its arguments (after the program), its filter graph and the pictures it lays over."""

    args: list[str]
    graph: str
    overlays: list[tuple[str, bytes]]
    duration: float
    width: int
    height: int
    has_audio: bool
    summary: str
    outputs: list[str] = field(default_factory=list)
    # A project's scenes: where each starts on the finished video, then where it ends.
    marks: list[float] = field(default_factory=list)
    # A voice is laid over it: a long quiet stretch is then a gap in the narration.
    narrated: bool = False


def _even(value: float) -> int:
    return max(2, int(value) // 2 * 2)


def _seconds_text(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def _atempo(factor: float) -> str:
    """atempo takes 0.5–2 per filter: chain them for larger changes."""
    parts: list[str] = []
    remaining = factor
    while remaining > 2.0 + 1e-9:
        parts.append("atempo=2")
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        parts.append("atempo=0.5")
        remaining /= 0.5
    if abs(remaining - 1.0) > 1e-9:
        parts.append(f"atempo={remaining:.6g}")
    return ",".join(parts) or "anull"


class _Builder:
    def __init__(self, inputs: list[InputVideo], overlay_start: int,
                 size: tuple[int, int] = CARD_SIZES["16:9"]) -> None:
        self.lines: list[str] = []
        self.count = 0
        self.overlays: list[tuple[str, bytes]] = []
        self.overlay_index = overlay_start
        first = inputs[0].info if inputs else None
        self.width, self.height = (_even(first.width), _even(first.height)) if first else size
        self.fps = round(first.fps, 3) if first else 30.0
        self.has_audio = any(item.info.has_audio for item in inputs)
        self.duration = sum(item.info.duration for item in inputs)
        self.video = ""
        self.audio: str | None = None
        self.lengths = [item.info.duration for item in inputs]
        # Voice lines a project's scenes lay: (when on the finished video, the file).
        self.voice_cues: list[tuple[float, str]] = []
        self.marks: list[float] = []
        self.narrated = False

    def label(self, kind: str) -> str:
        self.count += 1
        return f"[{kind}{self.count}]"

    def add(self, line: str) -> None:
        self.lines.append(line)

    # Inputs ------------------------------------------------------------

    def start(self, inputs: list[InputVideo]) -> None:
        w, h, fps = self.width, self.height, _seconds_text(self.fps)
        if not inputs:  # Title cards only: the scenes make every picture.
            return
        if len(inputs) == 1:
            info = inputs[0].info
            video = self.label("v")
            crop = f"crop={w}:{h}:0:0," if (info.width, info.height) != (w, h) else ""
            self.add(f"[0:v]{crop}setsar=1,format=yuv420p{video}")
            self.video = video
            if info.has_audio:
                audio = self.label("a")
                self.add(f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo{audio}")
                self.audio = audio
            return
        pieces: list[str] = []
        for index, item in enumerate(inputs):
            video = self.label("v")
            self.add(f"[{index}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps},format=yuv420p{video}")
            pieces.append(video)
            if self.has_audio:
                audio = self.label("a")
                if item.info.has_audio:
                    self.add(f"[{index}:a]aformat=sample_rates=48000:channel_layouts=stereo{audio}")
                else:  # Silence as long as the picture, so the sound stays in step.
                    self.add(f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                             f"atrim=duration={_seconds_text(item.info.duration)}{audio}")
                pieces.append(audio)
        video = self.label("v")
        audio = self.label("a") if self.has_audio else None
        self.add(f"{''.join(pieces)}concat=n={len(inputs)}:v=1:a={1 if self.has_audio else 0}{video}{audio or ''}")
        self.video, self.audio = video, audio

    # Pieces of the timeline --------------------------------------------

    def _pieces(self, cuts: list[tuple[float, float]], *, audio: bool) -> tuple[list[str], list[str]]:
        """Split the video (and the sound) into these time ranges, each starting at 0."""
        count = len(cuts)
        videos = [self.label("v") for _ in cuts]
        if count == 1:
            videos = [self.video]
        else:
            self.add(f"{self.video}split={count}{''.join(videos)}")
        trimmed: list[str] = []
        for (start, end), source in zip(cuts, videos):
            out = self.label("v")
            self.add(f"{source}trim=start={_seconds_text(start)}:end={_seconds_text(end)},"
                     f"setpts=PTS-STARTPTS{out}")
            trimmed.append(out)
        sounds: list[str] = []
        if audio and self.audio:
            sources = [self.label("a") for _ in cuts] if count > 1 else [self.audio]
            if count > 1:
                self.add(f"{self.audio}asplit={count}{''.join(sources)}")
            for (start, end), source in zip(cuts, sources):
                out = self.label("a")
                self.add(f"{source}atrim=start={_seconds_text(start)}:end={_seconds_text(end)},"
                         f"asetpts=PTS-STARTPTS{out}")
                sounds.append(out)
        return trimmed, sounds

    def _join(self, videos: list[str], sounds: list[str]) -> None:
        if len(videos) == 1:
            self.video = videos[0]
            if sounds:
                self.audio = sounds[0]
            return
        video = self.label("v")
        if sounds:
            audio = self.label("a")
            pairs = "".join(v + a for v, a in zip(videos, sounds))
            self.add(f"{pairs}concat=n={len(videos)}:v=1:a=1{video}{audio}")
            self.video, self.audio = video, audio
        else:
            self.add(f"{''.join(videos)}concat=n={len(videos)}:v=1:a=0{video}")
            self.video = video

    def _window(self, step: Step) -> tuple[float, float]:
        start = min(max(0.0, step.start or 0.0), self.duration)
        end = self.duration if step.end is None else min(step.end, self.duration)
        if end - start < 0.05:
            raise EditError(f"{step.op.capitalize()}: the times {_seconds_text(start)}–{_seconds_text(end)} s are "
                            f"outside the video, which is {_seconds_text(self.duration)} s long at that point.")
        return start, end

    @staticmethod
    def _cuts(start: float, end: float, duration: float) -> tuple[list[tuple[float, float]], int]:
        """The before / during / after ranges; the index of "during"."""
        cuts: list[tuple[float, float]] = []
        if start > 0.01:
            cuts.append((0.0, start))
        middle = len(cuts)
        cuts.append((start, end))
        if duration - end > 0.01:
            cuts.append((end, duration))
        return cuts, middle

    # Steps -------------------------------------------------------------

    def trim(self, step: Step) -> None:
        start, end = self._window(step)
        videos, sounds = self._pieces([(start, end)], audio=True)
        self._join(videos, sounds)
        self.duration = end - start

    def overlay_input(self, picture: Overlay) -> str:
        name = f"o{len(self.overlays) + 1:03d}.png"
        self.overlays.append((name, picture.png))
        index = self.overlay_index
        self.overlay_index += 1
        return f"[{index}:v]"

    def lay(self, video: str, picture: Overlay, *, start: float | None = None, end: float | None = None) -> str:
        """Lay a picture over a video stream (between start and end when given); the new stream."""
        source = self.overlay_input(picture)
        out = self.label("v")
        enable = ""
        if start is not None or end is not None:
            begin = _seconds_text(max(0.0, start or 0.0))
            finish = _seconds_text(end) if end is not None else "1e9"
            enable = f":enable='between(t,{begin},{finish})'"
        self.add(f"{video}{source}overlay=x={picture.x}:y={picture.y}:eof_action=repeat{enable}{out}")
        return out

    def speed(self, step: Step) -> None:
        factor = float(step.factor or 1)
        start, end = self._window(step)
        cuts, middle = self._cuts(start, end, self.duration)
        videos, sounds = self._pieces(cuts, audio=True)
        fast = self.label("v")
        self.add(f"{videos[middle]}setpts=PTS/{factor:.6g}{fast}")
        videos[middle] = fast
        if sounds:
            tempo = self.label("a")
            self.add(f"{sounds[middle]}{_atempo(factor)}{tempo}")
            sounds[middle] = tempo
        if step.label:
            label = Step(op="text", text=speed_words(factor), position=step.position or "top-right", style="badge")
            videos[middle] = self.lay(videos[middle], text_overlay(label, self.width, self.height))
        self._join(videos, sounds)
        self.duration = self.duration - (end - start) + (end - start) / factor

    def text(self, step: Step) -> None:
        picture = text_overlay(step, self.width, self.height)
        if step.start is not None and step.start >= self.duration:
            raise EditError(f"Text: it starts at {_seconds_text(step.start)} s, after the video ends "
                            f"({_seconds_text(self.duration)} s at that point).")
        self.video = self.lay(self.video, picture, start=step.start, end=step.end)

    def captions(self, step: Step) -> None:
        cues = [cue for cue in parse_srt(step.text) if cue.start < self.duration]
        if not cues:
            raise EditError("None of the captions start before the video ends.")
        for cue in cues:
            caption = Step(op="text", text=cue.text, position=step.position or "bottom", style="caption")
            self.video = self.lay(self.video, text_overlay(caption, self.width, self.height),
                                  start=cue.start, end=cue.end)

    def _region(self, step: Step, *, keep_shape: bool) -> tuple[int, int, int, int]:
        """The region in pixels; a zoom keeps the picture's shape (so nothing is stretched)."""
        w, h = self.width, self.height
        x, y = float(step.x or 0), float(step.y or 0)
        width, height = float(step.width or 1), float(step.height or 1)
        width, height = min(width, 1 - x), min(height, 1 - y)
        if keep_shape:
            centre_x, centre_y = (x + width / 2) * w, (y + height / 2) * h
            scale = max(width, height)
            region_w, region_h = w * scale, h * scale
            left = min(max(0.0, centre_x - region_w / 2), w - region_w)
            top = min(max(0.0, centre_y - region_h / 2), h - region_h)
            cw, ch = _even(region_w), _even(region_h)
            return cw, ch, int(left), int(top)
        return _even(width * w), _even(height * h), int(x * w), int(y * h)

    def zoom(self, step: Step) -> None:
        cw, ch, cx, cy = self._region(step, keep_shape=True)
        if (cw, ch) == (self.width, self.height):
            return
        start, end = self._window(step)
        cuts, middle = self._cuts(start, end, self.duration)
        videos, _ = self._pieces(cuts, audio=False)
        out = self.label("v")
        self.add(f"{videos[middle]}crop={cw}:{ch}:{cx}:{cy},scale={self.width}:{self.height}:flags=lanczos,"
                 f"setsar=1{out}")
        videos[middle] = out
        self._join(videos, [])

    def crop(self, step: Step) -> None:
        cw, ch, cx, cy = self._region(step, keep_shape=False)
        out = self.label("v")
        self.add(f"{self.video}crop={cw}:{ch}:{cx}:{cy},setsar=1{out}")
        self.video = out
        self.width, self.height = cw, ch

    def sound(self, step: Step) -> None:
        """Laid on the finished timeline once every picture step is done (``mix``)."""

    def scenes(self, step: Step) -> None:
        """A video project's scenes in order: each piece cut from its input, at its speed, with its zoom,
        its card for the first seconds and its line as a caption (``style="caption"``); its voice line
        is laid at the scene's start on the finished video."""
        if not step.scenes:
            raise EditError("A video needs at least one scene.")
        offsets = [sum(self.lengths[:index]) for index in range(len(self.lengths))]
        cuts: list[tuple[float, float]] = []
        lengths: list[float] = []
        for number, scene in enumerate(step.scenes, 1):
            if scene.input is None:
                if scene.end is None or scene.end - scene.start < 0.1:
                    raise EditError(f"Scene {number}: a title card needs how many seconds it lasts.")
                lengths.append(scene.end - scene.start)
                continue
            if not 0 <= scene.input < len(self.lengths):
                raise EditError(f"Scene {number}: its video isn't one of the inputs.")
            length = self.lengths[scene.input]
            start = min(max(0.0, scene.start), length)
            end = length if scene.end is None else min(scene.end, length)
            if end - start < 0.05:
                raise EditError(f"Scene {number}: {_seconds_text(start)}–{_seconds_text(end)} s is outside its "
                                f"video, which is {_seconds_text(length)} s long.")
            cuts.append((offsets[scene.input] + start, offsets[scene.input] + end))
            lengths.append(end - start)
        look = step.look or Look()
        videos, sounds = self._pieces(cuts, audio=True) if cuts else ([], [])
        pieces = iter(zip(videos, sounds or [None] * len(videos)))
        joined = [self._blank(look.colour, length) if scene.input is None else next(pieces)
                  for scene, length in zip(step.scenes, lengths)]
        self._join([video for video, _ in joined], [sound for _, sound in joined if sound])
        self.duration = sum(lengths)
        # A title card keeps its length: speed is for the pieces of a video.
        speeds = [1.0 if scene.input is None else scene.speed for scene in step.scenes]
        # Speeds from the last scene back, so each one's times are still those of the cut timeline.
        for index in range(len(step.scenes) - 1, -1, -1):
            factor = speeds[index]
            if abs(factor - 1) > 1e-3:
                begin = sum(lengths[:index])
                # Its badge is laid after the reframe, so a tall shape never cuts it off.
                self.speed(Step(op="speed", start=begin, end=begin + lengths[index], factor=factor, label=False))
        finals = [length / speed for length, speed in zip(lengths, speeds)]
        self.marks = [round(sum(finals[:index]), 3) for index in range(len(finals) + 1)]
        for index, scene in enumerate(step.scenes):
            if scene.zoom:
                x, y, width, height = scene.zoom
                self.zoom(Step(op="zoom", start=self.marks[index], end=self.marks[index + 1], x=x, y=y,
                               width=width, height=height))
        if step.aspect:
            self._reframe(step.aspect, step.scenes)
        fill = (*look.colour, 230) if step.look else None
        if look.intro or look.outro:
            self._bookends(look)
        for index, scene in enumerate(step.scenes):
            begin, end = self.marks[index], self.marks[index + 1]
            if abs(speeds[index] - 1) > 1e-3:
                badge = Step(op="text", text=speed_words(scene.speed), position="top-right", style="badge",
                             fill=fill)
                self.video = self.lay(self.video, text_overlay(badge, self.width, self.height), start=begin, end=end)
            if scene.input is None and scene.card:  # The whole scene, on its own backdrop (no box).
                png, width, height = render_text(scene.card, "title", self.width, self.height, plain=True,
                                                 shadow=True)
                x, y = place("center", width, height, self.width, self.height)
                self.video = self.lay(self.video, Overlay(png, width, height, x, y), start=begin, end=end)
            elif scene.card:
                card = Step(op="text", text=scene.card, style="title", position="center", fill=fill)
                self.video = self.lay(self.video, text_overlay(card, self.width, self.height),
                                      start=begin, end=min(end, begin + CARD_SECONDS))
            if scene.line and step.style == "caption":
                caption = (Step(op="text", text=scene.line, style="title", position="center", plain=True)
                           if look.captions == "title" else
                           Step(op="text", text=scene.line, style="caption", position="bottom",
                                plain=look.captions == "bold"))
                self.video = self.lay(self.video, text_overlay(caption, self.width, self.height),
                                      start=begin, end=end)
            if scene.voice:
                self.voice_cues.append((begin, scene.voice))
        for text, start in ((look.intro, 0.0), (look.outro, self.duration - BOOKEND_SECONDS)):
            if text:
                title = Step(op="text", text=text, style="title", position="center", plain=True)
                self.video = self.lay(self.video, text_overlay(title, self.width, self.height), start=start,
                                      end=start + BOOKEND_SECONDS)
        if look.logo:
            self.video = self.lay(self.video, logo_overlay(look.logo, look.logo_corner, self.width, self.height))

    def _blank(self, colour: tuple[int, int, int], length: float) -> tuple[str, str | None]:
        """A picture of one colour, *length* seconds long, with silence when the video has sound."""
        seconds = _seconds_text(length)
        flat = self.label("v")
        self.add(f"color=c=0x{colour[0]:02x}{colour[1]:02x}{colour[2]:02x}:s={self.width}x{self.height}:"
                 f"r={_seconds_text(self.fps)}:d={seconds},setsar=1{flat}")
        lit = self.lay(flat, Overlay(card_backdrop(colour, self.width, self.height), self.width, self.height, 0, 0))
        video = self.label("v")
        self.add(f"{lit}format=yuv420p{video}")
        if not self.audio:
            return video, None
        sound = self.label("a")
        self.add(f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={seconds}{sound}")
        return video, sound

    def _bookends(self, look: Look) -> None:
        """The intro and outro: cards of the brand's colour before the first scene and after the last."""

        def card() -> tuple[str, str | None]:
            return self._blank(look.colour, BOOKEND_SECONDS)

        pieces = ([card()] if look.intro else []) + [(self.video, self.audio)] + ([card()] if look.outro else [])
        self._join([video for video, _ in pieces], [sound for _, sound in pieces if sound])
        shift = BOOKEND_SECONDS if look.intro else 0.0
        self.marks = [round(mark + shift, 3) for mark in self.marks]
        self.duration += BOOKEND_SECONDS * (bool(look.intro) + bool(look.outro))

    def _reframe(self, aspect: str, scenes: list[SceneCut]) -> None:
        """Each scene cropped to the shape around its focus: the middle of its zoom, else where its pointer was,
        else the middle of the picture. A shape the video already has changes nothing."""
        if aspect not in ASPECTS:
            raise EditError(f"A video's shape is 16:9, 1:1 or 9:16, not {aspect}.")
        target, w, h = ASPECTS[aspect], self.width, self.height
        if abs(w / h - target) < 0.01:
            return
        cw, ch = (_even(h * target), h) if w / h > target else (w, _even(w / target))
        cuts = [(self.marks[index], self.marks[index + 1]) for index in range(len(scenes))]
        videos, _ = self._pieces(cuts, audio=False)
        for index, scene in enumerate(scenes):
            fx, fy = ((scene.zoom[0] + scene.zoom[2] / 2, scene.zoom[1] + scene.zoom[3] / 2) if scene.zoom
                      else scene.focus or (0.5, 0.5))
            left = int(min(max(0.0, fx * w - cw / 2), w - cw))
            top = int(min(max(0.0, fy * h - ch / 2), h - ch))
            out = self.label("v")
            self.add(f"{videos[index]}crop={cw}:{ch}:{left}:{top},setsar=1{out}")
            videos[index] = out
        self._join(videos, [])
        self.width, self.height = cw, ch

    def _amix(self, labels: list[str]) -> str:
        if len(labels) == 1:
            return labels[0]
        out = self.label("a")
        self.add(f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0{out}")
        return out

    def mix(self, steps: list[Step], first_input: int, sources: list[str]) -> None:
        """Voice lines at their times, music under them (ducked while a voice speaks) and the video's own sound,
        mixed and brought to one loudness: the narrated video's sound in one pass."""
        sounds = [step for step in steps if step.op == "sound"]
        sounds += [Step(op="sound", source=voice, role="voice", start=moment) for moment, voice in self.voice_cues]
        if not sounds:
            return
        total = _seconds_text(self.duration)
        voices: list[str] = []
        music: list[str] = []
        ducked = False
        original = 1.0
        for step in sounds:
            if step.role == "original":
                original = 1.0 if step.volume is None else step.volume
                continue
            start = step.start or 0.0
            if start >= self.duration:
                raise EditError(f"A sound starts at {_seconds_text(start)} s, after the video ends ({total} s).")
            index = first_input + sources.index(step.source)
            volume = step.volume if step.volume is not None else (1.0 if step.role == "voice" else 0.3)
            delay = int(round(start * 1000))
            chain = f"[{index}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={volume:.3f}"
            if step.role == "music":
                length = self.duration - start
                fade = max(0.0, length - 2.0)
                chain += f",atrim=0:{_seconds_text(length)},afade=t=out:st={_seconds_text(fade)}:d=2"
                ducked = ducked or step.duck
            label = self.label("a")
            self.add(f"{chain},adelay={delay}|{delay}{label}")
            (voices if step.role == "voice" else music).append(label)
        parts: list[str] = []
        if self.audio:
            # Its own sound, even at volume 0: a stream the graph makes must be read for ffmpeg to bind it.
            own = self.label("a")
            self.add(f"{self.audio}volume={original:.3f}{own}")
            parts.append(own)
        voice = self._amix(voices) if voices else ""
        bed = self._amix(music) if music else ""
        if voice and bed and ducked:
            # The music dips while a voice speaks, and comes back between lines.
            speaking, key = self.label("a"), self.label("a")
            self.add(f"{voice}asplit=2{speaking}{key}")
            under = self.label("a")
            self.add(f"{bed}{key}sidechaincompress=threshold=0.02:ratio=8:attack=20:release=450{under}")
            voice, bed = speaking, under
        parts += [label for label in (voice, bed) if label]
        mixed = self._amix(parts)
        out = self.label("a")
        self.add(f"{mixed}apad,atrim=0:{total},{LOUDNESS},aformat=sample_rates=48000:channel_layouts=stereo{out}")
        self.audio = out
        self.has_audio = True
        self.narrated = self.narrated or bool(voices)


def encoder_arguments(encoder: str) -> list[str]:
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    return ["-c:v", encoder, "-b:v", "8M"]


def build_plan(
    inputs: list[InputVideo],
    steps: list[Step],
    output: str,
    *,
    graph_file: str = "graph.txt",
    graph_option: str = "-/filter_complex",
    encoder: str = "libx264",
    sounds: dict[str, str] | None = None,
) -> Plan:
    """One ffmpeg run that makes the whole edit: arguments, filter graph and overlay pictures.

    Overlay pictures are named ``o001.png``…, and with the graph file and the output they are
    relative to the working folder ffmpeg runs in.
    """
    # A project of title cards only needs no video: its scenes make every picture.
    cards_only = bool(steps) and steps[0].op == "scenes" and all(cut.input is None for cut in steps[0].scenes)
    if not inputs and not cards_only:
        raise EditError("Give at least one video to edit.")
    if len(inputs) > MAX_INPUTS:
        raise EditError(f"At most {MAX_INPUTS} videos can be joined in one edit.")
    sources = sound_sources(steps)
    missing = [name for name in sources if name not in (sounds or {})]
    if missing:
        raise EditError(f"{missing[0]} isn't a sound file Row-Bot found.")
    size = CARD_SIZES.get(steps[0].aspect, CARD_SIZES["16:9"]) if cards_only else CARD_SIZES["16:9"]
    builder = _Builder(inputs, overlay_start=len(inputs) + len(sources), size=size)
    builder.start(inputs)
    for step in steps:
        getattr(builder, step.op)(step)
    builder.mix(steps, len(inputs), sources)
    video = builder.label("v")
    # One steady frame rate (sped-up parts would otherwise carry several times as many frames).
    builder.add(f"{builder.video}fps={_seconds_text(min(builder.fps, 60.0))},format=yuv420p{video}")
    builder.video = video
    args = ["-hide_banner", "-nostdin", "-y"]
    for item in inputs:
        args += input_arguments(item.path)
    for name in sources:
        args += sound_input_arguments((sounds or {})[name])
    for name, _data in builder.overlays:
        args += ["-protocol_whitelist", "file", "-i", file_argument(name)]
    args += [graph_option, graph_file, "-map", builder.video]
    if builder.audio:
        args += ["-map", builder.audio]
    args += encoder_arguments(encoder) + ["-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    args += ["-c:a", "aac", "-b:a", "160k"] if builder.audio else ["-an"]
    args += ["-progress", "pipe:1", "-nostats", file_argument(output)]
    return Plan(args=args, graph=";\n".join(builder.lines), overlays=builder.overlays,
                duration=builder.duration, width=builder.width, height=builder.height,
                has_audio=bool(builder.audio), summary=describe(steps, len(inputs)), outputs=[output],
                marks=builder.marks, narrated=builder.narrated)


def gif_arguments(source: str, output: str, *, width: int = 480, fps: int = 12) -> list[str]:
    """One ffmpeg run that makes a looping GIF of a finished video, with its own palette (clear colours)."""
    graph = (f"[0:v]fps={fps},scale={width}:-2:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];"
             "[b][p]paletteuse=dither=bayer:bayer_scale=4")
    return ["-hide_banner", "-nostdin", "-y", *input_arguments(source), "-filter_complex", graph, "-loop", "0",
            "-progress", "pipe:1", "-nostats", file_argument(output)]


def graph_option_for(version: str) -> str:
    """ffmpeg 7 reads a filter graph from a file with ``-/filter_complex``; older ones with ``-filter_complex_script``."""
    match = re.match(r"n?(\d+)\.", str(version or ""))
    return "-/filter_complex" if match and int(match.group(1)) >= 7 else "-filter_complex_script"


# ── Frames ──────────────────────────────────────────────────────────────────


def frame_times(duration: float, count: int, times: Iterable[float] | None = None) -> list[float]:
    """When to take each frame: the times asked for (within the video), else evenly spread."""
    if times:
        chosen = sorted({round(min(max(0.0, float(value)), max(0.0, duration - 0.05)), 3) for value in times})
        return chosen[:MAX_FRAMES]
    count = max(1, min(int(count or 1), MAX_FRAMES))
    return [round((index + 0.5) * duration / count, 3) for index in range(count)]


def frames_arguments(source: str, times: list[float], names: list[str], *, max_width: int = 0) -> list[str]:
    """One ffmpeg run that saves a still picture at each time (each time opens the video at that point)."""
    args = ["-hide_banner", "-nostdin", "-y"]
    for moment in times:
        args += ["-ss", _seconds_text(moment), *input_arguments(source)]
    for index, name in enumerate(names):
        args += ["-map", f"{index}:v:0", "-frames:v", "1"]
        if max_width:
            args += ["-vf", f"scale='min({max_width},iw)':-2"]
        args += ["-q:v", "3" if name.endswith(".jpg") else "1", file_argument(name)]
    return args


# ── Progress ────────────────────────────────────────────────────────────────

_PROGRESS = re.compile(r"out_time_(?:us|ms)=(\d+)")


def progress_seconds(output: str) -> float | None:
    """How far an encode has got, from the last ``-progress`` report in its output."""
    found = _PROGRESS.findall(output or "")
    return int(found[-1]) / 1_000_000 if found else None


def clock(seconds: float) -> str:
    """``0:42``, ``1:02:10``; a moment between whole seconds keeps its tenth (``0:01.5``)."""
    value = max(0.0, float(seconds))
    whole = round(value)
    if abs(value - whole) >= 0.05:
        minutes, rest = divmod(value, 60)
        hours, minutes = divmod(int(minutes), 60)
        return f"{hours}:{minutes:02d}:{rest:04.1f}" if hours else f"{minutes}:{rest:04.1f}"
    minutes, rest = divmod(int(whole), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{rest:02d}" if hours else f"{minutes}:{rest:02d}"


# ── Checks ──────────────────────────────────────────────────────────────────

QUIET_DB = -35.0
_MEAN = re.compile(r"mean_volume:\s*(-?[0-9.]+|-inf) dB")
_PEAK = re.compile(r"max_volume:\s*(-?[0-9.]+|-inf) dB")
_BLACK = re.compile(r"black_start:\s*([0-9.]+)\s+black_end:\s*([0-9.]+)")
_FROZEN = re.compile(r"freeze_start:\s*([0-9.]+)[\s\S]*?freeze_duration:\s*([0-9.]+)")
_SILENCE_START = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([0-9.]+)")
# A gap this long in a narrated video, or a picture held this long, is a pause a viewer notices.
QUIET_GAP_SECONDS = 6.0
LONG_STILL_SECONDS = 10.0
# A narrated video's picture may hold while the voice speaks over it (a viewer reads along); its quiet gaps are
# checked on their own.
NARRATED_STILL_SECONDS = 20.0


def check_arguments(path: Path | str, *, has_audio: bool, narrated: bool = False) -> list[str]:
    """One read of the video for its sound level, black stretches and still stretches (and, narrated, its
    quiet stretches)."""
    args = ["-hide_banner", "-nostdin", *input_arguments(path),
            "-vf", "blackdetect=d=0.5:pix_th=0.10,freezedetect=n=0.003:d=3"]
    if has_audio:
        quiet = f",silencedetect=n=-40dB:d={QUIET_GAP_SECONDS:g}" if narrated else ""
        args += ["-af", f"volumedetect{quiet}"]
    return args + ["-f", "null", "-"]


@dataclass(frozen=True)
class Checks:
    passed: bool
    lines: list[str]


def read_checks(output: str, info: VideoInfo, *, narrated: bool = False) -> Checks:
    """What the read found, in plain lines; a video passes when it has sound loud enough, no black stretch
    past its first and last second, no picture held still for 10 s or more (20 s narrated) and, narrated, no 6 s
    without the voice. Shorter still stretches are listed (a screen recording may rightly hold still)."""
    lines = [f"Length {clock(info.duration)}, {info.width}×{info.height}."]
    problems = 0
    mean, peak = _MEAN.search(output), _PEAK.search(output)
    if not info.has_audio or mean is None:
        lines.append("Sound: none. Add a voice or music if this video should have sound.")
        problems += 1
    else:
        level = float("-inf") if mean.group(1) == "-inf" else float(mean.group(1))
        top = peak.group(1) if peak else "?"
        if level < QUIET_DB:
            lines.append(f"Sound: too quiet (average {mean.group(1)} dB, peak {top} dB).")
            problems += 1
        else:
            lines.append(f"Sound: present (average {mean.group(1)} dB, peak {top} dB).")
    black = [(float(a), float(b)) for a, b in _BLACK.findall(output)]
    inside = [(a, b) for a, b in black if not (a <= 1.0 and b - a <= 1.5) and not (b >= info.duration - 1.0
                                                                                   and b - a <= 1.5)]
    if inside:
        problems += 1
        lines.append("Black: " + ", ".join(f"{clock(a)}–{clock(b)}" for a, b in inside[:5]) + ".")
    else:
        lines.append("Black: none.")
    if narrated and info.has_audio:
        starts = [max(0.0, float(a)) for a in _SILENCE_START.findall(output)]
        ends = [float(b) for b in _SILENCE_END.findall(output)] + [info.duration] * len(starts)
        gaps = [(a, b) for a, b in zip(starts, ends, strict=False) if b - a >= QUIET_GAP_SECONDS]
        if gaps:
            problems += 1
            lines.append("Quiet: " + ", ".join(f"{clock(a)}–{clock(b)}" for a, b in gaps[:5])
                         + " with no voice. Give those parts a line, or cut them shorter.")
    frozen = [(float(a), float(d)) for a, d in _FROZEN.findall(output)]
    limit = NARRATED_STILL_SECONDS if narrated and info.has_audio else LONG_STILL_SECONDS
    long = [(a, d) for a, d in frozen if d >= limit]
    if long:
        problems += 1
        lines.append("Still: " + ", ".join(f"{clock(a)} for {d:.0f} s" for a, d in long[:5])
                     + ": too long to watch. Cut those parts shorter or speed them up.")
    else:
        lines.append("Still: " + (", ".join(f"{clock(a)} for {d:.0f} s" for a, d in frozen[:5])
                                  + " (fine if nothing should move there)." if frozen else "none."))
    lines.append("Result: passed." if not problems else "Result: needs attention before it's done.")
    return Checks(not problems, lines)
