from types import SimpleNamespace


class _FakeOllamaClient:
    def __init__(self):
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"message": {"content": "vision ok"}}


def _write_ollama_catalog_cache(monkeypatch, tmp_path, rows):
    import row_bot.providers.model_catalog_cache as cache

    monkeypatch.setattr(cache, "CATALOG_CACHE_PATH", tmp_path / "model_catalog_cache.json")
    cache.write_model_catalog_cache(cache.CatalogCacheSnapshot(
        version=cache.CACHE_VERSION,
        generated_at=123.0,
        cloud_cache={},
        ollama_rows=list(rows),
        provider_status={"ollama": {"status": "ok", "count": len(rows)}},
        warnings=(),
        reason="test",
    ))


def test_local_vision_strips_ollama_provider_ref(monkeypatch):
    import row_bot.vision as vision

    client = _FakeOllamaClient()
    svc = vision.VisionService()
    svc._model = "model:ollama:gemma3:4b"

    monkeypatch.setattr(vision, "_ollama_mod", SimpleNamespace())
    monkeypatch.setattr("row_bot.models._ollama_client", lambda: client)

    assert svc._analyze_ollama_local("abc123", "what is visible?") == "vision ok"
    assert client.calls[0]["model"] == "gemma3:4b"


def test_local_vision_keeps_bare_ollama_model(monkeypatch):
    import row_bot.vision as vision

    client = _FakeOllamaClient()
    svc = vision.VisionService()
    svc._model = "gemma3:4b"

    monkeypatch.setattr(vision, "_ollama_mod", SimpleNamespace())
    monkeypatch.setattr("row_bot.models._ollama_client", lambda: client)

    assert svc._analyze_ollama_local("abc123", "what is visible?") == "vision ok"
    assert client.calls[0]["model"] == "gemma3:4b"


def test_vision_provider_ref_routes_provider_path(monkeypatch):
    import row_bot.vision as vision

    calls = []
    svc = vision.VisionService()
    svc._model = "model:codex:gpt-5.5"

    monkeypatch.setattr(
        svc,
        "_analyze_provider",
        lambda b64, question, **_kwargs: calls.append((b64, question)) or "provider ok",
    )
    monkeypatch.setattr(svc, "_analyze_ollama_local", lambda b64, question: "local bad")

    assert svc.analyze(b"image-bytes", "describe") == "provider ok"
    assert calls and calls[0][1] == "describe"


def test_custom_openai_vision_ref_routes_provider_runtime(tmp_path, monkeypatch):
    import row_bot.providers.config as provider_config
    import row_bot.vision as vision

    captured = {}
    svc = vision.VisionService()
    svc._model = "model:custom_openai_lm-studio:qwen/qwen3.5-vl"
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")

    class _FakeLLM:
        def invoke(self, messages):
            captured["messages"] = messages
            return SimpleNamespace(content="custom vision ok")

    def _get_llm_for(model_ref):
        captured["model_ref"] = model_ref
        return _FakeLLM()

    monkeypatch.setattr("row_bot.models.get_llm_for", _get_llm_for)
    monkeypatch.setattr(svc, "_analyze_ollama_local", lambda b64, question: "ollama bad")

    assert svc.analyze(b"image-bytes", "describe") == "custom vision ok"
    assert captured["model_ref"] == "model:custom_openai_lm-studio:qwen/qwen3.5-vl"
    content = captured["messages"][0].content
    assert content[0] == {"type": "text", "text": "describe"}
    assert content[1]["type"] == "image_url"


def test_provider_vision_preserves_png_mime_type(monkeypatch):
    import row_bot.vision as vision

    captured = {}
    svc = vision.VisionService()
    svc._model = "model:codex:gpt-5.6-sol"

    class _FakeLLM:
        def invoke(self, messages):
            captured["messages"] = messages
            return SimpleNamespace(content="png vision ok")

    monkeypatch.setattr("row_bot.models.get_llm_for", lambda _model_ref: _FakeLLM())

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"deterministic-test-image"
    assert svc.analyze(png_bytes, "locate the canvas") == "png vision ok"
    content = captured["messages"][0].content
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_vision_compatibility_uses_cached_ollama_vision_metadata(tmp_path, monkeypatch):
    import row_bot.providers.config as provider_config
    import row_bot.vision as vision

    model_id = "qwen3.6:35b-a3b-mtp-q4_K_M"
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _write_ollama_catalog_cache(monkeypatch, tmp_path, [{
        "provider_id": "ollama",
        "model_id": model_id,
        "capabilities_snapshot": {
            "capabilities": ["chat", "streaming", "text", "vision"],
            "input_modalities": ["image", "text"],
            "output_modalities": ["text"],
            "tasks": ["chat"],
            "transport": "ollama_chat",
        },
    }])

    result = vision.vision_model_compatibility(f"model:ollama:{model_id}")

    assert result["usable"] is True
    assert result["explicit"] is False


def test_vision_compatibility_rejects_cached_text_only_ollama_metadata(tmp_path, monkeypatch):
    import row_bot.providers.config as provider_config
    import row_bot.vision as vision

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _write_ollama_catalog_cache(monkeypatch, tmp_path, [{
        "provider_id": "ollama",
        "model_id": "plain-text-local:latest",
        "capabilities_snapshot": {
            "capabilities": ["chat", "streaming", "text"],
            "input_modalities": ["text"],
            "output_modalities": ["text"],
            "tasks": ["chat"],
            "transport": "ollama_chat",
        },
    }])

    result = vision.vision_model_compatibility("model:ollama:plain-text-local:latest")

    assert result["usable"] is False
    assert result["explicit"] is True
    assert "not compatible with vision" in result["reason"]


def test_custom_openai_vision_ref_blocks_manual_disabled_endpoint(tmp_path, monkeypatch):
    import row_bot.providers.config as provider_config
    import row_bot.vision as vision
    from row_bot.providers.custom import custom_provider_id, save_custom_endpoint

    provider_id = custom_provider_id("lm-studio")
    svc = vision.VisionService()
    svc._model = f"model:{provider_id}:qwen/qwen3.5-9b"
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    save_custom_endpoint({
        "id": "lm-studio",
        "name": "LM Studio",
        "base_url": "http://127.0.0.1:1234/v1",
        "auth_required": False,
        "manual_capabilities": {"vision": False},
        "models": [{
            "id": "qwen/qwen3.5-9b",
            "model_id": "qwen/qwen3.5-9b",
            "capabilities_snapshot": {
                "tasks": ["chat"],
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
        }],
    })

    monkeypatch.setattr(svc, "_analyze_provider", lambda b64, question: (_ for _ in ()).throw(AssertionError("provider called")))

    result = svc.analyze(b"image-bytes", "describe")

    assert "no longer marked as image-capable" in result
    assert "manual vision capability disabled" in result


def test_vision_compatibility_allows_unknown_provider_metadata(tmp_path, monkeypatch):
    import row_bot.providers.config as provider_config
    import row_bot.vision as vision

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")

    result = vision.vision_model_compatibility("model:custom_openai_lab:unknown-vl")

    assert result["usable"] is True
    assert result["explicit"] is False


def test_provider_vision_error_names_selected_provider_not_ollama(monkeypatch):
    import row_bot.vision as vision

    svc = vision.VisionService()
    svc._model = "model:custom_openai_lab:local-vl"

    def _raise(model_ref):
        raise ValueError("connection refused")

    monkeypatch.setattr("row_bot.models.get_llm_for", _raise)

    result = svc.analyze(b"image-bytes", "describe")

    assert "custom_openai_lab" in result
    assert "local-vl" in result
    assert "Ollama" not in result
