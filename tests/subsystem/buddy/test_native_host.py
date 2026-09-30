from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

import pytest

from row_bot.buddy.native_host import (
    CHANGED_EVENT_SCRIPT,
    OVERLAY_ROUTE,
    BuddyWindowHost,
    buddy_overlay_url,
    overlay_window_options,
    placement_callback,
    valid_conversation_id,
)
from row_bot.buddy.overlay import ScreenArea
from row_bot.native_client import NativeClientBridge, NativeDocumentAuthority, PyWebViewDriver

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


class Events:
    def __init__(self) -> None:
        self.closed: list[Callable[..., Any]] = []
        self.moved: list[Callable[..., Any]] = []

    def fire(self, name: str, *args: Any) -> None:
        for handler in getattr(self, name):
            handler(*args)


class Hooks:
    def __init__(self, events: Events) -> None:
        self._events = events

    @property
    def closed(self):
        return _Adder(self._events.closed)

    @closed.setter
    def closed(self, _value):
        pass

    @property
    def moved(self):
        return _Adder(self._events.moved)

    @moved.setter
    def moved(self, _value):
        pass


class _Adder:
    def __init__(self, handlers: list) -> None:
        self.handlers = handlers

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class Window:
    def __init__(self, options: dict[str, Any]) -> None:
        self.options = options
        self.calls: list[tuple] = []
        self.scripts: list[str] = []
        self._events = Events()
        self.events = Hooks(self._events)

    def __getattr__(self, name: str):
        if name in {"show", "hide", "restore", "destroy", "move"}:
            def call(*args):
                self.calls.append((name, *args))
                if name == "destroy":
                    self._events.fire("closed")
            return call
        raise AttributeError(name)

    def run_js(self, script: str) -> None:
        self.scripts.append(script)


class Harness:
    def __init__(self, *, config: dict[str, Any] | None = None, fail: int = 0,
                 screens: list[ScreenArea] | None = None, attach_error: bool = False) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self.windows: list[Window] = []
        self.attached: list[Window] = []
        self.timers: list[tuple[float, Callable[[], None]]] = []
        self.logs: list[str] = []
        self.fail = fail
        self.main = Window({})
        self.attach_error = attach_error

        def create_window(**options):
            if self.fail:
                self.fail -= 1
                raise TypeError("unsupported option")
            window = Window(options)
            self.windows.append(window)
            return window

        def attach(window):
            if self.attach_error:
                raise OSError("bridge down")
            self.attached.append(window)

        self.host = BuddyWindowHost(
            create_window=create_window,
            load_config=lambda: dict(self.config),
            save_config=lambda value: self.config.update(value),
            screens=lambda: list(screens or [ScreenArea(0, 0, 2560, 1392)]),
            main_window=lambda: self.main,
            attach=attach,
            port=8123,
            log=self.logs.append,
            platform="win32",
            start_timer=lambda delay, callback: self.timers.append((delay, callback)),
        )


def test_overlay_url_and_window_options_never_expose_a_js_api() -> None:
    assert buddy_overlay_url(8123) == "http://127.0.0.1:8123" + OVERLAY_ROUTE
    windows = overlay_window_options("u", x=1, y=2, platform="win32")
    mac = overlay_window_options("u", platform="darwin")[0]
    assert all("js_api" not in options for options in windows)
    assert windows[0]["transparent"] is False and mac["transparent"] is True
    assert windows[0]["hidden"] and windows[0]["on_top"] and windows[0]["frameless"]
    assert "background_color" not in windows[1]
    assert not {"transparent", "shadow", "on_top", "hidden"} & set(windows[2])
    assert windows[3] == {"title": "Buddy", "url": "u", "width": 380, "height": 230}


def test_tear_off_opens_hidden_attaches_the_bridge_and_reveals_on_ready() -> None:
    harness = Harness()
    host = harness.host
    assert host.tear_off(1280, 700) is True
    window = harness.windows[0]
    assert window.options["url"] == "http://127.0.0.1:8123/app-v2/buddy-overlay"
    assert (window.options["x"], window.options["y"]) == (1090, 624)
    assert harness.attached == [window]
    assert harness.config["placement"] == "desktop" and harness.config["overlay"] == {"x": 1090, "y": 624}
    assert ("show",) not in window.calls
    assert host.mark_ready() is True
    assert window.calls[-2:] == [("restore",), ("show",)]
    # The ready timeout finds the page ready and does nothing more.
    delay, callback = harness.timers[0]
    assert delay == 2.0
    shows = window.calls.count(("show",))
    callback()
    assert window.calls.count(("show",)) == shows


