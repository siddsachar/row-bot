"""Resuming a turn whose model call failed keeps one output per segment.

Live: the provider was overloaded mid-turn; Resume re-ran only the failed model
call (LangGraph had saved the model hook's step), so that call never passed the
hook that opens a new output segment. The next call then reused the same segment,
its answer's binding was refused (output_binding_conflict), and the reply showed
"interrupted" though it was written. The real graph, model hook and platform run
here; only the model and the tool are fakes.
"""
from __future__ import annotations

from types import SimpleNamespace

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langgraph.types import Command
import pytest

from tests.contracts.client_platform.test_headless_lifecycle import command, platform  # noqa: F401
from tests.helpers.client_platform_fakes import fixture_id
from tests.subsystem.client_platform.test_finished_turns_answer_every_call import _call

pytestmark = pytest.mark.subsystem


class OverloadedOnce(FakeMessagesListChatModel):
    """Answers in order; its second call fails as an overloaded provider does."""

    calls: int = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("Our servers are currently overloaded. Please try again later.")
        return super()._generate(messages, stop, run_manager, **kwargs)


@pytest.fixture
def turns(monkeypatch):
    from row_bot import agent, threads

    # The hook's segment bookkeeping is what runs; preparation just passes the history through.
    monkeypatch.setattr(agent, "_collect_agent_preparation_inputs",
                        lambda state, config=None: SimpleNamespace(execution_budget=None, messages=state["messages"]))
    monkeypatch.setattr(agent, "_prepare_with_compaction", lambda inputs: SimpleNamespace(messages=inputs.messages))

    @tool
    def lookup(query: str) -> str:
        """Look something up."""
        return f"Found {query}"

    model = OverloadedOnce(responses=[_call("lookup", "call-first", query="status"),
                                      _call("lookup", "call-again", query="apps"),
                                      AIMessage(content="Here is what I found.", id="ai-answer")])
    graph = create_react_agent(model=model, tools=[lookup], checkpointer=threads.checkpointer,
                               pre_model_hook=lambda state, config=None: {
                                   "llm_input_messages": agent._pre_model_trim(state, config)["llm_input_messages"]})

    def stream(text, enabled_tools, config, *, stop_event=None):
        submission = config["configurable"]["platform_submission_id"]
        yield from agent._stream_graph(graph, {"messages": [HumanMessage(content=text, id=submission)]},
                                       config, stop_event=stop_event)

    def resume(enabled_tools, config, approved, *, interrupt_ids=None, stop_event=None):
        value = {iid: approved for iid in interrupt_ids} if interrupt_ids else approved
        yield from agent._stream_graph(graph, Command(resume=value), config, stop_event=stop_event)

    return stream, resume


def _run(platform, kind: str, label: str, payload: dict):
    accepted = platform.execute(owner_id="fixture-owner", idempotency_key=fixture_id(label + ":key"),
                                target="conversation-a", command=command(kind, label, payload))
    handle = platform.registry.get(accepted["execution_id"])
    assert handle.producer_done.wait(10)
    return handle


def test_resuming_after_a_failed_model_call_binds_each_answer_once(platform, turns):
    from row_bot.runtime import admissions

    platform.stream_factory, platform.resume_factory = turns
    selection = {"provider_id": "fixture", "model_ref": "fixture/model"}
    failed = _run(platform, "conversation.submit", "overloaded", {
        "submission_id": fixture_id("overloaded"), "text": "Check my apps", "attachment_refs": [],
        "model_selection": selection})
    assert failed.status == "interrupted"

    resumed = _run(platform, "conversation.resume", "resume-it", {
        "submission_id": fixture_id("resume-it"), "model_selection": selection})

    assert resumed.status == "completed"
    assert platform.transcript("conversation-a")["rows"][-1]["blocks"][0]["text"] == "Here is what I found."
    with admissions.transaction() as conn:
        bound = conn.execute("SELECT native_message_id FROM generation_segments WHERE pass_id=? AND state='committed'",
                             (resumed.pass_id,)).fetchall()
    # The re-run call's tool request and the answer after it: one segment each.
    assert sorted(row[0] for row in bound) == ["ai-answer", "ai-call-again"]
