"""The studio pass of a video project (5.3.2): each scene of footage gets its camera and its frame.

Footage is anything: a phone or camera clip read where it is however long, an AI shot, a screen
recording, or a still picture held with a slow push in (Ken Burns). Every scene is conformed into one clip: one
size (cropped to fill), one frame rate, BT.709 colour in TV range, turned upright, its sound at one rate.

The camera follows what Row-Bot did while recording (``recordings.Recording.actions``): it eases in on the control
being clicked or typed into, pans to the next one when it comes soon after, and eases back out before a new page or
after a pause; a fast-forwarded stretch is never zoomed. A scene's own zoom (the agent's or the person's) wins, eased
in. The frame is the look: edge to edge by default ("full"), every picture of another shape cropped to fill around
where the action is (the camera's view, else the recording's pointer, else the middle), so clips of different sizes
play as one film; or, when asked, the picture inset on the brand's backdrop with rounded corners and a soft shadow
("framed": the brand kit's choice, or one scene's ``fit``).

Each scene is drawn into a clip of its own: a view that moves, and the framed look, frame by frame with Pillow
(sub-pixel crops, so a slow zoom never shimmers), a view that doesn't by ffmpeg's own scaler. A project keeps its
scenes' clips, each named by a hash of what it was drawn from, so making the video again draws only the scenes
that changed (C1). The edit reads the clips one after another as one video, then cuts, captions and mixes it as it
does any video. The camera path and one frame's look are pure; only ``Studio`` reads and writes video.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import uuid
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

FRAMINGS = ("framed", "full")
# A key of the camera: seconds from the scene's start (in its recording's time), where the view's middle is (0–1
# across the picture) and how far in it is (1 is the whole picture).
Key = tuple[float, float, float, float]

EASE_IN = 0.6
EASE_OUT = 0.5
PAN = 0.6
# How long the view stays on a control after its action ends, and how soon the next action must start to be
# followed there rather than eased out between them.
LINGER = 1.2
FOLLOW_GAP = 1.5
MAX_ZOOM = 1.8
# A control is framed to take about this much of the view across (and at most this much of its height).
FILL_WIDTH, FILL_HEIGHT = 0.4, 0.5
# A zoom smaller than this isn't worth the movement.
MIN_ZOOM = 1.25
# A scene sped up at least this much is a fast-forward: the camera stays still.
FAST = 1.5
# The framed look: the picture's share of the frame, its corner radius and its shadow (fractions of the height).
FRAME_FILL = 0.88
CORNER = 0.016
# A phone's picture (taller than wide) is set in a device: a dark rim this thick and rounded (fractions of its
# width), a little smaller in the frame so the rim fits.
PHONE_FILL, PHONE_RIM, PHONE_CORNER = 0.84, 0.045, 0.12
# Room a caption takes at the bottom of a 16:9 frame (a line in its largest style, and its margin).
CAPTION_ROOM = 0.13
SHADOW_BLUR, SHADOW_DROP, SHADOW_ALPHA = 0.022, 0.012, 120
# A picture held as a scene (a photo, a screenshot, generated art): this long when neither its end nor its line says,
# pushed in this far over it (Ken Burns), gently.
STILL_SECONDS = 3.5
PUSH_IN = 1.12
# Threads to decode a scene's video and to encode the clip: past 8, a 4K clip's decoding and the encoder's frames held
# for them doubled the memory (1.5 GB, from 0.7) and drew no faster.
THREADS = 8
# How a scene is drawn: a change to the drawing changes this, so no clip drawn the old way is reused.
DRAWING = 1
# A video in its own footage's shape is at most this long on its longer side, as HD is.
LONGEST = 1920
# A project's drawn clips that no make used are kept this long (a make running beside this one may still read them).
KEEP_SECONDS = 3600.0


def _ease(value: float) -> float:
    """Cubic ease in and out, 0–1 to 0–1."""
    value = min(1.0, max(0.0, value))
    return 4 * value ** 3 if value < 0.5 else 1 - (-2 * value + 2) ** 3 / 2


def camera(actions: Iterable[list[Any]], start: float, end: float, speed: float = 1.0) -> tuple[Key, ...]:
    """The camera of a scene cut from ``start`` to ``end`` of a recording with these actions ([from, until, kind,
    x, y, width, height, note], the recording's action log): keys in seconds from the scene's start."""
    if speed >= FAST or end - start < 0.5:
        return ()
    shots: list[list[Any]] = []  # [in, out, [(when, x, y, zoom), ...]]
    for begin, until, kind, x, y, width, height, *_note in sorted(actions, key=lambda action: action[0]):
        if kind in ("open", "scroll"):
            if shots and shots[-1][1] > begin:  # A new page or a scroll: back out before it.
                shots[-1][1] = max(shots[-1][0] + EASE_IN, float(begin))
            continue
        if until < start or begin > end:
            continue
        zoom = min(MAX_ZOOM, FILL_WIDTH / max(width, 1e-3), FILL_HEIGHT / max(height, 1e-3))
        if zoom < MIN_ZOOM:
            continue
        target = (max(start, float(begin) - 0.15), x + width / 2, y + height / 2, zoom)
        out = min(end, float(until) + LINGER)
        if shots and target[0] - shots[-1][1] < FOLLOW_GAP:
            shots[-1][1] = max(shots[-1][1], out)
            shots[-1][2].append(target)
        else:
            shots.append([target[0], out, [target]])
    keys: list[Key] = []
    for begin, out, targets in shots:
        zoom = min(target[3] for target in targets)  # One zoom per shot: panning, not pumping.
        keys.append((begin - start, 0.5, 0.5, 1.0))
        keys.append((begin - start + EASE_IN, targets[0][1], targets[0][2], zoom))
        for when, x, y, _zoom in targets[1:]:
            at = max(keys[-1][0], when - start)
            keys.append((at, keys[-1][1], keys[-1][2], zoom))
            keys.append((at + PAN, x, y, zoom))
        hold = max(keys[-1][0], out - start)
        keys.append((hold, keys[-1][1], keys[-1][2], zoom))
        keys.append((hold + EASE_OUT, 0.5, 0.5, 1.0))
    return tuple((round(t, 3), round(x, 4), round(y, 4), round(z, 3)) for t, x, y, z in keys)


def zoom_keys(box: tuple[float, float, float, float]) -> tuple[Key, ...]:
    """A scene's own zoom (x, y, width, height, 0–1 across the picture): eased in from the whole picture, held."""
    x, y, width, height = box
    zoom = max(1.0, min(1 / max(width, 1e-3), 1 / max(height, 1e-3)))
    return ((0.0, 0.5, 0.5, 1.0), (EASE_IN, x + width / 2, y + height / 2, round(zoom, 3)))


def view(keys: tuple[Key, ...], t: float) -> tuple[float, float, float]:
    """Where the camera is at ``t``: (middle x, middle y, zoom)."""
    if not keys:
        return 0.5, 0.5, 1.0
    if t <= keys[0][0]:
        return keys[0][1], keys[0][2], keys[0][3]
    for (t0, x0, y0, z0), (t1, x1, y1, z1) in zip(keys, keys[1:]):
        if t <= t1:
            k = _ease((t - t0) / (t1 - t0)) if t1 > t0 else 1.0
            return x0 + (x1 - x0) * k, y0 + (y1 - y0) * k, z0 * (z1 / z0) ** k
    return keys[-1][1], keys[-1][2], keys[-1][3]


def crop_box(x: float, y: float, zoom: float, width: int, height: int) -> tuple[float, float, float, float]:
    """The part of a picture this view shows, in its pixels (fractions kept: the crop is sub-pixel)."""
    zoom = max(1.0, zoom)
    seen_width, seen_height = width / zoom, height / zoom
    left = min(max(0.0, x * width - seen_width / 2), width - seen_width)
    top = min(max(0.0, y * height - seen_height / 2), height - seen_height)
    return left, top, left + seen_width, top + seen_height


@dataclass(frozen=True)
class Frame:
    """How each picture is set in the finished frame."""

    size: tuple[int, int]
    framing: str = "full"
    colour: tuple[int, int, int] = (31, 41, 55)
    # The share of the frame's height kept clear at the bottom for captions: the picture sits above them.
    captions: float = 0.0


@lru_cache(maxsize=4)
def _setting(frame: Frame, picture: tuple[int, int]) -> tuple[Any, tuple[int, int], tuple[int, int], Any]:
    """The framed look for a picture of this size: (the backdrop with its shadow, where the picture goes, its size
    there, its rounded mask)."""
    from PIL import Image, ImageDraw, ImageFilter

    from row_bot.video_editing import card_backdrop

    width, height = frame.size
    phone = picture[1] > picture[0]
    fill = PHONE_FILL if phone else FRAME_FILL
    above = height * (1 - frame.captions)
    scale = min(width * fill / picture[0], above * fill / picture[1])
    inner = (max(2, round(picture[0] * scale)), max(2, round(picture[1] * scale)))
    at = ((width - inner[0]) // 2, round((above - inner[1]) / 2))
    radius = round(inner[0] * PHONE_CORNER) if phone else round(height * CORNER)
    rim = round(inner[0] * PHONE_RIM) if phone else 0
    mask = Image.new("L", inner, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, inner[0] - 1, inner[1] - 1), radius=radius, fill=255)
    backdrop = Image.open(io.BytesIO(card_backdrop(frame.colour, width, height))).convert("RGB")
    outer = (at[0] - rim, at[1] - rim, at[0] + inner[0] + rim, at[1] + inner[1] + rim)
    shadow = Image.new("L", frame.size, 0)
    drop = round(height * SHADOW_DROP)
    ImageDraw.Draw(shadow).rounded_rectangle((outer[0], outer[1] + drop, outer[2], outer[3] + drop),
                                             radius=radius + rim, fill=SHADOW_ALPHA)
    shadow = shadow.filter(ImageFilter.GaussianBlur(height * SHADOW_BLUR))
    backdrop.paste(Image.new("RGB", frame.size, (0, 0, 0)), (0, 0), shadow)
    if phone:  # The device: a dark rim with a faint edge of light.
        device = ImageDraw.Draw(backdrop)
        device.rounded_rectangle(outer, radius=radius + rim, fill=(17, 19, 24), outline=(78, 82, 92),
                                 width=max(1, rim // 6))
    return backdrop, at, inner, mask


def placement(picture: tuple[int, int], frame: Frame, at: tuple[float, float, float] = (0.5, 0.5, 1.0)
              ) -> tuple[tuple[float, float, float, float], tuple[int, int, int, int]]:
    """Which part of a picture of this size the frame shows through this view (left, top, right, bottom in its
    pixels), and where in the frame it goes (the same, in the frame's)."""
    box = crop_box(*at, *picture)
    if frame.framing == "full":
        width, height = frame.size
        # Edge to edge: the view cropped to the frame's shape around the view's middle, kept inside the view.
        seen_width, seen_height = box[2] - box[0], box[3] - box[1]
        if seen_width / seen_height > width / height:
            kept = seen_height * width / height
            left = min(max(box[0], at[0] * picture[0] - kept / 2), box[2] - kept)
            box = (left, box[1], left + kept, box[3])
        else:
            kept = seen_width * height / width
            top = min(max(box[1], at[1] * picture[1] - kept / 2), box[3] - kept)
            box = (box[0], top, box[2], top + kept)
        return box, (0, 0, width, height)
    _backdrop, place, inner, _mask = _setting(frame, picture)
    return box, (place[0], place[1], place[0] + inner[0], place[1] + inner[1])


def compose(picture: Any, frame: Frame, at: tuple[float, float, float] = (0.5, 0.5, 1.0)) -> Any:
    """One finished frame: the picture through the camera's view, set in the frame's look."""
    from PIL import Image

    box, _where = placement((picture.width, picture.height), frame, at)
    if frame.framing == "full":
        return picture.resize(frame.size, Image.Resampling.LANCZOS, box=box)
    backdrop, place, inner, mask = _setting(frame, (picture.width, picture.height))
    finished = backdrop.copy()
    finished.paste(picture.resize(inner, Image.Resampling.LANCZOS, box=box), place, mask)
    return finished


def _lay_marks(image: Any, showing: tuple[Any, ...], picture: tuple[int, int], frame: Frame,
               at: tuple[float, float, float]) -> None:
    """Lay marks (``video_screen.lit``) on a finished frame that shows a picture of this size through this view."""
    from row_bot import video_screen

    box, where = placement(picture, frame, at)
    scale_x, scale_y = (where[2] - where[0]) / (box[2] - box[0]), (where[3] - where[1]) / (box[3] - box[1])

    def place(found: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        x, y, width, height = found
        return (where[0] + (x * picture[0] - box[0]) * scale_x, where[1] + (y * picture[1] - box[1]) * scale_y,
                where[0] + ((x + width) * picture[0] - box[0]) * scale_x,
                where[1] + ((y + height) * picture[1] - box[1]) * scale_y)

    video_screen.draw(image, showing, place, clip=None if frame.framing == "full" else where)


def push_in(seconds: float, box: tuple[float, float, float, float] | None = None) -> tuple[Key, ...]:
    """A picture's camera (Ken Burns): a slow push in over its whole scene, to its middle, or to its zoom's region."""
    target = (0.5, 0.5, PUSH_IN)
    if box:
        x, y, width, height = box
        target = (x + width / 2, y + height / 2, max(PUSH_IN, min(1 / max(width, 1e-3), 1 / max(height, 1e-3))))
    return ((0.0, 0.5, 0.5, 1.0), (round(max(seconds, 0.1), 3), *(round(value, 4) for value in target)))


def still_seconds(scene: Any) -> float:
    """How long a picture's scene lasts: its end when given, else its line and a breath after it, else
    ``STILL_SECONDS``; never shorter than its line."""
    from row_bot.video_editing import LINE_PAD

    said = scene.voice_seconds + LINE_PAD if scene.voice and scene.voice_seconds > 0 else 0.0
    given = scene.end - scene.start if scene.end is not None else said or STILL_SECONDS
    return round(max(given, said), 3)


def still_info(path: Path) -> Any:
    """A picture as one of an edit's inputs: its upright size, and no length of its own."""
    from PIL import Image

    from row_bot.video_editing import EditError, VideoInfo

    try:
        with Image.open(path) as image:
            width, height = image.size
            if image.getexif().get(0x0112) in (5, 6, 7, 8):  # Its EXIF orientation turns it a quarter.
                width, height = height, width
    except (OSError, ValueError, Image.DecompressionBombError):
        raise EditError(f"{path.name} can't be read as a picture.") from None
    return VideoInfo(duration=0.0, width=width, height=height, fps=30.0, has_audio=False, still=True)


def _picture(path: Path, size: tuple[int, int], zoom: float) -> Any:
    """A picture upright, in RGB, made only as large as filling the frame at its closest view needs: a 50-megapixel
    photo isn't resized 30 times a second."""
    from PIL import Image, ImageOps

    from row_bot.video_editing import EditError

    needed = (size[0] * zoom, size[1] * zoom)
    try:
        with Image.open(path) as image:
            longest = round(max(needed) * 2)
            image.draft("RGB", (longest, longest))  # A JPEG is read at a fraction of its size when that's enough.
            picture = ImageOps.exif_transpose(image).convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError):
        raise EditError(f"{path.name} can't be read as a picture.") from None
    scale = min(1.0, max(needed[0] / picture.width, needed[1] / picture.height))
    if scale < 1.0:
        picture = picture.resize((max(2, round(picture.width * scale)), max(2, round(picture.height * scale))),
                                 Image.Resampling.LANCZOS)
    return picture


def _upright(rotation: float) -> tuple[list[tuple[str, str]], bool]:
    """The filters that turn a phone's picture upright from its rotation (degrees counter-clockwise, as the file
    says), and whether they swap its width and height."""
    turns = round(rotation / 90) % 4
    return [[], [("transpose", "cclock")], [("hflip", ""), ("vflip", "")], [("transpose", "clock")]][turns], \
        turns % 2 == 1


def _filters(frame: Any, time_base: Any, chain: list[tuple[str, str]]) -> Callable[[Any], Any]:
    """ffmpeg's own filters for one scene's pictures: a frame in, a frame out."""
    import av.filter

    graph = av.filter.Graph()
    node = graph.add_buffer(width=frame.width, height=frame.height, format=frame.format.name, time_base=time_base)
    for name, arguments in [*chain, ("buffersink", "")]:
        following = graph.add(name, arguments) if arguments else graph.add(name)
        node.link_to(following)
        node = following
    graph.configure()

    def run(picture: Any) -> Any:
        graph.push(picture)
        return graph.pull()

    return run


def _chain(frame: Any, keys: tuple[Key, ...], look: Frame, direct: bool) -> list[tuple[str, str]]:
    """How a scene's decoded pictures are conformed: turned upright, then either filled into the frame around the
    view's middle in BT.709 colour (``direct``: a view that doesn't move, drawn by ffmpeg's own scaler), or made RGB
    and only as large as its closest view needs, for Pillow to draw through the camera and look."""
    from row_bot.video_editing import TO_HD_COLOUR

    turn, swaps = _upright(frame.rotation)
    width, height = (frame.height, frame.width) if swaps else (frame.width, frame.height)
    wide, high = look.size
    if direct:
        x, y = keys[0][1:3] if keys else (0.5, 0.5)
        cover = max(wide / width, high / height)
        scaled = (max(wide, round(width * cover)), max(high, round(height * cover)))
        left = round(min(max(0.0, x * scaled[0] - wide / 2), scaled[0] - wide))
        top = round(min(max(0.0, y * scaled[1] - high / 2), scaled[1] - high))
        return [*turn, ("scale", f"{scaled[0]}:{scaled[1]}:flags=lanczos:{TO_HD_COLOUR}"),
                ("crop", f"{wide}:{high}:{left}:{top}"), ("format", "yuv420p")]
    zoom = max([1.0, *(key[3] for key in keys)])
    fit = min(1.0, max(wide * zoom / width, high * zoom / height))
    return [*turn, ("scale", f"{max(2, round(width * fit))}:{max(2, round(height * fit))}:flags=lanczos"),
            ("format", "rgb24")]


class Studio:
    """Footage drawn into a clip (Matroska, H.264) at one size, frame rate and colour (BT.709, TV range): each frame
    its scene's picture through its camera and look, a view that doesn't move drawn by ffmpeg's own scaler, one that
    moves (or the framed look) by Pillow, drawn again only when the picture or the view has changed; its sound, when
    the video has some, kept in step at one rate as plain samples (silence where a scene has none), so clips drawn
    apart play one after another with no gap or drift."""

    RATE = 48000

    def __init__(self, output: Path, size: tuple[int, int], *, fps: float = 30.0, sound: bool = False) -> None:
        import av

        self.fps = fps
        self._tick = Fraction(1, round(fps))
        self.written = 0
        # Pictures decoded from the scenes' videos: a scene is read from a seek to just before it, so a long video
        # costs only the part used.
        self.decoded = 0
        self._samples = 0
        self._writer = av.open(str(output), "w")
        self._video = self._writer.add_stream("libx264", rate=round(fps))
        self._video.width, self._video.height = size
        self._video.pix_fmt = "yuv420p"
        self._video.options = {"crf": "18", "preset": "veryfast", "threads": str(THREADS)}
        self._video.thread_type = "AUTO"
        context = self._video.codec_context
        context.colorspace = context.color_primaries = context.color_trc = 1  # BT.709,
        context.color_range = 1  # in TV range.
        self._audio = self._writer.add_stream("pcm_s16le", rate=self.RATE, layout="stereo") if sound else None
        self._fifo = av.AudioFifo() if sound else None
        self._drawn: tuple[Any, Any] = (None, None)
        # Its colour (C5), from a small copy of every tenth frame: [pixels, each channel's sum, each one's squares].
        self.tone = [0.0] * 7

    def add(self, source: Path, start: float, end: float, keys: tuple[Key, ...], frame: Frame, *,
            stopped: Callable[[], bool] = lambda: False, marks: tuple[Any, ...] = ()) -> tuple[float, float]:
        """Draw ``start``–``end`` of a video (seconds from its start, as ffmpeg's ``-ss``) at the end of the clip:
        where it starts in the clip, and how long. It is read from a seek to the picture before ``start``, and each
        frame of the clip shows the latest picture at or before its moment, so any frame rate, a phone's varying
        one too, comes out at the clip's. While a mark (``video_screen.marks``, in the video's own seconds) shows,
        what was done is ringed or spotlit (C9)."""
        import av

        from row_bot import video_screen
        from row_bot.video_editing import INPUT_FORMATS, EditError

        first = self.written
        count = max(1, round((end - start) * self.fps))
        # A view that doesn't move, filling the frame: ffmpeg's scaler draws it, no Pillow.
        direct = frame.framing == "full" and (not keys or (len(keys) == 1 and keys[0][3] == 1.0))
        latest = shown = picture = run = None
        seen = b""
        upright = (0, 0)  # The footage's size, turned upright.

        def show(decoded: Any) -> None:
            nonlocal shown, picture, run, seen, upright
            if shown is not decoded:
                shown = decoded
                if run is None:
                    run = _filters(decoded, time_base, _chain(decoded, keys, frame, direct))
                    swaps = _upright(decoded.rotation)[1]
                    upright = (decoded.height, decoded.width) if swaps else (decoded.width, decoded.height)
                if direct:
                    picture = run(decoded)
                elif (luma := bytes(decoded.planes[0])) != seen:  # A still page repeats one picture: drawn once.
                    picture, seen = run(decoded).to_image(), luma
            showing = video_screen.lit(marks, start + (self.written - first) / self.fps) if marks else ()
            if direct and showing:
                self._mark(picture, showing, upright, frame, (*(keys[0][1:3] if keys else (0.5, 0.5)), 1.0))
            elif direct:
                self._put(picture)
            else:
                self._draw(picture, keys, frame, first, showing)

        resample = av.AudioResampler(format="fltp", layout="stereo", rate=self.RATE) if self._audio else None
        with av.open(str(source), options={"protocol_whitelist": "file", "format_whitelist": INPUT_FORMATS}) as reader:
            video = reader.streams.video[0]
            video.thread_type, video.thread_count = "AUTO", THREADS
            time_base = video.time_base
            sound = reader.streams.audio[0] if reader.streams.audio and self._audio else None
            offset = (reader.start_time or 0) / av.time_base
            if start > 0 and video.time_base:
                reader.seek(int((start + offset) / video.time_base), stream=video, backward=True)
            for packet in reader.demux(*([video] + ([sound] if sound is not None else []))):
                if stopped():
                    raise EditError("Stopped.")
                if packet.stream is sound:
                    for decoded in packet.decode():
                        self._hear(decoded, resample, start + offset, end + offset)
                    continue
                for decoded in packet.decode():
                    self.decoded += 1
                    when = (decoded.time if decoded.time is not None else offset) - offset
                    while latest is not None and self.written - first < count and \
                            start + (self.written - first) / self.fps < when - 0.5 / self.fps:
                        show(latest)
                    if when < end:
                        latest = decoded
                if self.written - first >= count:
                    break
            if latest is None:
                raise EditError(f"{source.name} has no pictures from {start:g} s.")
            while self.written - first < count:
                show(latest)
        self._pad()
        return first / self.fps, count / self.fps

    def still(self, source: Path, seconds: float, keys: tuple[Key, ...], frame: Frame) -> tuple[float, float]:
        """Hold a picture ``seconds`` at the end of the clip, through its camera (a slow push in): where it starts in
        the clip, and how long."""
        first = self.written
        count = max(1, round(seconds * self.fps))
        picture = _picture(source, frame.size, max([1.0, *(key[3] for key in keys)]))
        while self.written - first < count:
            self._draw(picture, keys, frame, first)
        self._pad()
        return first / self.fps, count / self.fps

    def _put(self, picture: Any) -> None:
        if self.written % TONE_EVERY == 0:
            _add_tone(self.tone, picture)
        picture.pts, picture.time_base = self.written, self._tick
        for packet in self._video.encode(picture):
            self._writer.mux(packet)
        self.written += 1

    def _draw(self, picture: Any, keys: tuple[Key, ...], frame: Frame, first: int,
              showing: tuple[Any, ...] = ()) -> None:
        import av

        at = view(keys, (self.written - first) / self.fps)
        key = (id(picture), frame, tuple(round(value, 5) for value in at), showing)
        if self._drawn[0] != key:
            drawn = compose(picture, frame, at)
            if showing:
                _lay_marks(drawn, showing, (picture.width, picture.height), frame, at)
            self._drawn = (key, drawn)
        self._put(av.VideoFrame.from_image(self._drawn[1]).reformat(format="yuv420p", dst_colorspace="ITU709",
                                                                    dst_color_range="MPEG"))

    def _mark(self, picture: Any, showing: tuple[Any, ...], upright: tuple[int, int], frame: Frame,
              at: tuple[float, float, float]) -> None:
        """A frame ffmpeg's scaler drew, with the marks showing laid on it (C9): made RGB only while one shows."""
        import av

        key = ("marked", id(picture), showing)
        if self._drawn[0] != key:
            image = picture.reformat(format="rgb24", src_colorspace="ITU709", src_color_range="MPEG").to_image()
            _lay_marks(image, showing, upright, frame, at)
            self._drawn = (key, av.VideoFrame.from_image(image).reformat(format="yuv420p", dst_colorspace="ITU709",
                                                                          dst_color_range="MPEG"))
        self._put(self._drawn[1])

    def _hear(self, decoded: Any, resample: Any, start: float, end: float) -> None:
        """A piece of a scene's sound, the part inside the scene, onto the clip's sound."""
        if decoded.time is None:
            return
        for piece in resample.resample(decoded):
            begin = float(decoded.time)
            samples = piece.to_ndarray()
            skip = max(0, round((start - begin) * self.RATE))
            keep = max(0, min(samples.shape[1], round((end - begin) * self.RATE)) - skip)
            if keep:
                self._push(samples[:, skip:skip + keep])

    def _push(self, samples: Any) -> None:
        import av
        import numpy

        # Planar floats in, 16-bit samples side by side out.
        packed = (numpy.clip(samples, -1.0, 1.0) * 32767).astype("int16").T.reshape(1, -1)
        piece = av.AudioFrame.from_ndarray(packed, format="s16", layout="stereo")
        piece.sample_rate = self.RATE
        piece.pts = None
        self._fifo.write(piece)
        size = self._audio.frame_size or 1024
        while self._fifo.samples >= size:
            self._encode(self._fifo.read(size))

    def _encode(self, piece: Any) -> None:
        piece.pts = self._samples
        piece.sample_rate = self.RATE
        self._samples += piece.samples
        for packet in self._audio.encode(piece):
            self._writer.mux(packet)

    def _pad(self) -> None:
        """Silence up to where the pictures are, so each scene's sound starts with its pictures."""
        if self._audio is None:
            return
        import numpy

        due = round(self.written / self.fps * self.RATE) - self._samples - self._fifo.samples
        if due > 0:
            self._push(numpy.zeros((2, due), dtype="float32"))

    @property
    def has_sound(self) -> bool:
        return self._audio is not None

    def close(self) -> None:
        for packet in self._video.encode(None):
            self._writer.mux(packet)
        if self._audio is not None:
            if self._fifo.samples:
                self._encode(self._fifo.read())
            for packet in self._audio.encode(None):
                self._writer.mux(packet)
        self._writer.close()


# Colour matching (C5, G5): every tenth frame of a scene, made this small, says its colour.
TONE_EVERY = 10
TONE_SIZE = (48, 27)
# How far each source moves toward the video's look (gains as a power, the mean as a share), and at most how far:
# real clips and AI shots meet as one film, a night scene stays darker than a day one.
MATCH = 0.7
MATCH_GAINS = (0.8, 1.25)
MATCH_SHIFT = 30.0
TONES = "tones.json"


def _add_tone(tone: list[float], picture: Any) -> None:
    """A frame's colour added to a clip's (``Studio.tone``)."""
    small = picture.reformat(width=TONE_SIZE[0], height=TONE_SIZE[1], format="rgb24", src_colorspace="ITU709",
                             src_color_range="MPEG").to_ndarray().reshape(-1, 3).astype("float64")
    tone[0] += small.shape[0]
    for channel in range(3):
        tone[1 + channel] += float(small[:, channel].sum())
        tone[4 + channel] += float((small[:, channel] ** 2).sum())


def clip_tone(path: Path) -> list[float]:
    """A drawn clip's colour, read from it (a clip drawn before Row-Bot noted its colour as it drew)."""
    import av

    tone = [0.0] * 7
    with av.open(str(path)) as reader:
        for number, decoded in enumerate(reader.decode(video=0)):
            if number % TONE_EVERY == 0:
                _add_tone(tone, decoded)
    return tone


def colour_match(tones: dict[Any, list[float]]) -> dict[Any, tuple[float, ...]]:
    """Each source's colour match (C5): its channels' spread and middle moved toward the whole video's (each
    source weighed by how much of it is shown), as each channel's gain then offset (0-255). Footage from one source
    only, or a source already close to the rest, needs none."""
    if len(tones) < 2:
        return {}

    def spread(tone: list[float]) -> tuple[list[float], list[float]]:
        count = max(tone[0], 1.0)
        means = [tone[1 + channel] / count for channel in range(3)]
        return means, [max(1.0, (tone[4 + channel] / count - means[channel] ** 2) ** 0.5) for channel in range(3)]

    total = sum(tone[0] for tone in tones.values()) or 1.0
    looks = {source: spread(tone) for source, tone in tones.items()}
    target = [[sum(tones[source][0] * looks[source][part][channel] for source in tones) / total
               for channel in range(3)] for part in (0, 1)]
    matched: dict[Any, tuple[float, ...]] = {}
    for source, (means, spreads) in looks.items():
        gains = [min(MATCH_GAINS[1], max(MATCH_GAINS[0], (target[1][channel] / spreads[channel]) ** MATCH))
                 for channel in range(3)]
        shifts = [min(MATCH_SHIFT, max(-MATCH_SHIFT, MATCH * (target[0][channel] - means[channel])))
                  for channel in range(3)]
        offsets = [means[channel] * (1 - gains[channel]) + shifts[channel] for channel in range(3)]
        if all(abs(gain - 1) < 0.02 for gain in gains) and all(abs(shift) < 1.5 for shift in shifts):
            continue
        matched[source] = tuple(round(value, 4) for value in (*gains, *offsets))
    return matched


def _tones(shelf: Path) -> dict[str, list[float]]:
    try:
        data = json.loads((shelf / TONES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(name): [float(value) for value in tone][:7] for name, tone in data.items()
            if isinstance(tone, list) and len(tone) == 7} if isinstance(data, dict) else {}


def _keep_tones(shelf: Path, tones: dict[str, list[float]]) -> None:
    partial = shelf / f"{TONES}.{os.getpid()}.{uuid.uuid4().hex[:6]}.part"
    partial.write_text(json.dumps(tones), encoding="utf-8", newline="\n")
    os.replace(partial, shelf / TONES)


def frame_rate(scenes: list[Any], inputs: list[Any]) -> float:
    """A video's frame rate: the one most of its footage plays at (by the seconds used), 24 to 60; 30 for pictures
    alone. The rest is conformed to it."""
    used: dict[float, float] = {}
    for scene in scenes:
        info = inputs[scene.input].info
        if info.still:
            continue
        rate = float(min(60, max(24, round(info.fps))))
        end = info.duration if scene.end is None else min(scene.end, info.duration)
        used[rate] = used.get(rate, 0.0) + max(0.0, end - scene.start)
    return max(used, key=lambda rate: (used[rate], rate)) if used else 30.0


def scene_key(item: Any, start: float, end: float, keys: tuple[Key, ...], frame: Frame, fps: float,
              sound: bool, parts: tuple[tuple[float, float], ...] = (), marks: tuple[Any, ...] = ()) -> str:
    """What a scene's clip is drawn from, as a hash: its footage (``InputVideo.identity``: which file, as it is
    now), the part of it (and the parts it keeps once its pauses are cut, C4), its camera, its marks (C9) and look,
    the frame rate and whether the video has sound. Its speed, card, line and voice are the edit's, not the
    drawing's: changing them draws nothing again."""
    what = [DRAWING, item.identity or item.path, item.info.still, round(start, 3), round(end, 3), keys,
            [*frame.size, frame.framing, *frame.colour, frame.captions], fps, sound]
    if parts:
        what.append([list(part) for part in parts])
    if marks:
        what.append(["marks", [list(mark) for mark in marks]])
    return hashlib.sha256(json.dumps(what).encode()).hexdigest()[:32]


def scene_parts(scene: Any, start: float, end: float) -> tuple[tuple[float, float], ...]:
    """The parts of start to end a scene keeps (``SceneCut.keeps``, C4), within them; () all of it."""
    parts = tuple((round(max(begin, start), 3), round(min(until, end), 3)) for begin, until in scene.keeps
                  if min(until, end) - max(begin, start) >= 0.05)
    return () if parts == ((round(start, 3), round(end, 3)),) else parts


def _concat(clips: list[tuple[Path, float]], output: Path) -> None:
    """The list ffmpeg reads the scenes' clips from, one after another, each exactly as long as drawn."""
    lines = ["ffconcat version 1.0"]
    for path, seconds in clips:
        lines += ["file '" + f"file:{path.resolve().as_posix()}".replace("'", "'\\''") + "'",
                  f"duration {seconds:.6f}"]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _keep(clip: Path) -> bool:
    """Whether a scene's clip drawn before is here to use again (marked used now, so it's kept)."""
    try:
        os.utime(clip)
    except OSError:
        return False
    return True


def _prune(cache: Path, size: tuple[int, int], used: set[str]) -> None:
    """Clips of this size no make used for ``KEEP_SECONDS``: what a project keeps is what it can use again."""
    old = time.time() - KEEP_SECONDS
    for path in cache.glob(f"{size[0]}x{size[1]}-*.mkv"):
        try:
            if path.name not in used and path.stat().st_mtime < old:
                path.unlink()
        except OSError:
            pass


def prepare(inputs: list[Any], step: Any, folder: Path, *, stopped: Callable[[], bool] = lambda: False,
            progress: Callable[[int, int], None] = lambda _done, _total: None,
            cache: Path | None = None) -> tuple[list[Any], Any, list[str]]:
    """A video project's footage drawn scene by scene in the video's shape (its own, else 16:9 when its first
    footage is wide, else that footage's): each scene cut from a video, read from a seek to its start, and each
    picture held with a slow push in, all through their cameras and look, conformed to one size, colour and frame
    rate (``frame_rate``), upright, with their sound at one rate. Each scene is a clip of its own (``scene_key``):
    in *cache*, a project's, one drawn before from the same inputs is used again and only changed scenes are drawn.
    The edit reads the clips one after another as one input (``scenes.txt``), however many scenes and sources there
    are, then cuts, captions and mixes it. The brand kit's framed look is for 16:9. Also how each scene was made:
    "new" (drawn now), "reused", or "" (a title card, which the edit draws)."""
    from dataclasses import replace

    from row_bot import video_screen
    from row_bot.video_editing import CARD_SIZES, EditError, InputVideo, Look, VideoInfo, fit_to_lines

    if step.aspect and step.aspect not in CARD_SIZES:
        raise EditError(f"A video's shape is 16:9, 1:1, 9:16 or 4:5, not {step.aspect}.")
    look = step.look or Look()
    given = [scene for scene in step.scenes if scene.input is not None]
    if not given or any(not 0 <= scene.input < len(inputs) for scene in given):
        return inputs, step, []  # (The edit says what's wrong with a scene that isn't one of the inputs.)
    held = [replace(scene, start=0.0, end=still_seconds(scene), speed=1.0)
            if scene.input is not None and inputs[scene.input].info.still else scene for scene in step.scenes]
    lengths = [max([scene.end for scene in held if scene.input == index] + [0.0]) if item.info.still
               else item.info.duration for index, item in enumerate(inputs)]
    fitted = [scene for scene, _hold in fit_to_lines(held, lengths)]
    footage = [scene for scene in fitted if scene.input is not None]
    first = inputs[footage[0].input].info
    shape = step.aspect or ("16:9" if first.width / max(1, first.height) > 1.2 else "")
    # Its own shape at most HD's size: footage recorded larger (twice over, so a zoom stays sharp: C9) still makes
    # a video of the usual size.
    fit = min(1.0, LONGEST / max(first.width, first.height, 1))
    size = CARD_SIZES[shape] if shape else (max(2, round(first.width * fit)) // 2 * 2,
                                            max(2, round(first.height * fit)) // 2 * 2)
    fill = Frame(size, look.framing if shape == "16:9" else "full", look.colour,
                 captions=CAPTION_ROOM if step.style == "caption" else 0.0)
    chosen = {True: replace(fill, framing="framed"), False: replace(fill, framing="full")}

    def frame_of(scene: Any) -> Frame:
        return chosen.get(scene.fit, fill)

    def keys_of(scene: Any) -> tuple[Key, ...]:
        info = inputs[scene.input].info
        if info.still:
            return push_in(scene.end - scene.start, scene.zoom)
        if scene.zoom:
            return zoom_keys(scene.zoom)
        if look.camera == "follow" and scene.camera:
            return scene.camera
        if scene.focus and abs(info.width / max(1, info.height) - size[0] / size[1]) > 0.01:
            return ((0.0, scene.focus[0], scene.focus[1], 1.0),)  # Cropped to fill around where the pointer was.
        return ()

    def marks_of(scene: Any, start: float, end: float, keys: tuple[Key, ...]) -> tuple[Any, ...]:
        """What was done in a recorded scene, ringed or spotlit (C9): none for a picture or a scene's own zoom."""
        if not scene.actions or scene.zoom or inputs[scene.input].info.still:
            return ()
        return video_screen.marks(scene.actions, start, end, scene.speed, look.camera,
                                  keys if look.camera == "follow" and scene.camera else ())

    fps = frame_rate(footage, inputs)
    sound = any(inputs[scene.input].info.has_audio for scene in footage)
    shelf = cache or folder
    shelf.mkdir(parents=True, exist_ok=True)
    clips: list[tuple[Path, float]] = []
    made: list[str] = []
    plans = []  # Each footage scene: (scene, its footage, its part, camera, look, its clip, kept from before).
    pieces: list[tuple[tuple[float, float], ...]] = []  # The parts of it each scene keeps (C4), in the same order.
    lights: list[tuple[Any, ...]] = []  # And what is ringed or spotlit in it (C9).
    for scene in fitted:
        if scene.input is None:
            plans.append((scene, None, (0.0, 0.0), (), fill, None, False))
            pieces.append(())
            lights.append(())
            continue
        item = inputs[scene.input]
        keys, frame = keys_of(scene), frame_of(scene)
        if item.info.still:
            start, end = 0.0, scene.end
        else:
            start = min(max(0.0, scene.start), item.info.duration)
            end = item.info.duration if scene.end is None else min(scene.end, item.info.duration)
        parts = () if item.info.still else scene_parts(scene, start, end)
        lit = marks_of(scene, start, end, keys)
        clip = shelf / f"{size[0]}x{size[1]}-{scene_key(item, start, end, keys, frame, fps, sound, parts, lit)}.mkv"
        plans.append((scene, item, (start, end), keys, frame, clip, cache is not None and _keep(clip)))
        pieces.append(parts or ((start, end),))
        lights.append(lit)
    drawing = sum(1 for *_rest, clip, kept in plans if clip is not None and not kept)

    def matched(scene: Any, item: Any, frame: Frame) -> str:
        """The source a scene's colour is matched as (C5), or "": a screen recording, a card or a framed picture."""
        return (item.identity or item.path) if item is not None and scene.matched and frame.framing == "full" else ""

    # Footage from more than one source is matched to one look: each source's colour, from its scenes' clips.
    matching = len({matched(scene, item, frame) for scene, item, _part, _keys, frame, _clip, _kept in plans} - {""}) > 1
    noted = _tones(shelf) if matching else {}
    tones: dict[str, list[float]] = {}
    scenes = []
    written = 0
    for (scene, item, (start, end), keys, frame, clip, kept), parts, lit in zip(plans, pieces, lights, strict=True):
        if clip is None:
            scenes.append(scene)
            made.append("")
            continue
        source = Path(item.path) if Path(item.path).is_absolute() else folder / item.path
        count = sum(max(1, round((until - begin) * fps)) for begin, until in parts)
        if kept:
            made.append("reused")
        else:
            progress(made.count("new"), drawing)
            part = folder / f"{clip.stem}.part.mkv"
            studio = Studio(part, size, fps=fps, sound=sound)
            try:
                if item.info.still:
                    studio.still(source, end, keys, frame)
                else:  # Each part it keeps one after another: its pauses and fillers cut (C4).
                    for begin, until in parts:
                        studio.add(source, begin, until, keys, frame, stopped=stopped, marks=lit)
            finally:
                studio.close()
            os.replace(part, clip)
            made.append("new")
            if matching:
                noted[clip.name] = studio.tone
        if matching and (named := matched(scene, item, frame)):
            tone = noted.get(clip.name) or noted.setdefault(clip.name, clip_tone(clip))
            pooled = tones.setdefault(named, [0.0] * 7)
            pooled[:] = [total + value for total, value in zip(pooled, tone)]
        at = written / fps
        written += count
        clips.append((clip, count / fps))
        # Its times are now the video's: what its recording logged moves with it.
        shift = at - start
        scenes.append(replace(scene, input=0, start=round(at, 3), end=round(at + count / fps, 3), zoom=None,
                              camera=(), actions=(), waits=tuple((begin + shift, until + shift) for begin, until in scene.waits)))
    if matching:
        _keep_tones(shelf, {path.name: noted[path.name] for path, _seconds in clips if path.name in noted})
        found = colour_match(tones)
        scenes = [replace(scene, tone=found.get(matched(scene, item, frame), ())) if item is not None else scene
                  for scene, (_asked, item, _part, _keys, frame, _clip, _kept) in zip(scenes, plans)]
    _concat(clips, folder / "scenes.txt")
    if cache is not None:
        _prune(cache, size, {path.name for path, _seconds in clips})
    joined = InputVideo(path="scenes.txt", label="scenes", concat=True,
                        info=VideoInfo(duration=written / fps, width=size[0], height=size[1], fps=fps,
                                       has_audio=sound))
    return [joined], replace(step, scenes=scenes), made


FRAME_WIDTH = 480


def card_frame(source: Path | bytes, *, picture: bool, at: float = 0.0) -> bytes:
    """A storyboard card's frame (C1), a JPEG at most ``FRAME_WIDTH`` wide: a picture upright, or a video's picture at
    *at* seconds (the last before it when the video is shorter), decoded here by PyAV, never made by a provider."""
    from PIL import Image

    try:
        shown = _card_picture(source, picture=picture, at=at)
    except (IndexError, Image.DecompressionBombError) as error:
        raise ValueError(str(error)) from None
    shown.thumbnail((FRAME_WIDTH, FRAME_WIDTH))
    out = io.BytesIO()
    shown.save(out, format="JPEG", quality=82)
    return out.getvalue()


def _card_picture(source: Path | bytes, *, picture: bool, at: float) -> Any:
    from PIL import Image, ImageOps

    from row_bot.video_editing import INPUT_FORMATS

    opened = source if isinstance(source, Path) else io.BytesIO(source)
    if picture:
        with Image.open(opened) as image:
            image.draft("RGB", (FRAME_WIDTH * 2, FRAME_WIDTH * 2))
            return ImageOps.exif_transpose(image).convert("RGB")
    import av

    shown = None
    try:
        with av.open(str(opened) if isinstance(opened, Path) else opened,
                     options={"protocol_whitelist": "file", "format_whitelist": INPUT_FORMATS}) as reader:
            video = reader.streams.video[0]
            offset = (reader.start_time or 0) / av.time_base
            if at > 0 and video.time_base:
                reader.seek(int((at + offset) / video.time_base), stream=video, backward=True)
            for decoded in reader.decode(video):
                shown = decoded
                if decoded.time is None or decoded.time - offset >= at:
                    break
    except av.error.FFmpegError as error:
        raise ValueError(str(error)) from None
    if shown is None:
        raise ValueError("The video has no pictures.")
    turned = shown.to_image()
    return turned.rotate(shown.rotation, expand=True) if shown.rotation else turned


def narration(lines: list[tuple[float, Path]], seconds: float, output: Path) -> None:
    """A video's voice lines laid at their times into one track, quiet between them: the edit then reads one
    sound file however many lines there are."""
    import wave

    import av
    import numpy

    from row_bot.video_editing import SOUND_FORMATS

    rate = Studio.RATE
    track = numpy.zeros(max(1, round(seconds * rate)), dtype="float32")
    for at, path in lines:
        resample = av.AudioResampler(format="flt", layout="mono", rate=rate)
        with av.open(str(path), options={"protocol_whitelist": "file", "format_whitelist": SOUND_FORMATS}) as reader:
            pieces = [piece.to_ndarray()[0] for frame in reader.decode(audio=0) for piece in resample.resample(frame)]
        pieces += [piece.to_ndarray()[0] for piece in resample.resample(None)]
        start = min(round(at * rate), track.size)
        said = numpy.concatenate(pieces)[:track.size - start] if pieces else track[:0]
        track[start:start + said.size] += said
    with wave.open(str(output), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes((numpy.clip(track, -1.0, 1.0) * 32767).astype("<i2").tobytes())
