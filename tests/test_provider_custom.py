import providers.config as provider_config
from providers.capabilities import model_supports_surface
from providers.custom import (
    CUSTOM_ENDPOINT_PROFILES,
    custom_model_cache_entries,
    custom_provider_id,
    delete_custom_endpoint,
    get_custom_endpoint,
    list_custom_provider_definitions,
    model_infos_from_openai_compatible_catalog,
    probe_custom_endpoint,
    refresh_custom_endpoint_models,
    save_custom_endpoint,
)


def test_custom_endpoint_catalog_parses_vllm_models(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    endpoint = {
        "id": "local-vllm",
        "name": "Local vLLM",
        "base_url": "http://127.0.0.1:8000/v1",
        "auth_required": False,
        "execution_location": "local",
    }
    payload = {
        "data": [
            {"id": "meta-llama/Llama-3.1-8B-Instruct", "context_length": 131072},
            {"id": "text-embedding-3-large"},
        ]
    }

    infos = model_infos_from_openai_compatible_catalog(endpoint, payload)

    assert [info.model_id for info in infos] == ["meta-llama/Llama-3.1-8B-Instruct", "text-embedding-3-large"]
    assert infos[0].provider_id == custom_provider_id("local-vllm")
    assert model_supports_surface(infos[0], "chat") is True
    assert model_supports_surface(infos[1], "chat") is False
    assert model_supports_surface(infos[1], "embeddings") is True


def test_custom_endpoint_catalog_infers_lmstudio_vision_models(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    endpoint = {
        "id": "lmstudio",
        "name": "LM Studio",
        "base_url": "http://127.0.0.1:1234/v1",
        "auth_required": False,
        "execution_location": "local",
    }
    payload = {"data": [{"id": "llava-phi3:3.8b"}, {"id": "google/gemma-3-4b-it"}, {"id": "qwen2.5-vl-7b-instruct"}]}

    infos = model_infos_from_openai_compatible_catalog(endpoint, payload)

    assert [info.model_id for info in infos] == ["llava-phi3:3.8b", "google/gemma-3-4b-it", "qwen2.5-vl-7b-instruct"]
    assert all(model_supports_surface(info, "vision") for info in infos)


def test_custom_endpoint_catalog_applies_manual_vision_capabilities(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    endpoint = {
        "id": "manual-vision",
        "name": "Manual Vision",
        "base_url": "http://127.0.0.1:1234/v1",
        "auth_required": False,
        "manual_capabilities": {"vision": True},
    }
    payload = {"data": [{"id": "local-model-with-sparse-metadata"}]}

    infos = model_infos_from_openai_compatible_catalog(endpoint, payload)

    assert model_supports_surface(infos[0], "vision") is True


def test_custom_endpoint_catalog_applies_manual_context_window(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    endpoint = {
        "id": "manual-context",
        "name": "Manual Context",
        "base_url": "http://127.0.0.1:1234/v1",
        "auth_required": False,
        "manual_capabilities": {"context_window": 32768},
    }
    payload = {"data": [{"id": "local-model-with-sparse-metadata"}]}

    infos = model_infos_from_openai_compatible_catalog(endpoint, payload)

    assert infos[0].context_window == 32768


def test_custom_endpoint_catalog_merges_lmstudio_native_context_and_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    endpoint = {
        "id": "lm-studio",
        "name": "LM Studio",
        "profile": "lmstudio",
        "base_url": "http://127.0.0.1:1234/v1",
        "auth_required": False,
    }
    payload = {"data": [{"id": "qwen/qwen3.5-9b"}]}
    native = {
        "qwen/qwen3.5-9b": {
            "max_context_length": 262144,
            "capabilities": {"trained_for_tool_use": True, "vision": True},
        },
    }

    infos = model_infos_from_openai_compatible_catalog(endpoint, payload, native_metadata_by_model=native)

    assert infos[0].context_window == 262144
    assert infos[0].tool_calling is True
    assert "tool_calling" in infos[0].capabilities


def test_custom_endpoint_definitions_include_saved_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")

    save_custom_endpoint({
        "id": "llama-cpp",
        "name": "llama.cpp",
        "base_url": "http://127.0.0.1:8080/v1/",
        "auth_required": False,
        "execution_location": "local",
    })

    definitions = list_custom_provider_definitions()

    assert definitions[0].id == custom_provider_id("llama-cpp")
    assert definitions[0].base_url == "http://127.0.0.1:8080/v1"
    assert definitions[0].risk_label == "local_private"


def test_custom_endpoint_profile_defaults_are_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")

    save_custom_endpoint({
        "id": "omlx",
        "name": "oMLX",
        "base_url": "http://127.0.0.1:8000/v1",
        "profile": "omlx",
        "auth_required": False,
        "execution_location": "local",
    })

    endpoint = get_custom_endpoint("omlx")

    assert endpoint["profile"] == "omlx"
    assert endpoint["message_content_mode"] == CUSTOM_ENDPOINT_PROFILES["omlx"]["message_content_mode"]
    assert endpoint["tool_history_mode"] == "native_required"
    assert endpoint["drop_unsupported_params"] is True


def test_custom_endpoint_delete_removes_config_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    save_custom_endpoint({"id": "localai", "base_url": "http://127.0.0.1:8080/v1", "auth_required": False})

    delete_custom_endpoint("localai")

    assert get_custom_endpoint("localai") is None


def test_custom_endpoint_refresh_persists_model_cache_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    save_custom_endpoint({"id": "dummy", "base_url": "http://127.0.0.1:8000/v1", "auth_required": False})

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "thoth-dummy-chat", "context_length": 8192}]}

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _Response())

    infos = refresh_custom_endpoint_models("dummy")
    entries = custom_model_cache_entries()

    assert [info.model_id for info in infos] == ["thoth-dummy-chat"]
    assert entries["thoth-dummy-chat"]["provider"] == custom_provider_id("dummy")
    assert entries["thoth-dummy-chat"]["capabilities_snapshot"]["tasks"] == ["chat"]


