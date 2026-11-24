"""How a window sits on its display, from Windows itself (Windows only); and the main display's size (Windows and
macOS), for placing what is done on a screen recording (C9).

Cua Driver works in physical pixels. A Row-Bot process isn't DPI aware by
default, so Windows would answer it in scaled ("logical") pixels; every call
here runs with the calling thread set to per-monitor DPI awareness (v2), the
way Cua and the Stop bar see the screen, and puts the thread back after.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from row_bot.computer_use.screen import Scaling

logger = logging.getLogger(__name__)

_PER_MONITOR_AWARE_V2 = -4
_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_MONITOR_DEFAULTTONEAREST = 2


@dataclass(frozen=True)
class WindowDisplay:
    """A window's rectangle and frame on screen (physical pixels) and its DPI against its monitor's."""

    rect: tuple[int, int, int, int]  # left, top, right, bottom, invisible border included
    frame: tuple[int, int, int, int]  # left, top, right, bottom as drawn (DWM's extended frame)
    window_dpi: int
    monitor_dpi: int

    @property
    def scaling(self) -> Scaling:
        scale = self.monitor_dpi / self.window_dpi if self.window_dpi > 0 and self.monitor_dpi > 0 else 1.0
        return Scaling(scale=scale, border=(float(self.frame[0] - self.rect[0]), float(self.frame[1] - self.rect[1])))

    def words(self) -> str:
        return (f"rect {self.rect}, frame {self.frame}, window DPI {self.window_dpi}, "
                f"monitor DPI {self.monitor_dpi} (scale {self.scaling.scale:g})")


@contextmanager
def _physical() -> Iterator[object]:
    import ctypes

    user32 = ctypes.windll.user32
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(_PER_MONITOR_AWARE_V2))
    try:
        yield user32
    finally:
        if previous:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))


def window_display(window_id: int) -> WindowDisplay | None:
    """The window's rectangle, frame and DPIs, or None (not Windows, or no such window)."""

    if sys.platform != "win32" or not window_id:
        return None
    import ctypes
    from ctypes import wintypes

    try:
        with _physical() as user32:
            hwnd = wintypes.HWND(window_id)
            if not user32.IsWindow(hwnd):
                return None
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
            frame = wintypes.RECT()
            if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(frame),
                                                          ctypes.sizeof(frame)) != 0:
                frame = rect
            user32.MonitorFromWindow.restype = wintypes.HMONITOR
            monitor = user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
            dpi_x, dpi_y = ctypes.c_uint(), ctypes.c_uint()
            if ctypes.windll.shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) != 0:
                return None
            window_dpi = int(user32.GetDpiForWindow(hwnd))
    except (AttributeError, OSError):
        logger.debug("Windows didn't say how window %s sits on its display", window_id, exc_info=True)
        return None
    return WindowDisplay((rect.left, rect.top, rect.right, rect.bottom),
                         (frame.left, frame.top, frame.right, frame.bottom), window_dpi, int(dpi_x.value))


def main_display_size() -> tuple[float, float] | None:
    """The main display's size, in the screen's own coordinates (Cua's): physical pixels on Windows, points on
    macOS; it starts at 0, 0. None elsewhere, or when the system doesn't say. A screen recording is of this display."""

    if sys.platform == "win32":
        try:
            with _physical() as user32:
                width, height = int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
        except (AttributeError, OSError):
            return None
        return (float(width), float(height)) if width > 0 and height > 0 else None
    if sys.platform == "darwin":
        import ctypes

        class _Rect(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("width", ctypes.c_double),
                        ("height", ctypes.c_double)]

        try:
            graphics = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            graphics.CGMainDisplayID.restype = ctypes.c_uint32
            graphics.CGDisplayBounds.restype = _Rect
            graphics.CGDisplayBounds.argtypes = [ctypes.c_uint32]
            bounds = graphics.CGDisplayBounds(graphics.CGMainDisplayID())
        except (AttributeError, OSError):
            return None
        return (float(bounds.width), float(bounds.height)) if bounds.width > 0 and bounds.height > 0 else None
    return None


def cursor_position() -> tuple[int, int] | None:
    """Where the pointer is on screen (physical pixels), or None off Windows."""

    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    try:
        with _physical() as user32:
            point = wintypes.POINT()
            if not user32.GetCursorPos(ctypes.byref(point)):
                return None
    except (AttributeError, OSError):
        return None
    return point.x, point.y
