"""The desktop pet's OS adapter on this machine: it loads, reads the cursor and
lists other apps' windows as bare frames (read-only), and on macOS lets the
click that brings Row-Bot forward reach the pet. No window is shown."""

from __future__ import annotations

import math
import sys
from types import SimpleNamespace

import pytest

from row_bot.buddy.pet import OtherWindow
from row_bot.buddy.pet_native import pet_native

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"), reason="the desktop pet is Windows and macOS only")
def test_the_adapter_reads_the_cursor_and_other_windows_frames_here():
    native = pet_native()
    assert native is not None

    point = native.cursor()
    assert point is None or (len(point) == 2 and all(math.isfinite(value) for value in point))

    windows = native.windows()
    assert len(windows) <= 32
    for window in windows:
        assert isinstance(window, OtherWindow)
        assert window.id > 0  # a real window number/handle, read with the right type
        assert window.frame.width >= 120 and window.frame.height >= 80

    front = native.foreground()
    assert front is None or (isinstance(front, OtherWindow) and front.frame.width >= 120)


@pytest.mark.skipif(sys.platform != "darwin", reason="AppKit's first click is macOS's")
def test_on_macos_one_click_reaches_the_pet_while_other_windows_keep_webkits_answer():
    """With another app active, AppKit asks the clicked view whether the click that brings
    Row-Bot forward may act too. The pet's web view says yes, so one click opens its bubble;
    the main window's keeps WebKit's own answer. No window is shown."""
    import AppKit
    import objc
    from webview.platforms.cocoa import BrowserView

    def window_with_web_view():
        rect = AppKit.NSMakeRect(0, 0, 200, 200)
        window = BrowserView.WindowHost.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False)
        window.focus = True
        view = BrowserView.WebKitHost.alloc().initWithFrame_(rect)
        window.setContentView_(view)
        return window, view

    def click_on(view):
        return AppKit.NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
            AppKit.NSEventTypeLeftMouseDown, AppKit.NSMakePoint(100, 100), 0, 0, view.window().windowNumber(), None, 0,
            1, 1.0)

    def first_click(view):  # through the Objective-C runtime, as AppKit asks
        return view.methodForSelector_(b"acceptsFirstMouse:")(view, click_on(view))

    pet, pet_view = window_with_web_view()
    _main, main_view = window_with_web_view()
    native = pet_native()
    assert native.prepare(SimpleNamespace(native=pet))
    assert native.prepare(SimpleNamespace(native=pet))  # safe to repeat

    assert first_click(pet_view) is True
    webkit = objc.lookUpClass("WKWebView").instanceMethodForSelector_(b"acceptsFirstMouse:")
    assert first_click(main_view) == webkit(main_view, click_on(main_view))


def test_elsewhere_there_is_no_adapter_and_the_pet_stays_an_ordinary_window():
    assert pet_native("linux") is None
