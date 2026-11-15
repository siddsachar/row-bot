"""The desktop pet's decisions, free of any window system.

Where Buddy sits (a window's top edge or the floor above the taskbar/Dock),
when it follows you to another window (D9: after ~3 s of use, at most every
20 s, never while its bubble is open or while dragged), how it gets there,
and which pixels of its window take clicks. Everything is in DIPs, with the
origin at the top-left of the primary display, and every clock is passed in.
"""

from __future__ import annotations

import base64
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping

from .overlay import ScreenArea, nearest_screen

#: The pet window when its bubble is closed: Buddy plus room to hop.
PET_WIDTH = 168
PET_HEIGHT = 176
#: How far above the window's bottom edge Buddy's feet are.
FOOT = 10
FOLLOW_DWELL_SECONDS = 3.0
FOLLOW_COOLDOWN_SECONDS = 20.0
#: A window must be at least this wide to be perched on.
MIN_PERCH_WIDTH = PET_WIDTH * 2
MASK_CELLS = 24
_MASK_BYTES = MASK_CELLS * MASK_CELLS // 8


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom


@dataclass(frozen=True)
class OtherWindow:
    """Another app's window: an opaque id and its visible frame. Never its title or contents."""

    id: int
    frame: Rect


def floor_position(pet_x: float, screens: Iterable[ScreenArea]) -> tuple[int, int]:
    """Standing on the floor of the screen under Buddy: the bottom of its work area."""

    screen = nearest_screen(pet_x + PET_WIDTH / 2, 0, screens)
    x = min(max(pet_x, screen.x), screen.right - PET_WIDTH)
    return round(x), round(screen.bottom - PET_HEIGHT + FOOT)


def perch_position(window: Rect, pet_x: float, screens: Iterable[ScreenArea]) -> tuple[int, int] | None:
    """Sitting on a window's top edge, nearest where Buddy is now; None when it can't sit there."""

    screens = list(screens)
    if window.width < MIN_PERCH_WIDTH:
        return None
    screen = nearest_screen(window.x + window.width / 2, window.y, screens)
    top = window.y - PET_HEIGHT + FOOT
    if top < screen.y or window.y > screen.bottom - 40:
        return None  # its top edge is off screen, or nearly at the floor
    x = min(max(pet_x, window.x + 8), window.right - PET_WIDTH - 8)
    return round(x), round(top)


def window_in_use(listed: Iterable[tuple[int, OtherWindow]], own_pid: int) -> OtherWindow | None:
    """The window you're using, from (owner pid, window) front to back: the frontmost, unless it's Row-Bot's own."""

    for pid, window in listed:
        return None if pid == own_pid else window
    return None


def window_under(x: float, y: float, windows: Iterable[OtherWindow]) -> OtherWindow | None:
    """The topmost window whose top edge band (±24 DIP) is under a drop point."""

    for window in windows:  # front to back
        if window.frame.x <= x < window.frame.right and abs(y - window.frame.y) <= 24:
            return window
    return None


@dataclass
class Move:
    """An eased path from one window position to another."""

    start: tuple[float, float]
    end: tuple[float, float]
    started: float
    duration: float
    kind: Literal["hop", "fall", "ride"]

    def at(self, now: float) -> tuple[int, int]:
        t = min(1.0, max(0.0, (now - self.started) / self.duration)) if self.duration > 0 else 1.0
        (sx, sy), (ex, ey) = self.start, self.end
        if self.kind == "fall":
            eased = t * t  # gravity
            return round(sx + (ex - sx) * t), round(sy + (ey - sy) * eased)
        eased = t * t * (3 - 2 * t)
        x = sx + (ex - sx) * eased
        y = sy + (ey - sy) * eased
        if self.kind == "hop":
            y -= math.sin(math.pi * t) * min(140.0, 40.0 + abs(ex - sx) * 0.25)
        return round(x), round(y)

    def done(self, now: float) -> bool:
        return now - self.started >= self.duration


def hop(start: tuple[float, float], end: tuple[float, float], now: float) -> Move:
    distance = math.dist(start, end)
    return Move(start, end, now, min(1.1, 0.45 + distance / 1800), "hop")


def fall(start: tuple[float, float], end: tuple[float, float], now: float) -> Move:
    height = max(0.0, end[1] - start[1])
    return Move(start, end, now, min(0.9, 0.18 + math.sqrt(height) / 45), "fall")


