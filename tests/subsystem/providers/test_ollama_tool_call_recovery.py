"""A local Ollama model gets one more try when Ollama can't hand back its tool call.

Two failures ended local-model turns (seen with Qwen3.5 and qwen3:8b through Row-Bot's real runtime):

- the model writes a slightly malformed tool call and Ollama's parser answers HTTP 500 ("XML syntax
  error …") before anything streams, so the whole turn ended as "The AI provider had a server error";
- the model calls a tool that isn't bound in the request (one of the deferred tools behind
  ``tool_search``/``tool_invoke``), Ollama drops the call, and the reply comes back empty.

Each case is asked again at most once: a malformed call with a note on writing it, an empty reply with
the deferred tools bound as stubs (a call to one is handed on through ``tool_invoke``), or with a note
when there are none. These tests drive the local chat model against a fake Ollama on loopback and read
what reached it.
"""

from __future__ import annotations

import asyncio
import contextvars

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from row_bot.providers import deferred_tools
from row_bot.providers.transports.ollama_local import RowBotChatOllama
from tests.helpers.fake_ollama import fake_streaming_ollama

MODEL = "fake-local:7b"
QUESTION = "What is 2 + 2?"
PARSE_ERROR = "XML syntax error on line 8: element <function> closed by </parameter>"


def _calculate(expression: str) -> str:
    return "4"


def _tool_invoke(name: str, arguments: dict) -> str:
    return "ok"


CALCULATE = StructuredTool.from_function(_calculate, name="calculate", description="Calculate.")
TOOL_INVOKE = StructuredTool.from_function(_tool_invoke, name="tool_invoke", description="Run a tool by name.")
CALCULATE_CALL = {"function": {"name": "calculate", "arguments": {"expression": "2 + 2"}}}
INVOKE_CALL = {"function": {"name": "tool_invoke",
                            "arguments": {"name": "calendar_update_event", "arguments": {"id": "evt_1"}}}}


DEFERRED = ("mail_send", "calendar_update_event")
STUB_CALL = {"function": {"name": "calendar_update_event",
                          "arguments": {"event_id": "evt_1", "start": "10:00", "note": "[1, 2]"}}}


def _ask(server, tools: list | None, *, use_async: bool = False, deferred: tuple[str, ...] = (), stream: bool = False):
    """One model call, in its own context, with *deferred* recorded as the run's deferred tools."""

    def run():
        deferred_tools.offer(deferred)
        chat = RowBotChatOllama(model=MODEL, base_url=server.url)
        model = chat.bind_tools(tools) if tools else chat
        prompt = [HumanMessage(content=QUESTION)]
        if stream:
            return list(model.stream(prompt))
        return asyncio.run(model.ainvoke(prompt)) if use_async else model.invoke(prompt)

    return contextvars.copy_context().run(run)


def _tool_names(request: dict) -> list[str]:
    return [tool["function"]["name"] for tool in request.get("tools") or ()]


@pytest.mark.parametrize("use_async", [False, True], ids=["invoke", "ainvoke"])
def test_a_malformed_tool_call_is_asked_again_once_with_a_note(use_async: bool) -> None:
    replies = [{"status": 500, "error": PARSE_ERROR}, {"tool_calls": [CALCULATE_CALL]}]
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=replies) as server:
        reply = _ask(server, [CALCULATE], use_async=use_async)
        first, second = server.chat_requests()

    assert [call["name"] for call in reply.tool_calls] == ["calculate"]
    assert first["messages"][-1]["content"] == QUESTION
    # The second request is the first plus one note from Row-Bot about how to write the call.
    assert second["messages"][:-1] == first["messages"]
    note = second["messages"][-1]
    assert note["role"] == "user"
    assert "<parameter=" in note["content"]


def test_a_malformed_tool_call_twice_still_ends_the_call() -> None:
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=[{"status": 500, "error": PARSE_ERROR}]) as server:
        with pytest.raises(Exception, match="XML syntax error"):
            _ask(server, [CALCULATE])
        assert len(server.chat_requests()) == 2


