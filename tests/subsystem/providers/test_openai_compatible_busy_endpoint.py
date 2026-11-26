"""An OpenAI-compatible endpoint that answers "busy" (rate-limited or overloaded) is asked again after a short wait,
streaming or not, before anything reached the caller; any other refusal, and a busy endpoint that stays busy,
still ends in its error. Driven against a scripted server on loopback that counts what reached it."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import HumanMessage

from row_bot.providers.transports import openai_compatible
from row_bot.providers.transports.openai_compatible import ChatOpenAICompatible
from tests.helpers.fake_provider_http import Reply, fake_provider_http

pytestmark = pytest.mark.subsystem

PROMPT = [HumanMessage(content="What is 2 + 2?")]
REMOTE = {"provider_id": "custom_openai_gateway", "profile": "generic_openai", "execution_location": "remote"}
BUSY = Reply(status=429, body=json.dumps({"error": {"message": "temporarily rate-limited upstream"}}).encode())
OVERLOADED = Reply(status=503, body=json.dumps({"error": {"message": "engine overloaded"}}).encode())
REFUSED = Reply(status=401, body=json.dumps({"error": {"message": "bad key"}}).encode())
ANSWER = Reply(body=json.dumps({"choices": [{"index": 0, "finish_reason": "stop",
                                             "message": {"role": "assistant", "content": "4"}}]}).encode())
STREAMED = Reply(body=("data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": "4"},
                                                            "finish_reason": "stop"}]}) + "\n\ndata: [DONE]\n\n").encode(),
                 content_type="text/event-stream")


@pytest.fixture(autouse=True)
def no_real_waits(monkeypatch):
    monkeypatch.setattr(openai_compatible, "BUSY_WAITS_S", (0.0, 0.0, 0.0, 0.0))


def _model(server) -> ChatOpenAICompatible:
    base_url = f"{server.url}/v1"
    return ChatOpenAICompatible(model_name="m", base_url=base_url, endpoint={**REMOTE, "base_url": base_url})


def test_a_busy_endpoint_is_asked_again_and_its_answer_arrives():
    with fake_provider_http([BUSY, OVERLOADED, ANSWER]) as server:
        reply = _model(server).invoke(PROMPT)

        assert reply.content == "4"
        assert len(server.seen) == 3


def test_a_busy_endpoint_is_asked_again_when_streaming():
    with fake_provider_http([BUSY, STREAMED]) as server:
        chunks = list(_model(server).stream(PROMPT))

        assert "".join(str(chunk.content) for chunk in chunks) == "4"
        assert len(server.seen) == 2
        assert all(json.loads(request.body)["stream"] for request in server.seen)


def test_an_endpoint_that_stays_busy_ends_in_its_error_after_the_waits():
    with fake_provider_http([BUSY] * 6) as server:
        with pytest.raises(RuntimeError, match="HTTP 429"):
            _model(server).invoke(PROMPT)

        assert len(server.seen) == len(openai_compatible.BUSY_WAITS_S) + 1


def test_a_refusal_that_is_not_busy_is_not_asked_again():
    with fake_provider_http([REFUSED, ANSWER]) as server:
        with pytest.raises(RuntimeError, match="HTTP 401"):
            _model(server).invoke(PROMPT)

        assert len(server.seen) == 1