def test_ready_timeout_forces_a_show_only_for_a_visible_torn_off_buddy() -> None:
    harness = Harness()
    harness.host.tear_off(100, 100)
    window = harness.windows[0]
    harness.timers[0][1]()
    assert ("show",) in window.calls
    assert "page-ready handshake timed out; forced native show" in harness.logs

    hidden = Harness()
    hidden.host.tear_off(100, 100)
    hidden.host.hide()
    hidden.timers[0][1]()
    assert ("show",) not in hidden.windows[0].calls


def test_window_creation_falls_back_and_a_failed_tear_off_stays_docked() -> None:
    harness = Harness(fail=2)
    assert harness.host.tear_off(500, 500) is True
    assert "background_color" not in harness.windows[0].options
    assert "transparent" not in harness.windows[0].options

    failing = Harness(fail=4)
    assert failing.host.tear_off(500, 500) is False
    assert failing.config["placement"] == "docked"


def test_a_missing_bridge_is_logged_and_the_timeout_still_reveals() -> None:
    harness = Harness(attach_error=True)
    assert harness.host.tear_off(500, 500) is True
    assert any("overlay bridge unavailable" in line for line in harness.logs)
    harness.timers[0][1]()
    assert ("show",) in harness.windows[0].calls


def test_reopen_reclamps_a_remembered_position_from_a_missing_monitor() -> None:
    harness = Harness(
        config={"placement": "desktop", "visible": True, "overlay": {"x": 5000, "y": 2000}},
        screens=[ScreenArea(0, 0, 1920, 1040)],
    )
    assert harness.host.open() is True
    window = harness.windows[0]
    assert (window.options["x"], window.options["y"]) == (1532, 802)
    # A docked Buddy never opens a window.
    docked = Harness(config={"placement": "docked"})
    assert docked.host.open() is False and not docked.windows


def test_show_hide_dock_move_and_main_close_follow_placement() -> None:
    harness = Harness()
    host = harness.host
    assert host.show() is False  # docked: nothing to show
    host.tear_off(300, 300)
    window = harness.windows[0]
    # An automatic show waits for the page; a manual one (tray) does not.
    assert host.show(manual=False) is True and ("show",) not in window.calls
    assert host.show(manual=True) is True and ("show",) in window.calls
    assert host.hide() is True and harness.config["visible"] is False
    window._events.fire("moved", 42, 43)
    assert harness.config["overlay"] == {"x": 42, "y": 43}
    assert host.main_closing() is False and ("hide",) in harness.main.calls
    assert host.dock() is True
    assert ("destroy",) in window.calls and host.window is None
    assert harness.config["placement"] == "docked"
    assert host.main_closing() is True
    assert host.close() is True


def test_the_followed_conversation_is_validated_and_nudges_only_on_change() -> None:
    harness = Harness()
    host = harness.host
    assert host.target() == {"conversationId": None, "revision": 0}
    assert host.publish_target("conversation-1") == {"conversationId": "conversation-1", "revision": 1}
    host.tear_off(300, 300)
    window = harness.windows[0]
    assert host.publish_target("conversation-1")["revision"] == 1
    assert window.scripts == []
    assert host.publish_target("conversation-2")["revision"] == 2
    assert window.scripts == [CHANGED_EVENT_SCRIPT]
    for bad in (None, "", "a b", "x" * 257, "<script>", 7):
        assert host.publish_target(bad) is None
    assert host.target() == {"conversationId": "conversation-2", "revision": 2}
    assert valid_conversation_id("0f14abac-455b-56df-abda-4fa8916bdaa1")


def test_show_main_restores_the_window_and_asks_it_to_open_a_conversation() -> None:
    harness = Harness()
    host = harness.host
    assert host.show_main("conversation-2") is True
    assert harness.main.calls[:2] == [("restore",), ("show",)]
    assert harness.main.scripts == [
        "window.dispatchEvent(new CustomEvent('row-bot-open-conversation', "
        "{ detail: \"conversation-2\" }));"]
    assert host.show_main(None) is True and len(harness.main.scripts) == 1
    assert host.show_main("'); alert(1); ('") is False
    harness.main = None
    assert host.show_main("conversation-2") is False


