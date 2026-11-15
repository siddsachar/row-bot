"""What the desktop pet asks of Windows and macOS, and nothing more.

Transparency with real per-pixel alpha, click-through outside Buddy's
silhouette, the cursor position, and the frames of other apps' windows (their
position and size only: never a title, a process name, contents or pixels).
Coordinates come back as physical pixels on Windows and as points on macOS;
the host converts them. Every call is cheap and safe to repeat; failures are
reported as None/False and the pet degrades (an opaque, clickable window).
"""

from __future__ import annotations

import functools
import logging
import os
import sys
from typing import Any, Protocol

from .pet import OtherWindow, Rect, window_in_use

logger = logging.getLogger(__name__)


class PetNative(Protocol):
    def prepare(self, window: Any) -> bool: ...
    def set_click_through(self, window: Any, through: bool) -> bool: ...
    def place(self, window: Any, frame: Rect) -> bool: ...
    def cursor(self) -> tuple[float, float] | None: ...
    def frame(self, window: Any) -> Rect | None: ...
    def scale(self, window: Any) -> float: ...
    def windows(self) -> list[OtherWindow]: ...
    def foreground(self) -> OtherWindow | None: ...


class _Windows:
    """Win32 through ctypes. Frames are physical pixels (the process is per-monitor-v2 aware)."""

    GWL_EXSTYLE = -20
    WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x80000, 0x20, 0x80, 0x40000
    LWA_ALPHA = 2
    DWMWA_EXTENDED_FRAME_BOUNDS, DWMWA_CLOAKED = 9, 14

    def __init__(self) -> None:
        import ctypes
        import ctypes.wintypes as wt

        self.ctypes, self.wt = ctypes, wt
        self.user32 = ctypes.windll.user32
        self.dwmapi = ctypes.windll.dwmapi
        u = self.user32
        u.GetWindowLongPtrW.restype = ctypes.c_longlong
        u.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
        u.SetWindowLongPtrW.restype = ctypes.c_longlong
        u.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_longlong]
        u.GetForegroundWindow.restype = wt.HWND
        u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        u.GetDpiForWindow.argtypes = [wt.HWND]
        u.GetDpiForWindow.restype = ctypes.c_uint
        self.pid = os.getpid()

    def _hwnd(self, window: Any):
        return self.wt.HWND(int(window.native.Handle.ToInt64()))

    def prepare(self, window: Any) -> bool:
        """Real per-pixel alpha that still takes clicks (D11): no colour key, DWM glass behind the page."""
        try:
            import System
            from System.Drawing import Color

            def apply() -> None:
                window.native.TransparencyKey = Color.Empty
                window.native.BackColor = Color.Black

            window.native.Invoke(System.Action(apply))
            hwnd = self._hwnd(window)
            style = self.user32.GetWindowLongPtrW(hwnd, self.GWL_EXSTYLE)
            style = (style | self.WS_EX_LAYERED | self.WS_EX_TOOLWINDOW) & ~self.WS_EX_APPWINDOW & ~self.WS_EX_TRANSPARENT
            self.user32.SetWindowLongPtrW(hwnd, self.GWL_EXSTYLE, style)
            self.user32.SetLayeredWindowAttributes(hwnd, 0, 255, self.LWA_ALPHA)

            class MARGINS(self.ctypes.Structure):
                _fields_ = [(name, self.ctypes.c_int) for name in ("l", "r", "t", "b")]

            self.dwmapi.DwmExtendFrameIntoClientArea(hwnd, self.ctypes.byref(MARGINS(-1, -1, -1, -1)))
            return True
        except Exception:
            logger.warning("Buddy pet: transparent window setup failed", exc_info=True)
            return False

    def set_click_through(self, window: Any, through: bool) -> bool:
        try:
            hwnd = self._hwnd(window)
            style = self.user32.GetWindowLongPtrW(hwnd, self.GWL_EXSTYLE)
            wanted = (style | self.WS_EX_TRANSPARENT) if through else (style & ~self.WS_EX_TRANSPARENT)
            if wanted != style:
                self.user32.SetWindowLongPtrW(hwnd, self.GWL_EXSTYLE, wanted)
            return True
        except Exception:
            return False

    def place(self, window: Any, frame: Rect) -> bool:
        """Move and size in one step (physical pixels), without activating or reordering."""
        try:
            flags = 0x0004 | 0x0010 | 0x4000  # SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS
            return bool(self.user32.SetWindowPos(self._hwnd(window), None, round(frame.x), round(frame.y),
                                                 round(frame.width), round(frame.height), flags))
        except Exception:
            return False

    def cursor(self) -> tuple[float, float] | None:
        point = self.wt.POINT()
        return (point.x, point.y) if self.user32.GetCursorPos(self.ctypes.byref(point)) else None

    def _bounds(self, hwnd) -> Rect | None:
        rect = self.wt.RECT()
        if self.dwmapi.DwmGetWindowAttribute(hwnd, self.DWMWA_EXTENDED_FRAME_BOUNDS, self.ctypes.byref(rect),
                                             self.ctypes.sizeof(rect)) != 0:
            if not self.user32.GetWindowRect(hwnd, self.ctypes.byref(rect)):
                return None
        return Rect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)

    def frame(self, window: Any) -> Rect | None:
        try:
            return self._bounds(self._hwnd(window))
        except Exception:
            return None

    def scale(self, window: Any) -> float:
        try:
            return max(1.0, self.user32.GetDpiForWindow(self._hwnd(window)) / 96)
        except Exception:
            return 1.0

    def _other(self, hwnd) -> OtherWindow | None:
        """An ordinary visible top-level window of another app, or None."""
        u = self.user32
        if not hwnd or not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd) or u.IsZoomed(hwnd):
            return None
        pid = self.wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, self.ctypes.byref(pid))
        if pid.value == self.pid:
            return None
        style = u.GetWindowLongPtrW(hwnd, self.GWL_EXSTYLE)
        if style & self.WS_EX_TOOLWINDOW or style & 0x8:  # tool windows, always-on-top
            return None
        cloaked = self.ctypes.c_int(0)
        self.dwmapi.DwmGetWindowAttribute(hwnd, self.DWMWA_CLOAKED, self.ctypes.byref(cloaked), 4)
        if cloaked.value:
            return None
        frame = self._bounds(hwnd)
        if frame is None or frame.width < 120 or frame.height < 80:
            return None
        return OtherWindow(int(hwnd.value if hasattr(hwnd, "value") else hwnd), frame)

    def foreground(self) -> OtherWindow | None:
        try:
            return self._other(self.user32.GetForegroundWindow())
        except Exception:
            return None

    def windows(self) -> list[OtherWindow]:
        found: list[OtherWindow] = []
        try:
            callback_type = self.ctypes.WINFUNCTYPE(self.wt.BOOL, self.wt.HWND, self.wt.LPARAM)

            def visit(hwnd, _):
                window = self._other(self.wt.HWND(hwnd) if isinstance(hwnd, int) else hwnd)
                if window is not None:
                    found.append(window)
                return len(found) < 32

            self.user32.EnumWindows(callback_type(visit), 0)  # top of the z-order first
        except Exception:
            logger.debug("Buddy pet: window list unavailable", exc_info=True)
        return found


