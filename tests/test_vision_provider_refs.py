from types import SimpleNamespace


class _FakeOllamaClient:
    def __init__(self):
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"message": {"content": "vision ok"}}


def test_local_vision_strips_ollama_provider_ref(monkeypatch):
    import vision

    client = _FakeOllamaClient()
    svc = vision.VisionService()
    svc._model = "model:ollama:gemma3:4b"

    monkeypatch.setattr(vision, "_ollama_mod", SimpleNamespace())
    monkeypatch.setattr("models._ollama_client", lambda: client)

    assert svc._analyze_local("abc123", "what is visible?") == "vision ok"
    assert client.calls[0]["model"] == "gemma3:4b"


def test_local_vision_keeps_bare_ollama_model(monkeypatch):
    import vision

    client = _FakeOllamaClient()
    svc = vision.VisionService()
    svc._model = "gemma3:4b"

    monkeypatch.setattr(vision, "_ollama_mod", SimpleNamespace())
    monkeypatch.setattr("models._ollama_client", lambda: client)

    assert svc._analyze_local("abc123", "what is visible?") == "vision ok"
    assert client.calls[0]["model"] == "gemma3:4b"


def test_vision_provider_ref_routes_cloud_path(monkeypatch):
    import vision

    calls = []
    svc = vision.VisionService()
    svc._model = "model:codex:gpt-5.5"

    monkeypatch.setattr("models.is_cloud_model", lambda model: model == "model:codex:gpt-5.5")
    monkeypatch.setattr(svc, "_analyze_cloud", lambda b64, question: calls.append((b64, question)) or "cloud ok")
    monkeypatch.setattr(svc, "_analyze_local", lambda b64, question: "local bad")

    assert svc.analyze(b"image-bytes", "describe") == "cloud ok"
    assert calls and calls[0][1] == "describe"
