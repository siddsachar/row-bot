"""The composer's model pill: never "Ready" for an unavailable model (B115).

Readiness is faked; nothing probes a runtime or a provider.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from row_bot.application import workspace_setup
from row_bot.providers.catalog import provider_billing

pytestmark = pytest.mark.subsystem


def _readiness(*, agent=True, chat=True, credential="configured", errors=()):
    result = SimpleNamespace(ready=agent, credential_status=credential, errors=list(errors))
    return SimpleNamespace(agent=result, chat=SimpleNamespace(ready=chat, credential_status=credential, errors=list(errors)))


@pytest.fixture
def service():
    return SimpleNamespace(readiness_factory=None)


@pytest.fixture(autouse=True)
def _no_vision_lookup(monkeypatch):
    monkeypatch.setattr(workspace_setup, "_sees_images", lambda _ref: None)


def _status(monkeypatch, service, readiness, *, provider="ollama", model="qwen3.8:27b", mode="agent"):
    monkeypatch.setattr("row_bot.providers.readiness.evaluate_runtime_readiness", lambda *_a, **_k: readiness)
    controls = {"model_selection": {"provider_id": provider, "model_ref": f"model:{provider}:{model}"},
                "runtime_mode": mode}
    return workspace_setup.model_status(service, controls)


def test_no_model_yet_offers_choose_a_model(service):
    assert workspace_setup.model_status(service, {"model_selection": None}) == {
        "state": "missing", "reason": "No model chosen yet", "fix": "choose", "local": False, "sees_images": None,
    }


def test_ready_models_carry_the_local_glyph(monkeypatch, service):
    local = _status(monkeypatch, service, _readiness())
    assert local == {"state": "ready", "reason": "", "fix": None, "local": True, "sees_images": None}
    cloud = _status(monkeypatch, service, _readiness(), provider="codex", model="gpt-5.6-sol")
    assert cloud["state"] == "ready" and cloud["local"] is False


def test_unavailable_models_say_why_and_offer_one_fix(monkeypatch, service):
    stopped = _status(monkeypatch, service, _readiness(agent=False, chat=False, credential="missing"))
    assert stopped["state"] == "unavailable"
    assert (stopped["reason"], stopped["fix"]) == ("Ollama isn't running", "reconnect")
    disconnected = _status(monkeypatch, service, _readiness(agent=False, chat=False, credential="missing"),
                           provider="openai", model="gpt-5.6")
    assert disconnected["reason"].endswith("isn't connected") and disconnected["fix"] == "reconnect"
    monkeypatch.setattr("row_bot.providers.custom.get_custom_endpoint", lambda _id: None)
    removed = _status(monkeypatch, service, _readiness(agent=False, chat=False, credential="missing"),
                      provider="custom_openai_lab", model="m")
    assert (removed["reason"], removed["fix"]) == ("Its endpoint was removed", "choose")
    tools = _status(monkeypatch, service, _readiness(agent=False, chat=True))
    assert tools["reason"].startswith("It can't use tools") and tools["fix"] == "choose"
    small = _status(monkeypatch, service, _readiness(agent=False, chat=False,
                                                    errors=["context window is 8,192 tokens"]))
    assert small["reason"] == "Its context window is too small"


def test_chat_only_mode_needs_only_chat_readiness(monkeypatch, service):
    status = _status(monkeypatch, service, _readiness(agent=False, chat=True), mode="chat_only")
    assert status["state"] == "ready"


def test_billing_tags_name_how_each_provider_is_paid_for():
    assert provider_billing("ollama") == "local"
    assert provider_billing("codex") == "subscription"
    assert provider_billing("claude_subscription") == "subscription"
    assert provider_billing("openrouter") == "credits"
    assert provider_billing("openai") == "pay_per_use"
    assert provider_billing("anthropic") == "pay_per_use"
    assert provider_billing("not-a-provider") is None


def test_vision_can_follow_the_chat_model_again(monkeypatch):
    from row_bot.application import client_models_settings

    service = SimpleNamespace(model="model:ollama:gemma3:12b")
    monkeypatch.setattr("row_bot.vision_runtime.get_vision_service", lambda: service)
    monkeypatch.setattr("row_bot.agent.clear_agent_cache", lambda: None)
    monkeypatch.setattr(client_models_settings, "read_models_settings", lambda **_kwargs: {"vision": {"current_ref": ""}})
    result = client_models_settings.update_model_surface("vision", "default", selection_ref="", validate=lambda: None)
    assert service.model == ""
    assert result == {"vision": {"current_ref": ""}}


def test_whisper_install_uses_the_saved_size(monkeypatch):
    from row_bot.application import settings_commands
    from row_bot import voice

    calls: list[str] = []

    class FakeVoice:
        whisper_size = "small"

        def install_whisper_model(self):
            calls.append(self.whisper_size)

    fake = FakeVoice()
    monkeypatch.setattr(voice, "get_voice_service", lambda: fake)
    monkeypatch.setattr(voice, "_load_voice_settings", lambda: {"whisper_model": "tiny"})
    settings_commands._run_voice_action("whisper.install")
    assert calls == ["tiny"]
    assert ("voice", "whisper.install") in settings_commands._ACTION_FIELDS
