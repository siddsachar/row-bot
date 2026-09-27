"""Placement, positioning and turn-target helpers for the desktop Buddy.

This module deliberately contains no NiceGUI or pywebview imports. The native
host (``native_host.BuddyWindowHost``) and the streaming runtime use these
small seams, which keeps placement, positioning and routing deterministic
under test. The desktop Buddy itself is the React overlay.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Callable, Iterable, Mapping


OVERLAY_WIDTH = 380
OVERLAY_HEIGHT = 230
OVERLAY_EDGE_MARGIN = 8


class BuddyPlacement(StrEnum):
    DOCKED = "docked"
    DESKTOP = "desktop"


class RuntimeSurface(StrEnum):
    CHAT = "normal_chat"
    DEVELOPER = "developer"
    DESIGNER = "designer"


@dataclass(frozen=True)
class BuddyPlacementState:
    """Canonical Buddy placement plus its independent presentation flags."""

    placement: BuddyPlacement = BuddyPlacement.DOCKED
    visible: bool = True
    collapsed: bool = False

    def tear_off(self) -> "BuddyPlacementState":
        return replace(self, placement=BuddyPlacement.DESKTOP, visible=True)

    def dock(self) -> "BuddyPlacementState":
        return BuddyPlacementState(BuddyPlacement.DOCKED, True, False)

    def hide(self) -> "BuddyPlacementState":
        return replace(self, visible=False)

    def show(self) -> "BuddyPlacementState":
        return replace(self, visible=True)

    def collapse(self) -> "BuddyPlacementState":
        if self.placement is not BuddyPlacement.DESKTOP:
            return self
        return replace(self, collapsed=True)

    def expand(self) -> "BuddyPlacementState":
        return replace(self, collapsed=False)


def placement_state_from_config(config: Mapping[str, Any] | None) -> BuddyPlacementState:
    cfg = config or {}
    raw = str(cfg.get("placement") or "").strip().lower()
    if raw not in {item.value for item in BuddyPlacement}:
        legacy_desktop = bool(cfg.get("desktop_enabled")) or str(cfg.get("mode") or "") == "desktop"
        raw = BuddyPlacement.DESKTOP.value if legacy_desktop else BuddyPlacement.DOCKED.value
    placement = BuddyPlacement(raw)
    visible = bool(cfg.get("visible", cfg.get("enabled", True)))
    collapsed = bool(cfg.get("collapsed", False)) if placement is BuddyPlacement.DESKTOP else False
    return BuddyPlacementState(placement, visible, collapsed)


def placement_state_for_app_startup(
    config: Mapping[str, Any] | None,
) -> BuddyPlacementState:
    """Return the safe in-app placement used for every fresh app launch.

    Tear-off is intentionally session-scoped.  A persisted hidden preference
    remains hidden, but a previous desktop placement never recreates a native
    overlay before the user explicitly tears Buddy off again.
    """

    state = placement_state_from_config(config)
    return BuddyPlacementState(BuddyPlacement.DOCKED, state.visible, False)


def apply_placement_state(config: Mapping[str, Any], state: BuddyPlacementState) -> dict[str, Any]:
    """Return a canonical config update without reviving legacy surface flags."""

    updated = dict(config)
    updated.update(
        {
            "placement": state.placement.value,
            "visible": bool(state.visible),
            "collapsed": bool(state.collapsed and state.placement is BuddyPlacement.DESKTOP),
            "mode": "desktop" if state.placement is BuddyPlacement.DESKTOP else "sidebar",
        }
    )
    return updated


def should_defer_native_show(*, ready: bool, manual: bool) -> bool:
    """Only page-driven automatic shows wait for the ready handshake.

    A tray or control-server recovery request must always call the native
    window's ``show`` method, even if the page-ready bridge was missed.
    """

    return not bool(ready) and not bool(manual)


def native_overlay_transparency(platform_name: str) -> bool:
    """Use an opaque Windows host so the overlay remains hit-testable.

    pywebview's WinForms transparency support uses ``TransparencyKey``.  With
    WebView2 composition that can make the rendered child visually present
    while Windows sends pointer input to the application underneath it.
    """

    return str(platform_name or "").lower() != "win32"


def enable_windows_per_monitor_dpi(
    platform_name: str | None = None,
    *,
    set_context: Callable[[Any], Any] | None = None,
) -> bool:
    """Opt the native host into per-monitor-v2 coordinates before WinForms.

    WinForms then reports physical work areas; the Buddy host converts them
    (and pywebview's physical "moved" positions) to the DIPs that pages and
    pywebview's create/move use (``native_host.BuddyWindowHost``).
    """

    if str(platform_name or sys.platform).lower() != "win32":
        return False
    try:
        if set_context is not None:
            return bool(set_context(-4))
        import ctypes

        setter = ctypes.windll.user32.SetProcessDpiAwarenessContext
        setter.argtypes = [ctypes.c_void_p]
        setter.restype = ctypes.c_bool
        return bool(setter(ctypes.c_void_p(-4)))
    except Exception:
        return False


@dataclass(frozen=True)
class ScreenArea:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + max(0, self.width)

    @property
    def bottom(self) -> int:
        return self.y + max(0, self.height)

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom

    def distance_squared(self, x: float, y: float) -> float:
        near_x = min(max(x, self.x), self.right)
        near_y = min(max(y, self.y), self.bottom)
        return (x - near_x) ** 2 + (y - near_y) ** 2


def _valid_screens(screens: Iterable[ScreenArea]) -> list[ScreenArea]:
    return [screen for screen in screens if screen.width > 0 and screen.height > 0]


def nearest_screen(x: float, y: float, screens: Iterable[ScreenArea]) -> ScreenArea:
    available = _valid_screens(screens)
    if not available:
        return ScreenArea(0, 0, 1920, 1080)
    for screen in available:
        if screen.contains(x, y):
            return screen
    return min(available, key=lambda screen: screen.distance_squared(x, y))


def clamp_overlay_position(
    x: float,
    y: float,
    screens: Iterable[ScreenArea],
    *,
    width: int = OVERLAY_WIDTH,
    height: int = OVERLAY_HEIGHT,
    margin: int = OVERLAY_EDGE_MARGIN,
) -> tuple[int, int]:
    """Clamp a window origin to the nearest visible work area.

    Negative coordinates are intentionally retained when the chosen monitor is
    left of, or above, the primary monitor.
    """

    screen = nearest_screen(x + width / 2, y + height / 2, screens)
    min_x = screen.x + margin
    min_y = screen.y + margin
    max_x = max(min_x, screen.right - width - margin)
    max_y = max(min_y, screen.bottom - height - margin)
    return (
        int(round(min(max(x, min_x), max_x))),
        int(round(min(max(y, min_y), max_y))),
    )


def position_for_drop(
    screen_x: float,
    screen_y: float,
    screens: Iterable[ScreenArea],
    *,
    width: int = OVERLAY_WIDTH,
    height: int = OVERLAY_HEIGHT,
) -> tuple[int, int]:
    """Centre the overlay around a dock drop point and keep it visible."""

    return clamp_overlay_position(
        screen_x - width / 2,
        screen_y - min(height / 3, 76),
        screens,
        width=width,
        height=height,
    )


def runtime_surface_for_state(state: Any) -> RuntimeSurface:
    if getattr(state, "active_developer_workspace_id", None):
        return RuntimeSurface.DEVELOPER
    if getattr(state, "active_designer_project", None):
        return RuntimeSurface.DESIGNER
    return RuntimeSurface.CHAT


@dataclass(frozen=True)
class OverlayTurnTarget:
    """The immutable thread/runtime identity captured when Send is pressed."""

    thread_id: str
    thread_name: str
    runtime_surface: RuntimeSurface
    developer_workspace_id: str = ""
    designer_project_id: str = ""
    designer_mode: str = ""
    model_override: str = ""
    approval_mode: str = ""
    messages: list[Any] | None = field(default=None, compare=False, repr=False)

    @classmethod
    def capture(cls, state: Any) -> "OverlayTurnTarget":
        project = getattr(state, "active_designer_project", None)
        return cls(
            thread_id=str(getattr(state, "thread_id", "") or ""),
            thread_name=str(getattr(state, "thread_name", "") or ""),
            runtime_surface=runtime_surface_for_state(state),
            developer_workspace_id=str(getattr(state, "active_developer_workspace_id", "") or ""),
            designer_project_id=str(getattr(project, "id", "") or ""),
            designer_mode=str(getattr(project, "mode", "") or ""),
            model_override=str(getattr(state, "thread_model_override", "") or ""),
            approval_mode=str(getattr(state, "thread_approval_mode", "") or ""),
            messages=getattr(state, "messages", None),
        )

    def configurable_values(self) -> dict[str, str]:
        values = {"runtime_surface": self.runtime_surface.value}
        if self.developer_workspace_id:
            values["developer_workspace_id"] = self.developer_workspace_id
        if self.designer_project_id:
            values["designer_project_id"] = self.designer_project_id
        if self.designer_mode:
            values["designer_mode"] = self.designer_mode
        if self.model_override:
            values["model_override"] = self.model_override
        if self.approval_mode:
            values["approval_mode"] = self.approval_mode
        return values


def screen_areas_from_native(screens: Iterable[Any]) -> list[ScreenArea]:
    """Normalise pywebview screens, preferring a native work-area frame."""

    result: list[ScreenArea] = []
    for screen in screens or ():
        try:
            frame = getattr(screen, "frame", None)
            # WinForms exposes Screen.WorkingArea through pywebview's frame
            # field. Cocoa currently exposes a full NSRect instead, so it
            # safely falls through to pywebview's coordinate fields below.
            if frame is not None and all(
                hasattr(frame, name) for name in ("X", "Y", "Width", "Height")
            ):
                result.append(
                    ScreenArea(
                        int(frame.X),
                        int(frame.Y),
                        int(frame.Width),
                        int(frame.Height),
                    )
                )
                continue
            result.append(
                ScreenArea(
                    int(getattr(screen, "x")),
                    int(getattr(screen, "y")),
                    int(getattr(screen, "width")),
                    int(getattr(screen, "height")),
                )
            )
        except (TypeError, ValueError, AttributeError):
            continue
    return result


def finite_coordinate(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback
