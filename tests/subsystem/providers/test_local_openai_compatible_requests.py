"""A model on a local OpenAI-compatible server (llama.cpp, LM Studio, vLLM) that sends nothing, or writes until
its reply limit showing nothing, ends in plain words and isn't asked again, as on Ollama.

These drive the OpenAI-compatible transport against a server on loopback that holds the request open or answers
with a scripted reply, and count what reached it. A remote endpoint keeps its old behaviour.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from row_bot.providers.transports import local_silence
from row_bot.providers.transports.local_silence import LocalModelSilentError
from row_bot.providers.transports.openai_compatible import ChatOpenAICompatible
from tests.helpers.fake_provider_http import HOLD, Hold, Reply, fake_provider_http

pytestmark = pytest.mark.subsystem

PROMPT = [HumanMessage(content="What is 2 + 2?")]
PROMPTLY = 2.0
LOCAL = {"provider_id": "custom_openai_llamacpp-8090", "profile": "llama_cpp", "execution_location": "local"}
REMOTE = {"provider_id": "custom_openai_gateway", "profile": "generic_openai", "execution_location": "remote"}


def _calculate(expression: str) -> str:
    return "4"


CALCULATE = StructuredTool.from_function(_calculate, name="calculate", description="Calculate.")


def _model(server, endpoint: dict, **kwargs) -> ChatOpenAICompatible:
    base_url = f"{server.url}/v1"
    return ChatOpenAICompatible(model_name="m", base_url=base_url, endpoint={**endpoint, "base_url": base_url},
                                **kwargs)


def _stream(*chunks: dict) -> Reply:
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks] + ["data: [DONE]\n\n"]
    return Reply(body="".join(lines).encode(), content_type="text/event-stream")


def _chunk(delta: dict, finish: str | None = None) -> dict:
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def _whole(message: dict, finish: str) -> Reply:
    return Reply(body=json.dumps({"choices": [{"index": 0, "message": message, "finish_reason": finish}]}).encode())


RAN_OUT = _stream(_chunk({"role": "assistant"}), _chunk({}, "length"))
EMPTY = _stream(_chunk({"role": "assistant"}), _chunk({}, "stop"))
ANSWER = _whole({"role": "assistant", "content": "4"}, "stop")
FIRST_WORD = f"data: {json.dumps(_chunk({'content': 'Hel'}))}\n\n".encode()


def _text(chunks) -> str:
    return "".join(str(chunk.content) for chunk in chunks)


@pytest.mark.parametrize("held", [HOLD, Hold(chunks=(FIRST_WORD,))], ids=["before-headers", "mid-stream"])
def test_a_local_stream_that_receives_nothing_ends_in_plain_words_and_is_not_asked_again(held, monkeypatch):
    monkeypatch.setattr(local_silence, "LOCAL_CHAT_IDLE_SECONDS", 0.2)
    with fake_provider_http([held, ANSWER]) as server:
        model = _model(server, LOCAL)
        received: list = []

        with pytest.raises(LocalModelSilentError, match="sent nothing for 0.2 seconds"):
            for chunk in model.stream(PROMPT):
                received.append(chunk)

        assert server.held(0).hung_up.wait(PROMPTLY)  # the server sees Row-Bot go, so it stops generating
        assert len(server.seen) == 1
        assert _text(received) == ("Hel" if held.chunks else "")


def test_a_local_request_without_streaming_that_receives_nothing_ends_in_plain_words():
    with fake_provider_http([HOLD, ANSWER]) as server:
        model = _model(server, LOCAL, timeout=0.2)

        with pytest.raises(LocalModelSilentError, match="sent nothing for 0.2 seconds"):
            model.invoke(PROMPT)

        assert len(server.seen) == 1


def test_a_local_reply_that_ran_to_its_limit_showing_nothing_ends_in_plain_words_after_one_request():
    with fake_provider_http([RAN_OUT, ANSWER]) as server:
        with pytest.raises(LocalModelSilentError, match="reply length limit"):
            list(_model(server, LOCAL).stream(PROMPT))

        assert len(server.seen) == 1


def test_a_local_reply_without_streaming_that_ran_to_its_limit_showing_nothing_ends_in_plain_words():
    # Tool requests go without streaming until streamed tool calls are probed.
    ran_out = _whole({"role": "assistant", "content": ""}, "length")
    with fake_provider_http([ran_out, ANSWER]) as server:
        model = _model(server, LOCAL).bind_tools([CALCULATE])

        with pytest.raises(LocalModelSilentError, match="reply length limit"):
            list(model.stream(PROMPT))

        assert len(server.seen) == 1


def test_a_local_reply_that_thought_until_its_limit_keeps_its_thinking():
    thought = _stream(_chunk({"reasoning_content": "Let me think"}), _chunk({}, "length"))
    with fake_provider_http([thought]) as server:
        chunks = list(_model(server, LOCAL).stream(PROMPT))

    assert "".join(chunk.additional_kwargs.get("reasoning_content", "") for chunk in chunks) == "Let me think"
    assert chunks[-1].response_metadata["finish_reason"] == "length"


def test_a_local_reply_the_server_finished_empty_is_not_generated_again():
    with fake_provider_http([EMPTY, ANSWER]) as server:
        chunks = list(_model(server, LOCAL).stream(PROMPT))

        assert _text(chunks) == ""
        assert len(server.seen) == 1


def test_an_empty_local_stream_with_tools_is_still_asked_again_without_streaming():
    # A server can stream plain replies but not the tool call it wrote: asked without streaming, it returns it.
    probed = {"last_probe": {"model_id": "m", "streaming_ok": True, "streaming_tool_calling": True}}
    call = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "calculate", "arguments": '{"expression": "2+2"}'}}]}
    with fake_provider_http([EMPTY, _whole(call, "tool_calls")]) as server:
        model = _model(server, {**LOCAL, **probed}).bind_tools([CALCULATE])

        reply = None
        for chunk in model.stream(PROMPT):
            reply = chunk if reply is None else reply + chunk

        assert [json.loads(server.seen[index].body)["stream"] for index in (0, 1)] == [True, False]
    assert [call["name"] for call in reply.tool_calls] == ["calculate"]


def test_a_remote_reply_that_ran_out_showing_nothing_is_still_asked_again_without_streaming():
    with fake_provider_http([RAN_OUT, ANSWER]) as server:
        chunks = list(_model(server, REMOTE).stream(PROMPT))

        assert _text(chunks) == "4"
        assert [json.loads(request.body)["stream"] for request in server.seen] == [True, False]