@pytest.mark.parametrize("role,allowed", [
    ("main", {"status", "tear_off", "dock"}),
    ("buddy", {"status", "dock", "hide", "ready"}),
    ("other", set()),
])
def test_placement_actions_are_limited_by_window_role(role: str, allowed: set[str]) -> None:
    for action in ("status", "tear_off", "dock", "hide", "ready", "collapse"):
        harness = Harness(config={"placement": "desktop", "visible": True})
        harness.host.open()
        result = placement_callback(harness.host, role)(action, 300.0, 300.0)
        assert (result is not None) is (action in allowed), (role, action)


def test_drop_and_moves_use_dips_on_a_scaled_display() -> None:
    """At 150 % the page drops in DIPs, WinForms work areas and pywebview's
    moved event are physical pixels, and pywebview creates windows in DIPs
    (B100). Two 3840x2088 physical monitors are 2560x1392 DIPs each."""
    screens = [ScreenArea(0, 0, 3840, 2088), ScreenArea(3840, 0, 3840, 2088)]
    harness = Harness(screens=screens)
    harness.host._scale = lambda: 1.5
    assert harness.host.screen_areas() == [ScreenArea(0, 0, 2560, 1392), ScreenArea(2560, 0, 2560, 1392)]
    # A drop in the middle of the second monitor stays centred on it.
    assert harness.host.tear_off(3840, 700) is True
    window = harness.windows[0]
    assert (window.options["x"], window.options["y"]) == (3650, 624)
    # A drop at the bottom keeps the whole window inside the DIP work area.
    low = Harness(screens=screens)
    low.host._scale = lambda: 1.5
    low.host.tear_off(1000, 1380)
    assert low.windows[0].options["y"] == 1392 - 230 - 8
    # Moves arrive in physical pixels and are remembered in DIPs, so the
    # next open lands where the person left Buddy.
    window._events.fire("moved", 5772, 946)
    assert harness.config["overlay"] == {"x": 3848, "y": 631}
    harness.host.close()
    assert harness.host.open() is True
    reopened = harness.windows[-1]
    assert (reopened.options["x"], reopened.options["y"]) == (3848, 631)


def test_tear_off_and_dock_repeat_and_the_main_window_bridge_keeps_working() -> None:
    """Buddy tore off once, then every tear-off failed (B224): three rounds,
    docking from Buddy and from the main window in turn, through the main
    window's own attested bridge."""
    harness = Harness()
    host = harness.host
    driver = PyWebViewDriver(SimpleNamespace(), buddy_placement=placement_callback(host, "main"),
                             read_clipboard=lambda: "fixture")
    bridge = NativeClientBridge(
        instance_id="instance", window_id="main", origin="http://127.0.0.1:8123",
        current_url=lambda: "http://127.0.0.1:8123/app-v2/conversations/c-1", driver=driver,
        authenticate_document=lambda _a, _c: NativeDocumentAuthority("session", "policy", "grant"),
        authorize_document=lambda _a, _c: True)
    proof = bridge._bind_loaded_document()
    assert bridge.native_client_dispatch(proof, "discover", {"attestation": "attest"})["status"] == "ok"
    buddy = placement_callback(host, "buddy")
    desktop = {"placement": "desktop", "visible": True}
    docked = {"placement": "docked", "visible": True}
    for number in range(3):
        assert bridge.native_client_dispatch(proof, "buddy_placement", {"action": "tear_off", "x": 900, "y": 600}) == {
            "status": "ok", "value": desktop}, number
        window = harness.windows[-1]
        assert harness.attached[-1] is window
        assert buddy("ready", None, None) == desktop
        if number % 2:
            assert bridge.native_client_dispatch(proof, "buddy_placement", {"action": "dock"})["value"] == docked
        else:
            assert buddy("dock", None, None) == docked
        assert ("destroy",) in window.calls and host.window is None
        assert bridge.native_client_dispatch(proof, "buddy_placement", {"action": "status"})["value"] == docked
        assert bridge.native_client_dispatch(proof, "clipboard_read", {}) == {"status": "ok", "value": "fixture"}
    assert len(harness.windows) == 3
