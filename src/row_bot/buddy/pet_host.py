"""The desktop pet's live loop: where its window is, what takes clicks, where it goes.

``BuddyWindowHost`` owns the window's lifecycle; this owns everything that
changes many times a second while the pet is out. One daemon thread ticks at
30 Hz: click-through follows Buddy's silhouette under the cursor, the cursor's
direction feeds Buddy's gaze, eased moves step the window, and ten times a
second the follower decides whether Buddy rides with its window, falls to the
floor or hops to the window you are using. The page reports its hit mask and
its layout, and reads the state, through the attested bridge.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from .pet import (
    FOOT,
    PET_HEIGHT,
    PET_WIDTH,
    Follower,
    HitMask,
    Move,
    OtherWindow,
    Rect,
    fall,
    floor_position,
    hop,
    perch_position,
    window_under,
)
from .pet_native import PetNative

logger = logging.getLogger(__name__)

BUBBLE_WIDTH = 380
BUBBLE_HEIGHT = 236
GAP = 8
EXPANDED_HEIGHT = BUBBLE_HEIGHT + 64
TICK = 1 / 30
DECIDE_EVERY = 0.1
CONFIG_EVERY = 2.0


class PetController:
    def __init__(self, host: Any, native: PetNative | None, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, threaded: bool = True) -> None:
        self._host = host
        self._native = native
        self._clock = clock
        self._sleep = sleep
        self._threaded = threaded
        self._lock = threading.RLock()
        self._window: Any = None
        self._pet = (0.0, 0.0)
        self._side = "right"
        self._expanded = False
        self._mask: HitMask | None = None
        self._through: bool | None = None
        self._follower = Follower()
        self._move: Move | None = None
        self._dragging = False
        self._landed = 0
        self._cursor: tuple[float, float] | None = None
        self._cursor_moved = -1e9
        self._last_decide = -1e9
        self._following_enabled = True
        self._config_read = -1e9
        self._running = False
        self._thread: threading.Thread | None = None

    # Lifecycle ----------------------------------------------------------
    def attach(self, window: Any, x: float, y: float, *, drop: bool = True) -> None:
        """A shown pet window, with Buddy's box at (x, y); it lands where it is unless told otherwise."""
        with self._lock:
            self._window = window
            self._pet = (float(x), float(y))
            self._expanded = False
            self._mask = None
            self._through = None
            self._follower = Follower()
            self._move = None
            self._dragging = False
            self._place()
            if drop:
                self._drop()
            if self._threaded and not self._running:
                self._running = True
                self._thread = threading.Thread(target=self._loop, daemon=True, name="buddy-pet")
                self._thread.start()

    def detach(self) -> None:
        with self._lock:
            self._running = False
            self._window = None
            self._move = None

    def _loop(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    return
            try:
                self.tick(self._clock())
            except Exception:
                logger.debug("Buddy pet tick failed", exc_info=True)
            self._sleep(TICK)

    # Geometry -----------------------------------------------------------
    def _screens(self):
        return self._host.screen_areas()

    def _factor(self) -> float:
        return self._host._factor()

    def _window_frame(self) -> Rect:
        """The window in DIPs for the current pet position and layout."""
        x, y = self._pet
        if not self._expanded:
            return Rect(x, y, PET_WIDTH, PET_HEIGHT)
        width = PET_WIDTH + GAP + BUBBLE_WIDTH
        left = x if self._side == "right" else x - GAP - BUBBLE_WIDTH
        return Rect(left, y - (EXPANDED_HEIGHT - PET_HEIGHT), width, EXPANDED_HEIGHT)

    def _pet_offset(self) -> tuple[float, float]:
        frame = self._window_frame()
        return self._pet[0] - frame.x, self._pet[1] - frame.y

    def _place(self) -> None:
        if self._window is None:
            return
        frame = self._window_frame()
        if self._native is not None:
            factor = self._factor()
            self._native.place(self._window, Rect(frame.x * factor, frame.y * factor,
                                                  frame.width * factor, frame.height * factor))
        else:
            self._host._on_ui(self._window.move, round(frame.x), round(frame.y))

    def _others(self) -> list[OtherWindow]:
        if self._native is None:
            return []
        factor = self._factor()
        return [OtherWindow(window.id, Rect(window.frame.x / factor, window.frame.y / factor,
                                            window.frame.width / factor, window.frame.height / factor))
                for window in self._native.windows()]

    def _drop(self) -> None:
        """Land on whatever is under Buddy's feet: a window's top edge, else the floor."""
        x, y = self._pet
        feet = (x + PET_WIDTH / 2, y + PET_HEIGHT - FOOT)
        others = self._others()
        target = window_under(feet[0], feet[1], others)
        spot = perch_position(target.frame, x, self._screens()) if target else None
        if spot is None:
            target = None
            spot = floor_position(x, self._screens())
        self._follower.dropped(target, spot[0])
        if abs(spot[0] - x) + abs(spot[1] - y) < 3:
            return  # already resting there: no landing to replay
        self._move = fall(self._pet, spot, self._clock()) if spot[1] >= y else hop(self._pet, spot, self._clock())

    # Page-facing --------------------------------------------------------
    def layout(self, expanded: bool) -> dict[str, Any]:
        """Grow the window for the bubble or menu (toward the side with room) or shrink it back."""
        with self._lock:
            if expanded and not self._expanded:
                screen = min(self._screens() or [None], key=lambda area: 0 if area is None else area.distance_squared(
                    self._pet[0], self._pet[1]))
                room_right = (screen.right - (self._pet[0] + PET_WIDTH)) if screen else 10**6
                self._side = "right" if room_right >= GAP + BUBBLE_WIDTH else "left"
            collapsing = self._expanded and not expanded
            self._expanded = bool(expanded)
            self._mask = None  # stale until the page reports the new layout's mask
            self._place()
            if collapsing and self._move is None:
                self._drop()  # dragged while open: land now
            return self._geometry()

    def _geometry(self) -> dict[str, Any]:
        frame = self._window_frame()
        offset_x, offset_y = self._pet_offset()
        return {"side": self._side, "expanded": self._expanded, "width": round(frame.width),
                "height": round(frame.height), "petX": round(offset_x), "petY": round(offset_y)}

    def hit(self, mask: HitMask) -> None:
        with self._lock:
            self._mask = mask

    def drag(self, phase: str) -> None:
        with self._lock:
            if phase == "start":
                self._dragging = True
                self._move = None
            elif phase == "end" and self._dragging:
                self._dragging = False
                if not self._expanded:
                    self._drop()

    def moved(self, x: float, y: float) -> None:
        """The window moved (a drag, in DIPs): Buddy's box moved with it."""
        with self._lock:
            if self._move is not None or self._window is None:
                return
            offset_x, offset_y = self._pet_offset()
            self._pet = (float(x) + offset_x, float(y) + offset_y)

    def position(self) -> tuple[float, float]:
        """Buddy's box on screen, in DIPs."""
        with self._lock:
            return self._pet

    def state(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            cursor = None
            if self._cursor is not None:
                cursor = {"x": round(self._cursor[0], 1), "y": round(self._cursor[1], 1),
                          "age": round(min(60.0, now - self._cursor_moved), 2)}
            return {**self._geometry(), "cursor": cursor, "landed": self._landed,
                    "perched": self._follower.perched_on is not None,
                    "following": self._following_enabled and self._follower.following}

    # The loop -----------------------------------------------------------
    def tick(self, now: float) -> None:
        with self._lock:
            window = self._window
            if window is None:
                return
            if self._move is not None:
                self._pet = self._move.at(now)
                self._place()
                if self._move.done(now):
                    self._landed += 1
                    self._move = None
                    self._host.pet_rest(*self._pet)
            self._track_cursor(now)
            if now - self._config_read >= CONFIG_EVERY:
                self._config_read = now
                try:
                    self._following_enabled = self._host._load_config().get("follow_windows", True) is not False
                except Exception:
                    pass
            if self._move is None and not self._dragging and now - self._last_decide >= DECIDE_EVERY:
                self._last_decide = now
                self._decide(now)

    def _track_cursor(self, now: float) -> None:
        native = self._native
        if native is None:
            return
        point = native.cursor()
        frame = native.frame(self._window)
        if point is None or frame is None:
            return
        scale = native.scale(self._window)
        local = ((point[0] - frame.x) / scale, (point[1] - frame.y) / scale)
        offset_x, offset_y = self._pet_offset()
        relative = (local[0] - offset_x, local[1] - offset_y)
        if self._cursor is None or abs(relative[0] - self._cursor[0]) + abs(relative[1] - self._cursor[1]) > 0.5:
            self._cursor_moved = now
        self._cursor = relative
        # Only Buddy's silhouette (and its open bubble) take clicks; a drag keeps them.
        inside = self._dragging or self._mask is None or self._mask.hit(*local)
        through = not inside
        if through != self._through and native.set_click_through(self._window, through):
            self._through = through

    def _decide(self, now: float) -> None:
        native = self._native
        if native is None:
            return
        busy = self._expanded or self._dragging
        foreground = native.foreground()
        factor = self._factor()
        if foreground is not None:
            foreground = OtherWindow(foreground.id, Rect(foreground.frame.x / factor, foreground.frame.y / factor,
                                                         foreground.frame.width / factor,
                                                         foreground.frame.height / factor))
        windows: dict[int, OtherWindow] = {}
        if self._follower.perched_on is not None:
            windows = {window.id: window for window in self._others()}
        decision = self._follower.decide(now=now, enabled=self._following_enabled, foreground=foreground,
                                         windows=windows, pet=self._pet, screens=self._screens(), busy=busy)
        if decision is None:
            return
        kind, spot = decision
        if kind == "ride":
            self._pet = (float(spot[0]), float(spot[1]))
            self._place()
        elif kind == "fall":
            self._move = fall(self._pet, spot, now)
        else:
            self._move = hop(self._pet, spot, now)
