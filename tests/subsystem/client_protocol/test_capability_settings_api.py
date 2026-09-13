"""Authenticated settings routes preserve review authority and closed payloads."""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from row_bot.api.v1.routes import create_client_platform_app
from row_bot.api.v1.security import ClientSecurity
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.client_protocol.test_protocol_application import _isolated_service

pytestmark = pytest.mark.subsystem
REVISION = "a" * 64
DIGEST = "b" * 64


@pytest.fixture
def api(tmp_path, monkeypatch):
    from row_bot import tasks
    from row_bot.runtime import admissions

    monkeypatch.setattr(tasks, "_DB_PATH", tmp_path / "tasks.db")
    monkeypatch.setattr(tasks, "_SCHEMA_READY_PATH", None)
    admissions.instance_identity()
    service = _isolated_service()
    security = ClientSecurity(instance_id=service.instance_id)
    app = create_client_platform_app(
        service, security=security, choices=lambda: {"models": [], "capabilities": []}
    )
    with TestClient(
        app, base_url="http://localhost", client=("127.0.0.1", 12345)
    ) as client:
        _, headers = bootstrap(client)
        yield client, headers


def _command(api, path: str, kind: str, payload: dict, review_id: str):
    client, headers = api
    command_id = str(uuid4())
    response = client.post(
        path,
        headers={**headers, "Idempotency-Key": command_id},
        json={
            "command_id": command_id,
            "client_session_id": headers["X-Client-Session"],
            "type": kind,
            "expected_revision": "0",
            "payload": {**payload, "review_id": review_id},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_channel_routes_bind_review_nonce_and_strip_it_from_domain(api, monkeypatch):
    from row_bot.application import channel_controls

    status = {
        "schema_version": 1,
        "channel_id": "slack",
        "display_name": "Slack",
        "source": {"kind": "core", "label": ""},
        "revision": REVISION,
        "configured": False,
        "running": False,
        "activity": "none",
        "activity_history": [],
        "fields": [],
        "paired_identities": [],
        "capabilities": [],
        "availability": {
            "configuration": "available",
            "lifecycle": "configuration_required",
            "pairing": "available",
            "monitor": "available",
        },
    }
    monkeypatch.setattr(
        channel_controls,
        "read_channels",
        lambda **kwargs: (
            kwargs["validate"](),
            {"schema_version": 1, "total": 1, "items": [status], "truncated": False},
        )[1],
    )
    review = {
        "channel_id": "slack",
        "revision": REVISION,
        "operation": "start",
        "field_key": None,
        "identity_id": None,
        "action_digest": DIGEST,
    }
    monkeypatch.setattr(channel_controls, "review_channel_command", lambda *_a, **_k: review)

    async def execute(**kwargs):
        assert "review_id" not in kwargs["command"]["payload"]
        kwargs["validate"]()
        kwargs["validate_review"](review)
        return {
            "command_id": kwargs["command"]["command_id"],
            "status": "completed",
            "operation": "start",
            "channel": status,
            "code": None,
        }

    monkeypatch.setattr(channel_controls, "execute_channel_command", execute)
    client, headers = api
    page = client.get("/api/v1/settings/channels", headers=headers)
    assert page.status_code == 200 and "slack" in page.text
    payload = {
        "channel_id": "slack",
        "revision": REVISION,
        "operation": "start",
        "field_key": None,
        "value": None,
        "identity_id": None,
    }
    reviewed = client.post(
        "/api/v1/settings/channels/review", headers=headers, json=payload
    )
    assert reviewed.status_code == 200, reviewed.text
    receipt = _command(
        api,
        "/api/v1/settings/channels/commands",
        "channel.control",
        payload,
        reviewed.json()["review_id"],
    )
    assert receipt["channel"]["channel_id"] == "slack"


def test_plugin_routes_bind_plugin_identity_and_review_nonce(api, monkeypatch):
    from row_bot.application import plugin_commands

    item = {
        "plugin_id": "sample-plugin",
        "name": "Sample",
        "version": "1.0",
        "description": "Offline fixture",
        "source": "installed",
        "installed": True,
        "enabled": False,
        "setup_complete": True,
        "health": "passed",
        "update_version": None,
        "permissions": [],
        "provides": {},
        "manifest_revision": REVISION,
        "capabilities": {"enable": {"available": True, "code": None}},
    }
    detail = {
        "schema_version": 1,
        **{key: item[key] for key in ("plugin_id", "name", "version", "description", "enabled", "permissions", "capabilities")},
        "revision": REVISION,
        "settings": [],
        "secrets": [],
        "health": {"status": "passed", "checks": []},
    }
    monkeypatch.setattr(
        plugin_commands,
        "read_plugin_catalog",
        lambda **_k: {
            "schema_version": 1,
            "revision": REVISION,
            "availability": "available",
            "items": [item],
            "total": 1,
            "next_cursor": None,
        },
    )
    monkeypatch.setattr(plugin_commands, "read_plugin_detail", lambda *_a, **_k: detail)
    review = {
        "schema_version": 1,
        "plugin_id": "sample-plugin",
        "action": "plugin.enable",
        "revision": REVISION,
        "action_digest": DIGEST,
        "changes": {"enabled": True},
        "disclosures": [],
    }
    monkeypatch.setattr(plugin_commands, "review_plugin_command", lambda *_a, **_k: review)

    def execute(**kwargs):
        assert "review_id" not in kwargs["command"]["payload"]
        kwargs["validate_review"](review)
        return {
            "command_id": kwargs["command"]["command_id"],
            "status": "completed",
            "plugin": {
                "plugin_id": "sample-plugin",
                "action": "plugin.enable",
                "enabled": True,
                "revision": REVISION,
            },
        }

    monkeypatch.setattr(plugin_commands, "execute_plugin_command", execute)
    client, headers = api
    assert client.get("/api/v1/settings/plugins", headers=headers).status_code == 200
    assert client.get("/api/v1/settings/plugins/sample-plugin", headers=headers).status_code == 200
    payload = {"plugin_id": "sample-plugin", "revision": REVISION}
    reviewed = client.post(
        "/api/v1/settings/plugins/sample-plugin/review",
        headers=headers,
        json={"action": "plugin.enable", "payload": payload},
    )
    assert reviewed.status_code == 200, reviewed.text
    receipt = _command(
        api,
        "/api/v1/settings/plugins/sample-plugin/commands",
        "plugin.enable",
        {**payload, "action_digest": DIGEST},
        reviewed.json()["review_id"],
    )
    assert receipt["plugin"]["enabled"] is True


def test_skill_routes_keep_review_id_for_recoverable_domain_owner(api, monkeypatch):
    from row_bot.application import skill_commands

    monkeypatch.setattr(
        skill_commands,
        "read_skill_library",
        lambda **_k: {
            "schema_version": 1,
            "revision": REVISION,
            "availability": "available",
            "items": [],
            "total": 0,
            "next_cursor": None,
        },
    )
    monkeypatch.setattr(
        skill_commands,
        "read_skill_proposals",
        lambda **_k: {
            "schema_version": 1,
            "revision": REVISION,
            "items": [],
            "truncated": False,
        },
    )
    review = {
        "schema_version": 1,
        "action": "skill.preference",
        "revision": REVISION,
        "target": "sample-skill",
        "before_revision": REVISION,
        "after": None,
        "action_digest": DIGEST,
    }
    monkeypatch.setattr(skill_commands, "review_skill_command", lambda *_a, **_k: review)

    def execute(command, **kwargs):
        assert command["payload"]["review_id"]
        kwargs["validate_action"](command["type"])
        kwargs["validate_review"](command, review)
        return {
            "command_id": command["command_id"],
            "status": "completed",
            "action": command["type"],
            "skill_id": "sample-skill",
            "revision": REVISION,
            "code": None,
        }

    monkeypatch.setattr(skill_commands, "execute_skill_command", execute)
    client, headers = api
    assert client.get("/api/v1/settings/skills", headers=headers).status_code == 200
    assert client.get("/api/v1/settings/skill-proposals", headers=headers).status_code == 200
    payload = {
        "revision": REVISION,
        "name": "sample-skill",
        "preference": "availability",
        "value": True,
    }
    reviewed = client.post(
        "/api/v1/settings/skills/review",
        headers=headers,
        json={"action": "skill.preference", "payload": payload},
    )
    assert reviewed.status_code == 200, reviewed.text
    receipt = _command(
        api,
        "/api/v1/settings/skills/commands",
        "skill.preference",
        payload,
        reviewed.json()["review_id"],
    )
    assert receipt["skill_id"] == "sample-skill"


def test_plugin_command_rejects_route_identity_mismatch(api):
    client, headers = api
    command_id = str(uuid4())
    response = client.post(
        "/api/v1/settings/plugins/other-plugin/commands",
        headers={**headers, "Idempotency-Key": command_id},
        json={
            "command_id": command_id,
            "client_session_id": headers["X-Client-Session"],
            "type": "plugin.enable",
            "expected_revision": "0",
            "payload": {
                "plugin_id": "sample-plugin",
                "revision": REVISION,
                "action_digest": DIGEST,
                "review_id": "unused-review",
            },
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_plugin_command"
