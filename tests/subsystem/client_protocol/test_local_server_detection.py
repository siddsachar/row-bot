"""On this computer: local model servers already running are found on loopback when Setup asks, and Use
connects one (Ollama through its own provider, the others as a local custom endpoint).

Loopback fakes stand in for Ollama and an OpenAI-compatible server (LM Studio); the known ports are
pointed at them. Nothing else is asked.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from row_bot.application import client_local_servers as found
from row_bot.application.client_local_servers import Known
from scripts import dummy_openai_endpoint as fake_model
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem


class _Fake:
    """A loopback HTTP server answering GET ``routes`` with JSON; it records every path it was asked."""

    def __init__(self, routes: dict[str, object]) -> None:
        fake = self
        self.asked: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                fake.asked.append(self.path)
                payload = routes.get(self.path)
                body = (payload if isinstance(payload, bytes) else json.dumps(payload).encode()) if payload else b""
                self.send_response(200 if payload is not None else 404)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self.server.server_address[1])
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def servers(tmp_path, monkeypatch):
    import row_bot.providers.config as provider_config

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    # Each also lists an embedding model, which never chats (W1).
    ollama = _Fake({"/api/tags": {"models": [{"name": "qwen3.8:27b"}, {"name": "nomic-embed-text:latest"},
                                             {"name": "llava:7b"}]}})
    studio = _Fake({"/v1/models": {"object": "list", "data": [
        {"id": "qwen3.8-27b"}, {"id": "text-embedding-nomic-embed-text-v1.5"}, {"id": "gemma-4"},
        {"id": "bge-m3", "type": "embeddings"}]}})
    page = _Fake({"/v1/models": b"<html>not a model server</html>"})
    dead = _free_port()
    monkeypatch.setattr(found, "known", lambda: (
        Known("ollama", "Ollama", ollama.port, "/api/tags"), Known("lmstudio", "LM Studio", studio.port, "/v1/models"),
        Known("vllm", "vLLM", dead, "/v1/models"), Known("llama_cpp", "llama.cpp server", page.port, "/v1/models")))
    monkeypatch.setattr(found, "_own_port", lambda: -1)
    # The check of a connected model runs after Use answers: here, when a test runs it.
    checks: list = []
    monkeypatch.setattr(found, "in_background", checks.append)
    yield {"ollama": ollama, "studio": studio, "page": page, "dead": dead, "checks": checks}
    for fake in (ollama, studio, page):
        fake.close()


def test_running_servers_are_found_with_their_chat_models_and_nothing_else_is_listed(servers):
    view = found.detect()

    assert [(server["kind"], server["models"]) for server in view["servers"]] == [
        ("ollama", ["qwen3.8:27b", "llava:7b"]), ("lmstudio", ["qwen3.8-27b", "gemma-4"])]
    ollama, studio = view["servers"]
    assert ollama["base_url"] == f"http://127.0.0.1:{servers['ollama'].port}" and ollama["provider_id"] == "ollama"
    assert studio["base_url"] == f"http://127.0.0.1:{servers['studio'].port}/v1" and studio["provider_id"] is None
    # Each was asked once, for its standard model list.
    assert servers["ollama"].asked == ["/api/tags"] and servers["studio"].asked == ["/v1/models"]


def test_row_bots_own_port_is_never_asked(servers, monkeypatch):
    monkeypatch.setattr(found, "_own_port", lambda: servers["studio"].port)

    view = found.detect()

    assert [server["kind"] for server in view["servers"]] == ["ollama"]
    assert servers["studio"].asked == []


def test_use_connects_a_found_server_as_a_local_endpoint_and_setup_can_choose_its_model(servers, monkeypatch):
    from row_bot import models as runtime_models
    from row_bot.application import client_first_run
    from row_bot.providers.catalog import provider_billing

    monkeypatch.setattr(runtime_models, "_current_model", "")
    monkeypatch.setattr("row_bot.application.client_diagnosis.recheck_default_model", lambda: None)

    connected = found.connect("lmstudio", servers["studio"].port)

    provider = connected["provider_id"]
    assert provider == f"custom_openai_lmstudio-{servers['studio'].port}"
    assert connected["model_refs"] == [f"model:{provider}:qwen3.8-27b", f"model:{provider}:gemma-4"]
    assert provider_billing(provider) == "local"
    assert found.detect()["servers"][1]["provider_id"] == provider  # found again: already connected
    assert client_first_run.choose_model(connected["model_refs"][0]) == connected["model_refs"][0]
    assert len(servers["checks"]) == 1  # its model is checked once Use has answered


class _ModelServer(fake_model.DummyOpenAIHandler):
    """An OpenAI-compatible server that answers the endpoint probe (a reply, a tool call, the round trip and
    streamed tool calls) for two chat models."""

    def log_message(self, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/v1/models":
            self._send_json({"object": "list", "data": [
                {"id": name, "object": "model", "context_length": 131072} for name in ("first-model", "chosen-model")]})
            return
        self._send_json({"error": {"message": "Not found"}}, status=404)


def test_use_checks_the_chosen_model_so_agent_mode_and_streamed_tool_replies_are_ready(servers, monkeypatch):
    from row_bot.providers.readiness import evaluate_agent_readiness

    model_server = ThreadingHTTPServer(("127.0.0.1", 0), _ModelServer)
    threading.Thread(target=model_server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    port = int(model_server.server_address[1])
    monkeypatch.setattr(found, "known", lambda: (Known("vllm", "vLLM", port, "/v1/models"),))
    try:
        connected = found.connect("vllm", port, "chosen-model")
        first, chosen = connected["model_refs"]
        assert evaluate_agent_readiness(chosen).ready is False  # Use answered before the check ran

        [check] = servers["checks"]
        check()
    finally:
        model_server.shutdown()
        model_server.server_close()

    ready = evaluate_agent_readiness(chosen)
    assert ready.ready is True, ready.errors
    assert (ready.tool_calling, ready.tool_round_trip, ready.streaming_tool_calling) == (True, True, True)
    assert evaluate_agent_readiness(first).ready is False  # only the chosen model was checked


def test_use_on_ollama_goes_through_its_own_provider(servers, monkeypatch):
    refreshed = []
    monkeypatch.setattr("row_bot.providers.model_catalog_cache.refresh_model_catalog_cache",
                        lambda **kwargs: refreshed.append(kwargs["provider_id"]))

    connected = found.connect("ollama", servers["ollama"].port)

    assert connected["model_refs"] == ["model:ollama:qwen3.8:27b", "model:ollama:llava:7b"]
    assert refreshed == ["ollama"]
    assert servers["checks"] == []  # not a custom endpoint: there is no endpoint probe to run


def test_only_a_known_runtime_on_its_known_port_can_be_connected(servers):
    from row_bot.application.client_platform import ClientPlatformError

    with pytest.raises(ClientPlatformError, match="invalid_command"):
        found.connect("lmstudio", servers["ollama"].port)
    with pytest.raises(ClientPlatformError, match="model_configuration_unavailable"):
        found.connect("vllm", servers["dead"])


def test_the_api_lists_found_servers_when_setup_asks(servers):
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        view = client.get("/api/v1/setup/local-servers", headers=headers)
        assert view.status_code == 200, view.text
        assert [server["label"] for server in view.json()["servers"]] == ["Ollama", "LM Studio"]
        connected = client.post("/api/v1/setup/local-servers/connect", headers=headers,
                                json={"kind": "lmstudio", "port": servers["studio"].port, "model": "gemma-4"})
        assert connected.status_code == 200, connected.text
        assert len(connected.json()["model_refs"]) == 2
        assert len(servers["checks"]) == 1


def test_the_known_ports_are_each_runtimes_usual_one_on_loopback():
    assert {(item.kind, item.port) for item in found.KNOWN} == {
        ("ollama", 11434), ("lmstudio", 1234), ("vllm", 8000), ("llama_cpp", 8080), ("llama_cpp", 8081)}
    assert found.HOST == "127.0.0.1"
