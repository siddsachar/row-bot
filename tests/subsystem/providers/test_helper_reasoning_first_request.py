"""Helper calls ask for a reasoning setting the model takes the first time (F5).

The conversation's title and the background calls lower reasoning to the
lowest the model allows. A fake OpenAI Responses API answers the real client
in process and refuses an effort the model does not take, the way OpenAI does,
so a refused first request shows up as a second request on the wire.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import HumanMessage

from row_bot import models
from row_bot.providers import reasoning, routing, runtime

pytestmark = pytest.mark.subsystem

# The efforts OpenAI takes for each model.
ACCEPTED = {
    "gpt-5-mini": ("minimal", "low", "medium", "high"),
    "gpt-5.1": ("none", "low", "medium", "high"),
    "gpt-5.4-mini": ("none", "low", "medium", "high", "xhigh"),
}
_TEXT_RESPONSE = {
    "id": "resp_1", "object": "response", "created_at": 0, "model": "gpt-5.4-mini", "status": "completed",
    "output": [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "Canberra Explained", "annotations": []}]}],
    "usage": {"input_tokens": 20, "output_tokens": 3, "total_tokens": 23},
    "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
}


@pytest.fixture(autouse=True)
def _fresh_process_memory(monkeypatch):
    from row_bot.providers.config import normalize_provider_config

    monkeypatch.setattr(reasoning, "_background_rejections", set())
    monkeypatch.setattr(models, "_override_llm_cache", {})
    monkeypatch.setattr(routing, "load_provider_config", lambda: normalize_provider_config({}))
    token = models._active_model_override.set(None)
    yield
    models._active_model_override.reset(token)


@pytest.fixture
def openai_api(monkeypatch) -> SimpleNamespace:
    """OpenAI's Responses API in process: refuses efforts the model does not take, records each body."""
    api = SimpleNamespace(bodies=[], accepted=())

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        api.bodies.append(body)
        effort = (body.get("reasoning") or {}).get("effort")
        if effort is not None and effort not in api.accepted:
            supported = ", ".join(f"'{value}'" for value in api.accepted)
            return httpx.Response(400, request=request, json={"error": {
                "message": f"Unsupported value: 'reasoning.effort' does not support '{effort}' with this model. "
                           f"Supported values are: {supported}.",
                "type": "invalid_request_error", "param": "reasoning.effort", "code": "unsupported_value"}})
        return httpx.Response(200, json=_TEXT_RESPONSE, request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    monkeypatch.setattr(runtime, "get_provider_secret", lambda provider_id, *args, **kwargs: "test-key")
    return api


def _title(model_ref: str):
    from row_bot.application.conversation_naming import _title_model

    return _title_model(model_ref)


def _condition(model_ref: str):
    from row_bot.providers.background import background_model_for

    return background_model_for("condition", thread_model=model_ref)


@pytest.mark.parametrize("helper", [_title, _condition], ids=["title", "background"])
@pytest.mark.parametrize("model", sorted(ACCEPTED))
def test_a_helper_call_sends_only_a_reasoning_effort_the_model_takes(openai_api, helper, model) -> None:
    openai_api.accepted = ACCEPTED[model]

    helper(f"model:openai:{model}").invoke([HumanMessage(content="What is the capital of Australia?")])

    [body] = openai_api.bodies
    assert body["reasoning"]["effort"] == ACCEPTED[model][0], "the lowest effort the model takes"
    assert not reasoning.background_reasoning_rejected(f"model:openai:{model}")


def test_a_refused_title_setting_falls_back_once_and_leaves_the_conversation_alone(
        openai_api, monkeypatch) -> None:
    from row_bot import threads

    ref = "model:openai:gpt-5.4-mini"
    openai_api.accepted = ()  # this model takes no effort at all: only its default
    resets: list[tuple] = []
    monkeypatch.setattr(threads, "set_thread_reasoning_selection", lambda *args: resets.append(args))

    _title(ref).invoke([HumanMessage(content="first chat")])
    _title(ref).invoke([HumanMessage(content="second chat")])

    efforts = [(body.get("reasoning") or {}).get("effort") for body in openai_api.bodies]
    assert efforts == ["none", None, None], "one refusal, then the provider default straight away"
    assert reasoning.background_reasoning_rejected(ref)
    assert resets == [], "a title's refusal never resets the conversation's reasoning setting"
