"""Hold a desktop hotkey to talk (push to talk), in the Windows desktop app.

Off by default. The page arms it only while a push-to-talk voice session is
open in a desktop window and the person chose a key on this computer; the
session ending, the page loading again and the window closing disarm it. So
it works only while Row-Bot runs, and only then takes that key from other
apps.

Windows: ``RegisterHotKey`` for exactly the chosen combination, on its own
thread with its own message queue (no keyboard hook, so no other keystroke is
ever seen), then ``GetAsyncKeyState`` of that one key until it is released.
macOS is not offered yet: its permission-free hotkey (Carbon's
``RegisterEventHotKey``) needs the app's main run loop, which a follow-up
wires and checks on a Mac.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)

_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_NOREPEAT = 0x4000
_VK_SPACE = 0x20
_VK_F13 = 0x7C
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_HOTKEY_ID = 0x5242  # "RB"

# The keys the person can choose (the client lists the same three).
HOTKEYS: dict[str, tuple[int, int]] = {
    "ctrl+alt+space": (_MOD_CONTROL | _MOD_ALT, _VK_SPACE),
    "ctrl+shift+space": (_MOD_CONTROL | _MOD_SHIFT, _VK_SPACE),
    "f13": (0, _VK_F13),
}
# How often a held key is checked for release, and how long arming waits for the system.
POLL_SECONDS = 0.02
ARM_WAIT_SECONDS = 2.0


class HotkeySystem(Protocol):
    """The operating system's side, one listening thread at a time."""

    def register(self, modifiers: int, key: int) -> bool: ...
    def wait_pressed(self) -> bool: ...
    def is_down(self, key: int) -> bool: ...
    def unregister(self) -> None: ...
    def wake(self) -> None: ...


class _Windows:
    """user32 through ctypes: the hotkey's message queue, and the one key's state."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                             wintypes.UINT]
        self._user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                              wintypes.UINT, wintypes.UINT]
        self._user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM,
                                                    wintypes.LPARAM]
        self._user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self._user32.GetAsyncKeyState.restype = ctypes.c_short
        self._thread = 0

    def register(self, modifiers: int, key: int) -> bool:
        message = self._wintypes.MSG()
        # Give this thread its message queue first, so a wake can always reach it.
        self._user32.PeekMessageW(self._ctypes.byref(message), None, 0, 0, 0)
        self._thread = self._kernel32.GetCurrentThreadId()
        return bool(self._user32.RegisterHotKey(None, _HOTKEY_ID, modifiers | _MOD_NOREPEAT, key))

    def wait_pressed(self) -> bool:
        message = self._wintypes.MSG()
        while True:
            got = self._user32.GetMessageW(self._ctypes.byref(message), None, 0, 0)
            if got in (0, -1):
                return False
            if message.message == _WM_HOTKEY and message.wParam == _HOTKEY_ID:
                return True

    def is_down(self, key: int) -> bool:
        return bool(self._user32.GetAsyncKeyState(key) & 0x8000)

    def unregister(self) -> None:
        self._user32.UnregisterHotKey(None, _HOTKEY_ID)

    def wake(self) -> None:
        if self._thread:
            self._user32.PostThreadMessageW(self._thread, _WM_QUIT, 0, 0)


class HoldHotkey:
    """One hold-to-talk hotkey for this process, armed by one window at a time."""

    def __init__(self, system: Callable[[], HotkeySystem], *, poll: float = POLL_SECONDS,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._system = system
        self._poll = poll
        self._sleep = sleep
        self._lock = threading.Lock()
        self._armed: tuple[str, str] | None = None
        self._stop: threading.Event | None = None
        self._live: HotkeySystem | None = None
        self._thread: threading.Thread | None = None

    @property
    def armed(self) -> tuple[str, str] | None:
        """The key and the window that armed it, or None."""

        return self._armed

    def arm(self, key: str, owner: str, on_hold: Callable[[bool], None]) -> bool:
        """Listen for *key* for *owner*; False when the key is unknown or another app holds it."""

        if key not in HOTKEYS:
            return False
        with self._lock:
            if self._armed == (key, owner):
                return True
        self.disarm()
        modifiers, code = HOTKEYS[key]
        system = self._system()
        stop = threading.Event()
        registered = threading.Event()
        result = {"ok": False}

        def listen() -> None:
            try:
                result["ok"] = system.register(modifiers, code)
            except Exception:
                logger.warning("Voice hotkey could not be registered", exc_info=True)
            registered.set()
            if not result["ok"]:
                return
            try:
                while not stop.is_set() and system.wait_pressed():
                    if stop.is_set():
                        break
                    on_hold(True)
                    while not stop.is_set() and system.is_down(code):
                        self._sleep(self._poll)
                    on_hold(False)
            except Exception:
                logger.warning("Voice hotkey stopped", exc_info=True)
            finally:
                system.unregister()

        thread = threading.Thread(target=listen, name="row-bot-voice-hotkey", daemon=True)
        thread.start()
        if not registered.wait(ARM_WAIT_SECONDS) or not result["ok"]:
            stop.set()
            return False
        with self._lock:
            self._armed = (key, owner)
            self._stop = stop
            self._live = system
            self._thread = thread
        return True

    def disarm(self, owner: str | None = None) -> bool:
        """Stop listening (only *owner*'s hotkey when given); True when nothing is left armed by it."""

        with self._lock:
            if self._armed is None or (owner is not None and self._armed[1] != owner):
                return True
            stop, system, thread = self._stop, self._live, self._thread
            self._armed = self._stop = self._live = self._thread = None
        if stop is not None:
            stop.set()
        if system is not None:
            system.wake()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        return True


_SHARED: HoldHotkey | None = None


def supported() -> bool:
    return sys.platform == "win32"


def shared_hotkey() -> HoldHotkey | None:
    """The process's hotkey on Windows; None where it isn't offered."""

    global _SHARED
    if not supported():
        return None
    if _SHARED is None:
        _SHARED = HoldHotkey(_Windows)
    return _SHARED


def window_hotkey(window: Any, hotkey: HoldHotkey | None = None) -> Callable[[str | None], bool] | None:
    """The bridge's ``voice_hotkey`` for one desktop window: arm a key, or disarm with None.

    A press and a release reach the window's page as a ``row-bot-voice-hotkey``
    event (``detail.held``). The window loading another page or closing
    disarms it.
    """

    hotkey = hotkey if hotkey is not None else shared_hotkey()
    if hotkey is None:
        return None
    owner = str(window.uid)

    def deliver(held: bool) -> None:
        script = ("window.dispatchEvent(new CustomEvent('row-bot-voice-hotkey', "
                  "{ detail: { held: " + json.dumps(bool(held)) + " } }));")
        try:
            (getattr(window, "run_js", None) or window.evaluate_js)(script)
        except Exception:
            pass

    def act(key: str | None) -> bool:
        if key is None:
            return hotkey.disarm(owner)
        return hotkey.arm(key, owner, deliver)

    def release(*_args: Any) -> None:
        hotkey.disarm(owner)

    window.events.before_load += release
    window.events.closed += release
    return act
