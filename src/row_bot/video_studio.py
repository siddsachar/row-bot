"""The studio pass of a video project (5.3.2): each scene cut from a video gets its camera and its frame.

The camera follows what Row-Bot did while recording (``recordings.Recording.actions``): it eases in on the control
being clicked or typed into, pans to the next one when it comes soon after, and eases back out before a new page or
after a pause; a fast-forwarded stretch is never zoomed. A scene's own zoom (the agent's or the person's) wins, eased
in. The frame is the look: the picture on the brand's backdrop with rounded corners and a soft shadow ("framed"), or
edge to edge ("full").

The scenes are drawn frame by frame (Pillow, sub-pixel crops, so a slow zoom never shimmers) one after another into
one clip; the edit then cuts, captions and mixes it as it does any video. The camera path and one frame's look are
pure; only ``Studio`` reads and writes video.
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
# A phone's picture (taller than wide) is set in a device: a dark rim this thick and rounded (fractions of its
# width), a little smaller in the frame so the rim fits.
PHONE_FILL, PHONE_RIM, PHONE_CORNER = 0.84, 0.045, 0.12
# Room a caption takes at the bottom of a 16:9 frame (a line in its largest style, and its margin).
CAPTION_ROOM = 0.13
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


class Studio:
    """The drawn scenes of a video, one after another in one clip (``scenes.mp4``): each frame the picture through
    its scene's camera and look, drawn again only when the picture or the view has changed; its sound, when any
    scene has some, kept in step (silence where a scene has none). One clip, however many scenes: the edit reads
    it as one input."""

    RATE = 48000

    def __init__(self, output: Path, size: tuple[int, int], *, fps: float = 30.0, sound: bool = False) -> None:
        import av

        self.fps = fps
        self.written = 0
        self._samples = 0
        self._writer = av.open(str(output), "w")
        self._video = self._writer.add_stream("libx264", rate=round(fps))
        self._video.width, self._video.height = size
        self._video.pix_fmt = "yuv420p"
        self._video.options = {"crf": "18", "preset": "veryfast"}
        self._video.thread_type = "AUTO"
        self._audio = self._writer.add_stream("aac", rate=self.RATE, layout="stereo") if sound else None
        self._fifo = av.AudioFifo() if sound else None
        self._drawn: tuple[Any, Any] = (None, None)

    def add(self, source: Path, start: float, end: float, keys: tuple[Key, ...], frame: Frame, *,
            stopped: Callable[[], bool] = lambda: False) -> tuple[float, float]:
        """Draw ``start``–``end`` of a video at the end of the clip: where it starts in the clip, and how long."""
        import av

        from row_bot.video_editing import INPUT_FORMATS, EditError

        first = self.written
        count = max(1, round((end - start) * self.fps))
        latest = None
        seen = b""
        resample = av.AudioResampler(format="fltp", layout="stereo", rate=self.RATE) if self._audio else None
        with av.open(str(source), options={"protocol_whitelist": "file", "format_whitelist": INPUT_FORMATS}) as reader:
            video = reader.streams.video[0]
            sound = reader.streams.audio[0] if reader.streams.audio and self._audio else None
            if start > 0 and video.time_base:
                reader.seek(int(start / video.time_base), stream=video, backward=True)
            for packet in reader.demux(*([video] + ([sound] if sound is not None else []))):
                if stopped():
                    raise EditError("Stopped.")
                if packet.stream is sound:
                    for decoded in packet.decode():
                        self._hear(decoded, resample, start, end)
                    continue
                for decoded in packet.decode():
                    when = decoded.time if decoded.time is not None else 0.0
                    # Each frame shows the latest picture at or before its moment.
                    while latest is not None and self.written - first < count and \
                            start + (self.written - first) / self.fps < when - 0.5 / self.fps:
                        self._draw(latest, keys, frame, first)
                    luma = bytes(decoded.planes[0])
                    if when < end and luma != seen:  # A still page repeats one picture: it's drawn once.
                        latest, seen = decoded.to_image(), luma
                if self.written - first >= count:
                    break
        if latest is None:
            raise EditError(f"{source.name} has no pictures from {start:g} s.")
        while self.written - first < count:
            self._draw(latest, keys, frame, first)
        self._pad()
        return first / self.fps, count / self.fps

    def _draw(self, picture: Any, keys: tuple[Key, ...], frame: Frame, first: int) -> None:
        import av

        at = view(keys, (self.written - first) / self.fps)
        key = (id(picture), frame, tuple(round(value, 5) for value in at))
        if self._drawn[0] != key:
            self._drawn = (key, compose(picture, frame, at))
        encoded = av.VideoFrame.from_image(self._drawn[1]).reformat(format="yuv420p")
        encoded.pts = self.written
        for packet in self._video.encode(encoded):
            self._writer.mux(packet)
        self.written += 1

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

        piece = av.AudioFrame.from_ndarray(samples.astype("float32"), format="fltp", layout="stereo")
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


def prepare(inputs: list[Any], step: Any, folder: Path, *, stopped: Callable[[], bool] = lambda: False,
            progress: Callable[[int, int], None] = lambda _done, _total: None) -> tuple[list[Any], Any]:
    """A video project's scenes cut from videos, drawn through their cameras and look one after another into one
    clip (``scenes.mp4``): the inputs and scenes the edit then joins, captions and mixes. A 16:9 video gets the
    look; another shape keeps each picture's shape and is reframed by the edit, as before."""
    from dataclasses import replace

    from row_bot.video_editing import InputVideo, Look, VideoInfo, fit_to_lines

    look = step.look or Look()
    fitted = [scene for scene, _hold in fit_to_lines(step.scenes, [item.info.duration for item in inputs])]
    footage = [scene for scene in fitted if scene.input is not None and 0 <= scene.input < len(inputs)]
    if len(footage) != len([scene for scene in fitted if scene.input is not None]) or not footage:
        return inputs, step  # (The edit says what's wrong with a scene that isn't one of the inputs.)
    first = inputs[footage[0].input].info
    wide = (step.aspect or ("16:9" if first.width / max(1, first.height) > 1.2 else "")) == "16:9"
    size = (1920, 1080) if wide else (first.width // 2 * 2, first.height // 2 * 2)
    frame = Frame(size, look.framing if wide else "full", look.colour,
                  captions=CAPTION_ROOM if step.style == "caption" else 0.0)

    def keys_of(scene: Any) -> tuple[Key, ...]:
        return zoom_keys(scene.zoom) if scene.zoom else scene.camera if look.camera == "follow" else ()

    drawn = [scene for scene in footage
             if needs_pass(keys_of(scene), frame, (inputs[scene.input].info.width, inputs[scene.input].info.height))]
    if not drawn:
        return inputs, step
    fps = float(round(first.fps or 30.0))
    studio = Studio(folder / "scenes.mp4", size, fps=fps,
                    sound=any(inputs[scene.input].info.has_audio for scene in drawn))
    kept = sorted({scene.input for scene in footage if scene not in drawn})
    scenes = []
    try:
        for scene in fitted:
            if scene.input is None:
                scenes.append(scene)
                continue
            if scene not in drawn:
                scenes.append(replace(scene, input=1 + kept.index(scene.input)))
                continue
            progress(len([done for done in scenes if done.input == 0]), len(drawn))
            info = inputs[scene.input].info
            start = min(max(0.0, scene.start), info.duration)
            end = info.duration if scene.end is None else min(scene.end, info.duration)
            source = Path(inputs[scene.input].path)
            at, seconds = studio.add(source if source.is_absolute() else folder / source, start, end,
                                     keys_of(scene), frame, stopped=stopped)
            scenes.append(replace(scene, input=0, start=round(at, 3), end=round(at + seconds, 3), zoom=None,
                                  camera=()))
    finally:
        studio.close()
    clip = InputVideo(path="scenes.mp4", label="scenes",
                      info=VideoInfo(duration=studio.written / fps, width=size[0], height=size[1], fps=fps,
                                     has_audio=studio.has_sound))
    return [clip] + [inputs[index] for index in kept], replace(step, scenes=scenes)


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
