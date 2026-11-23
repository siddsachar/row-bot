"""Hold-to-talk on a desktop hotkey: one key, only while armed, through the native bridge."""

from __future__ import annotations

import queue
import threading
from types import SimpleNamespace
from typing import Callable

import pytest

from row_bot.native_client import NativeClientBridge, NativeDocumentAuthority, PyWebViewDriver
from row_bot.voice_hotkey import HOTKEYS, HoldHotkey, window_hotkey

pytestmark = pytest.mark.platform

WAIT = 5.0


class FakeSystem:
    """The OS side: presses arrive on a queue, a wake ends the wait, keys are down while a test says."""

    def __init__(self, free: bool = True) -> None:
        self.free = free
        self.registered: list[tuple[int, int]] = []
        self.unregistered = 0
        self.down: set[int] = set()
        self.polled: set[int] = set()
        self.presses: queue.Queue[bool] = queue.Queue()

    def register(self, modifiers: int, key: int) -> bool:
        self.registered.append((modifiers, key))
        return self.free

    def wait_pressed(self) -> bool:
        return self.presses.get(timeout=WAIT)

    def is_down(self, key: int) -> bool:
        self.polled.add(key)
        return key in self.down

    def unregister(self) -> None:
        self.unregistered += 1

    def wake(self) -> None:
        self.presses.put(False)


class Holds:
    """What the window was told: each press and release (or script), waited for."""

    def __init__(self) -> None:
        self.seen: list = []
        self._changed = threading.Condition()

    def __call__(self, held) -> None:
        with self._changed:
            self.seen.append(held)
            self._changed.notify_all()

    def wait(self, count: int) -> list:
        with self._changed:
            assert self._changed.wait_for(lambda: len(self.seen) >= count, timeout=WAIT)
            return list(self.seen)


def hotkey(system: FakeSystem) -> HoldHotkey:
    return HoldHotkey(lambda: system, poll=0.001)


def test_holding_the_key_talks_until_it_is_released_and_disarming_lets_it_go() -> None:
    system = FakeSystem()
    keys = hotkey(system)
    holds = Holds()
    assert keys.arm("ctrl+alt+space", "main", holds)
    modifiers, code = HOTKEYS["ctrl+alt+space"]
    # Exactly that one combination is registered: no keyboard hook, no other key.
    assert system.registered == [(modifiers, code)]
    system.down.add(code)
    system.presses.put(True)
    assert holds.wait(1) == [True]
    system.down.discard(code)
    assert holds.wait(2) == [True, False]
    assert system.polled == {code}
    assert keys.disarm()
    assert keys.armed is None
    assert system.unregistered == 1


def test_a_key_another_app_holds_is_refused_and_nothing_stays_armed() -> None:
    system = FakeSystem(free=False)
    keys = hotkey(system)
    assert not keys.arm("ctrl+shift+space", "main", Holds())
    assert keys.armed is None
    made: list[FakeSystem] = []
    refusing = HoldHotkey(lambda: made.append(FakeSystem()) or made[-1])
    assert not refusing.arm("alt+f4", "main", Holds())
    assert made == []


def test_one_window_at_a_time_and_only_its_own_disarm_counts() -> None:
    systems: list[FakeSystem] = []

    def system() -> FakeSystem:
        systems.append(FakeSystem())
        return systems[-1]

    keys = HoldHotkey(system, poll=0.001)
    assert keys.arm("f13", "main", Holds())
    assert keys.arm("f13", "main", Holds())
    assert len(systems) == 1
    assert keys.arm("ctrl+alt+space", "other", Holds())
    assert systems[0].unregistered == 1
    assert keys.disarm("main")
    assert keys.armed == ("ctrl+alt+space", "other")
    assert keys.disarm("other")
    assert keys.armed is None


def test_disarming_while_held_releases_the_hold() -> None:
    system = FakeSystem()
    keys = hotkey(system)
    holds = Holds()
    assert keys.arm("f13", "main", holds)
    system.down.add(HOTKEYS["f13"][1])
    system.presses.put(True)
    holds.wait(1)
    keys.disarm()
    assert holds.wait(2) == [True, False]
    assert system.unregistered == 1


