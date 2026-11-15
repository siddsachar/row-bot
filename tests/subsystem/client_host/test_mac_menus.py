"""The Mac window has the standard Edit and Window menus (U35).

pywebview's menu bar had only the app and View menus: Cmd-C/V worked through its own key handling, but
Copy, Paste, Undo and Minimize were nowhere in the menus a Mac person looks in. The window script adds them
on the main thread after pywebview builds its menu. Run with AppKit on macOS, on a menu shaped like
pywebview's; no window is shown.
"""

from __future__ import annotations

import sys

import pytest

from row_bot import launcher

pytestmark = [pytest.mark.platform,
              pytest.mark.skipif(sys.platform != "darwin", reason="the menu bar is macOS's")]


def test_the_mac_window_has_edit_and_window_menus(monkeypatch):
    from AppKit import NSApplication, NSMenu, NSMenuItem
    from PyObjCTools import AppHelper

    app = NSApplication.sharedApplication()
    bar = NSMenu.alloc().init()
    for title in ("Row-Bot", "View"):  # pywebview's own menus
        holder = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
        holder.setSubmenu_(NSMenu.alloc().initWithTitle_(title))
        bar.addItem_(holder)
    app.setMainMenu_(bar)
    monkeypatch.setattr(AppHelper, "callAfter", lambda func, *args: func(*args))
    source = launcher._WINDOW_SCRIPT
    start = source.index("def _add_mac_edit_and_window_menus():")
    namespace: dict = {}
    exec(compile(source[start:source.index("def _on_main_window_closing():", start)], "<window-script>", "exec"),
         namespace)

    namespace["_add_mac_edit_and_window_menus"]()
    namespace["_add_mac_edit_and_window_menus"]()  # built once, however often the window starts

    menus = {item.submenu().title(): item.submenu() for item in app.mainMenu().itemArray()}
    assert list(menus) == ["Row-Bot", "Edit", "View", "Window"]
    edit = {item.title(): (item.action(), item.keyEquivalent()) for item in menus["Edit"].itemArray()
            if not item.isSeparatorItem()}
    assert edit == {"Undo": ("undo:", "z"), "Redo": ("redo:", "z"), "Cut": ("cut:", "x"), "Copy": ("copy:", "c"),
                    "Paste": ("paste:", "v"), "Select All": ("selectAll:", "a")}
    assert [item.action() for item in menus["Window"].itemArray()] == ["performMiniaturize:", "performZoom:"]
    assert app.windowsMenu() == menus["Window"]
