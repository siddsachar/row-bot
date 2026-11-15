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
    # Plain and honest: earlier steps of the turn may have acted, so it never says nothing happened (U13).
    assert "You said no, so Row-Bot stopped there." in json.dumps(last)


def test_a_declined_memory_save_says_it_was_not_saved(platform):
    """U13: after an incognito chat's turn had saved one memory, Don't save on the next read
    "The requested action was denied. No action was taken." — cold, and false."""
    memory = {**APPROVAL, "tool": "save_memory", "memory_save": {"text": "Tea: green, no sugar"}}
    fake = ScriptedAgentStream(
        (("interrupt", memory),),
        (("tool_done", {"name": "save_memory", "tool_call_id": "call-1", "content": "Not saved."}),
         ("token", "Saving it another way"),
         ("done", "Saving it another way")))
    first = _waiting(platform, fake, "memory-denial")

    receipt = _resolve(platform, first.approval_id, "deny", "dont-save")

    resumed = platform.registry.get(receipt["execution_id"])
    assert resumed.producer_done.wait(10)
    assert [event[0] for event in fake.events][-1] == "tool_done"  # still not asked again (B234)
    last = platform.transcript("conversation-a")["rows"][-1]
    assert "You chose not to save that, so Row-Bot didn't." in json.dumps(last)


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


def test_after_a_no_the_reply_says_what_the_turn_already_found(platform, monkeypatch):
    """U19: denying an install ended the turn with "You said no, so Row-Bot stopped there." and the revenue
    by region, worked out two steps earlier, was never shown. The reply now comes from the chat's model with
    no tools (it can't try the declined action again) and holds what was found."""
    from langchain_core.messages import AIMessage, ToolMessage

    from tests.helpers.client_platform_fakes import CheckpointCommit, fixture_id

    read = {"name": "shell", "args": {"command": "Import-Csv sales.csv"}, "id": "call-read"}
    install = {"name": "shell", "args": {"command": "winget install ffmpeg"}, "id": "call-install"}
    prompts: list = []

    class Model:
        def invoke(self, prompt):
            prompts.append(prompt)
            return AIMessage(content="Revenue by region: North 120, South 80. FFmpeg wasn't installed: you said no.")

    monkeypatch.setattr("row_bot.models.get_llm_for", lambda *_args, **_kwargs: Model())
    fake = ScriptedAgentStream(
        (CheckpointCommit((AIMessage(content="", id=fixture_id("read-step"), tool_calls=[read]),
                           ToolMessage(content="North 120, South 80", name="shell", tool_call_id="call-read",
                                       id=fixture_id("read-result")),
                           AIMessage(content="", id=fixture_id("install-step"), tool_calls=[install]))),
         ("interrupt", APPROVAL)),
        (("tool_done", {"name": "shell", "tool_call_id": "call-install", "content": "Action cancelled by user."}),
         ("tool_call", {"name": "shell", "tool_call_id": "call-again"}),
         ("done", "Trying another way")))
    first = _waiting(platform, fake, "found-then-denied")

    resumed = platform.registry.get(_resolve(platform, first.approval_id, "deny", "deny-install")["execution_id"])
    assert resumed.producer_done.wait(10)
    assert [event[0] for event in fake.events][-1] == "tool_done"  # the agent was never asked to go on
    assert "North 120, South 80" in prompts[0][1].content and "winget install ffmpeg" in prompts[0][1].content
    last = platform.transcript("conversation-a")["rows"][-1]
    assert "Revenue by region: North 120, South 80." in json.dumps(last)
