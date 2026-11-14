"""Desktop context for timing: read on this computer, unknown where it can't be told, never contents."""
from __future__ import annotations

import sys

import pytest

from row_bot.proactive import desktop_context
from row_bot.proactive.model import Desktop

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.mark.parametrize("program,kind", [
    ("chrome.exe", "browser"), ("msedge.exe", "browser"), ("com.apple.Safari", "browser"),
    ("Code.exe", "editor"), ("com.jetbrains.pycharm", "editor"),
    ("Zoom.exe", "meeting"), ("ms-teams.exe", "meeting"), ("us.zoom.xos", "meeting"),
    ("explorer.exe", "other"), ("", ""),
])
def test_the_foreground_app_is_read_as_a_kind_from_its_program_name_only(program, kind):
    assert desktop_context.app_kind_for_program(program) == kind


def test_reading_the_desktop_never_fails_and_every_signal_is_known_or_none():
    state = desktop_context.read()

    assert isinstance(state, Desktop)
    assert state.idle_seconds is None or state.idle_seconds >= 0
    for value in (state.locked, state.focus, state.mic_in_use, state.camera_in_use):
        assert value in (None, True, False)
    assert state.app_kind in (None, "browser", "editor", "meeting", "other")
    if sys.platform not in ("win32", "darwin"):
        assert not state.known and state.notes, "unknown, and it says why"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS readings")
def test_macos_says_which_signals_it_cannot_read():
    state = desktop_context.read()

    assert state.focus is None and state.mic_in_use is None and state.camera_in_use is None
    assert any("macOS" in note for note in state.notes)


def test_a_signal_that_fails_to_read_is_unknown_not_an_error(monkeypatch):
    def broken():
        raise OSError("no session")

    monkeypatch.setattr(desktop_context, "_windows_idle", broken)
    monkeypatch.setattr(desktop_context, "_mac_idle", broken)

    assert desktop_context.read().idle_seconds is None
