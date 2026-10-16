"""Buddy shows a recording of Row-Bot's browser or the screen as work while it lasts (F2)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.subsystem


@pytest.fixture(autouse=True)
def _isolate_buddy_durable_activity(monkeypatch):
    import row_bot.agent_orchestrator as orchestrator
    import row_bot.buddy.brain as brain_mod
    import row_bot.tasks as tasks

    monkeypatch.setattr(tasks, "get_pending_approvals", lambda: [])
    monkeypatch.setattr(tasks, "get_running_tasks", lambda: {})
    monkeypatch.setattr(orchestrator, "get_thread_orchestration_activity", lambda: {})
    monkeypatch.setattr(brain_mod, "get_buddy_config",
                        lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"})


@pytest.mark.parametrize(("kind", "label"), [("screen", "Recording the screen"),
                                             ("browser", "Recording the browser")])
def test_a_recording_reads_as_work_until_it_ends(monkeypatch, kind, label):
    import row_bot.buddy.brain as brain_mod
    import row_bot.recordings as recordings
    from row_bot.buddy.brain import BuddyBrain

    now = 1000.0
    monkeypatch.setattr(brain_mod.time, "time", lambda: now)
    running = [SimpleNamespace(recording_id="rec-1a2b3c4d", kind=kind)]
    monkeypatch.setattr(recordings, "active_recordings", lambda: list(running))
    brain = BuddyBrain()

    now = 1003.0  # After the idle grace.
    working = brain.resolve(None)
    assert working.message == label
    assert working.animation == "listen"

    running.clear()
    now = 1010.0
    assert brain.resolve(None).message == "Idle"
