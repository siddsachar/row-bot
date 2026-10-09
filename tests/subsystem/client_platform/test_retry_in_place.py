"""Retry runs the last message again in place.

Found live: Retry and "Send again" added a second copy of the person's message under the first. A retry
replaces the last turn (the message and whatever followed it) in the same write that admits it again, so the
transcript shows it once. A retry the server refuses changes nothing, and a turn whose tool steps already ran
stays on record. The real graph and platform run here; only the model is a fake.
"""
from __future__ import annotations

from types import SimpleNamespace

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt import create_react_agent
import pytest

from tests.contracts.client_platform.test_headless_lifecycle import command, platform  # noqa: F401
from tests.helpers.client_platform_fakes import fixture_id

pytestmark = pytest.mark.subsystem
SELECTION = {"provider_id": "fixture", "model_ref": "fixture/model"}


class FailsFirst(FakeMessagesListChatModel):
    """Its first call fails as an overloaded provider does; then it answers."""

    calls: int = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("Our servers are currently overloaded. Please try again later.")
        return super()._generate(messages, stop, run_manager, **kwargs)


@pytest.fixture
def turns(monkeypatch):
    from row_bot import agent, threads

    monkeypatch.setattr(agent, "_collect_agent_preparation_inputs",
                        lambda state, config=None: SimpleNamespace(execution_budget=None, messages=state["messages"]))
    monkeypatch.setattr(agent, "_prepare_with_compaction", lambda inputs: SimpleNamespace(messages=inputs.messages))
    model = FailsFirst(responses=[AIMessage(content="Your apps are fine.", id="ai-answer")])
    graph = create_react_agent(model=model, tools=[], checkpointer=threads.checkpointer,
                               pre_model_hook=lambda state, config=None: {
                                   "llm_input_messages": agent._pre_model_trim(state, config)["llm_input_messages"]})

    def stream(text, enabled_tools, config, *, stop_event=None):
        submission = config["configurable"]["platform_submission_id"]
        yield from agent._stream_graph(graph, {"messages": [HumanMessage(content=text, id=submission)]},
                                       config, stop_event=stop_event)

    def resume(enabled_tools, config, approved, *, interrupt_ids=None, stop_event=None):
        raise AssertionError("a retry is a new run, not a resume")

    return stream, resume


def _submit(platform, label: str, **extra):
    accepted = platform.execute(owner_id="fixture-owner", idempotency_key=fixture_id(label + ":key"),
                                target="conversation-a", command=command("conversation.submit", label, {
                                    "submission_id": fixture_id(label), "text": "Check my apps", "attachment_refs": [],
                                    "model_selection": SELECTION, **extra}))
    handle = platform.registry.get(accepted["execution_id"])
    assert handle.producer_done.wait(10)
    return handle


def _people(platform) -> list[str]:
    rows = platform.transcript("conversation-a")["rows"]
    return [block["text"] for row in rows if row["role"] == "user" for block in row["blocks"] if block.get("text")]


def test_retry_runs_the_last_message_again_in_place(platform, turns):
    platform.stream_factory, platform.resume_factory = turns
    failed = _submit(platform, "overloaded")
    assert failed.status != "completed"
    assert _people(platform).count("Check my apps") == 1

    retried = _submit(platform, "retry-it", retry=True)

    assert retried.status == "completed"
    assert _people(platform).count("Check my apps") == 1  # Not a second copy under the first.
    assert platform.transcript("conversation-a")["rows"][-1]["blocks"][0]["text"] == "Your apps are fine."


def _answered(text: str = "Check my apps", *, tool: bool = False) -> None:
    from row_bot import agent_runs, threads

    agent_runs.ensure_agent_run_schema(force=True)  # A chat with an answer checks its goal before the next turn.

    steps = [AIMessage(id="call", content="", tool_calls=[{"id": "t1", "name": "create_issue", "args": {}}]),
             ToolMessage(id="result", tool_call_id="t1", content="Issue 12 created.")] if tool else []
    assert threads.append_checkpoint_messages("conversation-a", [
        HumanMessage(id="first", content=text), *steps, AIMessage(id="answer", content="Something went wrong.")])


def test_a_retry_the_server_refuses_keeps_the_turn(platform, turns):
    from row_bot.application.client_platform import ClientPlatformError

    platform.stream_factory, platform.resume_factory = turns
    _answered()
    with pytest.raises(ClientPlatformError):  # Another conversation's attachment: refused before admission.
        _submit(platform, "refused", retry=True, attachment_refs=["conversation-b:file"])
    assert _people(platform) == ["Check my apps"]
    assert platform.transcript("conversation-a")["rows"][-1]["blocks"][0]["text"] == "Something went wrong."


def test_a_turn_whose_tools_ran_stays_on_record_and_the_retry_is_a_new_message(platform, turns):
    platform.stream_factory, platform.resume_factory = turns
    _answered(tool=True)

    _submit(platform, "after-tools", retry=True)

    assert _people(platform) == ["Check my apps", "Check my apps"]
    texts = [block.get("text", "") for row in platform.transcript("conversation-a")["rows"] for block in row["blocks"]]
    assert "Something went wrong." in texts  # The step and its outcome are still there to check.


def test_a_retry_of_another_message_is_sent_as_a_new_message(platform, turns):
    platform.stream_factory, platform.resume_factory = turns
    _answered("First question")

    _submit(platform, "other", retry=True)

    assert _people(platform) == ["First question", "Check my apps"]
