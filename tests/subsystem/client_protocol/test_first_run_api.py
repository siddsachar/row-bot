"""The first run asks how Row-Bot should think; nothing is chosen for the person.

Fakes stand in for Ollama, the provider validators and the model: no test
reaches a network, a real runtime or the keychain.
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from row_bot import models
from row_bot.application import client_first_run, client_onboarding
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem


@pytest.fixture
def fake_ollama(monkeypatch):
    state = {"running": False, "installed": False, "models": []}
    monkeypatch.setattr(client_first_run, "ollama_running", lambda: state["running"])
    monkeypatch.setattr(client_first_run, "ollama_installed", lambda: state["installed"])
    monkeypatch.setattr(client_first_run, "local_models", lambda: list(state["models"]))
    return state


def test_local_runtime_is_detected_without_asking(fake_ollama):
    assert client_first_run.detect_local_runtime()["state"] == "not_installed"
    fake_ollama["installed"] = True
    assert client_first_run.detect_local_runtime()["state"] == "installed"
    fake_ollama.update(running=True, models=["qwen3.8:27b", "llava:7b"])
    snapshot = client_first_run.detect_local_runtime()
    assert snapshot["state"] == "running"
    assert snapshot["platform"] in {"windows", "macos", "linux"}
    assert snapshot["download_url"] == "https://ollama.com/download"
    assert [row["model_ref"] for row in snapshot["models"]] == [
        "model:ollama:qwen3.8:27b", "model:ollama:llava:7b",
    ]


def test_first_pick_becomes_the_default(tmp_path, monkeypatch, fake_ollama):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(models, "_current_model", "")
    pinned: list[tuple[str, str]] = []
    refreshed: list[str] = []
    monkeypatch.setattr("row_bot.providers.selection.add_quick_choice_for_model",
                        lambda model_id, provider_id=None, **_kwargs: pinned.append((provider_id, model_id)))
    monkeypatch.setattr("row_bot.providers.model_catalog_cache.refresh_model_catalog_cache",
                        lambda **kwargs: refreshed.append(kwargs["provider_id"]))
    fake_ollama.update(running=True, models=["qwen3.8:27b"])
    first = client_onboarding.read_onboarding()
    assert first["needs_model"] is True

    with pytest.raises(Exception, match="model_configuration_unavailable"):
        client_onboarding.execute_onboarding(
            command_id=str(uuid4()), expected_revision=first["revision"], action="choose_model",
            profile=[], step="", model_ref="model:ollama:not-installed",
        )
    with pytest.raises(Exception, match="invalid_model_selection"):
        client_onboarding.execute_onboarding(
            command_id=str(uuid4()), expected_revision=first["revision"], action="choose_model",
            profile=[], step="", model_ref="qwen3.8:27b",
        )
    chosen = client_onboarding.execute_onboarding(
        command_id=str(uuid4()), expected_revision=first["revision"], action="choose_model",
        profile=[], step="", model_ref="model:ollama:qwen3.8:27b",
    )["snapshot"]

    assert chosen["needs_model"] is False
    assert chosen["default_model"] == "model:ollama:qwen3.8:27b"
    assert json.loads((tmp_path / "model_settings.json").read_text(encoding="utf-8"))["model"] == "model:ollama:qwen3.8:27b"
    assert models.get_current_model() == "model:ollama:qwen3.8:27b"
    assert pinned == [("ollama", "qwen3.8:27b")]
    assert refreshed == ["ollama"]


def test_setup_shows_the_real_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(client_onboarding, "_developer_enabled", lambda: True)
    (tmp_path / "app_config.json").write_text(json.dumps({
        "setup_complete": True, "onboarding_version": 4,
        "onboarding_completed_steps": ["models", "voice"],
        "onboarding_skipped_steps": ["developer", "channels"],
    }), encoding="utf-8")
    snapshot = client_onboarding.read_onboarding()
    # Developer tools are on by default (decision 13): done, never "skipped".
    assert "developer" in snapshot["live_done"]
    assert "developer" in snapshot["completed_steps"]
    assert snapshot["skipped_steps"] == ["channels"]
    # "Models" is done only while a default model exists.
    assert "models" not in snapshot["completed_steps"]
    (tmp_path / "model_settings.json").write_text(json.dumps({"model": "model:codex:gpt-5.6-sol"}), encoding="utf-8")
    snapshot = client_onboarding.read_onboarding()
    assert "models" in snapshot["completed_steps"]
    assert snapshot["default_model"] == "model:codex:gpt-5.6-sol"


def test_an_existing_default_is_never_taken_over(tmp_path, monkeypatch):
    """A profile with a saved default never lands in the first run, finished or not."""
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    (tmp_path / "model_settings.json").write_text(json.dumps({"model": "model:codex:gpt-5.6-sol"}), encoding="utf-8")
    snapshot = client_onboarding.read_onboarding()
    assert snapshot["needs_model"] is False
    assert snapshot["setup_complete"] is True


def test_import_is_offered_only_when_another_assistant_is_found(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert client_first_run.import_sources() == []
    (tmp_path / ".hermes").mkdir()
    (tmp_path / ".hermes" / "config.yaml").write_text("model: x\n", encoding="utf-8")
    assert client_first_run.import_sources() == [{"id": "hermes", "label": "Hermes Agent"}]


def test_model_test_reports_in_words(monkeypatch):
    monkeypatch.setattr(models, "_current_model", "")
    assert client_first_run.run_model_test() == {
        "schema_version": 1, "ok": False, "detail": models.NO_MODEL_CHOSEN, "elapsed_ms": 0,
    }
    monkeypatch.setattr(models, "_current_model", "model:ollama:qwen3.8:27b")
    calls: list[str] = []
    monkeypatch.setattr(client_first_run, "test_invoker", lambda ref: calls.append(ref) or "ready")
    result = client_first_run.run_model_test()
    assert result["ok"] is True and result["detail"] == "qwen3.8:27b answered."
    assert calls == ["model:ollama:qwen3.8:27b"]

    def refuse(_ref):
        raise RuntimeError("model 'qwen3.8:27b' not found")

    monkeypatch.setattr(client_first_run, "test_invoker", refuse)
    failed = client_first_run.run_model_test()
    assert failed["ok"] is False
    assert failed["detail"].startswith("qwen3.8:27b via ")
    assert "not found" in failed["detail"]
    monkeypatch.setattr(client_first_run, "test_invoker", lambda _ref: "  ")
    assert client_first_run.run_model_test()["ok"] is False


def test_key_check_asks_the_provider_and_saves_nothing(monkeypatch):
    from row_bot.providers import auth_store

    def no_write(*_args, **_kwargs):
        raise AssertionError("a key check must not save the key")

    monkeypatch.setattr(auth_store, "replace_provider_api_key", no_write)
    monkeypatch.setattr(auth_store, "set_provider_secret", no_write)
    seen: list[str] = []

    def validator(key):
        seen.append(key)
        if key == "sk-offline":
            raise OSError("no route")
        return key == "sk-good-fixture"

    monkeypatch.setattr(client_first_run, "key_validators", lambda: {"openai": validator})
    assert client_first_run.check_provider_key("openai", "  sk-good-fixture \n")["state"] == "valid"
    assert client_first_run.check_provider_key("openai", "sk-bad-fixture")["state"] == "invalid"
    unreachable = client_first_run.check_provider_key("openai", "sk-offline")
    assert unreachable["state"] == "unreachable"
    assert "sk-offline" not in unreachable["detail"]
    assert seen == ["sk-good-fixture", "sk-bad-fixture", "sk-offline"]
    assert client_first_run.check_provider_key("opencode_zen", "zen-fixture")["state"] == "unchecked"
    with pytest.raises(Exception, match="not_found"):
        client_first_run.check_provider_key("not_a_provider", "value")
    with pytest.raises(Exception, match="invalid_command"):
        client_first_run.check_provider_key("openai", "   ")


def test_first_run_routes(tmp_path, monkeypatch, fake_ollama):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(models, "_current_model", "")
    monkeypatch.setattr(client_first_run, "key_validators", lambda: {"anthropic": lambda key: key == "sk-ant-good"})
    fake_ollama.update(installed=True)
    client, _, _ = client_app()
    with client:
        assert client.get("/api/v1/setup/local-runtime").status_code in {401, 403}
        _, headers = bootstrap(client)
        runtime = client.get("/api/v1/setup/local-runtime", headers=headers)
        assert runtime.status_code == 200, runtime.text
        assert runtime.json()["state"] == "installed"
        tested = client.post("/api/v1/setup/model-test", headers=headers)
        assert tested.status_code == 200, tested.text
        assert tested.json()["ok"] is False
        checked = client.post("/api/v1/setup/provider-key/check", headers=headers,
                              json={"provider_id": "anthropic", "value": "sk-ant-wrong"})
        assert checked.status_code == 200, checked.text
        assert checked.json()["state"] == "invalid"
        assert "sk-ant-wrong" not in checked.text
        snapshot = client.get("/api/v1/setup/onboarding", headers=headers).json()
        assert snapshot["needs_model"] is True


def test_home_health_follows_the_first_pick_at_once(tmp_path, monkeypatch, fake_ollama):
    """B295: the start-up check ran while Setup was open; choosing a model checks the model again now."""
    from row_bot import status_checks
    from row_bot.application import client_diagnosis
    from tests.subsystem.runtime.test_monitor_checks import FakeScheduler

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(models, "_current_model", "")
    monkeypatch.setattr("row_bot.providers.selection.add_quick_choice_for_model", lambda *_a, **_kw: None)
    monkeypatch.setattr("row_bot.providers.model_catalog_cache.refresh_model_catalog_cache", lambda **_kw: None)
    monkeypatch.setattr(status_checks, "LOCAL_CHECKS", (status_checks.check_active_model,))
    scheduler = FakeScheduler()
    monkeypatch.setattr(client_diagnosis, "_scheduler", scheduler)
    fake_ollama.update(running=True, models=["qwen3.8:27b"])

    def model_check() -> dict:
        return next(check for check in client_diagnosis.read_system_health()["checks"] if check["name"] == "Model")

    client_diagnosis.run_local_checks()
    assert (model_check()["status"], model_check()["detail"]) == ("warn", "No model selected")
    first = client_onboarding.read_onboarding()
    client_onboarding.execute_onboarding(
        command_id=str(uuid4()), expected_revision=first["revision"], action="choose_model",
        profile=[], step="", model_ref="model:ollama:qwen3.8:27b",
    )
    scheduler.jobs[client_diagnosis.DEFAULT_MODEL_JOB].func()

    assert model_check()["status"] == "ok"
