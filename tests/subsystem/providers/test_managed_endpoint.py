"""A local endpoint whose server Row-Bot runs itself (Learning's llama.cpp server for personal models,
L2x, O75): its address and key exist only while it runs, so each request and probe asks its owner for
them (the owner starts the server then). Any other endpoint is untouched."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from row_bot.providers import custom
from row_bot.providers.transports.openai_compatible import ChatOpenAICompatible


class _Response:
    status_code = 200
    text = "{}"

    def json(self):
        return {"choices": [{"message": {"content": "hello"}}]}


class _Client:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs["headers"].get("Authorization")))
        return _Response()


def _endpoint(**extra):
    return {"id": "row-bot-personal-row-bot", "provider_id": "custom_openai_row-bot-personal-row-bot",
            "base_url": "http://127.0.0.1:8121/v1", "profile": "llama_cpp", "transport": "openai_chat",
            "supports_streaming": False, **extra}


def test_each_request_goes_where_the_running_server_is_with_its_current_key(monkeypatch):
    live = {"value": ("http://127.0.0.1:8125/v1", "key-1")}
    asked: list[bool] = []

    def connection(endpoint, *, start=True):
        asked.append(start)
        return live["value"]

    monkeypatch.setattr(custom, "managed_connection", connection)
    client = _Client()
    model = ChatOpenAICompatible(model_name="row-bot-personal-v1", base_url="http://127.0.0.1:8121/v1",
                                 endpoint=_endpoint(managed="learning"), http_client=client)

    model.invoke([HumanMessage(content="hi")])
    live["value"] = ("http://127.0.0.1:8126/v1", "key-2")  # it stopped while idle and started again
    model.invoke([HumanMessage(content="hi again")])

    assert client.calls == [("http://127.0.0.1:8125/v1/chat/completions", "Bearer key-1"),
                            ("http://127.0.0.1:8126/v1/chat/completions", "Bearer key-2")]
    assert asked == [True, True]  # started on demand


def test_a_managed_server_that_cant_run_fails_the_request_plainly(monkeypatch):
    import pytest

    monkeypatch.setattr(custom, "managed_connection", lambda endpoint, start=True: None)
    model = ChatOpenAICompatible(model_name="row-bot-personal-v1", base_url="http://127.0.0.1:8121/v1",
                                 endpoint=_endpoint(managed="learning"), http_client=_Client())

    with pytest.raises(RuntimeError, match="couldn't start the server that runs your personal model"):
        model.invoke([HumanMessage(content="hi")])


def test_any_other_endpoint_keeps_its_own_address_and_key(monkeypatch):
    monkeypatch.setattr(custom, "managed_connection", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError))
    client = _Client()
    model = ChatOpenAICompatible(model_name="qwen", base_url="http://127.0.0.1:8181/v1", api_key="person-key",
                                 endpoint=_endpoint(), http_client=client)

    model.invoke([HumanMessage(content="hi")])

    assert client.calls == [("http://127.0.0.1:8181/v1/chat/completions", "Bearer person-key")]


def test_only_a_known_owner_can_mark_an_endpoint_managed():
    owned = custom.normalize_custom_endpoint({"id": "x", "base_url": "http://127.0.0.1:1/v1", "managed": "learning"})
    stranger = custom.normalize_custom_endpoint({"id": "y", "base_url": "http://127.0.0.1:1/v1", "managed": "os.system"})

    assert owned["managed"] == "learning" and "managed" not in stranger
    assert custom.managed_connection(stranger) is None and custom.managed_connection(None) is None


def test_a_managed_endpoints_probe_carries_the_running_servers_key(monkeypatch):
    monkeypatch.setattr(custom, "managed_connection", lambda endpoint, start=True: ("http://127.0.0.1:8125/v1", "k"))

    headers = custom._custom_endpoint_headers(custom.normalize_custom_endpoint(  # noqa: SLF001 - the probe's headers
        {"id": "row-bot-personal-row-bot", "base_url": "http://127.0.0.1:8125/v1", "managed": "learning"}))

    assert headers["Authorization"] == "Bearer k"


def test_each_model_on_row_bots_server_keeps_its_own_probe_when_it_starts_on_another_port(tmp_path, monkeypatch):
    import row_bot.providers.config as provider_config

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    endpoint = {"id": "row-bot-personal-row-bot", "base_url": "http://127.0.0.1:8121/v1", "profile": "llama_cpp",
                "auth_required": False, "managed": "learning"}
    custom.save_custom_endpoint(endpoint)
    for model in ("row-bot-personal-base", "row-bot-personal-v2"):  # a version, then the starting model it's compared with
        custom._store_custom_endpoint_probe(  # noqa: SLF001 - what a probe stores
            "row-bot-personal-row-bot", {"ok": True, "model_id": model, "tool_calling": True, "tool_round_trip": True})
    custom.save_custom_endpoint({**endpoint, "base_url": "http://127.0.0.1:8125/v1"})  # its old port was taken

    saved = custom.get_custom_endpoint("custom_openai_row-bot-personal-row-bot")
    assert custom.custom_probe_for_model(saved, "row-bot-personal-base")["model_id"] == "row-bot-personal-base"
    assert custom.custom_probe_for_model(saved, "row-bot-personal-v2")["model_id"] == "row-bot-personal-v2"
    assert custom.custom_probe_for_model(saved, "row-bot-personal-v3") == {}  # never probed


def test_any_other_endpoint_still_keeps_only_its_last_probe_and_drops_it_for_a_new_address(tmp_path, monkeypatch):
    import row_bot.providers.config as provider_config

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    endpoint = {"id": "lab", "base_url": "http://127.0.0.1:8000/v1", "auth_required": False}
    custom.save_custom_endpoint(endpoint)
    for model in ("a", "b"):
        custom._store_custom_endpoint_probe(  # noqa: SLF001
            "lab", {"ok": True, "model_id": model, "tool_calling": True, "tool_round_trip": True})

    saved = custom.get_custom_endpoint("custom_openai_lab")
    assert custom.custom_probe_for_model(saved, "a") == {} and custom.custom_probe_for_model(saved, "b")["ok"]
    custom.save_custom_endpoint({**endpoint, "base_url": "http://127.0.0.1:9000/v1"})
    assert custom.custom_probe_for_model(custom.get_custom_endpoint("custom_openai_lab"), "b") == {}
