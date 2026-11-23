"""Local chat requests carry their model family's sampling values (L1 of the local-models uplift).

Qwen's model cards give each family's values for thinking and non-thinking work, with a presence
penalty against endless repetition. These tests read what reached the wire: from the OpenAI-compatible
transport (llama.cpp, LM Studio, vLLM and the rest) through a fake client, and from Ollama through a
fake server on loopback.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from row_bot.providers import custom
from row_bot.providers.transports.ollama_local import RowBotChatOllama
from row_bot.providers.transports.openai_compatible import ChatOpenAICompatible
from tests.helpers.fake_ollama import fake_streaming_ollama

SAMPLING = ("temperature", "top_p", "top_k", "min_p", "presence_penalty")
QWEN35_THINKING = {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5}
QWEN35_NON_THINKING = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5}
QWEN38_THINKING = {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0}
QWEN38_NON_THINKING = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5}
PROMPT = [HumanMessage(content="hi")]


class _Response:
    status_code = 200
    text = "{}"

    def json(self):
        return {"choices": [{"message": {"content": "hello"}}]}


class _Client:
    def __init__(self):
        self.bodies: list[dict] = []

    def post(self, url, **kwargs):
        self.bodies.append(kwargs["json"])
        return _Response()


def _sampling(body: dict) -> dict:
    return {key: body[key] for key in SAMPLING if key in body}


def _sent(model_name: str, *, invoke: dict | None = None, **endpoint) -> dict:
    client = _Client()
    endpoint = {"provider_id": "custom_openai_mine", "profile": "llama_cpp", "execution_location": "local",
                **endpoint}
    ChatOpenAICompatible(model_name=model_name, base_url="http://127.0.0.1:1/v1", endpoint=endpoint,
                         http_client=client).invoke(PROMPT, **(invoke or {}))
    return client.bodies[0]


def test_a_qwen_model_on_llama_cpp_gets_its_cards_values_for_the_mode_it_runs_in():
    assert _sampling(_sent("Qwen3.5-9B-Q8_0.gguf", reasoning_mode="off")) == QWEN35_NON_THINKING
    assert _sampling(_sent("Qwen3.5-9B-Q8_0.gguf", reasoning_mode="on")) == QWEN35_THINKING
    # Left to the template, Qwen thinks, so it gets the thinking values.
    assert _sampling(_sent("qwen3.5-9b")) == QWEN35_THINKING
    assert _sampling(_sent("qwen3.8-27b-q4")) == QWEN38_THINKING
    assert _sampling(_sent("qwen3.8-27b-q4", extra_body={"chat_template_kwargs": {"enable_thinking": False}})) \
        == QWEN38_NON_THINKING


def test_every_local_server_gets_the_values_lm_studio_without_min_p():
    for profile in ("vllm", "sglang", "localai", "omlx"):
        assert _sampling(_sent("Qwen/Qwen3.5-27B", profile=profile, execution_location="remote")) \
            == QWEN35_THINKING
    lm_studio = _sent("qwen/qwen3.5-9b", profile="lmstudio")
    assert _sampling(lm_studio) == {key: value for key, value in QWEN35_THINKING.items() if key != "min_p"}


def test_a_callers_value_and_the_endpoints_extra_body_win():
    body = _sent("qwen3.5-9b", invoke={"temperature": 0.2},
                 extra_body={"presence_penalty": 0.5, "top_k": 40})

    assert _sampling(body) == {**QWEN35_THINKING, "temperature": 0.2, "presence_penalty": 0.5, "top_k": 40}


def test_an_unknown_family_and_a_cloud_model_get_nothing():
    assert _sampling(_sent("llama-3.1-8b-instruct")) == {}
    assert _sampling(_sent("qwen3.8-flash-next")) == {}
    hosted = _sent("qwen/qwen3.5-397b-a17b", profile="generic_openai", execution_location="remote")
    assert _sampling(hosted) == {}


def test_learnings_models_get_nothing_and_the_built_in_runtime_gets_its_values(monkeypatch):
    monkeypatch.setattr(custom, "managed_connection", lambda endpoint, start=True: ("http://127.0.0.1:1/v1", "k"))

    learning = _sent("qwen3.5-9b", provider_id="custom_openai_row-bot-personal-row-bot", managed="learning",
                     reasoning_mode="off")
    learning_arm = _sent("qwen3.5-9b", provider_id="custom_openai_eval-arm", managed="learning")
    personal_name = _sent("row-bot-personal-v3", provider_id="custom_openai_mine")
    built_in = _sent("qwen3.5-9b", provider_id="custom_openai_llamacpp", managed="local")

    assert _sampling(learning) == _sampling(learning_arm) == _sampling(personal_name) == {}
    assert _sampling(built_in) == QWEN35_NON_THINKING


# ── Ollama ────────────────────────────────────────────────────────────────


@pytest.fixture
def ollama(tmp_path, monkeypatch):
    from row_bot import models

    monkeypatch.setattr(models, "_SETTINGS_PATH", tmp_path / "model_settings.json")
    monkeypatch.setattr(models, "_local_model_settings", {})
    with fake_streaming_ollama(tokens=["ok"], model="qwen3.5:9b") as server:
        yield server


def _ollama_options(server, model: str, **kwargs) -> dict:
    RowBotChatOllama(model=model, base_url=server.url, num_ctx=8_192, **kwargs).invoke(PROMPT)
    return server.chat_requests()[-1]["options"]


def test_ollama_gets_the_values_in_its_options(ollama):
    thinking = _ollama_options(ollama, "qwen3.5:9b", reasoning=True)
    plain = _ollama_options(ollama, "qwen3.5:9b", reasoning=False)
    default = _ollama_options(ollama, "qwen3.8:27b")

    assert _sampling(thinking) == QWEN35_THINKING
    assert _sampling(plain) == QWEN35_NON_THINKING
    assert _sampling(default) == QWEN38_THINKING
    assert thinking["num_ctx"] == 8_192  # the context size still goes with every request


def test_ollama_keeps_a_callers_own_values(ollama):
    RowBotChatOllama(model="qwen3.5:9b", base_url=ollama.url, num_ctx=8_192, reasoning=False).invoke(
        PROMPT, options={"temperature": 0.1, "num_predict": 64})
    set_on_the_model = _ollama_options(ollama, "qwen3.5:9b", reasoning=False, top_k=40)

    assert _sampling(ollama.chat_requests()[0]["options"]) == {**QWEN35_NON_THINKING, "temperature": 0.1}
    assert _sampling(set_on_the_model) == {**QWEN35_NON_THINKING, "top_k": 40}


def test_ollama_personal_models_and_unknown_families_get_nothing(ollama):
    from row_bot import models

    models.set_local_model_settings("qwen3.5:9b", context=16_384, reasoning="off", owner="learning")
    models.save_person_local_model_settings("ollama", "qwen3.8:27b", context=32_768)  # the person's own

    assert _sampling(_ollama_options(ollama, "qwen3.5:9b")) == {}
    assert _sampling(_ollama_options(ollama, "row-bot-personal-v2:abcd1234")) == {}
    assert _sampling(_ollama_options(ollama, "llama3.1:8b")) == {}
    # Only Learning's marker withholds the preset: a person's saved settings never do.
    assert _sampling(_ollama_options(ollama, "qwen3.8:27b")) == QWEN38_THINKING
