"""An approved parent-Agent action goes on in the parent's own turn (R15).

An overnight multi-agent run asked for a command in the parent turn; the person
approved it and nothing went on: the card went away, the command never ran, and
a later turn found the three calls of that step without results. The parent's
approval notice was written into the parent conversation as a newer checkpoint,
on top of the one the turn was paused on, so the resume found no question to
answer and asked for Resume instead. These run the real graph, approval gate and
checkpoint store with a fake model: they are security tests (the person's answer
is the gate) and stay in the pull request lane.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langgraph.types import Command
import pytest

from row_bot import approval_review
from row_bot.approval_review import ReviewVerdict
from row_bot.tools import approval_gate
from tests.subsystem.client_platform.test_finished_turns_answer_every_call import ProviderShapedModel

pytestmark = pytest.mark.subsystem

COMMAND = "python score.py"


@pytest.fixture
def parent(tmp_path, monkeypatch, reload_for_data_dir):
    """A parent Agent paused in its own conversation on one command of a three-call step."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    threads, tasks, _profiles, _settings, _runs, orchestrator = reload_for_data_dir(
        data_dir,
        "row_bot.threads",
        "row_bot.tasks",
        "row_bot.agent_profiles",
        "row_bot.agent_settings",
        "row_bot.agent_runs",
        "row_bot.agent_orchestrator",
    )
    # Under Auto the reviewer leaves this command to the person, as it did overnight.
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "auto")
    monkeypatch.setattr(approval_review, "review", lambda payload, **_kw: ReviewVerdict("ask", "Check the script first."))
    ran: list[str] = []

    @tool
    def look(name: str) -> str:
        """Read one file."""
        ran.append(name)
        return f"{name} read"

    @tool
    def run_command(command: str) -> str:
        """Run one command."""
        answer = approval_gate.request_approval({
            "tool": "run_command", "label": "Run shell command", "args": {"command": command},
            "description": f"Run shell command: {command}"})
        if answer is not True:
            return "The person denied the command."
        ran.append(command)
        return "scores: 3 of 3"

    step = AIMessage(content="", id="ai-step", tool_calls=[
        {"name": "look", "args": {"name": "audit.csv"}, "id": "call-audit", "type": "tool_call"},
        {"name": "look", "args": {"name": "forecast.csv"}, "id": "call-forecast", "type": "tool_call"},
        {"name": "run_command", "args": {"command": COMMAND}, "id": "call-score", "type": "tool_call"},
    ])
    model = ProviderShapedModel(responses=[step, AIMessage(content="All three models scored.", id="ai-final")])
    graph = create_react_agent(model=model, tools=[look, run_command], checkpointer=threads.checkpointer,
                               version="v2")
    thread_id = threads.create_thread("NYC 311 benchmark", seed_default_skills=False)
    config = {"configurable": {"thread_id": thread_id}}

    from row_bot.agent import _graph_interrupt_result, _resume_value

    def settled():
        state = graph.get_state(config)
        return _graph_interrupt_result(state) or state.values["messages"][-1].content

    def waiting(_tools, _config):
        # As the agent reads the paused interrupt group: from the conversation's latest checkpoint.
        return list((_graph_interrupt_result(graph.get_state(config)) or {}).get("interrupts") or [])

    resumed: list[object] = []

    def resume(_tools, _config, approved, *, interrupt_ids=None, resume_values=None):
        value = _resume_value(approved, interrupt_ids, resume_values)
        resumed.append(value)
        graph.invoke(Command(resume=value), config)
        return settled()

    monkeypatch.setattr("row_bot.agent.get_invoke_agent_interrupts", waiting)
    monkeypatch.setattr("row_bot.agent.resume_invoke_agent", resume)
    orchestration = orchestrator.create_or_get_orchestration(
        parent_thread_id=thread_id,
        parent_generation_id="generation-1",
        root_objective="Score the three models.",
        model_ref="provider:model",
        approval_mode="auto",
        runtime_surface="normal_chat",
        orchestration_version=2,
    )
    graph.invoke({"messages": [HumanMessage(content="Score the three models.")]}, config)
    paused = settled()
    assert isinstance(paused, dict) and [item["args"] for item in paused["interrupts"]] == [{"command": COMMAND}]
    # The background parent pass ends at the card, as the parent runner records it.
    orchestrator.complete_parent_pass(
        orchestration["id"], paused,
        continuation_state={"config": {"configurable": {"thread_id": thread_id}}, "enabled_tool_names": ["shell"]},
        foreground=False,
    )
    [card] = tasks.get_pending_approvals(parent_thread_id=thread_id)
    return {
        "threads": threads, "tasks": tasks, "orchestrator": orchestrator, "orchestration": orchestration,
        "thread_id": thread_id, "card": card, "ran": ran, "resumed": resumed, "paused": paused,
    }


