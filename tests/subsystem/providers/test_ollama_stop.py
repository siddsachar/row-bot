"""Stop ends a local Ollama call that streams nothing, and a call that receives nothing gives up on its own.

A small local model that opens a tool call and never closes it makes Ollama generate for tens of minutes while it
sends nothing, not even response headers (it holds a tool call back until the call is complete). Only closing the
connection makes Ollama stop. These tests drive the local chat model against a fake Ollama on loopback that accepts
a request and never answers, and read what reached it.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from row_bot.cancellation import CancellationScope, use_cancellation_scope
from row_bot.providers import deferred_tools
from row_bot.providers.transports import ollama_local
from row_bot.providers.transports.ollama_local import OllamaSilentError, RowBotChatOllama
from tests.helpers.fake_ollama import fake_streaming_ollama

# How a blocked read is ended differs by OS (closing a socket doesn't wake it on Linux), so these run everywhere.
pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

MODEL = "fake-local:7b"
PROMPT = [HumanMessage(content="What is 2 + 2?")]
HANG = {"hang": True}
ANSWER = {"tokens": ["4"]}
# Stop ends the call at once; the bound only keeps a broken test from hanging.
PROMPTLY = 2.0


def _calculate(expression: str) -> str:
    return "4"


def _tool_invoke(name: str, arguments: dict) -> str:
    return "ok"


CALCULATE = StructuredTool.from_function(_calculate, name="calculate", description="Calculate.")
TOOL_INVOKE = StructuredTool.from_function(_tool_invoke, name="tool_invoke", description="Run a tool by name.")


def _model(server):
    return RowBotChatOllama(model=MODEL, base_url=server.url, num_ctx=8_192).bind_tools([CALCULATE])


def _call(model, way: str, received: list):
    if way == "stream":
        for chunk in model.stream(PROMPT):
            received.append(chunk)
        return None
    if way == "ainvoke":
        return asyncio.run(model.ainvoke(PROMPT))
    return model.invoke(PROMPT)


def _under_scope(call, scope: CancellationScope) -> tuple[threading.Thread, dict]:
    """Run *call* on another thread under *scope*, as a turn's producer runs the model."""
    outcome: dict = {}

    def run() -> None:
        with use_cancellation_scope(scope):
            try:
                outcome["result"] = call()
            except BaseException as error:  # noqa: BLE001 - the test reads it
                outcome["error"] = error

    worker = threading.Thread(target=contextvars.copy_context().run, args=(run,), daemon=True)
    worker.start()
    return worker, outcome


@pytest.mark.parametrize("way", ["invoke", "ainvoke", "stream"])
def test_stop_ends_a_request_ollama_never_answers(way: str) -> None:
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=[HANG, ANSWER]) as server:
        model = _model(server)
        received: list = []
        scope = CancellationScope()
        worker, outcome = _under_scope(lambda: _call(model, way, received), scope)
        assert server.hanging.wait(PROMPTLY)

        scope.cancel("user")
        worker.join(PROMPTLY)

        assert not worker.is_alive()
        assert isinstance(outcome.get("error"), InterruptedError)
        assert received == []
        assert server.hung_up.wait(PROMPTLY)  # Ollama sees the client go, which ends the generation
        assert len(server.chat_requests()) == 1  # not asked again
        # The next request on the same model works as usual.
        assert model.invoke(PROMPT).content == "4"
        assert len(server.chat_requests()) == 2


def test_what_a_stopped_reply_held_back_is_never_passed_on() -> None:
    # Chunks are held until the reply shows something (so a reply asked again never repeats itself); Stop drops them.
    held_then_hang = {"tokens": [""], "hang": True}
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=[held_then_hang]) as server:
        model = _model(server)
        received: list = []
        scope = CancellationScope()
        worker, outcome = _under_scope(lambda: _call(model, "stream", received), scope)
        assert server.hanging.wait(PROMPTLY)

        scope.cancel("user")
        worker.join(PROMPTLY)

        assert not worker.is_alive()
        assert isinstance(outcome.get("error"), InterruptedError)
        assert received == []
        assert server.hung_up.wait(PROMPTLY)


def test_a_call_stopped_before_it_starts_sends_nothing() -> None:
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=[ANSWER]) as server:
        scope = CancellationScope()
        scope.cancel("user")
        with use_cancellation_scope(scope), pytest.raises(InterruptedError):
            _model(server).invoke(PROMPT)

        assert server.chat_requests() == []


def test_a_request_that_receives_nothing_gives_up_in_plain_words(monkeypatch) -> None:
    monkeypatch.setattr(ollama_local, "LOCAL_CHAT_IDLE_SECONDS", 0.2)
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=[HANG, ANSWER]) as server:
        model = _model(server)
        with pytest.raises(OllamaSilentError, match="sent nothing for 0.2 seconds"):
            model.invoke(PROMPT)

        assert server.hung_up.wait(PROMPTLY)  # given up, the connection is closed, so Ollama stops too
        assert len(server.chat_requests()) == 1
        assert model.invoke(PROMPT).content == "4"


def test_a_reply_that_filled_the_window_showing_nothing_ends_in_plain_words() -> None:
    # A model stuck in a tool call runs to num_predict and Ollama shows none of it. Asked again (even with the
    # deferred tools as stubs, which an empty reply otherwise gets), it fills the window again: it ends here.
    ran_out = {"eval_count": 8_192, "done_reason": "length"}
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=[ran_out, ANSWER]) as server:

        def run() -> None:
            deferred_tools.offer(("mail_send",))
            model = RowBotChatOllama(model=MODEL, base_url=server.url, num_ctx=8_192)
            with pytest.raises(OllamaSilentError, match="filled its whole context window"):
                model.bind_tools([CALCULATE, TOOL_INVOKE]).invoke(PROMPT)

        contextvars.copy_context().run(run)
        assert len(server.chat_requests()) == 1


def test_a_reply_is_never_longer_than_the_context_window() -> None:
    with fake_streaming_ollama(tokens=[], model=MODEL, replies=[ANSWER]) as server:
        model = RowBotChatOllama(model=MODEL, base_url=server.url, num_ctx=8_192)
        model.invoke(PROMPT)
        model.bind(options={"num_predict": 64}).invoke(PROMPT)
        capped, own = server.chat_requests()

    assert capped["options"]["num_predict"] == 8_192
    assert own["options"] == {"num_predict": 64, "num_ctx": 8_192}  # a caller's own limit stands
