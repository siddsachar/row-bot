"""A turn stopped at its browser limit can be resumed, or answered, on the Claude plan.

Live (20 Nov): a long browser task on the Claude plan hit the browser limit and showed "The response was
interrupted"; every Resume failed with "A Claude plan call needs the conversation to end with a message to
Claude." The stop broke out of the stream before LangGraph saved the model's step, so its patched result was
saved without the call it answers (dropped as an orphan) and a stop marker was saved after it: the
conversation ended on an assistant message. The real graph, its hooks and the platform run here; the model is
a fake that builds its request as the Claude plan transport does.
"""
from __future__ import annotations

from types import SimpleNamespace

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langgraph.types import Command
import pytest

from tests.contracts.client_platform.test_headless_lifecycle import command, platform  # noqa: F401
from tests.helpers.client_platform_fakes import fixture_id
from tests.subsystem.client_platform.test_finished_turns_answer_every_call import _call
from tests.subsystem.client_platform.test_interrupted_after_restart import _restart

pytestmark = pytest.mark.subsystem


class ClaudePlanShaped(FakeMessagesListChatModel):
    """Answers in order, after building its request as the Claude plan transport does (which refuses a
    conversation that doesn't end with a message to Claude)."""

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        from row_bot.providers.transports.claude_plan_cli import conversation_frames

        conversation_frames(messages)
        return super()._generate(messages, stop, run_manager, **kwargs)


@pytest.fixture
def turns(monkeypatch):
    from row_bot import agent, threads
    from row_bot.agent_budget import new_execution_budget, post_model_budget_hook

    monkeypatch.setattr(agent, "BROWSER_TOOL_LIMIT", 3)
    monkeypatch.setattr(agent, "_collect_agent_preparation_inputs",
                        lambda state, config=None: SimpleNamespace(execution_budget=None, messages=state["messages"]))
    monkeypatch.setattr(agent, "_prepare_with_compaction", lambda inputs: SimpleNamespace(messages=inputs.messages))

    @tool
    def browser_click(ref: str) -> str:
        """Click an element on the page."""
        return f"Clicked {ref}."

    model = ClaudePlanShaped(responses=[_call("browser_click", f"call-{n}", ref=f"e{n}") for n in (1, 2, 3)]
                             + [AIMessage(content="Posted the thread.", id="ai-answer")])
    graph = create_react_agent(model=model, tools=[browser_click], checkpointer=threads.checkpointer,
                               state_schema=agent.RowBotAgentState, version="v2",
                               pre_model_hook=lambda state, config=None: {
                                   "llm_input_messages": agent._pre_model_trim(state, config)["llm_input_messages"]},
                               post_model_hook=post_model_budget_hook)

    def stream(text, enabled_tools, config, *, stop_event=None):
        submission = config["configurable"]["platform_submission_id"]
        yield from agent._stream_graph(graph, {"messages": [HumanMessage(content=text, id=submission)],
                                               "execution_budget": new_execution_budget("conversation-a")},
                                       config, stop_event=stop_event)

    def resume(enabled_tools, config, approved, *, interrupt_ids=None, stop_event=None):
        yield from agent._stream_graph(graph, Command(resume=approved), config, stop_event=stop_event)

    return stream, resume


def _run(platform, kind: str, label: str, payload: dict):
    accepted = platform.execute(owner_id="fixture-owner", idempotency_key=fixture_id(label + ":key"),
                                target="conversation-a", command=command(kind, label, payload))
    handle = platform.registry.get(accepted["execution_id"])
    assert handle.producer_done.wait(10)
    return handle


@pytest.mark.parametrize("carry_on", ["conversation.resume", "conversation.submit"])
def test_a_turn_stopped_at_the_browser_limit_carries_on_on_the_claude_plan(platform, turns, carry_on):
    from row_bot import threads

    platform.stream_factory, platform.resume_factory = turns
    selection = {"provider_id": "claude_subscription", "model_ref": "claude_subscription/claude-opus-5-5"}
    stopped = _run(platform, "conversation.submit", "browse", {
        "submission_id": fixture_id("browse"), "text": "Post my thread on X", "attachment_refs": [],
        "model_selection": selection})
    assert stopped.status == "interrupted"

    # The saved conversation ends on the stopped step's result, right after the call it answers.
    saved = threads.get_latest_checkpoint_messages("conversation-a")
    assert [message.type for message in saved[-2:]] == ["ai", "tool"]
    assert saved[-1].tool_call_id == saved[-2].tool_calls[0]["id"] == "call-3"
    assert isinstance(saved[-1], ToolMessage)

    carried = _run(platform, carry_on, "carry-on", {
        "submission_id": fixture_id("carry-on"), "model_selection": selection,
        **({"text": "carry on", "attachment_refs": []} if carry_on == "conversation.submit" else {})})

    assert carried.status == "completed"
    assert platform.transcript("conversation-a")["rows"][-1]["blocks"][0]["text"] == "Posted the thread."

def test_a_browser_stop_still_shows_why_after_a_reload_and_a_restart(platform, turns):
    """Learning's check on the change above: the stop marker used to end the saved turn, and the notice's reason is
    not in the conversation. A reload, even after Row-Bot restarts, shows the stopped step as stopped, the reason
    and Resume."""
    platform.stream_factory, platform.resume_factory = turns
    stopped = _run(platform, "conversation.submit", "browse", {
        "submission_id": fixture_id("browse"), "text": "Post my thread on X", "attachment_refs": [],
        "model_selection": {"provider_id": "claude_subscription",
                            "model_ref": "claude_subscription/claude-opus-5-5"}})
    reason = platform.snapshot("conversation-a")["generation"]["failure_reason"]
    assert stopped.status == "interrupted" and reason

    _restart(platform)

    reloaded = platform.snapshot("conversation-a")
    assert (reloaded["generation"]["status"], reloaded["generation"]["failure_reason"]) == ("interrupted", reason)
    step = next(row for row in reloaded["rows"] if row["id"] == "assistant:checkpoint:ai-call-3")
    assert [item["status"] for trace in step["traces"] for item in trace["items"]] == ["cancelled"]
    assert reloaded["rows"][-1]["role"] == "tool" and reloaded["rows"][-1]["tool_call_id"] == "call-3"
