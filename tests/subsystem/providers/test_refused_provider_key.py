"""A provider that refuses its saved key says so in Settings › Models and Needs you (T10).

The owner's OpenCode Zen and Go keys were refused (401) at every model-list read, and Settings went on
showing both as Connected. Fake HTTP only: the provider's answers are scripted here.
"""
from __future__ import annotations

import httpx
import pytest

import row_bot.models as models
from row_bot import api_keys, secret_store, status_checks
from row_bot.providers import auth_store, capability_catalog, config, live_settings
from row_bot.providers import model_catalog_cache as cache
from row_bot.providers import status as provider_status_module
from row_bot.providers.opencode import opencode_models_url
from tests.subsystem.providers.test_opencode_first_class_provider import _native_opencode_registry

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

ZEN = "opencode_zen"


class MemorySecrets(dict):
    def get_password(self, service, account):
        return self.get((service, account))

    def set_password(self, service, account, value):
        self[service, account] = value

    def delete_password(self, service, account):
        self.pop((service, account), None)


@pytest.fixture
def zen(tmp_path, monkeypatch):
    """OpenCode Zen with a saved key, and its gateway answering as ``answer["status"]`` says."""
    monkeypatch.setattr(secret_store, "_backend_override", MemorySecrets())
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(cache, "CATALOG_CACHE_PATH", tmp_path / "model_catalog_cache.json")
    monkeypatch.setattr(api_keys, "KEYS_PATH", tmp_path / "api_keys.json")
    monkeypatch.setattr(api_keys, "_session_keys", {})
    monkeypatch.setattr(auth_store, "_session_provider_secrets", {})
    for name in auth_store.PROVIDER_API_KEY_ENV.values():
        monkeypatch.delenv(name, raising=False)
    original_cache = dict(models._cloud_model_cache)
    monkeypatch.setattr(models, "_save_cloud_cache", lambda: None)
    monkeypatch.setattr(models, "fetch_context_catalog", lambda: None)
    monkeypatch.setattr(models, "get_current_model", lambda: "")
    monkeypatch.setattr(capability_catalog, "refresh", lambda: None)
    monkeypatch.setattr(models, "_fetch_opencode_registry_payload", _native_opencode_registry)
    monkeypatch.setattr(cache, "_refresh_ollama_rows", lambda: [])
    # Every other provider is not connected: only OpenCode Zen's row is read for real.
    real_status = provider_status_module.provider_status
    monkeypatch.setattr(provider_status_module, "provider_status", lambda provider_id, **how: (
        real_status(provider_id, **how) if provider_id == ZEN else {"configured": False}))

    answer = {"status": 401}

    def gateway(url, **_kwargs):
        if url != opencode_models_url(ZEN):
            raise httpx.ConnectError("no other host in this test")
        if answer["status"] == "offline":
            raise httpx.ConnectError("offline")
        return httpx.Response(answer["status"], json={"data": [{"id": "glm-5.1"}]},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", gateway)
    auth_store.set_provider_secret(ZEN, "api_key", "synthetic-zen-key-1111")
    yield answer
    models._cloud_model_cache.clear()
    models._cloud_model_cache.update(original_cache)


def _refresh() -> None:
    cache.refresh_model_catalog_cache(force=True, provider_id=ZEN)


def _row() -> dict:
    return next(card for card in live_settings.read_live_provider_cards()["providers"] if card["provider_id"] == ZEN)


def _needs_you() -> list[tuple[str, str, str]]:
    return [(result.name, result.status, result.detail) for result in status_checks.check_sign_ins()]


REFUSED = ("OpenCode Zen API key", "warn", "OpenCode Zen refused its API key. Check the key to use its models.")


def test_a_refused_key_shows_on_the_provider_row_and_in_needs_you_until_the_provider_takes_it(zen) -> None:
    _refresh()
    row = _row()
    assert row["configured"] is True and row["key_refused"] is True
    assert _needs_you() == [REFUSED]

    zen["status"] = "offline"  # a failed connection says nothing about the key
    _refresh()
    assert _row()["key_refused"] is True

    zen["status"] = 200  # the provider answers: the key is fine after all
    _refresh()
    assert _row()["key_refused"] is False
    assert _needs_you() == []


@pytest.mark.parametrize("status_code", [401, 403])
def test_a_new_key_saved_after_a_refusal_is_not_the_refused_one(zen, status_code) -> None:
    zen["status"] = status_code
    _refresh()
    assert _row()["key_refused"] is True

    auth_store.set_provider_secret(ZEN, "api_key", "synthetic-zen-key-2222")

    assert _row()["key_refused"] is False
    assert _needs_you() == []


def test_a_provider_that_fails_for_another_reason_is_not_said_to_refuse_its_key(zen) -> None:
    zen["status"] = 500
    _refresh()
    assert _row()["key_refused"] is False
    assert _needs_you() == []
