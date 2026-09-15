"""Interactive provider settings read connection facts and refresh on request."""
from __future__ import annotations

import pytest

from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem


def test_live_cards_use_nicegui_status_owner_and_require_authentication(monkeypatch):
    from row_bot.providers import live_settings

    calls = []

    def cards(*, refresh_tokens=False):
        calls.append(refresh_tokens)
        return [{
            "provider_id": "openai", "display_name": "OpenAI API", "group": "API Providers",
            "icon": "◇", "configured": True, "source": "keyring", "runtime_enabled": True,
            "model_count": 91, "chat_count": 85, "media_count": 6,
            "fingerprint": "****QBA", "risk_label": "api_key",
            "token_health": "expired",
            "api_key": "PRIVATE_SENTINEL", "last_error": "PRIVATE_SENTINEL",
        }]

    monkeypatch.setattr(live_settings, "provider_status_cards", cards)
    client, _, active = client_app(remote=True)
    with client:
        assert client.get("/api/v1/settings/providers/live").status_code == 401
        _, headers = bootstrap(client)
        response = client.get("/api/v1/settings/providers/live", headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["Cache-Control"] == "no-store"
        card = response.json()["providers"][0]
        assert card["configured"] is True and card["model_count"] == 91
        assert card["fingerprint"] == "****QBA"
        assert card["reconnect_required"] is True
        assert "token_health" not in card
        assert "PRIVATE_SENTINEL" not in response.text
        assert calls == [False]
        active["value"] = False
        assert client.get("/api/v1/settings/providers/live", headers=headers).status_code == 401
        assert calls == [False]


def test_refresh_is_explicit_targeted_and_reports_completion(monkeypatch):
    from row_bot.providers import model_catalog_cache as cache

    calls = []
    monkeypatch.setattr(cache, "start_model_catalog_refresh_background", lambda **kwargs: calls.append(kwargs) or True)
    monkeypatch.setattr(cache, "model_catalog_refresh_state", lambda: {
        "running": False, "last_result": {"ok": True, "provider_id": "openai",
        "provider_status": {"openai": {"status": "live", "count": 91}}},
    })
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        assert client.post("/api/v1/settings/providers/live/unknown/refresh", headers=headers).status_code == 404
        assert calls == []
        started = client.post("/api/v1/settings/providers/live/openai/refresh", headers=headers)
        assert started.status_code == 200, started.text
        assert started.json()["started"] is True
        assert started.json()["provider_id"] == "openai"
        assert calls == [{"reason": "manual", "provider_id": "openai", "force": True}]
        complete = client.get("/api/v1/settings/providers/live/refresh", headers=headers)
        assert complete.status_code == 200, complete.text
        assert complete.json()["ok"] is True and complete.json()["model_count"] == 91
        assert complete.json()["provider_id"] == "openai"


def test_runtime_icon_runs_the_same_explicit_probe_as_nicegui(monkeypatch):
    from row_bot.providers import claude_subscription, xai_oauth

    calls = []
    monkeypatch.setattr(claude_subscription, "run_claude_subscription_runtime_probe", lambda: calls.append("claude") or {"ok": True})
    monkeypatch.setattr(xai_oauth, "run_xai_oauth_runtime_probe", lambda: calls.append("xai") or {"ok": False, "errors": ["PRIVATE_SENTINEL"]})
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        assert client.post("/api/v1/settings/providers/live/openai/runtime-test", headers=headers).status_code == 404
        passed = client.post("/api/v1/settings/providers/live/claude_subscription/runtime-test", headers=headers)
        failed = client.post("/api/v1/settings/providers/live/xai_oauth/runtime-test", headers=headers)
        assert passed.status_code == 200, passed.text
        assert passed.json()["ok"] is True
        assert failed.status_code == 200, failed.text
        assert failed.json()["ok"] is False
        assert "PRIVATE_SENTINEL" not in failed.text
        assert calls == ["claude", "xai"]