class _Event:
    def __init__(self) -> None:
        self.handlers: list[Callable[..., object]] = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self) -> None:
        for handler in self.handlers:
            handler()


def test_a_window_hears_presses_as_page_events_and_loading_or_closing_disarms() -> None:
    system = FakeSystem()
    keys = hotkey(system)
    scripts = Holds()
    window = SimpleNamespace(uid="main-window", run_js=scripts,
                             events=SimpleNamespace(before_load=_Event(), closed=_Event()))
    act = window_hotkey(window, keys)
    assert act is not None
    assert act("ctrl+alt+space")
    code = HOTKEYS["ctrl+alt+space"][1]
    system.down.add(code)
    system.presses.put(True)
    system.down.discard(code)
    assert scripts.wait(2) == [
        "window.dispatchEvent(new CustomEvent('row-bot-voice-hotkey', { detail: { held: true } }));",
        "window.dispatchEvent(new CustomEvent('row-bot-voice-hotkey', { detail: { held: false } }));",
    ]
    window.events.before_load.fire()
    assert keys.armed is None
    assert act("f13")
    window.events.closed.fire()
    assert keys.armed is None
    assert act(None)


class _Driver:
    def __init__(self, hotkey: Callable[[str | None], bool] | None) -> None:
        self.inner = PyWebViewDriver(SimpleNamespace(), voice_hotkey=hotkey)

    def capabilities(self) -> list[str]:
        return self.inner.capabilities()

    def voice_hotkey(self, key: str | None) -> bool:
        return self.inner.voice_hotkey(key)


def _bridge(hotkey: Callable[[str | None], bool] | None):
    bridge = NativeClientBridge(
        instance_id="instance", window_id="window", origin="http://localhost:8080",
        current_url=lambda: "http://localhost:8080/app-v2/", driver=_Driver(hotkey),
        authenticate_document=lambda _attestation, _context: NativeDocumentAuthority("s", "p", "g"),
        authorize_document=lambda _authority, _context: True,
    )
    proof = bridge._bind_loaded_document()
    assert proof
    assert bridge.native_client_dispatch(proof, "discover", {"attestation": "attestation"})["status"] == "ok"
    return bridge, proof


def test_the_bridge_arms_only_listed_keys_for_a_current_document() -> None:
    calls: list[str | None] = []
    bridge, proof = _bridge(lambda key: calls.append(key) or key != "f13")
    discovered = bridge.native_client_dispatch(proof, "discover", {})
    assert "voice_hotkey" in discovered["value"]["capabilities"]
    assert bridge.native_client_dispatch(proof, "voice_hotkey", {"key": "ctrl+alt+space"}) == {
        "status": "ok", "value": None}
    assert bridge.native_client_dispatch(proof, "voice_hotkey", {"key": None})["status"] == "ok"
    # Another app holds this one: the page hears it can't be used.
    assert bridge.native_client_dispatch(proof, "voice_hotkey", {"key": "f13"}) == {
        "status": "unavailable", "reason": "hotkey_unavailable"}
    for payload in ({"key": "alt+f4"}, {"key": "ctrl+alt+space", "scan": True}, {}, {"key": 32}):
        assert bridge.native_client_dispatch(proof, "voice_hotkey", payload)["status"] == "unavailable"
    assert calls == ["ctrl+alt+space", None, "f13"]
    bridge._invalidate()
    assert bridge.native_client_dispatch(proof, "voice_hotkey", {"key": "ctrl+alt+space"})["status"] == "unavailable"
    assert calls == ["ctrl+alt+space", None, "f13"]


def test_a_window_without_the_hotkey_never_offers_it() -> None:
    bridge, proof = _bridge(None)
    discovered = bridge.native_client_dispatch(proof, "discover", {})
    assert "voice_hotkey" not in discovered["value"]["capabilities"]
    assert bridge.native_client_dispatch(proof, "voice_hotkey", {"key": "f13"})["status"] == "unavailable"