def test_any_other_server_error_is_not_asked_again() -> None:
    replies = [{"status": 500, "error": "model runner has unexpectedly stopped"}, {"tool_calls": [CALCULATE_CALL]}]
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=replies) as server:
        with pytest.raises(Exception, match="unexpectedly stopped"):
            _ask(server, [CALCULATE])
        assert len(server.chat_requests()) == 1


@pytest.mark.parametrize("use_async", [False, True], ids=["invoke", "ainvoke"])
def test_an_empty_reply_with_tools_is_asked_again_pointing_to_tool_invoke(use_async: bool) -> None:
    replies = [
        {"eval_count": 57, "prompt_eval_count": 900},  # the model called a tool Ollama dropped
        {"tool_calls": [INVOKE_CALL], "eval_count": 20, "prompt_eval_count": 950},
    ]
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=replies) as server:
        reply = _ask(server, [CALCULATE, TOOL_INVOKE], use_async=use_async)
        first, second = server.chat_requests()

    assert [call["name"] for call in reply.tool_calls] == ["tool_invoke"]
    assert reply.tool_calls[0]["args"]["name"] == "calendar_update_event"
    assert second["messages"][:-1] == first["messages"]
    note = second["messages"][-1]
    assert note["role"] == "user" and "tool_invoke" in note["content"]
    # Both attempts' tokens are counted.
    assert reply.usage_metadata["input_tokens"] == 1850
    assert reply.usage_metadata["output_tokens"] == 77


def test_without_tool_invoke_the_note_asks_to_answer_or_use_a_listed_tool() -> None:
    replies = [{"eval_count": 30}, {"tokens": ["4"], "eval_count": 2}]
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=replies) as server:
        reply = _ask(server, [CALCULATE])
        note = server.chat_requests()[1]["messages"][-1]

    assert reply.content == "4"
    assert "tool_invoke" not in note["content"]
    assert note["role"] == "user"


def test_an_empty_reply_twice_comes_back_empty() -> None:
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=[{"eval_count": 30}]) as server:
        reply = _ask(server, [CALCULATE, TOOL_INVOKE])
        assert len(server.chat_requests()) == 2

    assert reply.content == ""
    assert reply.tool_calls == []


def test_an_empty_reply_in_plain_chat_is_not_asked_again() -> None:
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=[{"eval_count": 30}]) as server:
        _ask(server, None)
        assert len(server.chat_requests()) == 1


def test_an_answer_or_a_tool_call_goes_through_untouched() -> None:
    replies = [{"tokens": ["Four", "."], "eval_count": 3}]
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=replies) as server:
        reply = _ask(server, [CALCULATE])
        assert len(server.chat_requests()) == 1
    assert reply.content == "Four."

    with fake_streaming_ollama(tokens=[], model=MODEL, replies=[{"tool_calls": [CALCULATE_CALL]}]) as server:
        reply = _ask(server, [CALCULATE])
        assert len(server.chat_requests()) == 1
    assert [call["name"] for call in reply.tool_calls] == ["calculate"]


@pytest.mark.parametrize("use_async", [False, True], ids=["invoke", "ainvoke"])
def test_an_empty_reply_is_asked_again_with_the_deferred_tools_as_stubs_and_the_call_goes_through_tool_invoke(
    use_async: bool,
) -> None:
    replies = [
        {"eval_count": 57, "prompt_eval_count": 900},  # the model called calendar_update_event; Ollama dropped it
        {"tool_calls": [STUB_CALL, CALCULATE_CALL], "eval_count": 30, "prompt_eval_count": 980},
    ]
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=replies) as server:
        reply = _ask(server, [CALCULATE, TOOL_INVOKE], use_async=use_async, deferred=DEFERRED)
        first, second = server.chat_requests()

    # Asked again as it was, with a bare stub for each deferred tool, and no note.
    assert second["messages"] == first["messages"]
    assert _tool_names(first) == ["calculate", "tool_invoke"]
    assert _tool_names(second) == ["calculate", "tool_invoke", "calendar_update_event", "mail_send"]
    stub = second["tools"][2]["function"]
    assert stub["parameters"] == {"type": "object", "properties": {}}
    # The call to the stub reaches the Agent through tool_invoke, its arguments as written (text stays text);
    # a call to a bound tool is untouched.
    assert [(call["name"], call["args"]) for call in reply.tool_calls] == [
        ("tool_invoke", {"name": "calendar_update_event",
                         "arguments": {"event_id": "evt_1", "start": "10:00", "note": "[1, 2]"}}),
        ("calculate", {"expression": "2 + 2"}),
    ]
    assert reply.usage_metadata["input_tokens"] == 1880
    assert reply.usage_metadata["output_tokens"] == 87


