"""The studio pass of a video project (5.3.2): each scene cut from a video gets its camera and its frame.

The camera follows what Row-Bot did while recording (``recordings.Recording.actions``): it eases in on the control
being clicked or typed into, pans to the next one when it comes soon after, and eases back out before a new page or
after a pause; a fast-forwarded stretch is never zoomed. A scene's own zoom (the agent's or the person's) wins, eased
in. The frame is the look: the picture on the brand's backdrop with rounded corners and a soft shadow ("framed"), or
edge to edge ("full").

Each such scene is drawn frame by frame (Pillow, sub-pixel crops, so a slow zoom never shimmers) and encoded once
into its own clip; the edit then joins, captions and mixes those clips as it does any video. The camera path and
one frame's look are pure; only ``render_scene`` reads and writes video.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

FRAMINGS = ("framed", "full")
CAMERAS = ("follow", "still")
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
SHADOW_BLUR, SHADOW_DROP, SHADOW_ALPHA = 0.022, 0.012, 120


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
    framing: str = "framed"
    colour: tuple[int, int, int] = (31, 41, 55)


@lru_cache(maxsize=4)
def _setting(frame: Frame, picture: tuple[int, int]) -> tuple[Any, tuple[int, int], tuple[int, int], Any]:
    """The framed look for a picture of this size: (the backdrop with its shadow, where the picture goes, its size
    there, its rounded mask)."""
    from PIL import Image, ImageDraw, ImageFilter

    from row_bot.video_editing import card_backdrop

    width, height = frame.size
    scale = min(width * FRAME_FILL / picture[0], height * FRAME_FILL / picture[1])
    inner = (max(2, round(picture[0] * scale)), max(2, round(picture[1] * scale)))
    at = ((width - inner[0]) // 2, (height - inner[1]) // 2)
    radius = round(height * CORNER)
    mask = Image.new("L", inner, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, inner[0] - 1, inner[1] - 1), radius=radius, fill=255)
    backdrop = Image.open(io.BytesIO(card_backdrop(frame.colour, width, height))).convert("RGB")
    shadow = Image.new("L", frame.size, 0)
    drop = round(height * SHADOW_DROP)
    ImageDraw.Draw(shadow).rounded_rectangle((at[0], at[1] + drop, at[0] + inner[0], at[1] + drop + inner[1]),
                                             radius=radius, fill=SHADOW_ALPHA)
    shadow = shadow.filter(ImageFilter.GaussianBlur(height * SHADOW_BLUR))
    backdrop.paste(Image.new("RGB", frame.size, (0, 0, 0)), (0, 0), shadow)
    return backdrop, at, inner, mask


def compose(picture: Any, frame: Frame, at: tuple[float, float, float] = (0.5, 0.5, 1.0)) -> Any:
    """One finished frame: the picture through the camera's view, set in the frame's look."""
    from PIL import Image

    box = crop_box(*at, picture.width, picture.height)
    if frame.framing == "full":
        width, height = frame.size
        # Edge to edge: the view filled to the frame's shape, cropped around its middle.
        seen_width, seen_height = box[2] - box[0], box[3] - box[1]
        if seen_width / seen_height > width / height:
            trim = (seen_width - seen_height * width / height) / 2
            box = (box[0] + trim, box[1], box[2] - trim, box[3])
        else:
            trim = (seen_height - seen_width * height / width) / 2
            box = (box[0], box[1] + trim, box[2], box[3] - trim)
        return picture.resize(frame.size, Image.Resampling.LANCZOS, box=box)
    backdrop, place, inner, mask = _setting(frame, (picture.width, picture.height))
    finished = backdrop.copy()
    finished.paste(picture.resize(inner, Image.Resampling.LANCZOS, box=box), place, mask)
    return finished


def needs_pass(keys: tuple[Key, ...], frame: Frame, picture: tuple[int, int]) -> bool:
    """Whether a scene is drawn here: it has a camera, the framed look, or a picture not the frame's size."""
    return bool(keys) or frame.framing == "framed" or tuple(picture) != frame.size


