from types import SimpleNamespace


def test_designer_text_refinement_uses_active_agent_model(monkeypatch):
    import row_bot.designer.ai_content as ai_content
    import row_bot.models as models
    import row_bot.providers.readiness as readiness

    calls = {}

    class FakeLLM:
        def invoke(self, messages):
            calls["messages"] = messages
            return SimpleNamespace(content="Refined copy")

    token = models._active_model_override.set("model:codex:gpt-5.5")
    monkeypatch.setattr(readiness, "ensure_agent_ready", lambda model_label: calls.setdefault("ready", model_label))
    monkeypatch.setattr(models, "get_llm_for", lambda model_label: calls.setdefault("llm", model_label) and FakeLLM())
    monkeypatch.setattr(models, "get_llm", lambda: (_ for _ in ()).throw(AssertionError("Designer helpers must not use the global default LLM")))
    try:
        assert ai_content.refine_text("rough copy", "professional") == "Refined copy"
    finally:
        models._active_model_override.reset(token)

    assert calls["ready"] == "model:codex:gpt-5.5"
    assert calls["llm"] == "model:codex:gpt-5.5"


def test_designer_speaker_notes_uses_active_agent_model(monkeypatch):
    import row_bot.designer.ai_content as ai_content
    import row_bot.models as models
    import row_bot.providers.readiness as readiness

    calls = {}

    class FakeLLM:
        def invoke(self, messages):
            calls["messages"] = messages
            return SimpleNamespace(content="Presenter line")

    token = models._active_model_override.set("model:ollama:qwen3.6:27b")
    monkeypatch.setattr(readiness, "ensure_agent_ready", lambda model_label: calls.setdefault("ready", model_label))
    monkeypatch.setattr(models, "get_llm_for", lambda model_label: calls.setdefault("llm", model_label) and FakeLLM())
    monkeypatch.setattr(models, "get_llm", lambda: (_ for _ in ()).throw(AssertionError("Designer helpers must not use the global default LLM")))
    try:
        assert ai_content.generate_speaker_notes("Title", {"text": ["A"]}) == "Presenter line"
    finally:
        models._active_model_override.reset(token)

    assert calls["ready"] == "model:ollama:qwen3.6:27b"
    assert calls["llm"] == "model:ollama:qwen3.6:27b"
