"""Record a conversation's page in Row-Bot's managed browser to an MP4 (F2).

The browser's live picture (the same screencast the Browser pane shows) offers each new JPEG;
the recorder keeps only the latest, and an encoder thread writes that frame at a steady rate into
an H.264 MP4 through PyAV (installed with Row-Bot's
voice support; recording says plainly when it isn't there). Pages with a password, one-time
code or card field, and anything shown while the person has taken over the browser, are
recorded as a blank "hidden" frame, as the live preview hides them.

While it records, Row-Bot's steps report where they point (``pointer``): the video shows a drawn
pointer that eases to each target and rings where it clicks, so a viewer can follow. ``beat``
marks a moment on the recording's clock as ``live`` (watch it as it is), ``work`` (a wait or busy
stretch to speed up) or ``cut`` (leave it out): a video project makes its scenes from them.
"""

from __future__ import annotations

import io
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

FPS = 30
# Frames are scaled down so neither side is larger than this.
MAX_SIDE = 1920
# Picture quality: interface text stays readable when a video project zooms in on it.
CRF = "18"
# Pointer moves kept with a recording, for following it in another shape.
TRAIL_POINTS = 2_000
# The drawn pointer: how long it takes to reach a target, and how long a click's ring shows.
MOVE_SECONDS = 0.45
RING_SECONDS = 0.5
BEATS = ("live", "work", "cut")
# What Row-Bot did while recording, for a video's camera to follow: clicks, typing, scrolls, pages opened.
ACTIONS = ("click", "type", "scroll", "open")
MAX_ACTIONS = 500
# Fields whose contents a recording never shows (the same as the live preview's shield).
PROTECTED_FIELDS = (
    'input[type="password"], input[autocomplete="one-time-code"], '
    'input[autocomplete="cc-number"], input[autocomplete="cc-csc"]'
)
_HIDDEN_COLOUR = (38, 40, 46)


def encoder_problem() -> str:
    """Why this copy of Row-Bot can't encode a recording, in plain words; empty when it can."""

    try:
        import av

        av.codec.Codec("libx264", "w")
    except Exception:
        return (
            "Recording needs Row-Bot's video support, which isn't installed in this copy "
            "(it comes with the full install)."
        )
    return ""


