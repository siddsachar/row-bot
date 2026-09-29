"""React turns and opened conversations count as the person's activity (B188).

Memory extraction waits until the person has been away for a while and never
extracts from the conversation in front of them. Only the NiceGUI page used to
report either, so React use left the idle timer running and every conversation
eligible. Admitting a turn now marks activity, and opening a conversation marks
it as the one in front of the person.
"""
from __future__ import annotations

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import platform  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401

pytestmark = pytest.mark.subsystem


@pytest.fixture
def extraction(monkeypatch):
    from row_bot import memory_extraction

    clock = {"now": 10_000.0}
    monkeypatch.setattr(memory_extraction.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(memory_extraction, "_last_activity_ts", 0.0)
    monkeypatch.setattr(memory_extraction, "_active_threads", set())
    monkeypatch.setattr(memory_extraction, "_open_thread", None, raising=False)
    return memory_extraction


def test_admitting_a_turn_marks_activity(platform, extraction):
    assert extraction.idle_seconds() == 10_000.0

    handle = platform.admit_execution(
        "conversation-a",
        {"configurable": {"platform_submission_id": "activity-turn"}},
        text="A synthetic message",
    )
    try:
        assert extraction.idle_seconds() == 0.0
    finally:
        platform.finish_execution(handle, "interrupted")


def test_opening_a_conversation_keeps_it_out_of_extraction(service, extraction):
    from row_bot import threads
    from row_bot.application.conversation_open import read_open

    service.readiness_factory = lambda _: False
    first = threads.create_thread("First open", seed_default_skills=False)
    second = threads.create_thread("Second open", seed_default_skills=False)

    read_open(service, first)
    assert extraction._active_threads == {first}
    assert extraction.idle_seconds() == 0.0

    read_open(service, second)
    assert extraction._active_threads == {second}