@dataclass
class Follower:
    """Which window Buddy sits on, and when it moves. The host feeds it what it sees."""

    perched_on: int | None = None
    #: Following is paused after a drop on the floor, until a drop on a window.
    following: bool = True
    candidate: int | None = None
    candidate_since: float = 0.0
    last_hop: float = -math.inf
    offset_x: float = 0.0  # where along its window Buddy sits

    def dropped(self, on: OtherWindow | None, pet_x: float, *, by_hand: bool = True) -> None:
        self.perched_on = on.id if on else None
        # Put on the floor by hand, Buddy stays there; landing there on its own (popped out, the app starting) doesn't.
        self.following = on is not None or not by_hand
        self.offset_x = pet_x - on.frame.x if on else 0.0
        self.candidate = None

    def decide(self, *, now: float, enabled: bool, foreground: OtherWindow | None,
               windows: Mapping[int, OtherWindow], pet: tuple[float, float],
               screens: list[ScreenArea], busy: bool) -> tuple[Literal["hop", "fall", "ride"], tuple[int, int]] | None:
        """Where Buddy should go now, if anywhere (busy: its bubble is open, or it is being dragged)."""

        current = windows.get(self.perched_on) if self.perched_on is not None else None
        if self.perched_on is not None:
            spot = perch_position(current.frame, current.frame.x + self.offset_x, screens) if current else None
            if spot is None:
                # Its window closed, minimised, maximised or went full screen: down to the floor.
                self.perched_on = None
                return "fall", floor_position(pet[0], screens)
            if (abs(spot[0] - pet[0]) > 1 or abs(spot[1] - pet[1]) > 1):
                return "ride", spot
        if not (enabled and self.following) or busy or foreground is None or foreground.id == self.perched_on:
            self.candidate = None
            return None
        if foreground.id != self.candidate:
            self.candidate, self.candidate_since = foreground.id, now
            return None
        if now - self.candidate_since < FOLLOW_DWELL_SECONDS or now - self.last_hop < FOLLOW_COOLDOWN_SECONDS:
            return None
        spot = perch_position(foreground.frame, pet[0], screens)
        if spot is None:
            return None
        self.perched_on = foreground.id
        self.offset_x = spot[0] - foreground.frame.x
        self.last_hop = now
        self.candidate = None
        return "hop", spot


@dataclass(frozen=True)
class HitMask:
    """Which parts of the pet window take clicks: Buddy's silhouette cells and the open bubble."""

    pet: Rect
    cells: bytes = b""
    bubble: Rect | None = None
    menu: Rect | None = field(default=None)

    def hit(self, x: float, y: float) -> bool:
        if self.bubble is not None and self.bubble.contains(x, y):
            return True
        if self.menu is not None and self.menu.contains(x, y):
            return True
        if not self.pet.contains(x, y) or len(self.cells) != _MASK_BYTES:
            return False
        column = min(MASK_CELLS - 1, int((x - self.pet.x) / self.pet.width * MASK_CELLS))
        row = min(MASK_CELLS - 1, int((y - self.pet.y) / self.pet.height * MASK_CELLS))
        index = row * MASK_CELLS + column
        return bool(self.cells[index >> 3] & (0x80 >> (index & 7)))


def _rect(value: Any, *, limit: float = 4096) -> Rect:
    if (not isinstance(value, list) or len(value) != 4
            or not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)):
        raise ValueError("invalid_hit_mask")
    x, y, width, height = (float(item) for item in value)
    if not all(math.isfinite(item) for item in (x, y, width, height)) or width <= 0 or height <= 0:
        raise ValueError("invalid_hit_mask")
    if abs(x) > limit or abs(y) > limit or width > limit or height > limit:
        raise ValueError("invalid_hit_mask")
    return Rect(x, y, width, height)


def parse_hit_mask(payload: Mapping[str, Any]) -> HitMask:
    """A hit mask from the pet page: rects in window DIPs and 24×24 silhouette bits (base64)."""

    if set(payload) - {"action", "pet", "cells", "bubble", "menu"} or "pet" not in payload or "cells" not in payload:
        raise ValueError("invalid_hit_mask")
    cells = payload["cells"]
    if not isinstance(cells, str) or len(cells) > 128:
        raise ValueError("invalid_hit_mask")
    try:
        raw = base64.b64decode(cells, validate=True)
    except ValueError:
        raise ValueError("invalid_hit_mask") from None
    if len(raw) != _MASK_BYTES:
        raise ValueError("invalid_hit_mask")
    optional = {key: _rect(payload[key]) if payload.get(key) is not None else None for key in ("bubble", "menu")}
    return HitMask(_rect(payload["pet"]), raw, optional["bubble"], optional["menu"])
