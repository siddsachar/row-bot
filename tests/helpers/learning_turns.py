"""Real Agent turns with a fake model and fake tools, for Learning's capture tests.

The graph is the one ``agent.get_agent_graph`` builds (the Agent's own model call, the pre-model
hook, the budget hook, the platform tool wrapper, the checkpointer); only the provider and the
tools are fakes, and turns go through ``agent.stream_agent`` and its resume.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage


class ScriptedModel(FakeMessagesListChatModel):
    """Answers in order and keeps every request it was sent (``seen``)."""

    seen: list = []
    overflows: int = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        if len(self.seen) <= self.overflows:
            raise ValueError("Error code: 400 - prompt is too long: 250000 tokens > 200000 maximum")
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def call(tool_name: str, call_id: str, /, **args: Any) -> dict[str, Any]:
    return {"name": tool_name, "args": args, "id": call_id, "type": "tool_call"}


def asks(*calls: dict[str, Any], content: str = "") -> AIMessage:
    """A model reply that calls tools (several at once for a parallel batch)."""

    return AIMessage(content=content, id=f"ai-{uuid.uuid4().hex[:8]}", tool_calls=list(calls))


def says(text: str) -> AIMessage:
    return AIMessage(content=text, id=f"ai-{uuid.uuid4().hex[:8]}")


class AgentTurns:
    """One conversation whose turns run through ``agent.stream_agent`` on a real graph."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, model: Any, tools: list, *,
                 thread_id: str = "", approval_mode: str = "approve") -> None:
        import row_bot.agent as agent
        from langchain_core.utils.function_calling import convert_to_openai_tool
        from langgraph.prebuilt import ToolNode

        from row_bot import threads
        from row_bot.agent_budget import RowBotAgentState, post_model_budget_hook
        from tests.helpers.agent_requests import MODEL_REF, AgentConversation

        self.agent = agent
        self.model_ref = MODEL_REF
        self.conversation = AgentConversation(agent, monkeypatch, thread_id=thread_id)
        self.thread_id = self.conversation.thread_id
        self.model = model
        self.approval_mode = approval_mode
        tool_node = ToolNode(tools, wrap_tool_call=agent._wrap_platform_tool_call) if tools else None
        bound = list(tool_node.tools_by_name.values()) if tool_node is not None else []
        self.graph = agent.create_react_agent(
            model=agent._AgentModelCall(model, bound), tools=tool_node if tool_node is not None else [],
            prompt=None, pre_model_hook=agent._pre_model_trim, post_model_hook=post_model_budget_hook,
            checkpointer=threads.checkpointer, name="row_bot_agent", state_schema=RowBotAgentState, version="v2",
        )
        self.canonical_tools = tuple(convert_to_openai_tool(tool) for tool in bound)

        def graph_for_this_turn(*_args: Any, **_kwargs: Any) -> Any:
            # As get_agent_graph activates its cached graph's tool schemas for the turn.
            agent._current_canonical_tools_var.set(self.canonical_tools)
            return self.graph

        monkeypatch.setattr(agent, "get_agent_graph", graph_for_this_turn)
        self.turn = 0
        self.config: dict[str, Any] = {}

    def send(self, text: str, *, stop_event: Any = None, **configurable: Any) -> list[tuple[str, Any]]:
        self.turn += 1
        self.config = {"configurable": {
            "thread_id": self.thread_id,
            "generation_id": f"{self.thread_id}-turn-{self.turn}",
            "runtime_surface": "normal_chat",
            "runtime_mode": "agent",
            "approval_mode": self.approval_mode,
            "model_override": self.model_ref,
            **configurable,
        }}
        return list(self.agent.stream_agent(text, [], self.config, stop_event=stop_event))

    def answer(self, approved: bool, events: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
        """Answer the approval card the last turn paused at."""

        [interrupts] = [payload for kind, payload in events if kind == "interrupt"]
        ids = [item["__interrupt_id"] for item in interrupts]
        return list(self.agent.resume_stream_agent([], self.config, approved, interrupt_ids=ids))


def model_call_in_turn(conversation: Any, model: Any) -> AIMessage:
    """One Agent model call (the pre-model hook, then ``_AgentModelCall``) inside a Learning turn,
    opened and closed as ``stream_agent`` opens and closes one, for an ``AgentConversation``."""

    import contextvars

    from langgraph.graph.message import add_messages

    agent = conversation.agent

    def run() -> AIMessage:
        generation = f"{conversation.thread_id}-turn-{conversation.turns}"
        agent._set_active_runtime_context(
            thread_id=conversation.thread_id, runtime_surface="normal_chat", requested_runtime_mode="agent",
            selected_runtime_mode="agent", generation_id=generation, model_override=conversation.policy.model_ref,
            enabled_tool_names=("filesystem",),
        )
        configurable = {"thread_id": conversation.thread_id, "generation_id": generation,
                        "runtime_surface": "normal_chat"}
        agent._learning_begin_turn(configurable)
        status = "failed"
        try:
            update = agent._pre_model_trim(conversation.state)
            conversation.state["messages"] = add_messages(conversation.state["messages"],
                                                          update.get("messages") or [])
            request = update["llm_input_messages"]
            node_state = {**conversation.state, "llm_input_messages": request, "messages": request}
            answer = agent._AgentModelCall(model, [])(node_state).invoke(request, {"configurable": configurable})
            status = "completed"
            return answer
        finally:
            agent._learning_end_turn(status)

    return contextvars.copy_context().run(run)


__all__ = ["AgentTurns", "ScriptedModel", "asks", "call", "model_call_in_turn", "says"]