def _even(value: float) -> int:
    return max(2, int(value) // 2 * 2)


class PageRecorder:
    """One recording of one conversation's page.

    The browser offers frames (``offer_frame``) and sets ``shielded``; the encoder runs on its
    own thread and calls ``on_end(reason, path)`` once when it stops.
    """

    def __init__(
        self,
        path: Path,
        *,
        max_seconds: float,
        size_limit: int,
        on_end: Callable[[str, Path | None], None],
        clock: Callable[[], float] = time.monotonic,
        tick: float = 1.0 / FPS,
        size: tuple[int, int] | None = None,
        pointer_size: float = 0.0,
    ) -> None:
        self.path = Path(path)
        # The video's size, fixed for the whole recording: the page's size while it records. A picture of
        # another size (a tab still at the pane's size for a moment) is fitted into it. None: the first
        # picture's size.
        self.size = (_even(size[0]), _even(size[1])) if size else None
        # The drawn pointer's height in the video's pixels (0: from the picture's size).
        self.pointer_size = float(pointer_size)
        self._tick = float(tick)
        self.max_seconds = float(max_seconds)
        self.size_limit = int(size_limit)
        self._on_end = on_end
        self._clock = clock
        self._lock = threading.Lock()
        self._frame: bytes | None = None
        self._stop = threading.Event()
        self._reason = ""
        self.shielded = False
        # The browser's live-picture subscription that feeds this recording.
        self.watch: Any = None
        self.frames = 0
        self.bytes_written = 0
        self.started_at = 0.0
        # Where the pointer goes: (when, x, y, clicked, from x, from y), x and y 0–1 across the page.
        self._pointer: list[tuple[float, float, float, bool, float, float]] = []
        # Where the pointer went, kept with the recording: a square or tall cut follows it (5.3.1).
        self.trail: list[tuple[float, float, float]] = []
        # Marked moments: (seconds into the recording, live | work | cut, a note).
        self.beats: list[tuple[float, str, str]] = []
        # What was done: [from, until, click | type | scroll | open, x, y, width, height, a note], seconds into the
        # recording and the control's box 0–1 across the page (the whole page when there's no control).
        self.actions: list[list[Any]] = []
        self._thread = threading.Thread(target=self._run, daemon=True, name="row-bot-browser-recording")

    # ---- Control ----------------------------------------------------------------------

    def start(self) -> None:
        self.started_at = self._clock()
        self._thread.start()

    def request_stop(self, reason: str) -> None:
        if not self._reason:
            self._reason = str(reason or "agent")
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def join(self, timeout: float | None) -> bool:
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _now(self) -> float:
        return max(0.0, self._clock() - self.started_at) if self.started_at else 0.0

    def pointer(self, x: float, y: float, *, click: bool = False) -> None:
        """The pointer goes to (x, y), each 0–1 across the page, easing from where it was; ``click`` rings."""
        x, y = min(1.0, max(0.0, float(x))), min(1.0, max(0.0, float(y)))
        now = self._now()
        with self._lock:
            here = pointer_at(self._pointer, now)
            start = (here[0], here[1]) if here else (x, y)
            self._pointer.append((now, x, y, bool(click), *start))
            del self._pointer[:-16]
            self.trail.append((round(now, 2), round(x, 4), round(y, 4)))
            del self.trail[:-TRAIL_POINTS]

    def beat(self, kind: str, note: str = "") -> float:
        """Mark this moment; its seconds into the recording."""
        if kind not in BEATS:
            raise ValueError(f"A beat is {', '.join(BEATS)}.")
        at = round(self._now(), 2)
        with self._lock:
            self.beats.append((at, kind, str(note or "")[:120]))
        return at

    def act(self, kind: str, box: tuple[float, float, float, float] | None = None, note: str = "") -> None:
        """Something is being done now, on this box of the page (x, y, width, height, each 0–1 across it)."""
        if kind not in ACTIONS:
            raise ValueError(f"An action is {', '.join(ACTIONS)}.")
        x, y, width, height = box or (0.0, 0.0, 1.0, 1.0)
        x, y = min(1.0, max(0.0, float(x))), min(1.0, max(0.0, float(y)))
        width, height = min(1.0 - x, max(0.0, float(width))), min(1.0 - y, max(0.0, float(height)))
        now = round(self._now(), 2)
        with self._lock:
            self.actions.append([now, now, kind, round(x, 4), round(y, 4), round(width, 4), round(height, 4),
                                 str(note or "")[:80]])
            del self.actions[:-MAX_ACTIONS]

    def act_done(self) -> None:
        """The last action has finished (typing ends after its last key)."""
        with self._lock:
            if self.actions:
                self.actions[-1][1] = round(self._now(), 2)

    def offer_frame(self, data: bytes) -> None:
        """The page's latest picture (a JPEG)."""

        with self._lock:
            self._frame = bytes(data)

    # ---- Encoder thread ---------------------------------------------------------------

    def _run(self) -> None:
        reason, saved = "error", None
        try:
            reason, saved = self._encode()
        except Exception:
            logger.warning("The browser recording could not be encoded", exc_info=True)
            reason, saved = "error", None
        finally:
            try:
                self._on_end(reason, saved)
            except Exception:
                logger.warning("A finished browser recording could not be saved", exc_info=True)

    def _encode(self) -> tuple[str, Path | None]:
        import av
        from PIL import Image, ImageOps

        container = None
        stream = None
        size: tuple[int, int] | None = None
        last_data: bytes | None = None
        last_shielded = False
        last_frame = None
        base = None
        drawn: tuple[float, float, float] | None = None
        index = 0
        reason = ""
        try:
            while True:
                if self._stop.is_set():
                    reason = self._reason or "agent"
                    break
                elapsed = self._clock() - self.started_at
                if elapsed >= self.max_seconds:
                    reason = "limit"
                    break
                if self.bytes_written >= self.size_limit:
                    reason = "size"
                    break
                with self._lock:
                    data = self._frame
                if data is not None:
                    if container is None:
                        size = self.size
                        if size is None:
                            first = Image.open(io.BytesIO(data))
                            scale = min(1.0, MAX_SIDE / max(first.size))
                            size = (_even(first.size[0] * scale), _even(first.size[1] * scale))
                        self.path.parent.mkdir(parents=True, exist_ok=True)
                        container = av.open(str(self.path), mode="w", format="mp4")
                        stream = container.add_stream("libx264", rate=FPS)
                        stream.width, stream.height = size
                        stream.pix_fmt = "yuv420p"
                        stream.options = {"preset": "veryfast", "crf": CRF}
                    shielded = self.shielded
                    if base is None or data is not last_data or shielded != last_shielded:
                        if shielded:
                            base = Image.new("RGB", size, _HIDDEN_COLOUR)
                        else:
                            picture = Image.open(io.BytesIO(data)).convert("RGB")
                            base = picture if picture.size == size else ImageOps.pad(
                                picture, size, method=Image.Resampling.LANCZOS, color=(0, 0, 0))
                        last_frame, drawn = None, None
                        last_data, last_shielded = data, shielded
                    # Keep real time: write the latest picture for every frame due so far.
                    due = int(elapsed * FPS)
                    caught_up = 0
                    while index <= due and caught_up < FPS * 2:
                        with self._lock:
                            state = None if shielded else pointer_at(self._pointer, index / FPS)
                        if last_frame is None or state != drawn:
                            image = base
                            if state is not None:
                                image = base.copy()
                                draw_pointer(image, *state, size=self.pointer_size)
                            last_frame, drawn = av.VideoFrame.from_image(image), state
                        last_frame.pts = index
                        for packet in stream.encode(last_frame):
                            self.bytes_written += packet.size
                            container.mux(packet)
                        index += 1
                        caught_up += 1
                    self.frames = index
                self._stop.wait(self._tick)
        finally:
            if container is not None and stream is not None:
                for packet in stream.encode(None):
                    container.mux(packet)
                container.close()
        if container is None or index == 0:
            return reason or "agent", None
        return reason, self.path


def _ease(progress: float) -> float:
    """Ease in and out (cubic): a pointer starts and arrives gently."""
    progress = min(1.0, max(0.0, progress))
    return 4 * progress ** 3 if progress < 0.5 else 1 - (-2 * progress + 2) ** 3 / 2


def pointer_at(events: list[tuple[float, float, float, bool, float, float]],
               at: float) -> tuple[float, float, float] | None:
    """Where the drawn pointer is at ``at`` seconds, 0–1 across the page, and how far its click ring has
    grown (0 for none); None before the first target."""
    current = [event for event in events if event[0] <= at]
    if not current:
        return None
    when, x, y, _click, from_x, from_y = current[-1]
    step = _ease((at - when) / MOVE_SECONDS)
    here_x, here_y = from_x + (x - from_x) * step, from_y + (y - from_y) * step
    clicks = [event[0] for event in current if event[3] and at - event[0] < RING_SECONDS]
    ring = round((at - clicks[-1]) / RING_SECONDS, 3) if clicks else 0.0
    return round(here_x, 4), round(here_y, 4), ring


def draw_pointer(image: Any, x: float, y: float, ring: float = 0.0, *, size: float = 0.0) -> None:
    """Draw an arrow pointer at (x, y), 0–1 across the picture, and a click's ring while it grows.

    ``size`` is the arrow's height in the picture's pixels; 0 sizes it from the picture's shorter side.
    """
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image, "RGBA")
    width, height = image.size
    left, top = x * width, y * height
    size = size or max(12.0, min(width, height) * 0.032)
    if ring > 0:
        radius = size * (0.4 + 1.2 * ring)
        alpha = round(200 * (1 - ring))
        draw.ellipse((left - radius, top - radius, left + radius, top + radius),
                     outline=(59, 130, 246, alpha), width=max(2, round(size * 0.14)))
    arrow = [(0, 0), (0, 1.0), (0.27, 0.76), (0.44, 1.12), (0.58, 1.05), (0.42, 0.7), (0.76, 0.7)]
    points = [(left + dx * size, top + dy * size) for dx, dy in arrow]
    draw.polygon(points, fill=(255, 255, 255, 255), outline=(17, 24, 39, 255))
    draw.line(points + points[:1], fill=(17, 24, 39, 255), width=max(1, round(size * 0.07)))
