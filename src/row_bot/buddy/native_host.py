"""Native host for the desktop Buddy window.

The launcher's pywebview window process owns one ``BuddyWindowHost``. It opens
the React overlay (``/app-v2/buddy-overlay``) in a frameless, always-on-top
380×230 window, attaches the attested client bridge to it, reveals it only
after the page reports ready, and relays which conversation the main window
shows. Nothing here imports pywebview: the window process passes in
``webview.create_window`` and the windows, so the lifecycle is testable.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from typing import Any, Callable, Iterable, Mapping

from .overlay import (
    OVERLAY_HEIGHT,
    OVERLAY_WIDTH,
    BuddyPlacement,
    BuddyPlacementState,
    ScreenArea,
    apply_placement_state,
    finite_coordinate,
    native_overlay_transparency,
    placement_state_from_config,
    position_for_drop,
    should_defer_native_show,
)

OVERLAY_ROUTE = "/app-v2/buddy-overlay"
READY_TIMEOUT_SECONDS = 2.0
# Conversation ids the host accepts (the bridge's scope value).
CONVERSATION_ID = re.compile(r"[A-Za-z0-9:_.-]{1,256}")
# A content-free nudge: the page re-reads through its own attested bridge.
CHANGED_EVENT_SCRIPT = (
    "window.dispatchEvent(new Event('row-bot-native-changed'));"
)


def buddy_overlay_url(port: int) -> str:
    """The React overlay document on the local app server."""

    return f"http://127.0.0.1:{int(port)}{OVERLAY_ROUTE}"


def overlay_window_options(
    url: str,
    *,
    width: int = OVERLAY_WIDTH,
    height: int = OVERLAY_HEIGHT,
    x: int | None = None,
    y: int | None = None,
    platform: str | None = None,
) -> list[dict[str, Any]]:
    """``create_window`` keyword sets, richest first.

    None of them carries a ``js_api``: the overlay reaches the host only
    through the attested bridge, never the legacy shared API. Windows keeps
    an opaque host so the overlay stays hit-testable; macOS is transparent.
    """

    rich: dict[str, Any] = {
        "title": "Buddy",
        "url": url,
        "width": int(width),
        "height": int(height),
        "x": int(x) if x is not None else None,
        "y": int(y) if y is not None else None,
        "resizable": False,
        "frameless": True,
        "shadow": False,
        "focus": True,
        "on_top": True,
        "easy_drag": False,
        "hidden": True,
        "background_color": "#0B1119",
        "transparent": native_overlay_transparency(platform or sys.platform),
    }
    fallback = {key: value for key, value in rich.items() if key != "background_color"}
    minimal = {
        key: value
        for key, value in fallback.items()
        if key not in {"transparent", "shadow", "on_top", "hidden"}
    }
    plain = {"title": "Buddy", "url": url, "width": int(width), "height": int(height)}
    return [rich, fallback, minimal, plain]


def valid_conversation_id(value: object) -> bool:
    return isinstance(value, str) and bool(CONVERSATION_ID.fullmatch(value))


def _start_timer(delay: float, callback: Callable[[], None]) -> None:
    def run() -> None:
        time.sleep(delay)
        callback()

    threading.Thread(target=run, daemon=True, name="buddy-ready-timeout").start()


class BuddyWindowHost:
    """Placement, window lifecycle and the followed conversation."""

    def __init__(
        self,
        *,
        create_window: Callable[..., Any],
        load_config: Callable[[], dict[str, Any]],
        save_config: Callable[[dict[str, Any]], Any],
        screens: Callable[[], Iterable[ScreenArea]],
        main_window: Callable[[], Any | None],
        attach: Callable[[Any], Any] | None,
        port: int,
        log: Callable[[str], None] = lambda _message: None,
        platform: str | None = None,
        start_timer: Callable[[float, Callable[[], None]], None] = _start_timer,
        ready_timeout: float = READY_TIMEOUT_SECONDS,
    ) -> None:
        self._create_window = create_window
        self._load_config = load_config
        self._save_config = save_config
        self._screens = screens
        self._main_window = main_window
        self._attach = attach
        self._port = int(port)
        self._log = log
        self._platform = platform or sys.platform
        self._start_timer = start_timer
        self._ready_timeout = ready_timeout
        self._lock = threading.RLock()
        self.window: Any | None = None
        self.ready = False
        self._target: str | None = None
        self._target_revision = 0

    # Placement -----------------------------------------------------------
    def placement(self) -> BuddyPlacementState:
        return placement_state_from_config(self._load_config())

    def _save(self, state: BuddyPlacementState, *, x: int | None = None, y: int | None = None) -> None:
        config = apply_placement_state(self._load_config(), state)
        overlay = dict(config.get("overlay") or {})
        if x is not None:
            overlay["x"] = int(x)
        if y is not None:
            overlay["y"] = int(y)
        config["overlay"] = overlay
        self._save_config(config)

    def status(self) -> dict[str, Any]:
        state = self.placement()
        return {"placement": state.placement.value, "visible": bool(state.visible)}

    # Window lifecycle ----------------------------------------------------
    def tear_off(self, screen_x: object, screen_y: object) -> bool:
        """Put Buddy on the desktop centred on a drop point (screen CSS px)."""

        try:
            drop_x = finite_coordinate(screen_x)
            drop_y = finite_coordinate(screen_y)
            x, y = position_for_drop(drop_x, drop_y, list(self._screens()))
        except Exception:
            return False
        state = self.placement().tear_off()
        self._save(state, x=x, y=y)
        self._log(f"tear-off drop={drop_x:.0f},{drop_y:.0f} position={x},{y}")
        opened = self.open(x=x, y=y)
        if not opened:
            self._save(state.dock())
        return opened

    def open(
        self,
        *,
        x: int | None = None,
        y: int | None = None,
        width: int = OVERLAY_WIDTH,
        height: int = OVERLAY_HEIGHT,
    ) -> bool:
        config = self._load_config()
        state = placement_state_from_config(config)
        if state.placement is not BuddyPlacement.DESKTOP:
            return False
        stored = dict(config.get("overlay") or {})
        requested_x = stored.get("x") if x is None else x
        requested_y = stored.get("y") if y is None else y
        if requested_x is not None and requested_y is not None:
            # Re-clamp a remembered position: its monitor may be gone.
            x, y = position_for_drop(
                finite_coordinate(requested_x) + width / 2,
                finite_coordinate(requested_y) + min(height / 3, 76),
                list(self._screens()),
                width=width,
                height=height,
            )
            self._save(state, x=x, y=y)
        else:
            x = y = None
        with self._lock:
            existing = self.window
        if existing is not None:
            try:
                if x is not None and y is not None:
                    existing.move(int(x), int(y))
                try:
                    existing.restore()
                except Exception:
                    pass
                if state.visible and self.ready:
                    existing.show()
                return True
            except Exception:
                with self._lock:
                    if self.window is existing:
                        self.window = None
        window = None
        url = buddy_overlay_url(self._port)
        for options in overlay_window_options(
            url, width=width, height=height, x=x, y=y, platform=self._platform
        ):
            try:
                window = self._create_window(**options)
                break
            except Exception as exc:
                self._log(f"create_window failed with keys {sorted(options)}: {exc}")
        if window is None:
            return False
        with self._lock:
            self.window = window
            self.ready = False
        if self._attach is not None:
            try:
                self._attach(window)
            except Exception as exc:
                # Without the bridge the page cannot report ready; the
                # timeout below still reveals it.
                self._log(f"overlay bridge unavailable: {exc}")
        try:
            window.events.closed += lambda *_args: self._forget(window)
            window.events.moved += lambda moved_x, moved_y: self.moved(moved_x, moved_y)
        except Exception:
            pass
        self._start_timer(self._ready_timeout, lambda: self._ready_timeout_show(window))
        return True

    def _forget(self, window: Any) -> None:
        with self._lock:
            if self.window is window:
                self.window = None
                self.ready = False

    def _ready_timeout_show(self, window: Any) -> None:
        with self._lock:
            if self.window is not window or self.ready:
                return
        state = self.placement()
        if state.placement is not BuddyPlacement.DESKTOP or not state.visible:
            return
        try:
            window.show()
            self._log("page-ready handshake timed out; forced native show")
        except Exception as exc:
            self._log(f"page-ready timeout show failed: {exc}")

    def mark_ready(self) -> bool:
        with self._lock:
            window = self.window
            self.ready = window is not None
        if window is None:
            return False
        state = self.placement()
        if state.placement is not BuddyPlacement.DESKTOP or not state.visible:
            return True
        try:
            try:
                window.restore()
            except Exception:
                pass
            window.show()
            self._log("page ready; native window shown")
            return True
        except Exception as exc:
            self._log(f"page ready show failed: {exc}")
            return False

    def show(self, manual: bool = True) -> bool:
        state = self.placement()
        if state.placement is not BuddyPlacement.DESKTOP:
            return False
        if manual:
            self._save(state.show())
        with self._lock:
            window, ready = self.window, self.ready
        if window is None:
            return self.open()
        if should_defer_native_show(ready=ready, manual=bool(manual)):
            return True
        try:
            try:
                window.restore()
            except Exception:
                pass
            window.show()
            if manual and not ready:
                self._log("manual recovery forced native show before page-ready handshake")
            return True
        except Exception as exc:
            self._log(f"native show failed: {exc}")
            return False

    def hide(self, manual: bool = True) -> bool:
        state = self.placement()
        if state.placement is not BuddyPlacement.DESKTOP:
            return False
        if manual:
            self._save(state.hide())
        with self._lock:
            window = self.window
        if window is None:
            return False
        try:
            window.hide()
            return True
        except Exception:
            return False

    def close(self) -> bool:
        with self._lock:
            window = self.window
        if window is None:
            return True
        try:
            window.destroy()
        except Exception:
            return False
        self._forget(window)
        return True

    def dock(self) -> bool:
        self._save(self.placement().dock())
        self._log("docked")
        return self.close()

    def moved(self, x: object, y: object) -> None:
        try:
            self._save(self.placement(), x=int(x), y=int(y))  # type: ignore[arg-type]
        except Exception:
            pass

    def main_closing(self) -> bool:
        """Return False to cancel closing the main window while Buddy is out."""

        if self.placement().placement is not BuddyPlacement.DESKTOP:
            return True
        window = self._main_window()
        try:
            if window is not None:
                window.hide()
            self._log("main-window close cancelled; hidden while Buddy is desktop")
            return False
        except Exception as exc:
            self._log(f"main-window hide-on-close failed: {exc}")
            return True

    # The followed conversation -------------------------------------------
    def target(self) -> dict[str, Any]:
        with self._lock:
            return {"conversationId": self._target, "revision": self._target_revision}

    def publish_target(self, conversation_id: object) -> dict[str, Any] | None:
        """A main window shows this conversation; Buddy follows it."""

        if not valid_conversation_id(conversation_id):
            return None
        with self._lock:
            changed = conversation_id != self._target
            if changed:
                self._target = str(conversation_id)
                self._target_revision += 1
            value = {"conversationId": self._target, "revision": self._target_revision}
            window = self.window
        if changed and window is not None:
            _run_script(window, CHANGED_EVENT_SCRIPT)
        return value

    def show_main(self, conversation_id: object) -> bool:
        """Bring the main window forward, asking it to open a conversation."""

        if conversation_id is not None and not valid_conversation_id(conversation_id):
            return False
        window = self._main_window()
        if window is None:
            return False
        try:
            try:
                window.restore()
            except Exception:
                pass
            window.show()
        except Exception:
            return False
        if conversation_id is not None:
            detail = json.dumps(str(conversation_id))
            _run_script(
                window,
                "window.dispatchEvent(new CustomEvent('row-bot-open-conversation', "
                "{ detail: " + detail + " }));",
            )
        return True


def _run_script(window: Any, script: str) -> None:
    """Fire and forget: never wait on another window's page."""

    try:
        runner = getattr(window, "run_js", None) or window.evaluate_js
        runner(script)
    except Exception:
        pass


def placement_callback(
    host: BuddyWindowHost, role: str
) -> Callable[[str, float | None, float | None], dict[str, Any] | None]:
    """The bridge's ``buddy_placement`` for one window role.

    Main windows tear Buddy off and dock it; the desktop Buddy docks, hides
    itself and reports ready. Anything else is refused (``None``).
    """

    allowed: Mapping[str, frozenset[str]] = {
        "main": frozenset({"status", "tear_off", "dock"}),
        "buddy": frozenset({"status", "dock", "hide", "ready"}),
    }

    def act(action: str, x: float | None, y: float | None) -> dict[str, Any] | None:
        if action not in allowed.get(role, frozenset()):
            return None
        if action == "tear_off" and not host.tear_off(x, y):
            return None
        if action == "dock" and not host.dock():
            return None
        if action == "hide" and not host.hide(True):
            return None
        if action == "ready" and not host.mark_ready():
            return None
        return host.status()

    return act
