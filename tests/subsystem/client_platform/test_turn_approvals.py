"""Approving the rest of a turn's actions of one kind (F21).

The real graph streams and resumes with a fake model; the tools interrupt as
the Developer tools do. Approving for the turn answers later approvals of the
same kind in that turn, and nothing beyond it: another kind still asks, and
the next turn asks again.
"""
# ruff: noqa: F811 -- the imported platform fixture is requested by name.
from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.types import interrupt
import pytest

from tests.contracts.client_platform.test_headless_lifecycle import command, platform  # noqa: F401
from tests.subsystem.client_platform.test_finished_turns_answer_every_call import (
    ProviderShapedModel,
    RealGraphTurns,
    _call,
    _submit,
)

pytestmark = pytest.mark.subsystem


def _settle(platform) -> None:
    """Wait for this and any follow-on pass (an auto-answered approval starts one) to finish.

    A pass marks itself done before its finishing step, under the command lock, answers a granted approval
    and starts the next pass; taking the lock waits that step out.
    """
    from row_bot.application.client_platform import _COMMAND_LOCK

    for _ in range(50):
        with _COMMAND_LOCK:
            active = platform.registry.active("conversation-a")
        if not active:
            return
        for handle in active:
            assert handle.producer_done.wait(10)
    raise AssertionError("conversation never settled")


def _approve(platform, approval_id: str, label: str, scope: str = "once") -> None:
    receipt = platform.execute(owner_id="fixture-owner", idempotency_key=label, target=approval_id,
                               command=command("approval.resolve", label, {"decision": "approve", "scope": scope}))
    assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)
    _settle(platform)


def _cards(platform) -> list[str]:
    events = platform.projection.events_since("conversation-a", "0")["events"]
    return [event["payload"]["action_label"] for event in events if event["type"] == "approval.required"]


@pytest.fixture
def tools():
    done: list[str] = []

    def gated(name: str, effect: str) -> str:
        if not interrupt({"tool": name, "label": name, "description": effect, "args": {"message": effect}}):
            return "Cancelled."
        done.append(effect)
        return f"Done: {effect}"

    @tool
    def developer_commit_changes(message: str) -> str:
        """Commit."""
        return gated("developer_commit_changes", message)

    @tool
    def developer_push_current_branch() -> str:
        """Push."""
        return gated("developer_push_current_branch", "push")

    return done, [developer_commit_changes, developer_push_current_branch]


def test_approving_for_the_turn_answers_later_ones_of_that_kind_only(platform, tools):
    done, tool_list = tools
    model = ProviderShapedModel(responses=[
        _call("developer_commit_changes", "c1", message="one"),
        _call("developer_commit_changes", "c2", message="two"),
        _call("developer_push_current_branch", "p1"),
        AIMessage(content="Committed twice and pushed.", id="ai-done"),
    ])
    turns = RealGraphTurns(model, tool_list)

    first = _submit(platform, turns, "commit-twice", "Commit twice, then push")
    assert first.status == "waiting_approval"
    assert platform.get_approval(first.approval_id)["repeatable"] is True
    _approve(platform, first.approval_id, "commit-turn", scope="turn")

    # The second commit ran without a card; the push, another kind, waits.
    assert done == ["one", "two"]
    push =[event for event in platform.projection.events_since("conversation-a", "0")["events"]
            if event["type"] == "approval.required"][-1]["payload"]
    assert platform.get_approval(push["approval_id"])["repeatable"] is False
    assert len(_cards(platform)) == 2

    _approve(platform, push["approval_id"], "push-once")
    assert done == ["one", "two", "push"]


def test_the_next_turn_asks_again(platform, tools):
    done, tool_list = tools
    model = ProviderShapedModel(responses=[
        _call("developer_commit_changes", "c1", message="one"),
        AIMessage(content="Committed.", id="ai-one"),
        _call("developer_commit_changes", "c2", message="two"),
        AIMessage(content="Committed again.", id="ai-two"),
    ])
    turns = RealGraphTurns(model, tool_list)

    first = _submit(platform, turns, "commit-one", "Commit")
    _approve(platform, first.approval_id, "commit-turn", scope="turn")
    assert done == ["one"]

    second = _submit(platform, turns, "commit-two", "Commit again")
    assert second.status == "waiting_approval"
    assert done == ["one"]
    assert len(_cards(platform)) == 2


def test_a_push_approved_for_the_turn_still_asks_next_time(platform, tools):
    done, tool_list = tools
    model = ProviderShapedModel(responses=[
        _call("developer_push_current_branch", "p1"),
        _call("developer_push_current_branch", "p2"),
        AIMessage(content="Pushed.", id="ai-done"),
    ])
    turns = RealGraphTurns(model, tool_list)

    first = _submit(platform, turns, "push-twice", "Push twice")
    _approve(platform, first.approval_id, "push-turn", scope="turn")

    assert done == ["push"]
    assert len(_cards(platform)) == 2
