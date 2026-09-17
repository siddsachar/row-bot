"""Authenticated Models state and local controls stay separate from explicit refresh."""
from __future__ import annotations

import pytest

from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem


def _state() -> dict:
    empty = {"current_ref": "", "enabled": None, "warning": "", "options": []}
    return {
        "schema_version": 1, "brain": empty,
        "vision": {**empty, "enabled": True}, "image": {**empty, "enabled": True},
        "video": {**empty, "enabled": False}, "camera_index": 0,
        "context": {"policy_kind": "provider", "selected_cap": None,
                    "native_max": 272000, "effective_cap": 272000, "warning": ""},
        "freshness": "fresh", "generated_at": 1000.0, "refresh_running": False,
    }


def test_models_read_is_authenticated_and_never_refreshes(monkeypatch):
    from row_bot.application import client_models_settings as owner
    from row_bot.providers import model_catalog_cache as cache

    calls = []
    monkeypatch.setattr(owner, "read_models_settings", lambda **kwargs: calls.append("read") or _state())
    monkeypatch.setattr(cache, "start_model_catalog_refresh_background",
                        lambda **kwargs: pytest.fail("read must not refresh providers"))
    client, _, _ = client_app()
    with client:
        assert client.get("/api/v1/settings/models/state").status_code == 401
        _, headers = bootstrap(client)
        response = client.get("/api/v1/settings/models/state", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["brain"]["options"] == []
        assert response.json()["context"]["native_max"] == 272000
        assert response.headers["Cache-Control"] == "no-store"
        assert calls == ["read"]


def test_surface_and_context_writes_are_typed_authenticated_and_validated(monkeypatch):
    from row_bot.application import client_models_settings as owner

    calls = []
    monkeypatch.setattr(owner, "update_model_surface",
                        lambda surface, action, **kwargs: calls.append((surface, action, kwargs)) or _state())
    monkeypatch.setattr(owner, "update_model_context",
                        lambda kind, cap, **kwargs: calls.append((kind, cap, kwargs)) or _state())
    client, _, _ = client_app()
    with client:
        assert client.post("/api/v1/settings/models/surface", json={"surface": "vision", "action": "enabled", "enabled": True}).status_code == 403
        _, headers = bootstrap(client)
        assert client.post("/api/v1/settings/models/surface", headers=headers,
            json={"surface": "vision", "action": "camera", "camera_index": -1}).status_code == 422
        assert client.post("/api/v1/settings/models/context", headers=headers,
            json={"policy_kind": "provider", "cap": 2}).status_code == 422
        assert calls == []
        saved = client.post("/api/v1/settings/models/surface", headers=headers,
            json={"surface": "vision", "action": "enabled", "enabled": False})
        assert saved.status_code == 200, saved.text
        cap = client.post("/api/v1/settings/models/context", headers=headers,
            json={"policy_kind": "provider", "cap": 65536})
        assert cap.status_code == 200, cap.text
        assert calls[0][0:2] == ("vision", "enabled")
        assert calls[0][2]["enabled"] is False
        assert calls[1][0:2] == ("provider", 65536)


def test_agent_settings_save_reset_persist_and_notify(tmp_path, monkeypatch):
    from row_bot import agent_settings

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    notices = []
    monkeypatch.setattr(agent_settings, "_notify_dispatcher", lambda: notices.append("changed"))
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        before = client.get("/api/v1/settings/models/agents", headers=headers)
        assert before.status_code == 200 and before.json()["max_iterations"] == 90
        assert not agent_settings.agent_runtime_settings_path().exists()
        invalid = {**before.json(), "max_iterations": 0}
        assert client.post("/api/v1/settings/models/agents", headers=headers, json=invalid).status_code == 422
        assert notices == []
        saved = client.post("/api/v1/settings/models/agents", headers=headers,
            json={**before.json(), "max_iterations": 121})
        assert saved.status_code == 200, saved.text
        assert saved.json()["max_iterations"] == 121
        assert agent_settings.load_agent_runtime_settings().max_iterations == 121
        reset = client.post("/api/v1/settings/models/agents/reset", headers=headers)
        assert reset.status_code == 200 and reset.json()["max_iterations"] == 90
        assert notices == ["changed", "changed"]


def test_catalog_refresh_and_camera_probe_run_only_on_explicit_posts(monkeypatch):
    from row_bot.providers import model_catalog_cache as cache
    from row_bot import vision

    calls = []
    monkeypatch.setattr(cache, "start_model_catalog_refresh_background",
                        lambda **kwargs: calls.append(kwargs) or True)
    monkeypatch.setattr(vision, "list_cameras", lambda: calls.append("cameras") or [0, 1])
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        assert calls == []
        refresh = client.post("/api/v1/settings/models/refresh", headers=headers)
        cameras = client.post("/api/v1/settings/models/cameras/refresh", headers=headers)
        assert refresh.status_code == 200 and refresh.json()["started"] is True
        assert cameras.status_code == 200 and cameras.json()["cameras"] == [0, 1]
        assert calls == [{"reason": "manual", "force": True}, "cameras"]


def test_catalog_cursor_expiry_is_reported_for_bounded_models_route(monkeypatch):
    from row_bot.providers import client_status

    def expired(**kwargs):
        assert kwargs["surface"] == "chat" and kwargs["limit"] == 80
        assert kwargs["readiness"] is True
        raise client_status.ProviderStatusError("cursor_expired")

    monkeypatch.setattr(client_status, "list_cached_models", expired)
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        response = client.get("/api/v1/settings/models/catalog?surface=chat&cursor=old", headers=headers)
        assert response.status_code == 410, response.text
        assert response.json()["code"] == "cursor_expired"