def test_without_tool_invoke_the_deferred_tools_are_not_stubbed() -> None:
    replies = [{"eval_count": 30}, {"tokens": ["4"], "eval_count": 2}]
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=replies) as server:
        _ask(server, [CALCULATE], deferred=DEFERRED)
        second = server.chat_requests()[1]

    assert _tool_names(second) == ["calculate"]
    assert second["messages"][-1]["role"] == "user"


def test_an_empty_reply_after_the_stubs_comes_back_empty() -> None:
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=[{"eval_count": 30}]) as server:
        reply = _ask(server, [CALCULATE, TOOL_INVOKE], deferred=DEFERRED)
        assert len(server.chat_requests()) == 2

    assert reply.content == ""
    assert reply.tool_calls == []


@pytest.mark.parametrize(
    "replies, ends",
    [
        # A malformed call, then an empty reply, then a malformed call again: the error stands.
        ([{"status": 500, "error": PARSE_ERROR}, {"eval_count": 30}, {"status": 500, "error": PARSE_ERROR}],
         "error"),
        # An empty reply, then a malformed call, then an empty reply again: the reply stays empty.
        ([{"eval_count": 30}, {"status": 500, "error": PARSE_ERROR}, {"eval_count": 30}], "empty"),
    ],
    ids=["malformed-empty-malformed", "empty-malformed-empty"],
)
def test_each_kind_is_asked_again_at_most_once(replies: list[dict], ends: str) -> None:
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=replies) as server:
        if ends == "error":
            with pytest.raises(Exception, match="XML syntax error"):
                _ask(server, [CALCULATE, TOOL_INVOKE], deferred=DEFERRED)
        else:
            assert _ask(server, [CALCULATE, TOOL_INVOKE], deferred=DEFERRED).content == ""
        first, second, third = server.chat_requests()

    # The last request carries both fixes: the note on writing the call and the stubs.
    assert third["messages"][:-1] == first["messages"]
    assert "<parameter=" in third["messages"][-1]["content"]
    assert _tool_names(third) == ["calculate", "tool_invoke", "calendar_update_event", "mail_send"]


def test_nothing_streams_twice() -> None:
    replies = [{"eval_count": 30}, {"tokens": ["Fo", "ur"], "eval_count": 2}]
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=replies) as server:
        chunks = _ask(server, [CALCULATE, TOOL_INVOKE], deferred=DEFERRED, stream=True)
        assert len(server.chat_requests()) == 2

    assert "".join(chunk.content for chunk in chunks) == "Four"


def test_a_reply_already_passed_on_is_never_asked_again() -> None:
    replies = [{"tokens": ["Let me check."], "stream_error": PARSE_ERROR}, {"tokens": ["Four"]}]
    seen: list[str] = []
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=replies) as server:
        with pytest.raises(Exception, match="XML syntax error"):
            for chunk in contextvars.copy_context().run(
                lambda: RowBotChatOllama(model=MODEL, base_url=server.url).bind_tools([CALCULATE]).stream(QUESTION)
            ):
                seen.append(chunk.content)
        assert len(server.chat_requests()) == 1

    assert "".join(seen) == "Let me check."
