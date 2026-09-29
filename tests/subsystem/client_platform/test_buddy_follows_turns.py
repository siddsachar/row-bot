"""Buddy follows a conversation turn: thinking, tools, approval, done (B201).

Only the NiceGUI page told Buddy about its own turns, so Buddy never reacted
to a turn in the React client. The client platform now reports each turn's
start, tool use, approval wait, end and failure to Buddy.
"""
from __future__ import annotations

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import command, platform, submit  # noqa: F401
from tests.helpers.client_platform_fakes import ScriptedAgentStream

pytestmark = pytest.mark.subsystem


def _events_after(start: int) -> list[tuple[str, str]]:
    from row_bot.buddy.events import get_buddy_event_bus

    return [(str(event.type), str(event.payload.get("thread_id") or ""))
            for event in get_buddy_event_bus().recent(after_id=start, limit=100)]


def _latest_id() -> int:
    from row_bot.buddy.events import get_buddy_event_bus

    latest = get_buddy_event_bus().latest()
    return latest.id if latest else 0


def test_buddy_sees_a_turn_think_use_a_tool_and_finish(platform):
    start = _latest_id()
    fake = ScriptedAgentStream((
        ("tool_call", {"name": "fixture_tool", "tool_call_id": "call-1"}),
        ("tool_done", {"name": "fixture_tool", "tool_call_id": "call-1", "content": "ok"}),
        ("token", "Finished"),
        ("done", "Finished"),
    ))

    accepted = submit(platform, fake, "buddy-turn")
    assert platform.registry.get(accepted["execution_id"]).producer_done.wait(10)

    events = [kind for kind, thread in _events_after(start) if thread == "conversation-a"]
    assert events == ["generation.started", "tool.started", "tool.finished", "generation.done"]


def test_buddy_sees_a_turn_wait_for_approval(platform):
    start = _latest_id()
    fake = ScriptedAgentStream((("interrupt", {"__interrupt_id": "buddy-approval", "tool": "fixture_tool",
                                               "description": "Synthetic approval"}),),)

    accepted = submit(platform, fake, "buddy-approval")
    assert platform.registry.get(accepted["execution_id"]).producer_done.wait(10)

    events = [kind for kind, thread in _events_after(start) if thread == "conversation-a"]
    assert events == ["generation.started", "approval.needed"]


def test_buddy_sees_a_failed_turn(platform):
    start = _latest_id()
    fake = ScriptedAgentStream((("error", "Synthetic provider failure"),))

    accepted = submit(platform, fake, "buddy-error")
    assert platform.registry.get(accepted["execution_id"]).producer_done.wait(10)

    events = [kind for kind, thread in _events_after(start) if thread == "conversation-a"]
    assert events == ["generation.started", "generation.error"]
