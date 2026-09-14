"""Passive Settings snapshot and reviewed saved-field mutations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from row_bot.api.v1.routes import create_client_platform_app
from row_bot.api.v1.schemas import SettingsSnapshot
from row_bot.api.v1.security import ClientSecurity
from tests.subsystem.client_protocol.test_protocol_application import _isolated_service
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem
BASE = "/api/v1/settings/snapshot"


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.fixture
def api(tmp_path, monkeypatch):
    data = tmp_path / "row-bot"
    data.mkdir()
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data))
    _write(
        data / "voice_runtime_settings.json",
        {"captions_enabled": False, "talk_model": "saved-talk", "retained": True},
    )
    _write(data / "voice_settings.json", {"whisper_model": "base"})
    _write(data / "tts_settings.json", {"voice": "af_bella", "speed": 1.25})
    _write(data / "embedding_config.json", {"provider": "local", "dimension": 768})
    _write(
        data / "tools_config.json",
        {
            "tools": {"shell": True, "tracker": False, "gmail": True},
            "tool_configs": {
                "shell": {"blocked_commands": "unsafe"},
                "web_search": {"api_key": "PRIVATE_SENTINEL"},
                "gmail": {"selected_operations": ["search_gmail"]},
            },
            "global": {"compression_mode": "deep"},
        },
    )
    _write(data / "user_config.json", {"identity": {"name": "Ada"}})
    _write(data / "app_config.json", {"window_mode": "native"})
    _write(
        data / "dream_config.json",
        {"enabled": False, "window_start": 2, "window_end": 6},
    )
    _write(
        data / "update_config.json",
        {"channel": "beta", "last_check": "2026-01-02T03:04:05Z"},
    )

    from row_bot import api_keys, tasks
    from row_bot.application import settings_snapshot
    from row_bot.runtime import admissions

    monkeypatch.setattr(api_keys, "DATA_DIR", data)
    monkeypatch.setattr(api_keys, "KEYS_PATH", data / "api_keys.json")
    monkeypatch.setattr(tasks, "_DB_PATH", tmp_path / "tasks.db")
    monkeypatch.setattr(tasks, "_SCHEMA_READY_PATH", None)
    monkeypatch.setattr(
        settings_snapshot,
        "_registered_tools",
        lambda: {
            name: {
                "id": name,
                "label": name.replace("_", " ").title(),
                "enabled": enabled,
            }
            for name, enabled in {
                "shell": True,
                "tracker": False,
                "gmail": True,
                "calendar": False,
                "x": False,
                "web_search": True,
                "calculator": True,
            }.items()
        },
    )
    secret_state: dict[str, str] = {}

    def credential(name: str) -> dict[str, object]:
        return {
            "configured": name in secret_state,
            "source": "session" if name in secret_state else "none",
            "fingerprint": "masked" if name in secret_state else "",
        }

    monkeypatch.setattr(settings_snapshot, "_credential_status", credential)
    monkeypatch.setattr(
        settings_snapshot,
        "_plugins",
        lambda validate: {
            "availability": "available",
            "total": 1,
            "installed": 1,
            "enabled": 1,
            "items": [
                {
                    "plugin_id": "sample-plugin",
                    "name": "Sample Plugin",
                    "version": "1.0.0",
                    "enabled": True,
                    "health": "passed",
                }
            ],
        },
    )
    monkeypatch.setattr(
        api_keys, "set_key", lambda name, value: secret_state.__setitem__(name, value)
    )
    monkeypatch.setattr(
        api_keys, "delete_key", lambda name: secret_state.pop(name, None)
    )

    admissions.instance_identity()
    service = _isolated_service()
    security = ClientSecurity(instance_id=service.instance_id)
    app = create_client_platform_app(
        service,
        security=security,
        choices=lambda: {"models": [], "capabilities": []},
    )
    with TestClient(
        app, base_url="http://localhost", client=("127.0.0.1", 12345)
    ) as client:
        _, headers = bootstrap(client)
        yield client, headers, data, secret_state


def _review(client: TestClient, headers: dict[str, str], request: dict) -> dict:
    response = client.post(BASE + "/review", headers=headers, json=request)
    assert response.status_code == 200, response.text
    return response.json()


def _execute(
    client: TestClient,
    headers: dict[str, str],
    request: dict,
    review: dict,
    command_id: str | None = None,
):
    identity = command_id or str(uuid4())
    return client.post(
        BASE + "/commands",
        headers={**headers, "Idempotency-Key": identity},
        json={
            "command_id": identity,
            "client_session_id": headers["X-Client-Session"],
            "type": "settings.update",
            "payload": {
                **request,
                "action_digest": review["action_digest"],
                "review_id": review["review_id"],
            },
        },
    )


def test_snapshot_is_closed_masked_and_does_not_write(api):
    client, headers, data, _ = api
    before = _tree(data)
    response = client.get(BASE, headers=headers)
    assert response.status_code == 200, response.text
    snapshot = SettingsSnapshot.model_validate(response.json())
    assert snapshot.voice.runtime.talk_model == "saved-talk"
    assert snapshot.voice.tts.voice == "af_bella"
    assert snapshot.system.shell.blocked_patterns == "unsafe"
    assert snapshot.documents.embedding.dimension == 768
    assert snapshot.preferences.identity.name == "Ada"
    assert snapshot.plugins.items[0].name == "Sample Plugin"
    assert "PRIVATE_SENTINEL" not in response.text
    assert _tree(data) == before


def test_review_save_receipt_and_retry_preserve_unrelated_values(api):
    client, headers, data, _ = api
    before = client.get(BASE, headers=headers).json()
    request = {
        "settings_revision": before["revision"],
        "page": "voice",
        "field": "runtime.captions_enabled",
        "value": True,
    }
    review = _review(client, headers, request)
    command_id = str(uuid4())
    response = _execute(client, headers, request, review, command_id)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["status"] == "completed"
    assert receipt["snapshot"]["voice"]["runtime"]["captions_enabled"] is True
    saved = json.loads((data / "voice_runtime_settings.json").read_text())
    assert saved["retained"] is True and saved["talk_model"] == "saved-talk"
    after = (data / "voice_runtime_settings.json").read_bytes()
    replay = _execute(client, headers, request, review, command_id)
    assert replay.status_code == 200 and replay.json() == receipt
    assert (data / "voice_runtime_settings.json").read_bytes() == after
    observed = client.get(BASE + "/commands/" + command_id, headers=headers)
    assert observed.status_code == 200 and observed.json() == receipt


def test_secret_review_and_receipt_never_echo_plaintext(api):
    client, headers, _, secret_state = api
    before = client.get(BASE, headers=headers).json()
    secret = "synthetic-private-value"
    request = {
        "settings_revision": before["revision"],
        "page": "voice",
        "field": "openai_realtime_credential",
        "value": secret,
    }
    review_response = client.post(BASE + "/review", headers=headers, json=request)
    assert review_response.status_code == 200, review_response.text
    assert review_response.json()["secret"] is True
    assert secret not in review_response.text
    response = _execute(client, headers, request, review_response.json())
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert secret_state["OPENAI_API_KEY"] == secret
    assert secret not in response.text


def test_stale_or_unallowlisted_changes_are_rejected_without_writes(api):
    client, headers, data, _ = api
    before = client.get(BASE, headers=headers).json()
    unavailable = client.post(
        BASE + "/review",
        headers=headers,
        json={
            "settings_revision": before["revision"],
            "page": "tracker",
            "field": "delete_all",
            "value": True,
        },
    )
    assert unavailable.status_code == 409
    assert unavailable.json()["code"] == "settings_action_unavailable"
    request = {
        "settings_revision": before["revision"],
        "page": "preferences",
        "field": "window_mode",
        "value": "browser",
    }
    review = _review(client, headers, request)
    _write(data / "app_config.json", {"window_mode": "ask", "concurrent": True})
    retained = (data / "app_config.json").read_bytes()
    response = _execute(client, headers, request, review)
    assert response.status_code == 409
    assert response.json()["code"] == "settings_changed"
    assert (data / "app_config.json").read_bytes() == retained


@pytest.mark.parametrize(
    ("page", "field", "value", "saved_file", "saved_path"),
    [
        (
            "system",
            "shell.blocked_patterns",
            "blocked",
            "tools_config.json",
            ("tool_configs", "shell", "blocked_commands"),
        ),
        ("tracker", "enabled", True, "tools_config.json", ("tools", "tracker")),
        (
            "documents",
            "embedding.auto_unload",
            True,
            "embedding_config.json",
            ("auto_unload",),
        ),
        (
            "tools",
            "compression_mode",
            "off",
            "tools_config.json",
            ("global", "compression_mode"),
        ),
        (
            "accounts",
            "x.engage_operations",
            ["x_like", "x_bookmark"],
            "tools_config.json",
            ("tool_configs", "x", "engage_operations"),
        ),
        (
            "utilities",
            "calculator.enabled",
            False,
            "tools_config.json",
            ("tools", "calculator"),
        ),
        (
            "preferences",
            "identity.personality",
            "Concise and curious",
            "user_config.json",
            ("identity", "personality"),
        ),
    ],
)
def test_each_snapshot_owned_page_has_a_reviewed_saved_mutation(
    api, page, field, value, saved_file, saved_path
):
    client, headers, data, _ = api
    snapshot = client.get(BASE, headers=headers).json()
    request = {
        "settings_revision": snapshot["revision"],
        "page": page,
        "field": field,
        "value": value,
    }
    review = _review(client, headers, request)
    response = _execute(client, headers, request, review)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    saved = json.loads((data / saved_file).read_text())
    for part in saved_path:
        saved = saved[part]
    assert saved == value
