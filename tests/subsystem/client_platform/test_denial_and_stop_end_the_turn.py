"""A denial or a Stop ends the turn for good (B199, B200).

The NiceGUI page settled a denied approval without asking the model again, so
it could not retry the action or reach for another way, and it made an old
approval card inert once the person pressed Stop. The React path resumed the
whole stream after a denial and left a waiting approval answerable after Stop,
where answering it started the run again.
"""
from __future__ import annotations

import json

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import command, platform, submit  # noqa: F401
from tests.helpers.client_platform_fakes import ScriptedAgentStream

pytestmark = pytest.mark.subsystem

APPROVAL = {"__interrupt_id": "synthetic-interrupt", "tool": "fixture_tool", "description": "Synthetic approval"}


def _waiting(platform, fake, label):
    accepted = submit(platform, fake, label)
    first = platform.registry.get(accepted["execution_id"])
    assert first.producer_done.wait(10)
    assert first.status == "waiting_approval"
    return first


def _resolve(platform, approval_id, decision, label):
    return platform.execute(owner_id="owner", idempotency_key=label, target=approval_id,
                            command=command("approval.resolve", label, {"decision": decision}))


def test_a_denied_approval_ends_the_turn_before_the_model_can_try_again(platform):
    fake = ScriptedAgentStream(
        (("interrupt", APPROVAL),),
        (("tool_done", {"name": "fixture_tool", "tool_call_id": "call-1", "content": "Action cancelled by user."}),
         ("tool_call", {"name": "fixture_tool", "tool_call_id": "call-2"}),
         ("token", "Trying another way"),
         ("done", "Trying another way")))
    first = _waiting(platform, fake, "denial")

    receipt = _resolve(platform, first.approval_id, "deny", "deny-once")

    resumed = platform.registry.get(receipt["execution_id"])
    assert resumed.producer_done.wait(10)
    assert resumed.status == "completed"
    assert fake.calls[-1]["approved"] is False
    emitted = [event[0] for event in fake.events]
    assert emitted[-1] == "tool_done"  # the model was never asked again
    assert platform.get_approval(first.approval_id)["status"] == "denied"
    last = platform.transcript("conversation-a")["rows"][-1]
    assert last["role"] == "assistant"
    assert "The requested action was denied. No action was taken." in json.dumps(last)


def test_an_approval_still_resumes_the_whole_turn(platform):
    fake = ScriptedAgentStream(
        (("interrupt", APPROVAL),),
        (("tool_done", {"name": "fixture_tool", "tool_call_id": "call-1", "content": "Done."}),
         ("token", "All done"),
         ("done", "All done")))
    first = _waiting(platform, fake, "approval")

    receipt = _resolve(platform, first.approval_id, "approve", "approve-once")

    resumed = platform.registry.get(receipt["execution_id"])
    assert resumed.producer_done.wait(10)
    assert [event[0] for event in fake.events][-2:] == ["token", "done"]


def test_stop_withdraws_the_approval_the_turn_waits_on(platform):
    from row_bot.application.client_platform import ClientPlatformError

    fake = ScriptedAgentStream((("interrupt", APPROVAL),), (("done", "Should never run"),))
    first = _waiting(platform, fake, "stopped")

    platform.execute(owner_id="owner", idempotency_key="stop-it", target="conversation-a",
                     command=command("conversation.stop", "stop-it"))

    # The old card is refused (it is no longer current), and nothing runs.
    with pytest.raises(ClientPlatformError, match="approval_already_resolved|revision_conflict"):
        _resolve(platform, first.approval_id, "approve", "late-approve")
    assert len(fake.calls) == 1
    assert platform.get_approval(first.approval_id)["status"] == "cancelled"
