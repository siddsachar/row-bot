"""A reply cut off by the output token limit says so, on every provider shape (F17)."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

pytestmark = pytest.mark.subsystem

CUT_SHORT = "cut short by the model's output token limit"


def _stream(response_metadata: dict, *, streamed: str = "The first half") -> list:
    import row_bot.agent as agent

    class FakeState:
        next: list = []
        tasks: list = []
        values = {"messages": [HumanMessage(content="write it all"), AIMessage(content="The first half")]}

    class FakeGraph:
        def stream(self, input_data, config=None, stream_mode=None):
            meta = {"langgraph_node": "agent", "run_id": "provider-call-1"}
            if streamed:
                yield ("messages", (AIMessageChunk(content=streamed), meta))
            # The final chunk says why the provider stopped.
            yield ("messages", (AIMessageChunk(content="", response_metadata=response_metadata), meta))

        def get_state(self, config):
            return FakeState()

    return list(agent._stream_graph(
        FakeGraph(),
        {"messages": [("human", "write it all")]},
        {"configurable": {"thread_id": "thread-limit", "generation_id": "thread-limit:gen-1"}},
    ))


@pytest.mark.parametrize(
    "response_metadata",
    [
        {"stop_reason": "max_tokens", "stop_sequence": None},  # Anthropic, streamed message_delta
        {"finish_reason": "length"},  # OpenAI-style
        {"done_reason": "length"},  # Ollama
        {"finish_reason": "MAX_TOKENS", "model_name": "gemini-fixture", "safety_ratings": []},  # Gemini
        # Responses APIs (OpenAI, Codex, xAI OAuth): no stop reason, an incomplete status instead.
        {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "model_name": "gpt-fixture"},
    ],
)
def test_a_reply_that_hit_the_output_limit_tells_the_person(response_metadata):
    events = _stream(response_metadata)

    [done] = [payload for event_type, payload in events if event_type == "done"]
    assert done.startswith("The first half")
    assert CUT_SHORT in done


@pytest.mark.parametrize(
    "response_metadata",
    [
        {"stop_reason": "end_turn", "stop_sequence": None},
        {"finish_reason": "stop"},
        {"finish_reason": "STOP", "model_name": "gemini-fixture"},
        {"status": "completed", "model_name": "gpt-fixture"},
        {"status": "incomplete", "incomplete_details": {"reason": "content_filter"}},
    ],
)
def test_a_reply_that_finished_normally_has_no_notice(response_metadata):
    events = _stream(response_metadata)

    assert [payload for event_type, payload in events if event_type == "done"] == ["The first half"]


def test_a_streamed_reply_is_saved_with_its_stop_reason():
    """The transcript row and a workflow's result read the saved reply, so it must keep why it stopped."""
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.outputs import ChatGenerationChunk
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.prebuilt import create_react_agent

    from row_bot.providers.stop_reason import stop_reason

    class StreamingModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "output-limit-fixture"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise AssertionError("the graph streams")

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            yield ChatGenerationChunk(message=AIMessageChunk(content="The first half"))
            yield ChatGenerationChunk(message=AIMessageChunk(
                content="", response_metadata={"stop_reason": "max_tokens", "stop_sequence": None}))

    graph = create_react_agent(model=StreamingModel(), tools=[], checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-saved"}}
    list(graph.stream({"messages": [HumanMessage(content="write it all")]}, config, stream_mode=["messages", "updates"]))

    saved = graph.get_state(config).values["messages"][-1]
    assert saved.content == "The first half"
    assert stop_reason(saved.response_metadata) == "max_tokens"


def test_the_notice_streams_too_so_a_reply_built_from_tokens_ends_with_it():
    # Channels assemble their reply from the streamed tokens, not from "done".
    events = _stream({"stop_reason": "max_tokens"})

    tokens = [payload for event_type, payload in events if event_type == "token"]
    [done] = [payload for event_type, payload in events if event_type == "done"]
    assert tokens[0] == "The first half"
    assert CUT_SHORT in tokens[-1]
    assert "".join(tokens) == done


def test_an_answer_read_back_from_the_checkpoint_says_so_once():
    # Nothing streamed: the answer comes whole from the saved message in "done",
    # which a channel then uses as its reply.
    events = _stream({"finish_reason": "length"}, streamed="")

    assert not any(CUT_SHORT in str(payload) for event_type, payload in events if event_type == "token")
    [done] = [payload for event_type, payload in events if event_type == "done"]
    assert done.startswith("The first half")
    assert done.count(CUT_SHORT) == 1


def _chat_only(monkeypatch, tmp_path, response_metadata: dict) -> tuple[list, list]:
    from row_bot import models
    import row_bot.agent as agent
    import row_bot.providers.readiness as readiness
    import row_bot.threads as threads
    from tests.subsystem.providers.test_chat_only_runtime import _chat_ready_result

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / ".row-bot"))
    monkeypatch.setattr(models, "_current_model", "model:ollama:fixture-model")

    class FakeLLM:
        def stream(self, messages, config=None):  # a chat model's own signature
            yield AIMessageChunk(content="The first half")
            yield AIMessageChunk(content="", response_metadata=response_metadata)

    persisted: list = []
    monkeypatch.setattr(readiness, "evaluate_chat_readiness", lambda model_label: _chat_ready_result())
    monkeypatch.setattr(agent, "_chat_only_llm", lambda model_label: FakeLLM())
    monkeypatch.setattr(threads, "get_latest_checkpoint_messages", lambda thread_id: [])
    monkeypatch.setattr(threads, "append_checkpoint_messages",
                        lambda thread_id, messages: persisted.extend(messages) or True)
    events = list(agent.stream_chat_only(
        "write it all",
        {"configurable": {"thread_id": "thread-chat", "model_override": "model:custom_openai_lab:local-chat"}},
    ))
    return events, persisted