def test_custom_endpoint_probe_persists_models_and_probe_result(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    save_custom_endpoint({"id": "dummy", "base_url": "http://127.0.0.1:8000/v1", "auth_required": False})

    class _GetResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "thoth-dummy-chat", "max_model_len": 16384}]}

    class _PostResponse:
        def __init__(self, payload=None):
            self._payload = payload or {"choices": [{"message": {"content": "pong"}}]}

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_lines(self):
            yield b'data: {"choices":[{"delta":{"content":"p"}}]}'

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _GetResponse())
    def _post(*args, **kwargs):
        body = kwargs.get("json") or {}
        if body.get("tools"):
            return _PostResponse({
                "choices": [{
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "thoth_probe_echo", "arguments": "{\"value\":\"ok\"}"},
                        }],
                    },
                }],
            })
        return _PostResponse()

    monkeypatch.setattr(httpx, "post", _post)
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: _PostResponse())

    probe = probe_custom_endpoint("dummy")
    endpoint = get_custom_endpoint("dummy")

    assert probe["ok"] is True
    assert probe["agent_ok"] is True
    assert probe["chat_only_ok"] is False
    assert probe["classification"] == "agent_ready"
    assert probe["models_ok"] is True
    assert probe["chat_ok"] is True
    assert probe["tool_calling"] is True
    assert probe["tool_round_trip"] is True
    assert probe["streaming_ok"] is True
    assert probe["context_window"] == 16384
    assert endpoint["last_probe"]["ok"] is True
    assert endpoint["last_probe"]["classification"] == "agent_ready"
    assert endpoint["models"][0]["context_window"] == 16384


def test_custom_endpoint_probe_classifies_chat_only_when_tools_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    save_custom_endpoint({"id": "dummy", "base_url": "http://127.0.0.1:8000/v1", "auth_required": False})

    class _GetResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "thoth-dummy-chat", "max_model_len": 32768}]}

    class _PostResponse:
        def __init__(self, payload=None):
            self._payload = payload or {"choices": [{"message": {"content": "pong"}}]}

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_lines(self):
            yield b'data: {"choices":[{"delta":{"content":"p"}}]}'

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _GetResponse())
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: _PostResponse())
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: _PostResponse())

    probe = probe_custom_endpoint("dummy")
    endpoint = get_custom_endpoint("dummy")

    assert probe["ok"] is False
    assert probe["agent_ok"] is False
    assert probe["chat_only_ok"] is True
    assert probe["classification"] == "chat_only"
    assert endpoint["last_probe"]["classification"] == "chat_only"