def _unanswered_calls(threads, thread_id: str) -> list[str]:
    messages = threads.get_latest_checkpoint_messages(thread_id)
    answered = {message.tool_call_id for message in messages if getattr(message, "type", "") == "tool"}
    return [call["id"] for message in messages for call in getattr(message, "tool_calls", None) or []
            if call["id"] not in answered]


def test_an_approved_parent_command_runs_once_and_the_parent_turn_goes_on(parent):
    tasks, orchestrator, threads = parent["tasks"], parent["orchestrator"], parent["threads"]

    assert tasks.respond_to_approval(parent["card"]["resume_token"], True, source="test")

    final = orchestrator.get_orchestration(parent["orchestration"]["id"])
    assert final["status"] == "completed", final["error_message"]
    # The approved command ran once; the two reads beside it kept their results and never ran again.
    assert sorted(parent["ran"]) == sorted(["audit.csv", "forecast.csv", COMMAND])
    # Only the interrupt the person approved was resumed.
    [approved_id] = [item["__interrupt_id"] for item in parent["paused"]["interrupts"]]
    assert parent["resumed"] == [{approved_id: True}]
    assert _unanswered_calls(threads, parent["thread_id"]) == []
    assert threads.get_latest_checkpoint_messages(parent["thread_id"])[-1].content == "All three models scored."
    assert tasks.get_pending_approvals(parent_thread_id=parent["thread_id"]) == []


def test_a_denied_parent_command_never_runs_and_the_parent_turn_goes_on(parent):
    tasks, orchestrator = parent["tasks"], parent["orchestrator"]

    assert tasks.respond_to_approval(parent["card"]["resume_token"], False, source="test")

    final = orchestrator.get_orchestration(parent["orchestration"]["id"])
    assert final["status"] == "completed", final["error_message"]
    assert COMMAND not in parent["ran"]
    assert _unanswered_calls(parent["threads"], parent["thread_id"]) == []


def test_a_helpers_approval_meanwhile_leaves_the_parents_question_answerable(parent):
    """A helper asks in the same chat while the parent waits; its card shows from the approvals (B348)."""
    from row_bot.application.client_monitor import read_pending_approvals
    from row_bot.channels.thread_notifications import notify_agent_run_approval

    tasks, thread_id = parent["tasks"], parent["thread_id"]
    _token, helper_card = tasks.create_approval_request(
        "helper-run", "", "agent_interrupt", "Run shell command: python audit.py", agent_run_id="helper-run",
        resume_kind="agent_run", source_label="Audit helper", source_thread_id="helper-thread",
        parent_thread_id=thread_id)
    notify_agent_run_approval(helper_card)
    shown = {item["id"]: item for item in read_pending_approvals()["items"]}
    assert shown[helper_card]["parent_conversation_id"] == thread_id

    assert tasks.respond_to_approval(parent["card"]["resume_token"], True, source="test")

    final = parent["orchestrator"].get_orchestration(parent["orchestration"]["id"])
    assert final["status"] == "completed", final["error_message"]
    assert COMMAND in parent["ran"]
    assert _unanswered_calls(parent["threads"], thread_id) == []

