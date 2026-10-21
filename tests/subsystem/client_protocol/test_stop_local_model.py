"""Stop ends a chat turn whose local model streams nothing, and the turn ends as stopped.

A small local model stuck in a tool call it never closes makes Ollama send nothing for as long as it generates.
The turn's model call runs the Agent graph with the local chat model against a fake Ollama on loopback that never
answers; Stop over the client API must end it at once, as a stopped turn rather than a failed one.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import create_react_agent

from row_bot.providers.transports.ollama_local import RowBotChatOllama
from tests.helpers.fake_ollama import fake_streaming_ollama
from tests.subsystem.client_protocol.test_protocol_application import (
    _client,
    _command,
    service,  # noqa: F401
)
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

MODEL = "fake-local:7b"
# Stop ends the call at once; the bound only keeps a broken test from hanging.
PROMPTLY = 2.0


def _calculate(expression: str) -> str:
    return "4"


def test_stop_ends_a_turn_whose_local_model_sends_nothing(service, monkeypatch):  # noqa: F811
    import row_bot.agent as agent

    notices: list = []
    monkeypatch.setattr("row_bot.notifications.notify", lambda *args, **kwargs: notices.append(args))
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=[{"hang": True}]) as server:
        model = RowBotChatOllama(model=MODEL, base_url=server.url, num_ctx=8_192)
        tool = StructuredTool.from_function(_calculate, name="calculate", description="Calculate.")
        graph = create_react_agent(model=model, tools=[tool], checkpointer=InMemorySaver())

        def stream(text, enabled, config, *, stop_event=None):
            return agent._stream_graph(graph, {"messages": [HumanMessage(content=text)]},
                                       {"configurable": {"thread_id": config["configurable"]["thread_id"]}},
                                       stop_event=stop_event)

        service.stream_factory = stream
        with _client(service) as client:
            _, headers = bootstrap(client)
            created = _command(client, headers, "conversation.create", {"title": "Local model"})
            conversation = created.json()["conversation_id"]
            submitted = _command(client, headers, "conversation.submit", {
                "submission_id": str(uuid4()), "text": "What is 2 + 2?", "attachment_refs": [],
                "model_selection": {"provider_id": "ollama", "model_ref": f"model:ollama:{MODEL}"},
            }, target=conversation, revision=service.get_conversation(conversation)["revision"])
            assert submitted.status_code == 202, submitted.text
            handle = service.registry.get(submitted.json()["execution_id"])
            assert server.hanging.wait(PROMPTLY)

            stopped = _command(client, headers, "conversation.stop", {"generation_id": handle.generation_id},
                               target=conversation, revision=service.get_conversation(conversation)["revision"])
            assert stopped.status_code == 200, stopped.text

            assert handle.producer_done.wait(PROMPTLY)
            assert handle.status == "stopped"
            assert server.hung_up.wait(PROMPTLY)
            events = service.events_since(conversation)["events"]
            assert not [event for event in events if event.get("type") == "generation.error"]
            assert notices == []
