"""Retained voice controls use the same safe admission result as the API."""
from uuid import uuid4

import pytest

from tests.subsystem.voice.test_dictation_lifecycle import runtime  # noqa: F401

pytestmark = pytest.mark.subsystem


def test_busy_voice_admission_is_recoverable_without_taking_over(runtime, monkeypatch):  # noqa: F811
    from nicegui import ui
    from row_bot.ui.voice_lifecycle import start_voice_for_ui
    notices = []
    monkeypatch.setattr(ui, "notify", lambda text, **kwargs: notices.append((text, kwargs)))
    capture = runtime.adapter.start(runtime.owner, request_id=str(uuid4()), validate=lambda: None)
    assert start_voice_for_ui(runtime.coordinator.start_talk) is None
    assert len(notices) == 1 and "another window" in notices[0][0]
    assert not runtime.voice.effects
    assert runtime.adapter.snapshot(runtime.owner, capture.handle, validate=lambda: None).state == "capturing"
    runtime.adapter.stop(runtime.owner, capture.handle, validate=lambda: None)
    assert start_voice_for_ui(runtime.coordinator.start_browser) is not None


def test_unrelated_start_errors_are_not_misreported_as_busy(monkeypatch):
    from nicegui import ui
    from row_bot.ui.voice_lifecycle import start_voice_for_ui
    monkeypatch.setattr(ui, "notify", lambda *args, **kwargs: pytest.fail("Unrelated failure hidden"))
    def broken():
        raise ValueError("synthetic startup failure")
    with pytest.raises(ValueError, match="synthetic startup failure"):
        start_voice_for_ui(broken)
