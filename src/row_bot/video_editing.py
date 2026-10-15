"""Building ffmpeg commands for video edits (F3): trim, join, speed, text, zoom, crop, captions, frames.

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

Op = Literal["trim", "speed", "text", "zoom", "crop", "captions"]
OPS: tuple[str, ...] = ("trim", "speed", "text", "zoom", "crop", "captions")
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
        steps.append(Step(op=op, start=start, end=end, factor=factor, label=True if label is None else bool(label),
                          text=text, position=position, style=style, **region))
    if len(steps) > MAX_STEPS:
        raise EditError(f"At most {MAX_STEPS} steps fit in one edit.")
    return steps


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


def render_text(text: str, style: str, frame_width: int, frame_height: int) -> tuple[bytes, int, int]:
    """Draw text as a picture: a rounded dark badge, a large title, or a caption box."""
    from PIL import Image, ImageDraw

    style = style or "badge"
    size = max(12, round(frame_height * _STYLE_SIZES[style]))
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
    fill = (17, 24, 39, 214) if style != "caption" else (0, 0, 0, 178)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=fill)
    for index, line in enumerate(lines):
        line_width = probe.textlength(line, font=typeface)
        left = (width - line_width) / 2
        top = pad_y + index * line_height + (line_height - size) / 2 - size * 0.1
        draw.text((left, top), line, font=typeface, fill=(255, 255, 255, 255))
    output = io.BytesIO()
    picture.save(output, format="PNG", optimize=True)
    return output.getvalue(), width, height


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
    png, width, height = render_text(step.text, style, frame_width, frame_height)
    default = {"badge": "top-right", "title": "center", "caption": "bottom"}[style]
    x, y = place(step.position or default, width, height, frame_width, frame_height)
    return Overlay(png, width, height, x, y, step.start, step.end)


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
    def __init__(self, inputs: list[InputVideo], overlay_start: int) -> None:
        self.lines: list[str] = []
        self.count = 0
        self.overlays: list[tuple[str, bytes]] = []
        self.overlay_index = overlay_start
        first = inputs[0].info
        self.width, self.height = _even(first.width), _even(first.height)
        self.fps = round(first.fps, 3)
        self.has_audio = any(item.info.has_audio for item in inputs)
        self.duration = sum(item.info.duration for item in inputs)
        self.video = ""
        self.audio: str | None = None

    def label(self, kind: str) -> str:
        self.count += 1
        return f"[{kind}{self.count}]"

    def add(self, line: str) -> None:
        self.lines.append(line)

    # Inputs ------------------------------------------------------------

    def start(self, inputs: list[InputVideo]) -> None:
        w, h, fps = self.width, self.height, _seconds_text(self.fps)
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
) -> Plan:
    """One ffmpeg run that makes the whole edit: arguments, filter graph and overlay pictures.

    Overlay pictures are named ``o001.png``…, and with the graph file and the output they are
    relative to the working folder ffmpeg runs in.
    """
    if not inputs:
        raise EditError("Give at least one video to edit.")
    if len(inputs) > MAX_INPUTS:
        raise EditError(f"At most {MAX_INPUTS} videos can be joined in one edit.")
    builder = _Builder(inputs, overlay_start=len(inputs))
    builder.start(inputs)
    for step in steps:
        getattr(builder, step.op)(step)
    video = builder.label("v")
    # One steady frame rate (sped-up parts would otherwise carry several times as many frames).
    builder.add(f"{builder.video}fps={_seconds_text(min(builder.fps, 60.0))},format=yuv420p{video}")
    builder.video = video
    args = ["-hide_banner", "-nostdin", "-y"]
    for item in inputs:
        args += input_arguments(item.path)
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
                has_audio=bool(builder.audio), summary=describe(steps, len(inputs)), outputs=[output])


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
