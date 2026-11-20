"""S5, S6 (18 Nov): two argument slips no longer fail a call.

Opus on the Claude plan sent browser_wait_for ``seconds: "null"`` ("Input should be a valid number") and
task_update ``run_at: "null"`` for "leave it out", and tool_invoke its ``arguments`` as JSON text ("Input
should be a valid dictionary"). Each call failed and the model tried again by hand. Run through the production
ToolNode wrapper: an optional "null" is left out, an object given as JSON text is that object.
"""
from __future__ import annotations

import contextvars

import pytest
from langchain_core.messages import AIMessage

import row_bot.agent as agent

pytestmark = pytest.mark.subsystem


def _ran_with(args: dict) -> tuple[dict, str]:
    from langchain_core.tools import StructuredTool
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    seen: dict = {}

    def wait_for(text: str, seconds: float | None = None, gone: str = "", arguments: dict | None = None) -> str:
        """Waits."""
        seen.update(text=text, seconds=seconds, gone=gone, arguments=arguments)
        return "waited"

    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode([StructuredTool.from_function(func=wait_for, name="browser_wait_for")],
                                     wrap_tool_call=agent._wrap_platform_tool_call))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    call = {"id": "call-0", "name": "browser_wait_for", "args": args, "type": "tool_call"}

    def invoke() -> dict:
        agent._current_thread_id_var.set("argument-slips")
        return graph.compile().invoke({"messages": [AIMessage(content="", tool_calls=[call], id="ai-0")]})

    stored = contextvars.copy_context().run(invoke)["messages"][-1]
    return seen, str(stored.content)


def test_an_optional_argument_sent_as_the_text_null_is_left_out():
    seen, result = _ran_with({"text": "Row-Bot 5.3", "gone": "null", "seconds": "null"})
    assert result == "waited"
    assert (seen["seconds"], seen["gone"]) == (None, "")


def test_an_object_argument_sent_as_json_text_is_that_object():
    seen, result = _ran_with({"text": "Row-Bot", "arguments": '{"action": "create", "aspect": "16:9"}'})
    assert result == "waited"
    assert seen["arguments"] == {"action": "create", "aspect": "16:9"}


def test_a_required_null_and_text_that_is_not_an_object_still_fail_their_check():
    _seen, result = _ran_with({"text": "Row-Bot", "seconds": "soon"})
    assert "valid number" in result
    _seen, result = _ran_with({"text": "Row-Bot", "arguments": "[1, 2]"})
    assert "valid dictionary" in result
