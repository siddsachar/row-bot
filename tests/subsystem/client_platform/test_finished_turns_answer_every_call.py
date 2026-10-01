"""A finished turn leaves no tool call without its result (B234).

A denied command kept spinning "Running a command": the denial closed the
stream after the tool's step ran but before LangGraph saved it, so the call
had no result in the conversation. Providers need every call paired with its
result, and the transcript showed the unanswered call as still running. These
tests run the real graph streaming and approval gate with a fake model and
tool; only the provider and the effect are fakes.
"""
from __future__ import annotations

import json
import threading

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langgraph.types import Command
import pytest

from tests.contracts.client_platform.test_headless_lifecycle import command, platform  # noqa: F401
from tests.helpers.client_platform_fakes import fixture_id

pytestmark = pytest.mark.subsystem


def _unanswered(messages) -> list[str]:
    """Tool calls not followed straight away by their results, as a provider checks."""
    missing: list[str] = []
    for index, message in enumerate(messages):
        calls = [call["id"] for call in getattr(message, "tool_calls", None) or []]
        answered = set()
        for later in messages[index + 1:]:
            if getattr(later, "type", "") != "tool":
                break
            answered.add(later.tool_call_id)
        missing.extend(call for call in calls if call not in answered)
    return missing


class ProviderShapedModel(FakeMessagesListChatModel):
    """Answers in order, and refuses a history with an unanswered call."""

    rejected: list = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if _unanswered(messages):
            self.rejected.append(_unanswered(messages))
            raise ValueError("invalid history: a tool call has no result")
        return super()._generate(messages, stop, run_manager, **kwargs)


def _call(name: str, call_id: str, **args) -> AIMessage:
    return AIMessage(content="", id=f"ai-{call_id}",
                     tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


class RealGraphTurns:
    """The platform's stream and resume factories, over a real LangGraph graph."""

    def __init__(self, model, tools) -> None:
        from row_bot import threads
        self.graph = create_react_agent(model=model, tools=tools, checkpointer=threads.checkpointer)

    def stream(self, text, enabled_tools, config, *, stop_event=None):
        from row_bot.agent import _stream_graph
        submission = config["configurable"]["platform_submission_id"]
        yield from _stream_graph(self.graph, {"messages": [HumanMessage(content=text, id=submission)]},
                                 config, stop_event=stop_event)

    def resume(self, enabled_tools, config, approved, *, interrupt_ids=None, stop_event=None):
        from row_bot.agent import _stream_graph
        yield from _stream_graph(self.graph, Command(resume={iid: approved for iid in interrupt_ids or ()}),
                                 config, stop_event=stop_event)


@pytest.fixture
def ask_mode(monkeypatch):
    from row_bot.tools import approval_gate
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "approve")


def _submit(platform, turns: RealGraphTurns, label: str, text: str) -> object:
    platform.stream_factory = turns.stream
    platform.resume_factory = turns.resume
    accepted = platform.execute(
        owner_id="fixture-owner", idempotency_key=fixture_id(label + ":key"), target="conversation-a",
        command=command("conversation.submit", label, {
            "submission_id": fixture_id(label), "text": text, "attachment_refs": [],
            "model_selection": {"provider_id": "fixture", "model_ref": "fixture/model"}}))
    handle = platform.registry.get(accepted["execution_id"])
    assert handle.producer_done.wait(10)
    return handle


def _trace_items(platform) -> list[dict]:
    return [item for row in platform.transcript("conversation-a")["rows"]
            for group in row.get("traces") or () for item in group["items"]]