def test_chat_only_says_when_the_output_limit_cut_the_reply(tmp_path, monkeypatch):
    events, persisted = _chat_only(monkeypatch, tmp_path, {"finish_reason": "length"})

    tokens = [payload for event_type, payload in events if event_type == "token"]
    [done] = [payload for event_type, payload in events if event_type == "done"]
    assert CUT_SHORT in tokens[-1]
    assert done == "".join(tokens) and done.startswith("The first half")
    # The saved reply is the model's own text; the client marks the row from its stop reason.
    assert persisted[-1].content == "The first half"
    assert persisted[-1].response_metadata["finish_reason"] == "length"


def test_chat_only_reply_that_finished_normally_has_no_notice(tmp_path, monkeypatch):
    events, _persisted = _chat_only(monkeypatch, tmp_path, {"finish_reason": "stop"})

    assert [payload for event_type, payload in events if event_type == "done"] == ["The first half"]


@pytest.mark.parametrize("path", ["invoke", "invoke_stoppable", "resume_after_approval"])
@pytest.mark.parametrize(
    ("response_metadata", "cut_short"),
    [
        ({"stop_reason": "max_tokens"}, True),
        ({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}, True),
        ({"stop_reason": "end_turn"}, False),
    ],
)
def test_a_workflow_or_delegated_agents_reply_says_when_it_was_cut_short(monkeypatch, path, response_metadata,
                                                                         cut_short):
    import threading

    from row_bot import models
    import row_bot.agent as agent

    monkeypatch.setattr(models, "_current_model", "model:ollama:fixture-model")
    reply = AIMessage(content="The first half", response_metadata=response_metadata)

    class FakeState:
        next: tuple = ()
        tasks: tuple = ()
        values = {"messages": [HumanMessage(content="write it all"), reply]}

    class FakeGraph:
        def stream(self, input_data, config=None, stream_mode=None, **kwargs):
            return iter(())

        def invoke(self, input_data, config=None):
            return FakeState.values

        def get_state(self, config):
            return FakeState()

    monkeypatch.setattr(agent, "get_agent_graph", lambda *args, **kwargs: FakeGraph())
    monkeypatch.setattr(agent, "_new_agent_graph_input", lambda text, config, **kwargs: (config, {"messages": []}))
    monkeypatch.setattr(agent, "_resume_agent_graph_config", lambda graph, config: config)
    config = {"configurable": {"thread_id": "thread-workflow", "model_override": "model:ollama:fixture-model"}}

    if path == "resume_after_approval":
        result = agent._resume_invoke_agent_graph([], config, True)
    else:
        result = agent._invoke_agent_graph(
            "write it all", [], config, stop_event=threading.Event() if path == "invoke_stoppable" else None,
        )

    assert result.startswith("The first half")
    assert (CUT_SHORT in result) is cut_short
