"""Screen and app footage (C9): what a recording logged being done, shown in its video.

A recording of Row-Bot's browser, or of the screen while Row-Bot used the computer, keeps what was done
(``recordings.Recording.actions``: clicks, typing, scrolls, pages and apps opened, each with the control's box). Its
video's camera follows them (``video_studio.camera``); this module marks them:

- **ring:** a highlight ring around the control, once the camera has eased in on it, fading out before it moves on;
- **spotlight:** the rest of the picture dimmed around the control, where the view stays wide (a control too big to
  zoom to, or a video whose camera is ``spotlight``), with the same ring.

A video's camera is ``follow`` (zoom in, ring each control, spotlight where it can't zoom; the default),
``spotlight`` (stay wide, spotlight each control) or ``still`` (nothing). A fast-forwarded stretch is never marked.

Marks are drawn on the studio's finished frames (``video_studio.Studio``): each mark's masks are drawn once (a
spotlight's at a quarter of the frame's size, then scaled), cached, and laid on with Pillow's paste, so a frame costs a
paste or two and only while a mark shows; nothing is drawn pixel by pixel in Python.

A recording with no beats marked is cut into scenes by what was done (``bursts``): one scene for each burst of
actions, a quiet stretch between them sped up, a quiet end left out.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Callable, Iterable

# A mark: (from, until, x, y, width, height, spotlight), seconds in its recording's time and the control's box 0–1
# across the picture (a point when it has no size); spotlight dims around the control as well as ringing it.
Mark = tuple[float, float, float, float, float, float, bool]
# What's marked: what is done on a control. A page or app opening, or a scroll, ends the mark before it.
MARKED = ("click", "type")
FADE_IN, FADE_OUT = 0.25, 0.3
# A spotlight starts this long before its action, so the eye gets there with it, and stays this long after it ends.
LEAD = 0.2
LINGER = 1.2
# Actions on the same control this close together are one mark (a click, then typing into the field).
JOIN_GAP = 1.5
# A mark shorter than this isn't worth showing.
SHORTEST = 0.4
# A control larger than this share of the picture isn't spotlit: dimming the rest of it shows nothing.
SPOT_AREA = 0.3
# The look, as shares of the frame's height: the ring's gap from the control, its width, its corners; a point's
# ring; the spotlight's gap, its soft edge and how dark the rest goes (0–255).
PAD, RING_WIDTH, CORNER = 0.008, 0.0038, 0.008
POINT = 0.022
SPOT_PAD, FEATHER, DIM = 0.018, 0.02, 140
RING_COLOUR = (59, 130, 246)
RING_ALPHA, GLOW_ALPHA = 235, 90
# A ring grows in from this much larger as it fades in.
GROW = 0.6
# Strengths are drawn in this many steps, so a fading mark's masks are cached, not drawn again every frame.
STEPS = 12
# Beats from what was done (a recording with none marked): actions this close together are one burst; a burst's
# scene starts this long before its first action and runs on this long after its last; a quiet stretch at least
# this long between bursts is sped up (a work beat), and at the end, left out.
BURST_GAP, BURST_LEAD, BURST_TAIL, QUIET = 3.0, 1.0, 1.5, 3.0


def _ease(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return 4 * value ** 3 if value < 0.5 else 1 - (-2 * value + 2) ** 3 / 2


def _box(action: list[Any]) -> tuple[float, float, float, float]:
    return float(action[3]), float(action[4]), float(action[5]), float(action[6])


def _centre(box: tuple[float, float, float, float]) -> tuple[float, float]:
    return box[0] + box[2] / 2, box[1] + box[3] / 2


def marks(actions: Iterable[list[Any]], start: float, end: float, speed: float = 1.0, camera: str = "follow",
          keys: tuple[tuple[float, float, float, float], ...] = ()) -> tuple[Mark, ...]:
    """The marks of a scene cut from ``start`` to ``end`` of a recording with these actions ([from, until, kind, x,
    y, width, height, note]), its camera (``video_brand.CAMERAS``) and that camera's keys (``video_studio.camera``,
    seconds from the scene's start): seconds in the recording's time."""
    from row_bot.video_studio import FAST

    if camera not in ("follow", "spotlight") or speed >= FAST or end - start < 0.5:
        return ()
    done = sorted((list(action) for action in actions), key=lambda action: float(action[0]))
    breaks = [float(action[0]) for action in done if action[2] in ("open", "scroll")]
    marked = [action for action in done if action[2] in MARKED and float(action[1]) >= start and
              float(action[0]) <= end]
    found: list[list[Any]] = []
    held: set[int] = set()
    if camera == "follow":
        # Where the camera holds in on a control: a ring there, from when it arrives until it moves on.
        for (t0, x0, y0, z0), (t1, x1, y1, z1) in zip(keys, keys[1:]):
            if z0 <= 1.0 or (x0, y0, z0) != (x1, y1, z1):
                continue
            chosen = min((index for index, action in enumerate(marked)
                          if abs(_centre(_box(action))[0] - x0) < 2e-3 and abs(_centre(_box(action))[1] - y0) < 2e-3),
                         key=lambda index: abs(float(marked[index][0]) - (start + t0)), default=None)
            if chosen is not None:
                held.add(chosen)
                found.append([start + t0, start + t1, *_box(marked[chosen]), False])
    moving = [(start + t0, start + t1) for (t0, *_a, z0), (t1, *_b, z1) in zip(keys, keys[1:]) if max(z0, z1) > 1.0]
    for index, action in enumerate(marked):
        box = _box(action)
        if index in held or box[2] * box[3] > SPOT_AREA:
            continue
        begin, until = float(action[0]) - LEAD, float(action[1]) + LINGER
        if camera == "follow" and any(a < until and begin < b for a, b in moving):
            continue  # The camera is moving or in on another control then.
        if found and found[-1][6] and tuple(found[-1][2:6]) == box and begin - found[-1][1] < JOIN_GAP:
            found[-1][1] = max(found[-1][1], until)
        else:
            found.append([begin, until, *box, True])
    found.sort(key=lambda mark: mark[0])
    kept: list[Mark] = []
    for index, (begin, until, x, y, width, height, spot) in enumerate(found):
        if index + 1 < len(found):  # One at a time: a mark gives way to the next.
            until = min(until, found[index + 1][0])
        until = min([until, end, *(at for at in breaks if at > begin)])
        begin = max(begin, start)
        if until - begin >= SHORTEST:
            kept.append((round(begin, 3), round(until, 3), x, y, width, height, spot))
    return tuple(kept)


def strength(mark: Mark, at: float) -> tuple[float, int]:
    """How strongly a mark shows at ``at`` (its recording's time), 0–1 in steps of 1/``STEPS``: eased in after it
    starts, out before it ends; and how far its ring has grown in, in ``STEPS`` (``STEPS``: in place)."""
    begin, until = mark[0], mark[1]
    if not begin <= at < until:
        return 0.0, STEPS
    room = until - begin
    fade_in, fade_out = min(FADE_IN, room / 2), min(FADE_OUT, room / 2)
    rising = _ease((at - begin) / fade_in) if fade_in else 1.0
    shown = min(rising, _ease((until - at) / fade_out) if fade_out else 1.0)
    return round(shown * STEPS) / STEPS, round(STEPS * (1.0 + GROW * (1.0 - rising)))


def lit(found: tuple[Mark, ...], at: float) -> tuple[tuple[Mark, float, int], ...]:
    """The marks showing at ``at``: each with its strength and how far its ring has grown in (``strength``)."""
    return tuple((mark, *shown) for mark in found if (shown := strength(mark, at))[0] > 0)


def draw(image: Any, showing: tuple[tuple[Mark, float, int], ...],
         place: Callable[[tuple[float, float, float, float]], tuple[float, float, float, float]],
         clip: tuple[int, int, int, int] | None = None) -> None:
    """Lay the marks showing on a finished frame (a Pillow picture, in place): ``place`` turns a box 0–1 across
    the footage into the frame's pixels (left, top, right, bottom); ``clip`` is where the footage is in the frame
    (all of it by default), the only part a spotlight dims."""
    width, height = image.size
    clip = clip or (0, 0, width, height)
    for (_begin, _until, x, y, box_width, box_height, spot), shown, grow in showing:
        left, top, right, bottom = place((x, y, box_width, box_height))
        rect = (round(left), round(top), round(right), round(bottom))
        step = round(shown * STEPS)
        if spot:
            dark = _spotlight((width, height), rect, clip)
            image.paste((0, 0, 0), (0, 0, width, height), dark if step >= STEPS else _faded(dark, shown))
        at, mask, glow = _ring(height, rect, grow, step)
        image.paste(RING_COLOUR, at, glow)
        image.paste(RING_COLOUR, at, mask)


def _faded(mask: Any, shown: float) -> Any:
    """A mask at a strength below full (Pillow's lookup table)."""
    return mask.point([round(value * shown) for value in range(256)])


@lru_cache(maxsize=8)
def _spotlight(size: tuple[int, int], rect: tuple[int, int, int, int], clip: tuple[int, int, int, int]) -> Any:
    """How dark each pixel goes around a control: ``DIM`` within the footage, clear in a rounded hole around the
    control, its edge soft; drawn at a quarter of the frame's size, then scaled up."""
    from PIL import Image, ImageDraw, ImageFilter

    width, height = size
    small = (max(2, width // 4), max(2, height // 4))
    mask = Image.new("L", small, 0)
    paint = ImageDraw.Draw(mask)
    paint.rectangle(tuple(value / 4 for value in clip), fill=DIM)
    hole, radius = _around(rect, SPOT_PAD * height, (POINT + SPOT_PAD) * height, CORNER * height)
    paint.rounded_rectangle(tuple(value / 4 for value in hole), radius=radius / 4, fill=0)
    mask = mask.filter(ImageFilter.GaussianBlur(FEATHER * height / 4))
    return mask.resize(size, Image.Resampling.BILINEAR)


def _around(rect: tuple[int, int, int, int], gap: float, point: float,
            corner: float) -> tuple[tuple[float, float, float, float], float]:
    """A control's box with ``gap`` around it and its corners' radius; a point (or a control smaller than one) as a
    circle of ``point`` radius."""
    left, top, right, bottom = rect
    middle_x, middle_y = (left + right) / 2, (top + bottom) / 2
    half_x, half_y = max((right - left) / 2 + gap, point), max((bottom - top) / 2 + gap, point)
    if right - left < point and bottom - top < point:
        half_x = half_y = radius = max(half_x, half_y)
    else:
        radius = min(corner + gap, half_x, half_y)
    return (middle_x - half_x, middle_y - half_y, middle_x + half_x, middle_y + half_y), radius


@lru_cache(maxsize=96)
def _ring(height: int, rect: tuple[int, int, int, int], grow: int, step: int) -> tuple[tuple[int, int], Any, Any]:
    """A ring around a control, ``grow``/``STEPS`` times its gap while it grows in, at ``step``/``STEPS`` strength:
    where its masks go, the ring (drawn three times larger and scaled down, for smooth edges) and its soft glow."""
    from PIL import Image, ImageDraw, ImageFilter

    gap = PAD * height * grow / STEPS
    stroke = max(2.0, RING_WIDTH * height)
    ring, radius = _around(rect, gap, POINT * height * grow / STEPS, CORNER * height)
    margin = stroke * 3
    at = (int(ring[0] - margin), int(ring[1] - margin))
    size = (max(2, int(ring[2] + margin) - at[0]), max(2, int(ring[3] + margin) - at[1]))
    big = Image.new("L", (size[0] * 3, size[1] * 3), 0)
    shape = [(value - at[index % 2]) * 3 for index, value in enumerate(ring)]
    ImageDraw.Draw(big).rounded_rectangle(shape, radius=radius * 3, outline=255, width=round(stroke * 3))
    full = big.resize(size, Image.Resampling.LANCZOS)
    shown = step / STEPS
    mask = full.point([round(value * RING_ALPHA / 255 * shown) for value in range(256)])
    glow = full.filter(ImageFilter.GaussianBlur(stroke * 1.2)).point(
        [min(255, round(value * GLOW_ALPHA / 255 * 1.6 * shown)) for value in range(256)])
    return at, mask, glow


def bursts(actions: Iterable[list[Any]], length: float) -> list[tuple[float, str, str, str]]:
    """Beats for a recording with none marked, from what was done in it: a live beat where each burst of actions
    starts, a work beat (sped up) where a quiet stretch between bursts starts, a cut where a quiet end does;
    [(seconds, live | work | cut, note, line)], with no notes or lines (the storyboard's are written for it). None
    when nothing was done."""
    times = sorted((float(action[0]), float(action[1])) for action in actions)
    if not times:
        return []
    groups: list[list[float]] = []
    for begin, until in times:
        if groups and begin - groups[-1][1] < BURST_GAP:
            groups[-1][1] = max(groups[-1][1], until)
        else:
            groups.append([begin, until])
    beats: list[tuple[float, str, str, str]] = []
    after = 0.0  # Where the last burst's scene ends.
    for begin, until in groups:
        lead = max(0.0, begin - BURST_LEAD)
        if lead - after >= QUIET:
            beats.append((round(after, 2), "work", "", ""))
        elif not beats:
            lead = 0.0  # A short wait before the first burst is part of its scene.
        beats.append((round(lead, 2), "live", "", ""))
        after = min(length, until + BURST_TAIL)
    if length - after >= QUIET:
        beats.append((round(after, 2), "cut", "", ""))
    return beats