class _Mac:
    """AppKit for the window and cursor; CoreGraphics (ctypes) for other windows' bounds, in points."""

    def __init__(self) -> None:
        import ctypes
        import ctypes.util

        self.ctypes = ctypes
        self.cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
        self.cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
        cf, cg = self.cf, self.cg
        cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
        cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        cf.CFDictionaryGetValue.restype = ctypes.c_void_p
        cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cf.CFNumberGetValue.restype = ctypes.c_bool
        cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        cf.CFRelease.argtypes = [ctypes.c_void_p]

        class CGRect(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]

        self.CGRect = CGRect
        cg.CGRectMakeWithDictionaryRepresentation.restype = ctypes.c_bool
        cg.CGRectMakeWithDictionaryRepresentation.argtypes = [ctypes.c_void_p, ctypes.POINTER(CGRect)]
        self.keys = {name: ctypes.c_void_p.in_dll(cg, name).value
                     for name in ("kCGWindowLayer", "kCGWindowOwnerPID", "kCGWindowBounds", "kCGWindowNumber",
                                  "kCGWindowAlpha")}
        self.pid = os.getpid()
        from PyObjCTools import AppHelper

        self.on_main = AppHelper.callAfter

    def prepare(self, window: Any) -> bool:
        """pywebview's Cocoa window is already transparent and takes clicks; make the first one count."""
        try:
            _first_click_where_marked()
            setattr(window.native, _FIRST_CLICK, True)
            return True
        except Exception:
            logger.warning("Buddy pet: first-click setup failed", exc_info=True)
            return False

    def set_click_through(self, window: Any, through: bool) -> bool:
        try:
            self.on_main(window.native.setIgnoresMouseEvents_, bool(through))
            return True
        except Exception:
            return False

    def place(self, window: Any, frame: Rect) -> bool:
        try:
            from AppKit import NSMakeRect, NSScreen

            height = NSScreen.screens()[0].frame().size.height
            rect = NSMakeRect(frame.x, height - frame.y - frame.height, frame.width, frame.height)
            self.on_main(window.native.setFrame_display_, rect, True)
            return True
        except Exception:
            return False

    def cursor(self) -> tuple[float, float] | None:
        try:
            from AppKit import NSEvent, NSScreen

            point = NSEvent.mouseLocation()
            height = NSScreen.screens()[0].frame().size.height
            return float(point.x), float(height - point.y)
        except Exception:
            return None

    def frame(self, window: Any) -> Rect | None:
        try:
            from AppKit import NSScreen

            frame = window.native.frame()
            height = NSScreen.screens()[0].frame().size.height
            return Rect(frame.origin.x, height - frame.origin.y - frame.size.height, frame.size.width,
                        frame.size.height)
        except Exception:
            return None

    def scale(self, window: Any) -> float:
        return 1.0  # AppKit frames are already points, as pywebview's moves are

    def _number(self, dictionary, key: str) -> float | None:
        value = self.cf.CFDictionaryGetValue(dictionary, self.keys[key])
        if not value:
            return None
        out = self.ctypes.c_double()
        # 13: kCFNumberDoubleType, matching the buffer; CoreFoundation converts the integer keys exactly.
        return out.value if self.cf.CFNumberGetValue(value, 13, self.ctypes.byref(out)) else None

    def _listed(self, limit: int) -> list[tuple[int, OtherWindow]]:
        """Ordinary windows (layer 0) front to back, with their owner's pid, Row-Bot's own included."""
        found: list[tuple[int, OtherWindow]] = []
        array = self.cg.CGWindowListCopyWindowInfo(1 | 16, 0)  # on screen only, no desktop elements
        if not array:
            return found
        try:
            for index in range(min(self.cf.CFArrayGetCount(array), 200)):  # front to back
                info = self.cf.CFArrayGetValueAtIndex(array, index)
                if self._number(info, "kCGWindowLayer") != 0:
                    continue
                if (self._number(info, "kCGWindowAlpha") or 0) <= 0:
                    continue
                rect = self.CGRect()
                bounds = self.cf.CFDictionaryGetValue(info, self.keys["kCGWindowBounds"])
                if not bounds or not self.cg.CGRectMakeWithDictionaryRepresentation(bounds, self.ctypes.byref(rect)):
                    continue
                if rect.w < 120 or rect.h < 80:
                    continue
                found.append((int(self._number(info, "kCGWindowOwnerPID") or 0),
                              OtherWindow(int(self._number(info, "kCGWindowNumber") or 0),
                                          Rect(rect.x, rect.y, rect.w, rect.h))))
                if len(found) >= limit:
                    break
        finally:
            self.cf.CFRelease(array)
        return found

    def windows(self) -> list[OtherWindow]:
        return [window for pid, window in self._listed(48) if pid != self.pid][:32]

    def foreground(self) -> OtherWindow | None:
        return window_in_use(self._listed(1), self.pid)


_FIRST_CLICK = "row_bot_first_click"  # set on the pet's NSWindow


@functools.cache
def _first_click_where_marked() -> None:
    """Let pywebview's web views act on the click that activates Row-Bot, in marked windows only.

    WebKit says no, so a click on an inactive window only brings it forward;
    every unmarked window (the main one) keeps that answer. The method is added
    to pywebview's class once: swapping a live web view's class instead crashed
    macOS 27, as WebKit observes its views.
    """
    import objc
    from webview.platforms.cocoa import BrowserView

    host = BrowserView.WebKitHost

    def acceptsFirstMouse_(self, event):
        if getattr(self.window(), _FIRST_CLICK, False):
            return True
        return objc.super(host, self).acceptsFirstMouse_(event)

    objc.classAddMethods(host, [acceptsFirstMouse_])


def pet_native(platform: str | None = None) -> PetNative | None:
    """The pet's OS adapter here, or None (Linux, or the OS refused): the pet then stays opaque."""

    name = (platform or sys.platform).lower()
    try:
        if name == "win32":
            return _Windows()
        if name == "darwin":
            return _Mac()
    except Exception:
        logger.warning("Buddy pet: native helpers unavailable", exc_info=True)
    return None