def render_scene(source: Path, start: float, end: float, keys: tuple[Key, ...], frame: Frame, output: Path, *,
                 fps: float = 30.0, stopped: Callable[[], bool] = lambda: False) -> tuple[float, bool]:
    """Draw ``start``–``end`` of a video through its camera into ``output`` (H.264, its sound copied): the clip's
    seconds and whether it has sound."""
    import av

    from row_bot.video_editing import EditError

    count = max(1, round((end - start) * fps))
    latest = None
    seen = b""
    with av.open(str(source)) as reader, av.open(str(output), "w") as writer:
        video = reader.streams.video[0]
        sound = reader.streams.audio[0] if reader.streams.audio else None
        out = writer.add_stream("libx264", rate=round(fps))
        out.width, out.height = frame.size
        out.pix_fmt = "yuv420p"
        out.options = {"crf": "18", "preset": "veryfast"}
        out.thread_type = "AUTO"
        clip = _Clip(writer, out, keys, frame, fps)
        copied = writer.add_stream_from_template(sound) if sound is not None else None
        if start > 0 and video.time_base:
            reader.seek(int(start / video.time_base), stream=video, backward=True)
        for packet in reader.demux(*([video] + ([sound] if sound is not None else []))):
            if stopped():
                raise EditError("Stopped.")
            if packet.stream is sound:
                if packet.pts is not None and packet.dts is not None and sound.time_base:
                    if start <= float(packet.pts * sound.time_base) < end:
                        shift = int(start / sound.time_base)
                        packet.pts -= shift
                        packet.dts -= shift
                        packet.stream = copied
                        writer.mux(packet)
                continue
            for decoded in packet.decode():
                when = decoded.time if decoded.time is not None else 0.0
                # Each frame of the clip shows the latest picture at or before its moment.
                while latest is not None and clip.written < count and start + clip.written / fps < when - 0.5 / fps:
                    clip.write(latest)
                luma = bytes(decoded.planes[0])
                if when < end and luma != seen:  # A still page repeats one picture: it's drawn once.
                    latest, seen = decoded.to_image(), luma
            if clip.written >= count:
                break
        while latest is not None and clip.written < count:
            clip.write(latest)
        for packet in out.encode(None):
            writer.mux(packet)
    if latest is None:
        raise EditError(f"{source.name} has no pictures from {start:g} s.")
    return clip.written / fps, sound is not None


class _Clip:
    """The clip being written: each frame the picture through the camera's view at its moment, drawn again only
    when the picture or the view has changed."""

    def __init__(self, writer: Any, out: Any, keys: tuple[Key, ...], frame: Frame, fps: float) -> None:
        self.writer, self.out, self.keys, self.frame, self.fps = writer, out, keys, frame, fps
        self.written = 0
        self._drawn: tuple[Any, Any] = (None, None)

    def write(self, picture: Any) -> None:
        import av

        at = view(self.keys, self.written / self.fps)
        key = (id(picture), tuple(round(value, 5) for value in at))
        if self._drawn[0] != key:
            self._drawn = (key, compose(picture, self.frame, at))
        encoded = av.VideoFrame.from_image(self._drawn[1]).reformat(format="yuv420p")
        encoded.pts = self.written
        for packet in self.out.encode(encoded):
            self.writer.mux(packet)
        self.written += 1


def prepare(inputs: list[Any], step: Any, folder: Path, *, stopped: Callable[[], bool] = lambda: False,
            progress: Callable[[int, int], None] = lambda _done, _total: None) -> tuple[list[Any], Any]:
    """A video project's scenes cut from videos, each drawn through its camera and look into its own clip
    (``scene-01.mp4``…): the inputs and scenes the edit then joins, captions and mixes. A 16:9 video gets the
    look; another shape keeps each picture's own and is reframed by the edit, as before."""
    from dataclasses import replace

    from row_bot.video_editing import InputVideo, Look, VideoInfo, fit_to_lines

    look = step.look or Look()
    fitted = [scene for scene, _hold in fit_to_lines(step.scenes, [item.info.duration for item in inputs])]
    footage = [scene for scene in fitted if scene.input is not None and 0 <= scene.input < len(inputs)]
    if len(footage) != len([scene for scene in fitted if scene.input is not None]) or not footage:
        return inputs, step  # (The edit says what's wrong with a scene that isn't one of the inputs.)
    first = inputs[footage[0].input].info
    wide = (step.aspect or ("16:9" if first.width / max(1, first.height) > 1.2 else "")) == "16:9"
    clips: list[Any] = []
    originals: list[int] = []
    placed: list[tuple[Any, str, int]] = []
    for scene in fitted:
        if scene.input is None:
            placed.append((scene, "card", 0))
            continue
        info = inputs[scene.input].info
        keys = zoom_keys(scene.zoom) if scene.zoom else scene.camera if look.camera == "follow" else ()
        frame = (Frame((1920, 1080), look.framing, look.colour) if wide else
                 Frame((info.width // 2 * 2, info.height // 2 * 2), "full", look.colour))
        if not needs_pass(keys, frame, (info.width, info.height)):
            if scene.input not in originals:
                originals.append(scene.input)
            placed.append((scene, "kept", scene.input))
            continue
        progress(len(clips), len(footage))
        name = f"scene-{len(clips) + 1:02d}.mp4"
        start = min(max(0.0, scene.start), info.duration)
        end = info.duration if scene.end is None else min(scene.end, info.duration)
        source = Path(inputs[scene.input].path)
        seconds, sound = render_scene(source if source.is_absolute() else folder / source, start, end, keys, frame,
                                      folder / name, fps=info.fps or 30.0, stopped=stopped)
        clips.append(InputVideo(path=name, label=inputs[scene.input].label,
                                info=VideoInfo(duration=seconds, width=frame.size[0], height=frame.size[1],
                                               fps=float(round(info.fps or 30.0)), has_audio=sound)))
        placed.append((replace(scene, start=0.0, end=round(seconds, 3), zoom=None, camera=()), "clip",
                       len(clips) - 1))
    scenes = [scene if kind == "card" else
              replace(scene, input=where if kind == "clip" else len(clips) + originals.index(where))
              for scene, kind, where in placed]
    return clips + [inputs[index] for index in originals], replace(step, scenes=scenes)