def test_custom_endpoint_probe_logs_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    save_custom_endpoint({"id": "dummy", "base_url": "http://127.0.0.1:8000/v1", "auth_required": False})

    import httpx
    import providers.custom as custom

    def _raise(*args, **kwargs):
        raise OSError("connection refused")

    logged = []
    monkeypatch.setattr(httpx, "get", _raise)
    monkeypatch.setattr(httpx, "post", _raise)
    monkeypatch.setattr(httpx, "stream", _raise)
    monkeypatch.setattr(custom.logger, "warning", lambda *args, **kwargs: logged.append(args))

    probe = probe_custom_endpoint("dummy")

    assert probe["ok"] is False
    assert probe["classification"] == "unavailable"
    assert "connection refused" in "; ".join(probe["errors"])
    assert logged
    assert "Custom endpoint probe failed" in logged[0][0]


def test_custom_endpoint_probe_records_streaming_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    save_custom_endpoint({"id": "dummy", "base_url": "http://127.0.0.1:8000/v1", "auth_required": False})

    class _GetResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "thoth-dummy-chat"}]}

    class _PostResponse:
        def __init__(self, payload=None):
            self._payload = payload or {"choices": [{"message": {"content": "pong"}}]}

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _EmptyStreamResponse(_PostResponse):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_lines(self):
            return iter(())

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _GetResponse())
    def _post(*args, **kwargs):
        body = kwargs.get("json") or {}
        if body.get("tools"):
            return _PostResponse({
                "choices": [{
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "thoth_probe_echo", "arguments": "{\"value\":\"ok\"}"},
                        }],
                    },
                }],
            })
        return _PostResponse()

    monkeypatch.setattr(httpx, "post", _post)
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: _EmptyStreamResponse())

    probe = probe_custom_endpoint("dummy")

    assert probe["ok"] is True
    assert probe["chat_ok"] is True
    assert probe["streaming_ok"] is False
    assert "streaming: no usable stream delta returned" in probe["errors"]


def test_custom_endpoint_probe_does_not_treat_done_only_sse_as_streaming(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    save_custom_endpoint({"id": "dummy", "base_url": "http://127.0.0.1:8000/v1", "auth_required": False})

    class _GetResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "thoth-dummy-chat"}]}

    class _PostResponse:
        def __init__(self, payload=None):
            self._payload = payload or {"choices": [{"message": {"content": "pong"}}]}

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _DoneOnlyStreamResponse(_PostResponse):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_lines(self):
            yield b"data: [DONE]"

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _GetResponse())
    def _post(*args, **kwargs):
        body = kwargs.get("json") or {}
        if body.get("tools"):
            return _PostResponse({
                "choices": [{
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "thoth_probe_echo", "arguments": "{\"value\":\"ok\"}"},
                        }],
                    },
                }],
            })
        return _PostResponse()

    monkeypatch.setattr(httpx, "post", _post)
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: _DoneOnlyStreamResponse())

    probe = probe_custom_endpoint("dummy")

    assert probe["ok"] is True
    assert probe["chat_ok"] is True
    assert probe["streaming_ok"] is False


def test_no_auth_custom_endpoint_refresh_skips_secret_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    save_custom_endpoint({"id": "dummy", "base_url": "http://127.0.0.1:8000/v1", "auth_required": False})

    import httpx
    import providers.custom as custom

    class _GetResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "thoth-dummy-chat"}]}

    monkeypatch.setattr(custom, "custom_endpoint_secret", lambda provider_id: (_ for _ in ()).throw(AssertionError("secret lookup should be skipped")))
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _GetResponse())

    infos = refresh_custom_endpoint_models("dummy")

    assert [info.model_id for info in infos] == ["thoth-dummy-chat"]
