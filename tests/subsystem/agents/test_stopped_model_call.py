"""A model call that Stop ends is a stopped turn, not an API error.

Stop closes a local Ollama request that streams nothing (a model stuck in a tool call it never closes), so the
model call ends with an error. The turn must end as stopped: no error, no "API Error" notification, and nothing of
the stopped reply saved. A call that receives nothing for too long is a real error, reported in plain words.
These run the Agent graph with the local chat model against a fake Ollama on loopback that never answers.
"""

from __future__ import annotations

import contextvars
import threading

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import create_react_agent

from row_bot.cancellation import CancellationScope, use_cancellation_scope
from row_bot.providers.transports import ollama_local
from row_bot.providers.transports.ollama_local import RowBotChatOllama
from tests.helpers.fake_ollama import fake_streaming_ollama

pytestmark = pytest.mark.subsystem

MODEL = "fake-local:7b"
QUESTION = "What is 2 + 2?"
# Stop ends the call at once; the bound only keeps a broken test from hanging.
PROMPTLY = 2.0


def _calculate(expression: str) -> str:
    return "4"


CALCULATE = StructuredTool.from_function(_calculate, name="calculate", description="Calculate.")


@pytest.fixture
def notices(monkeypatch) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr("row_bot.notifications.notify", lambda title, message, *args, **kwargs: seen.append((title, message)))
    return seen


def _graph(server):
    model = RowBotChatOllama(model=MODEL, base_url=server.url, num_ctx=8_192)
    return create_react_agent(model=model, tools=[CALCULATE], checkpointer=InMemorySaver())


def _under_scope(run, scope: CancellationScope) -> tuple[threading.Thread, dict]:
    """Run *run* on another thread under *scope*, as a turn's producer does."""
    outcome: dict = {}

    def owned() -> None:
        with use_cancellation_scope(scope):
            try:
                outcome["result"] = run()
            except BaseException as error:  # noqa: BLE001 - the test reads it
                outcome["error"] = error

    worker = threading.Thread(target=contextvars.copy_context().run, args=(owned,), daemon=True)
    worker.start()
    return worker, outcome


def test_stop_during_a_silent_local_call_ends_the_turn_as_stopped(notices) -> None:
    import row_bot.agent as agent

    with fake_streaming_ollama(tokens=[], model=MODEL, replies=[{"hang": True}]) as server:
        graph = _graph(server)
        config = {"configurable": {"thread_id": "stopped-local-call"}}
        scope = CancellationScope()
        worker, outcome = _under_scope(lambda: list(agent._stream_graph(
            graph, {"messages": [HumanMessage(content=QUESTION)]}, config, stop_event=scope.stop_event)), scope)
        assert server.hanging.wait(PROMPTLY)

        scope.cancel("user")
        worker.join(PROMPTLY)

        assert not worker.is_alive()
        assert "error" not in outcome
        assert [kind for kind, *_ in outcome["result"] if kind == "error"] == []
        assert notices == []
        assert server.hung_up.wait(PROMPTLY)
        assert [message.type for message in graph.get_state(config).values["messages"]] == ["human"]


def test_a_workflow_step_stopped_during_a_silent_local_call_is_stopped(monkeypatch, notices) -> None:
    import row_bot.agent as agent

    with fake_streaming_ollama(tokens=[], model=MODEL, replies=[{"hang": True}]) as server:
        graph = _graph(server)
        monkeypatch.setattr(agent, "get_agent_graph", lambda *args, **kwargs: graph)
        monkeypatch.setattr(agent, "get_context_size", lambda *args, **kwargs: 8_192)
        monkeypatch.setattr(agent, "_new_agent_graph_input",
                            lambda text, config, agent=None: (config, {"messages": [HumanMessage(content=text)]}))
        config = {"configurable": {"thread_id": "stopped-workflow-step", "model_override": f"model:ollama:{MODEL}"}}
        scope = CancellationScope()
        worker, outcome = _under_scope(lambda: agent.invoke_agent(
            QUESTION, ["calculate"], config, stop_event=scope.stop_event), scope)
        assert server.hanging.wait(PROMPTLY)

        scope.cancel("user")
        worker.join(PROMPTLY)

        assert not worker.is_alive()
        assert isinstance(outcome.get("error"), agent.TaskStoppedError)
        assert notices == []


def test_a_local_call_that_receives_nothing_ends_the_turn_in_plain_words(monkeypatch, notices) -> None:
    import row_bot.agent as agent

    monkeypatch.setattr(ollama_local, "LOCAL_CHAT_IDLE_SECONDS", 0.2)
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=[{"hang": True}]) as server:
        events = list(agent._stream_graph(
            _graph(server), {"messages": [HumanMessage(content=QUESTION)]},
            {"configurable": {"thread_id": "silent-local-call"}}, stop_event=threading.Event()))

    [error] = [payload for kind, payload, *_ in events if kind == "error"]
    assert "local model sent nothing" in error
    assert "timed out" not in error and "Error" not in error.removeprefix("⚠️ API error:")