def test_a_denied_call_keeps_its_result_and_the_next_turn_pairs_every_call(platform, ask_mode):
    from row_bot import threads
    from row_bot.tools.approval_gate import gate_action

    removed: list[str] = []

    @tool
    def remove_file(path: str) -> str:
        """Remove one file."""
        refusal = gate_action({"tool": "remove_file", "label": "Remove file",
                               "description": f"Remove {path}", "args": {"path": path}})
        if refusal:
            return refusal
        removed.append(path)
        return f"Removed {path}"

    model = ProviderShapedModel(responses=[_call("remove_file", "call-remove", path="notes.txt"),
                                           AIMessage(content="You're welcome.", id="ai-thanks")])
    turns = RealGraphTurns(model, [remove_file])

    first = _submit(platform, turns, "delete-it", "Delete notes.txt")
    assert first.status == "waiting_approval"
    receipt = platform.execute(owner_id="fixture-owner", idempotency_key="deny-it", target=first.approval_id,
                               command=command("approval.resolve", "deny-it", {"decision": "deny"}))
    denied = platform.registry.get(receipt["execution_id"])
    assert denied.producer_done.wait(10)

    assert removed == []  # the gate held: nothing ran
    messages = threads.get_latest_checkpoint_messages("conversation-a")
    assert _unanswered(messages) == []
    result = next(message for message in messages if getattr(message, "type", "") == "tool")
    assert "denied by you" in str(result.content)
    assert messages[-1].content == "The requested action was denied. No action was taken."
    assert [item["status"] for item in _trace_items(platform)] == ["cancelled"]

    second = _submit(platform, turns, "thanks", "Thanks anyway")
    assert second.status == "completed"
    assert model.rejected == []
    assert platform.transcript("conversation-a")["rows"][-1]["blocks"][0]["text"] == "You're welcome."


def test_stop_during_a_tool_keeps_the_result_it_produced(platform):
    from row_bot import threads

    entered, release = threading.Event(), threading.Event()

    @tool
    def slow_lookup(query: str) -> str:
        """Look something up slowly."""
        entered.set()
        assert release.wait(10)
        return f"Found {query}"

    model = ProviderShapedModel(responses=[_call("slow_lookup", "call-slow", query="weather")])
    turns = RealGraphTurns(model, [slow_lookup])
    platform.stream_factory = turns.stream
    accepted = platform.execute(
        owner_id="fixture-owner", idempotency_key=fixture_id("slow:key"), target="conversation-a",
        command=command("conversation.submit", "slow", {
            "submission_id": fixture_id("slow"), "text": "Look up the weather", "attachment_refs": [],
            "model_selection": {"provider_id": "fixture", "model_ref": "fixture/model"}}))
    handle = platform.registry.get(accepted["execution_id"])
    assert entered.wait(10)
    platform.execute(owner_id="fixture-owner", idempotency_key="stop-slow", target="conversation-a",
                     command=command("conversation.stop", "stop-slow"))
    release.set()
    assert handle.producer_done.wait(10)

    assert handle.status == "stopped"
    messages = threads.get_latest_checkpoint_messages("conversation-a")
    assert _unanswered(messages) == []
    assert [item["status"] for item in _trace_items(platform)] == ["succeeded"]
    assert "Found weather" in json.dumps(platform.transcript("conversation-a")["rows"])


def test_stop_while_an_approval_waits_records_that_the_call_never_ran(platform, ask_mode):
    from row_bot import threads
    from row_bot.tools.approval_gate import gate_action

    @tool
    def send_email(to: str) -> str:
        """Send an email."""
        return gate_action({"tool": "send_email", "label": "Send email",
                            "description": f"Send to {to}", "args": {"to": to}}) or "Sent"

    model = ProviderShapedModel(responses=[_call("send_email", "call-send", to="someone@example.invalid")])
    turns = RealGraphTurns(model, [send_email])
    first = _submit(platform, turns, "send-it", "Send the email")
    assert first.status == "waiting_approval"

    platform.execute(owner_id="fixture-owner", idempotency_key="stop-waiting", target="conversation-a",
                     command=command("conversation.stop", "stop-waiting"))

    messages = threads.get_latest_checkpoint_messages("conversation-a")
    assert _unanswered(messages) == []
    assert [item["status"] for item in _trace_items(platform)] == ["cancelled"]
