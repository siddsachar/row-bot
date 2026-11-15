"""A tool call from Claude (your Claude plan) goes through Row-Bot's approval gate like any provider's (N1),
and a whole Agent turn runs in one Claude Code.

Security: Claude Code's own tools are off and Row-Bot's tool server runs nothing, so every tool call it
reports comes back to Row-Bot's agent loop, which asks first where the approval level says so; Claude Code
gets a result only once the loop has run the call. A fake ``claude`` replays recorded replies; the agent
graph, the approval gate and the transport are real.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from tests.helpers.fake_claude import install_fake_claude, text_reply, tool_reply
from tests.subsystem.providers.test_provider_settings_controls import store as store  # noqa: F401

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


def test_an_ask_level_tool_claude_calls_waits_for_the_persons_approval(store, tmp_path, monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    import row_bot.agent as agent
    from row_bot.providers.claude_subscription import forget_claude_plan_status
    from row_bot.providers.transports.claude_plan_cli import ChatClaudePlan, stop_waiting_runs

    ran: list[str] = []

    def delete_note(title: str) -> str:
        """Delete one of the person's notes."""
        ran.append(title)
        return "deleted"

    tool = StructuredTool.from_function(delete_note)
    agent._wrap_with_interrupt_gate(tool)
    token = agent._approval_mode_var.set("approve")  # Ask
    fake = install_fake_claude(tmp_path, monkeypatch, replies=[
        tool_reply("delete_note", {"title": "Groceries"}), text_reply("Deleted your groceries note.")])
    graph = agent.create_react_agent(model=ChatClaudePlan(model_name="claude-sonnet-5"), tools=[tool],
                                     checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "claude-plan-approval"}}
    try:
        graph.invoke({"messages": [HumanMessage(content="Delete my groceries note.")]}, config)
        state = graph.get_state(config)
        asked = [item.value for task in state.tasks for item in task.interrupts]
        assert [question["tool"] for question in asked] == ["delete_note"]
        assert "Groceries" in asked[0]["description"]
        assert ran == []  # it waits for the person; neither Claude Code nor Row-Bot ran it
        assert fake.model_runs()[0]["tool_calls"] == []  # Claude Code waits too, with no result

        final = graph.invoke(Command(resume=True), config)
    finally:
        agent._approval_mode_var.reset(token)
        stop_waiting_runs()
        forget_claude_plan_status()

    assert ran == ["Groceries"]  # Row-Bot ran it once, after the person's yes
    assert final["messages"][-1].content == "Deleted your groceries note."
    [run] = fake.model_runs()  # the same Claude Code was handed the result and answered
    [call] = run["tool_calls"]
    assert call["arguments"] == {"title": "Groceries"}
    assert call["answer"]["content"][0]["text"].endswith("deleted")


def test_a_whole_agent_turn_runs_in_one_claude_code(store, tmp_path, monkeypatch):
    """The prompt cache fix on Row-Bot's real Agent graph: every model call of a turn is the one before plus
    the reply and its tool results, so one Claude Code answers them all, given Row-Bot's results."""
    from langchain_core.tools import tool

    from row_bot.providers.transports.claude_plan_cli import ChatClaudePlan, stop_waiting_runs
    from tests.helpers.learning_turns import AgentTurns

    reads: list[str] = []

    @tool
    def read_note(name: str) -> str:
        """Read one of the person's notes."""
        reads.append(name)
        return f"The {name} note says: water the plants."

    fake = install_fake_claude(tmp_path, monkeypatch, replies=[
        tool_reply("read_note", {"name": "garden"}),
        tool_reply("read_note", {"name": "kitchen"}, call_id="toolu_synthetic02"),
        text_reply("Water the plants in both."),
    ])
    turns = AgentTurns(monkeypatch, ChatClaudePlan(model_name="claude-sonnet-5"), [read_note])
    try:
        events = turns.send("What do my garden and kitchen notes say?")
    finally:
        stop_waiting_runs()

    assert reads == ["garden", "kitchen"]
    assert "Water the plants in both." in "".join(str(payload) for kind, payload in events if kind == "token")
    [run] = fake.model_runs()
    assert [call["arguments"]["name"] for call in run["tool_calls"]] == ["garden", "kitchen"]
    assert "The kitchen note says" in run["tool_calls"][1]["answer"]["content"][0]["text"]
