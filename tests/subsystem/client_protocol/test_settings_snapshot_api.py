"""Passive Settings snapshot and reviewed saved-field mutations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys
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


def _seed_tracker(data: Path) -> Path:
    path = data / "tracker" / "tracker.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE trackers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            unit TEXT,
            icon TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE entries (
            id TEXT PRIMARY KEY,
            tracker_id TEXT NOT NULL REFERENCES trackers(id) ON DELETE CASCADE,
            timestamp TEXT NOT NULL,
            value TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL
        );
        INSERT INTO trackers VALUES
            ('water', 'Water', 'counter', 'glasses', 'drop', '2026-01-01'),
            ('sleep', 'Sleep', 'duration', 'hours', 'bed', '2026-01-01');
        INSERT INTO entries VALUES
            ('entry-1', 'water', '2026-01-02T09:00:00', '1', NULL, '2026-01-02'),
            ('entry-2', 'sleep', '2026-01-03T09:00:00', '8', NULL, '2026-01-03');
        """
    )
    connection.commit()
    connection.close()
    return path


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
        data / "processed_files.json",
        ["synthetic-one.pdf", "synthetic-two.txt", "synthetic-one.pdf"],
    )
    _write(
        data / "buddy_config.json",
        {
            "enabled": True,
            "visible": True,
            "placement": "desktop",
            "collapsed": True,
            "personality": "curious_scholar",
            "personality_description": "Synthetic saved companion",
            "bubble_verbosity": "chatty",
            "hatch_prompt": "Synthetic familiar",
            "pack_id": "hatch-synthetic",
            "latest_hatch_preview": "PRIVATE_PATH_SENTINEL",
        },
    )
    _write(
        data / "buddy" / "packs" / "hatch-synthetic" / "manifest.json",
        {
            "id": "hatch-synthetic",
            "name": "Buddy Synthetic",
            "version": "2.0.0",
            "runtime": "generated_still",
            "preview": "preview.png",
            "private": "PRIVATE_PACK_SENTINEL",
        },
    )
    (data / "buddy" / "packs" / "hatch-synthetic" / "preview.png").write_bytes(
        b"synthetic-preview"
    )
    vault = data / "saved-vault"
    _write(
        data / "wiki_config.json",
        {
            "enabled": True,
            "vault_path": str(vault),
            "wiki_command": "PRIVATE_WIKI_SENTINEL",
        },
    )
    (vault / "wiki" / "fact").mkdir(parents=True)
    (vault / "wiki" / "fact" / "one.md").write_text("one", encoding="utf-8")
    (vault / "wiki" / "index.md").write_text("index", encoding="utf-8")
    (vault / "conversations").mkdir(parents=True)
    (vault / "conversations" / "thread.md").write_text("thread", encoding="utf-8")
    graph = sqlite3.connect(data / "memory.db")
    graph.execute(
        """CREATE TABLE entities (
            id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, subject TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '', aliases TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '', properties TEXT NOT NULL DEFAULT '{}',
            source TEXT NOT NULL DEFAULT 'live', created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    graph.execute(
        """CREATE TABLE relations (
            id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
            relation_type TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 1.0,
            properties TEXT NOT NULL DEFAULT '{}', source TEXT NOT NULL DEFAULT 'live',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )"""
    )
    graph.executemany(
        """INSERT INTO entities
           (id, entity_type, subject, created_at, updated_at)
           VALUES (?, ?, ?, '2026-01-01', '2026-01-01')""",
        [("a", "fact", "A"), ("b", "fact", "B"), ("c", "person", "C")],
    )
    graph.execute(
        """INSERT INTO relations
           (id, source_id, target_id, relation_type, created_at, updated_at)
           VALUES ('r1', 'a', 'b', 'related_to', '2026-01-01', '2026-01-01')"""
    )
    graph.execute(
        "UPDATE entities SET properties = ? WHERE id = 'c'",
        (json.dumps({"status": "archived"}),),
    )
    graph.executescript(
        """
        CREATE TABLE knowledge_projection_state (
            singleton INTEGER PRIMARY KEY,
            revision INTEGER NOT NULL DEFAULT 0,
            generation TEXT,
            vector_revision INTEGER NOT NULL DEFAULT -1,
            vector_error TEXT,
            lexical_error TEXT
        );
        INSERT INTO knowledge_projection_state
            (singleton, generation, vector_error)
        VALUES (1, 'synthetic', NULL);
        CREATE TABLE knowledge_projection_work (
            entity_id TEXT PRIMARY KEY,
            semantic_pending INTEGER NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0,
            wiki_pending INTEGER NOT NULL DEFAULT 1,
            deleted_type TEXT,
            deleted_source TEXT
        );
        INSERT INTO knowledge_projection_work (entity_id, semantic_pending)
        VALUES ('a', 1);
        """
    )
    graph.commit()
    graph.close()
    _write(
        data / "tools_config.json",
        {
            "tools": {"shell": True, "tracker": False, "gmail": True},
            "tool_configs": {
                "shell": {"blocked_commands": "unsafe"},
                "filesystem": {"workspace_root": str(data / "private-workspace")},
                "web_search": {"api_key": "PRIVATE_SENTINEL"},
                "gmail": {"selected_operations": ["search_gmail"]},
            },
            "global": {"compression_mode": "deep"},
        },
    )
    (data / "private-workspace").mkdir()
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
    monkeypatch.setattr(
        settings_snapshot,
        "_document_vector_status",
        lambda _root, _config: {
            "state": "current",
            "detail": "Synthetic saved document vectors are current.",
        },
    )
    monkeypatch.setattr(
        settings_snapshot,
        "_local_embedding_status",
        lambda _config: {
            "state": "cached",
            "detail": "Synthetic model is available in the local cache.",
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
    selected_workspace = data / "selected-workspace"
    selected_workspace.mkdir()
    from row_bot.application.folder_selections import FolderSelections

    app = create_client_platform_app(
        service,
        security=security,
        choices=lambda: {"models": [], "capabilities": []},
        folder_selections=FolderSelections(picker=lambda: selected_workspace),
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
    assert snapshot.buddy.placement == "desktop"
    assert snapshot.buddy.personality == "curious_scholar"
    assert (
        next(
            pack for pack in snapshot.buddy.packs if pack.pack_id == "hatch-synthetic"
        ).name
        == "Synthetic"
    )
    assert snapshot.voice.tts.voice == "af_bella"
    assert snapshot.system.shell.blocked_patterns == "unsafe"
    assert snapshot.system.workspace.label == "private-workspace"
    assert str(data / "private-workspace") not in response.text
    assert snapshot.knowledge.entities == 3
    assert snapshot.knowledge.relations == 1
    assert snapshot.knowledge.connected_components == 2
    assert snapshot.knowledge.largest_component == 2
    assert snapshot.knowledge.isolated_entities == 1
    assert snapshot.knowledge.status_counts.active == 2
    assert snapshot.knowledge.status_counts.archived == 1
    assert snapshot.wiki.articles == 1
    assert snapshot.wiki.conversations == 1
    assert snapshot.documents.embedding.dimension == 768
    assert snapshot.documents.indexed_documents == 2
    assert snapshot.documents.active_embedding == "Mixedbread Embed Large v1 (local)"
    assert snapshot.documents.document_vectors.state == "current"
    assert snapshot.documents.local_runtime.state == "cached"
    assert snapshot.documents.memory_index.state == "pending"
    assert snapshot.preferences.identity.name == "Ada"
    assert snapshot.plugins.items[0].name == "Sample Plugin"
    assert len(snapshot.utilities.items) == 9
    assert all(item.available for item in snapshot.utilities.items)
    assert [item.label for item in snapshot.utilities.items] == [
        "Tasks",
        "Timer",
        "URL Reader",
        "Calculator",
        "Weather",
        "Charts",
        "System Info",
        "Conversation Search",
        "Custom Tool Builder",
    ]
    assert "PRIVATE_SENTINEL" not in response.text
    assert "PRIVATE_PATH_SENTINEL" not in response.text
    assert "PRIVATE_PACK_SENTINEL" not in response.text
    assert "PRIVATE_WIKI_SENTINEL" not in response.text
    assert _tree(data) == before


def test_x_account_requires_saved_client_credentials_before_token_state(
    tmp_path, monkeypatch
):
    from row_bot.application import settings_snapshot

    _write(tmp_path / "x" / "token.json", {"access_token": "PRIVATE_SENTINEL"})
    credentials = {
        "X_CLIENT_ID": {
            "configured": False,
            "source": "none",
            "fingerprint": "",
        },
        "X_CLIENT_SECRET": {
            "configured": False,
            "source": "none",
            "fingerprint": "",
        },
        "GITHUB_TOKEN": {
            "configured": False,
            "source": "none",
            "fingerprint": "",
        },
    }
    monkeypatch.setattr(
        settings_snapshot, "_credential_status", lambda name: credentials[name]
    )

    result = settings_snapshot._accounts(tmp_path, {}, {}, {})

    assert result["x"]["configured"] is False
    assert result["x"]["authentication_state"] == "not_configured"


def test_account_snapshot_never_returns_google_credential_paths(tmp_path, monkeypatch):
    from row_bot.application import settings_snapshot

    private_path = tmp_path / "private" / "client-secret.json"
    private_path.parent.mkdir()
    private_path.write_text("{}", encoding="utf-8")
    credentials = {
        "GITHUB_TOKEN": {"configured": False, "source": "none", "fingerprint": ""},
        "X_CLIENT_ID": {"configured": False, "source": "none", "fingerprint": ""},
        "X_CLIENT_SECRET": {"configured": False, "source": "none", "fingerprint": ""},
    }
    monkeypatch.setattr(
        settings_snapshot, "_credential_status", lambda name: credentials[name]
    )

    result = settings_snapshot._accounts(
        tmp_path,
        {},
        {
            "gmail": {"credentials_path": str(private_path)},
            "calendar": {"credentials_path": str(private_path)},
        },
        {},
    )

    assert result["gmail"]["configured"] is True
    assert result["calendar"]["configured"] is True
    assert "credentials_path" not in result["gmail"]
    assert str(private_path) not in json.dumps(result)


def test_knowledge_component_analysis_fails_closed_above_bound(tmp_path, monkeypatch):
    from row_bot.application import settings_snapshot

    graph = sqlite3.connect(tmp_path / "memory.db")
    graph.execute("CREATE TABLE entities (id TEXT PRIMARY KEY, entity_type TEXT)")
    graph.execute("CREATE TABLE relations (source_id TEXT, target_id TEXT)")
    graph.executemany(
        "INSERT INTO entities VALUES (?, 'fact')",
        [(f"entity-{index}",) for index in range(6)],
    )
    graph.executemany(
        "INSERT INTO relations VALUES (?, ?)",
        [(f"entity-{index}", f"entity-{index + 1}") for index in range(5)],
    )
    graph.commit()
    graph.close()
    monkeypatch.setattr(settings_snapshot, "_MAX_GRAPH_COMPONENT_ENTITIES", 5)

    result = settings_snapshot._knowledge(tmp_path, {}, {})

    assert result == {
        "availability": "unavailable",
        "memory_available": False,
        "memory_enabled": None,
        "entities": 6,
        "relations": 5,
        "entity_types": [{"kind": "fact", "count": 6}],
        "connected_components": 0,
        "largest_component": 0,
        "isolated_entities": 0,
        "status_counts": {
            "active": 6,
            "needs_review": 0,
            "superseded": 0,
            "archived": 0,
        },
    }


def test_document_runtime_snapshot_is_passive_bounded_and_does_not_import_owners(
    tmp_path, monkeypatch
):
    from row_bot.application import settings_snapshot

    _write(tmp_path / "embedding_config.json", {"provider": "local"})
    _write(tmp_path / "processed_files.json", ["b.pdf", "a.txt", "a.txt"])
    _write(
        tmp_path / "document_index" / "manifest.json",
        {
            "version": 1,
            "documents": [{"document_id": "document", "generation": "one"}],
        },
    )
    _write(
        tmp_path
        / "document_index"
        / "documents"
        / "document"
        / "one"
        / "manifest.json",
        {
            "complete": True,
            "embedding": {
                "provider": "local",
                "model": "mixedbread-ai/mxbai-embed-large-v1",
                "dimension": 1024,
            },
        },
    )
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "model-cache"))
    monkeypatch.setitem(sys.modules, "row_bot.document_index", None)
    monkeypatch.setitem(sys.modules, "row_bot.embedding_providers", None)
    before = _tree(tmp_path)

    result = settings_snapshot._documents(tmp_path)

    assert result["indexed_documents"] == 2
    assert result["active_embedding"] == "Mixedbread Embed Large v1 (local)"
    assert result["document_vectors"]["state"] == "current"
    assert result["local_runtime"]["state"] == "missing"
    assert result["memory_index"]["state"] == "missing"
    assert _tree(tmp_path) == before

    monkeypatch.setattr(settings_snapshot, "_MAX_DOCUMENT_MARKERS", 1)
    assert settings_snapshot._documents(tmp_path)["indexed_documents"] is None


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
            "field": "delete_one",
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


def test_tracker_delete_all_review_is_passive_and_describes_exact_scope(api):
    client, headers, data, _ = api
    _seed_tracker(data)
    snapshot = client.get(BASE, headers=headers).json()
    before = _tree(data)

    review = _review(
        client,
        headers,
        {
            "settings_revision": snapshot["revision"],
            "page": "tracker",
            "field": "delete_all",
            "value": True,
        },
    )

    assert review["value_summary"] == (
        "Delete all tracker data, including every tracker and entry. "
        "This cannot be undone."
    )
    assert review["secret"] is False
    assert _tree(data) == before


def test_workspace_folder_uses_ephemeral_local_owner_grant_without_path_input(api):
    client, headers, data, _ = api
    picked = client.post("/api/v1/resources/folder-selection", headers=headers)
    assert picked.status_code == 200, picked.text
    grant = picked.json()
    assert grant["status"] == "selected"
    assert "selected-workspace" in grant["name"]
    snapshot = client.get(BASE, headers=headers).json()
    request = {
        "settings_revision": snapshot["revision"],
        "page": "system",
        "field": "workspace.folder_grant",
        "value": grant["grant_id"],
    }
    review = _review(client, headers, request)
    assert "selected local folder" in review["value_summary"]
    assert str(data) not in json.dumps(review)

    result = _execute(client, headers, request, review)

    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "completed"
    assert body["snapshot"]["system"]["workspace"] == {
        "label": "selected-workspace",
        "configured": True,
        "exists": True,
    }
    assert str(data / "selected-workspace") not in result.text
    saved = json.loads((data / "tools_config.json").read_text(encoding="utf-8"))
    assert saved["tool_configs"]["filesystem"]["workspace_root"] == str(
        data / "selected-workspace"
    )


@pytest.mark.parametrize(
    ("field", "summary"),
    [
        ("tts.install", "Download and install Kokoro speech output locally"),
        ("sensevoice.install", "Download and install SenseVoice Small locally"),
        (
            "tts.test",
            "Play one local test phrase through the selected output device",
        ),
    ],
)
def test_voice_actions_are_passive_during_review_and_execute_once(
    api, monkeypatch, field, summary
):
    from row_bot.application import settings_commands

    client, headers, data, _ = api
    snapshot = client.get(BASE, headers=headers).json()
    before = _tree(data)
    request = {
        "settings_revision": snapshot["revision"],
        "page": "voice",
        "field": field,
        "value": True,
    }
    review = _review(client, headers, request)
    assert review["value_summary"] == summary
    assert _tree(data) == before

    calls = []
    monkeypatch.setattr(settings_commands, "_run_voice_action", calls.append)
    command_id = str(uuid4())
    result = _execute(client, headers, request, review, command_id)

    assert result.status_code == 200, result.text
    assert result.json()["status"] == "completed"
    assert calls == [field]
    assert (
        _execute(client, headers, request, review, command_id).json() == result.json()
    )
    assert calls == [field]
    assert (
        _execute(client, headers, request, review, command_id).json() == result.json()
    )
    assert calls == [field]


@pytest.mark.parametrize(
    "field",
    [
        "browser.install",
        "computer_use.install",
        "computer_use.test",
        "computer_use.use_managed_runtime",
        "tunnel.check",
        "tunnel.start_main",
        "tunnel.stop_main",
        "logging.open",
    ],
)
def test_system_actions_are_reviewed_passive_and_execute_once(api, monkeypatch, field):
    from row_bot.application import settings_commands

    client, headers, data, _ = api
    snapshot = client.get(BASE, headers=headers).json()
    before = _tree(data)
    request = {
        "settings_revision": snapshot["revision"],
        "page": "system",
        "field": field,
        "value": True,
    }
    review = _review(client, headers, request)
    assert _tree(data) == before
    calls = []
    monkeypatch.setattr(settings_commands, "_run_system_action", calls.append)
    command_id = str(uuid4())

    result = _execute(client, headers, request, review, command_id)

    assert result.status_code == 200, result.text
    assert result.json()["status"] == "completed"
    assert calls == [field]


def test_computer_use_disclosure_and_check_are_explicit_and_recoverable(
    api, monkeypatch
):
    from row_bot.computer_use import readiness

    client, headers, data, _ = api
    calls = []

    def check():
        calls.append("check")
        return readiness.CuaReadiness(
            readiness.ReadinessCode.PERMISSION_MISSING,
            "Cua Driver diagnostics need attention.",
            remediation="Grant the required screen and control permissions, then check again.",
        )

    monkeypatch.setattr(readiness, "run_cua_diagnostics", check)
    snapshot = client.get(BASE, headers=headers).json()
    assert (
        snapshot["system"]["computer_use"]["disclosure_text"]
        == readiness.DISCLOSURE_TEXT
    )
    assert snapshot["system"]["computer_use"]["runtime_state"] == "disabled"
    assert calls == []
    assert not (data / "computer_use_settings.json").exists()

    accept = {
        "settings_revision": snapshot["revision"],
        "page": "system",
        "field": "computer_use.disclosure_acknowledged",
        "value": True,
    }
    review = _review(client, headers, accept)
    assert "Cua Driver telemetry notice" in review["value_summary"]
    assert not (data / "computer_use_settings.json").exists()
    result = _execute(client, headers, accept, review, str(uuid4()))
    assert result.status_code == 200, result.text
    assert result.json()["snapshot"]["system"]["computer_use"][
        "disclosure_acknowledged"
    ]

    check = {
        "settings_revision": result.json()["settings_revision"],
        "page": "system",
        "field": "computer_use.check",
        "value": True,
    }
    reviewed_check = _review(client, headers, check)
    assert calls == []
    command_id = str(uuid4())
    first = _execute(client, headers, check, reviewed_check, command_id)
    assert first.status_code == 200, first.text
    assert first.json()["action_result"]["code"] == "permission_missing"
    assert "Grant the required" in first.json()["action_result"]["remediation"]
    assert calls == ["check"]
    assert (
        _execute(client, headers, check, reviewed_check, command_id).json()
        == first.json()
    )
    assert calls == ["check"]


def test_system_cua_override_requires_disclosure_and_redacts_path(api, monkeypatch):
    from row_bot.application.settings_commands import _apply, _intent
    from row_bot.computer_use import readiness

    client, headers, data, _ = api
    snapshot = client.get(BASE, headers=headers).json()
    request = {
        "settings_revision": snapshot["revision"],
        "page": "system",
        "field": "computer_use.system_binary_verify",
        "value": str(data / "synthetic-cua-binary"),
    }
    before = _tree(data)
    review = _review(client, headers, request)
    assert request["value"] not in str(review)
    assert _tree(data) == before

    with pytest.raises(readiness.CuaDisclosureRequired):
        _apply(_intent(**request), validate=lambda: None)
    assert _tree(data) == before

    readiness.acknowledge_disclosure()
    snapshot = client.get(BASE, headers=headers).json()
    request["settings_revision"] = snapshot["revision"]
    review = _review(client, headers, request)
    calls = []

    def fake_verify():
        calls.append("verify")
        return readiness.CuaReadiness(
            readiness.ReadinessCode.VERSION_MISMATCH,
            "System Cua is outside the reviewed version pin.",
            remediation="Use the reviewed version.",
        )

    monkeypatch.setattr(readiness, "verify_system_cua", fake_verify)
    command_id = str(uuid4())
    result = _execute(client, headers, request, review, command_id)
    assert result.status_code == 200, result.text
    assert result.json()["action_result"]["code"] == "version_mismatch"
    assert request["value"] not in result.text
    assert calls == ["verify"]
    assert (
        _execute(client, headers, request, review, command_id).json() == result.json()
    )
    assert calls == ["verify"]


def test_managed_cua_removal_is_explicit_and_deduplicated(api, monkeypatch):
    from row_bot.computer_use import readiness

    client, headers, data, _ = api
    calls = []
    monkeypatch.setattr(
        readiness, "uninstall_cua_runtime", lambda: calls.append("remove") or True
    )
    snapshot = client.get(BASE, headers=headers).json()
    request = {
        "settings_revision": snapshot["revision"],
        "page": "system",
        "field": "computer_use.remove",
        "value": True,
    }
    before = _tree(data)
    review = _review(client, headers, request)
    assert "Remove" in review["value_summary"]
    assert calls == [] and _tree(data) == before
    command_id = str(uuid4())
    result = _execute(client, headers, request, review, command_id)
    assert result.status_code == 200, result.text
    assert result.json()["action_result"]["code"] == "removed"
    assert result.json()["snapshot"]["system"]["computer_use"]["enabled"] is False
    assert calls == ["remove"]
    assert (
        _execute(client, headers, request, review, command_id).json() == result.json()
    )
    assert calls == ["remove"]


def test_macos_privacy_actions_open_only_fixed_local_panes(monkeypatch):
    import platform
    from row_bot.application.settings_commands import (
        SettingsCommandError,
        _run_system_action,
    )
    from row_bot.computer_use import readiness

    calls = []
    monkeypatch.setattr(readiness, "open_macos_privacy_settings", calls.append)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    with pytest.raises(SettingsCommandError, match="settings_action_unavailable"):
        _run_system_action("computer_use.open_accessibility")
    assert calls == []
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    result = _run_system_action("computer_use.open_accessibility")
    assert result["code"] == "opened"
    _run_system_action("computer_use.open_screen_recording")
    assert calls == ["accessibility", "screen_recording"]


def test_reviewed_main_tunnel_persists_restart_choice_and_reports_webhook_url(
    api, monkeypatch
):
    from row_bot.tunnel import tunnel_manager

    client, headers, data, _ = api
    active: dict[int, str] = {}
    opened: list[int] = []
    closed: list[int] = []

    def start(port: int, *, label: str) -> str:
        assert label == "main_app"
        opened.append(port)
        active[port] = "https://synthetic.ngrok.example"
        return active[port]

    def stop(port: int) -> None:
        closed.append(port)
        active.pop(port, None)

    monkeypatch.setattr(tunnel_manager, "get_url", active.get)
    monkeypatch.setattr(tunnel_manager, "start_tunnel", start)
    monkeypatch.setattr(tunnel_manager, "stop_tunnel", stop)
    before = _tree(data)
    initial = client.get(BASE, headers=headers).json()
    assert initial["system"]["tunnel"]["main_app_enabled"] is False
    assert initial["system"]["tunnel"]["main_app_url"] is None
    assert _tree(data) == before

    request = {
        "settings_revision": initial["revision"],
        "page": "system",
        "field": "tunnel.start_main",
        "value": True,
    }
    review = _review(client, headers, request)
    assert _tree(data) == before
    identity = str(uuid4())
    response = _execute(client, headers, request, review, identity)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    tunnel = response.json()["snapshot"]["system"]["tunnel"]
    assert tunnel["main_app_enabled"] is True
    assert tunnel["main_app_url"] == "https://synthetic.ngrok.example"
    assert tunnel["local_owner_control_available"] is True
    assert (
        json.loads((data / "channels_config.json").read_text())["tunnel"][
            "tunnel_main_app"
        ]
        is True
    )
    assert (
        _execute(client, headers, request, review, identity).json() == response.json()
    )
    assert len(opened) == 1

    request = {
        **request,
        "settings_revision": response.json()["snapshot"]["revision"],
        "field": "tunnel.stop_main",
    }
    review = _review(client, headers, request)
    response = _execute(client, headers, request, review)
    assert response.status_code == 200, response.text
    assert response.json()["snapshot"]["system"]["tunnel"]["main_app_enabled"] is False
    assert response.json()["snapshot"]["system"]["tunnel"]["main_app_url"] is None
    assert (
        json.loads((data / "channels_config.json").read_text())["tunnel"][
            "tunnel_main_app"
        ]
        is False
    )
    assert len(closed) == 1


def test_failed_main_tunnel_start_does_not_save_restart_choice(api, monkeypatch):
    from row_bot.tunnel import tunnel_manager

    client, headers, data, _ = api
    monkeypatch.setattr(tunnel_manager, "get_url", lambda port: None)
    monkeypatch.setattr(
        tunnel_manager,
        "start_tunnel",
        lambda port, *, label: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
    )
    before = _tree(data)
    snapshot = client.get(BASE, headers=headers).json()
    request = {
        "settings_revision": snapshot["revision"],
        "page": "system",
        "field": "tunnel.start_main",
        "value": True,
    }
    review = _review(client, headers, request)
    result = _execute(client, headers, request, review)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "partial"
    assert _tree(data) == before


@pytest.mark.parametrize("already_active", [False, True])
def test_main_tunnel_save_failure_closes_only_newly_opened_tunnel(
    api, monkeypatch, already_active
):
    from row_bot.application import settings_commands
    from row_bot.app_port import get_app_port
    from row_bot.tunnel import tunnel_manager

    client, headers, data, _ = api
    active: dict[int, str] = {}
    if already_active:
        active[get_app_port()] = "https://synthetic.ngrok.example"
    closed: list[int] = []
    monkeypatch.setattr(tunnel_manager, "get_url", active.get)

    def start(port: int, *, label: str) -> str:
        active[port] = "https://synthetic.ngrok.example"
        return active[port]

    def stop(port: int) -> None:
        closed.append(port)
        active.pop(port, None)

    monkeypatch.setattr(tunnel_manager, "start_tunnel", start)
    monkeypatch.setattr(tunnel_manager, "stop_tunnel", stop)
    monkeypatch.setattr(
        settings_commands,
        "_write_json_setting",
        lambda *args: (_ for _ in ()).throw(OSError("synthetic save failure")),
    )
    before = _tree(data)
    snapshot = client.get(BASE, headers=headers).json()
    request = {
        "settings_revision": snapshot["revision"],
        "page": "system",
        "field": "tunnel.start_main",
        "value": True,
    }
    review = _review(client, headers, request)
    command_id = str(uuid4())
    result = _execute(client, headers, request, review, command_id)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "partial"
    assert result.json()["code"] == "settings_save_unconfirmed"
    assert bool(closed) is not already_active
    assert bool(active) is already_active
    assert _tree(data) == before
    receipt = client.get(BASE + "/commands/" + command_id, headers=headers)
    assert receipt.status_code == 200 and receipt.json() == result.json()


@pytest.mark.parametrize(
    "field",
    ["tunnel.start_main", "computer_use.check", "computer_use.disclosure_acknowledged"],
)
def test_remote_settings_hide_webhook_url_and_deny_local_system_actions(
    tmp_path, monkeypatch, field
):
    from row_bot.tunnel import tunnel_manager
    from tests.subsystem.client_protocol.test_protocol_security import client_app

    data = tmp_path / "remote-tunnel"
    data.mkdir()
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data))
    _write(data / "channels_config.json", {"tunnel": {"tunnel_main_app": [True]}})
    monkeypatch.setattr(
        tunnel_manager, "get_url", lambda port: "https://synthetic.ngrok.example"
    )
    local, _, _ = client_app()
    remote, _, _ = client_app(remote=True)
    with local, remote:
        _, local_headers = bootstrap(local)
        remote_handshake, remote_headers = bootstrap(remote)
        local_response = local.get(BASE, headers=local_headers)
        remote_response = remote.get(BASE, headers=remote_headers)
        assert local_response.status_code == remote_response.status_code == 200
        local_tunnel = local_response.json()["system"]["tunnel"]
        remote_tunnel = remote_response.json()["system"]["tunnel"]
        assert local_tunnel["main_app_url"] == "https://synthetic.ngrok.example"
        assert local_tunnel["main_app_enabled"] is True
        assert local_tunnel["local_owner_control_available"] is True
        assert remote_tunnel["main_app_url"] is None
        assert remote_tunnel["local_owner_control_available"] is False
        request = {
            "settings_revision": remote_response.json()["revision"],
            "page": "system",
            "field": field,
            "value": True,
        }
        review = remote.post(BASE + "/review", headers=remote_headers, json=request)
        assert review.status_code == 403
        assert review.json()["code"] == "action_denied"
        command_id = str(uuid4())
        command = remote.post(
            BASE + "/commands",
            headers={**remote_headers, "Idempotency-Key": command_id},
            json={
                "command_id": command_id,
                "client_session_id": remote_handshake["client_session_id"],
                "type": "settings.update",
                "payload": {
                    **request,
                    "action_digest": "a" * 64,
                    "review_id": "b" * 64,
                },
            },
        )
        assert command.status_code == 403
        assert command.json()["code"] == "action_denied"


@pytest.mark.parametrize(
    "field",
    [
        "vectors.rebuild",
        "memory_index.rebuild",
        "local_model.retry",
        "local_model.download",
        "local_model.repair",
    ],
)
def test_document_maintenance_actions_are_reviewed_passive_and_execute_once(
    api, monkeypatch, field
):
    from row_bot.application import settings_commands

    client, headers, data, _ = api
    snapshot = client.get(BASE, headers=headers).json()
    before = _tree(data)
    request = {
        "settings_revision": snapshot["revision"],
        "page": "documents",
        "field": field,
        "value": True,
    }
    review = _review(client, headers, request)
    assert _tree(data) == before
    calls = []
    monkeypatch.setattr(
        settings_commands,
        "_run_documents_action",
        lambda action, root: calls.append((action, root)),
    )
    command_id = str(uuid4())

    result = _execute(client, headers, request, review, command_id)

    assert result.status_code == 200, result.text
    assert result.json()["status"] == "completed"
    assert calls == [(field, data.absolute())]
    assert (
        _execute(client, headers, request, review, command_id).json() == result.json()
    )
    assert calls == [(field, data.absolute())]


def test_tracker_delete_all_is_atomic_and_replays_durable_receipt(api):
    client, headers, data, _ = api
    path = _seed_tracker(data)
    snapshot = client.get(BASE, headers=headers).json()
    request = {
        "settings_revision": snapshot["revision"],
        "page": "tracker",
        "field": "delete_all",
        "value": True,
    }
    review = _review(client, headers, request)
    command_id = str(uuid4())

    response = _execute(client, headers, request, review, command_id)

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "completed"
    assert result["snapshot"]["tracker"]["items"] == []
    assert result["snapshot"]["tracker"]["total_entries"] == 0
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT COUNT(*) FROM trackers").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
    connection.close()
    receipt = client.get(BASE + "/commands/" + command_id, headers=headers)
    assert receipt.status_code == 200 and receipt.json() == result
    assert _execute(client, headers, request, review, command_id).json() == result


def test_tracker_delete_all_rejects_stale_review_without_deleting(api):
    client, headers, data, _ = api
    path = _seed_tracker(data)
    snapshot = client.get(BASE, headers=headers).json()
    request = {
        "settings_revision": snapshot["revision"],
        "page": "tracker",
        "field": "delete_all",
        "value": True,
    }
    review = _review(client, headers, request)
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?)",
        (
            "entry-concurrent",
            "water",
            "2026-01-04T09:00:00",
            "1",
            None,
            "2026-01-04",
        ),
    )
    connection.commit()
    connection.close()

    response = _execute(client, headers, request, review)

    assert response.status_code == 409
    assert response.json()["code"] == "settings_changed"
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT COUNT(*) FROM trackers").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 3
    connection.close()


def test_tracker_delete_all_uncertain_outcome_is_not_replayed(api, monkeypatch):
    from row_bot.application import settings_commands

    client, headers, data, _ = api
    path = _seed_tracker(data)
    snapshot = client.get(BASE, headers=headers).json()
    request = {
        "settings_revision": snapshot["revision"],
        "page": "tracker",
        "field": "delete_all",
        "value": True,
    }
    review = _review(client, headers, request)
    command_id = str(uuid4())
    original = settings_commands._clear_tracker_data
    calls = 0

    def lose_confirmation(root, settings_revision, *, validate):
        nonlocal calls
        calls += 1
        original(root, settings_revision, validate=validate)
        raise RuntimeError("synthetic transport loss after commit")

    monkeypatch.setattr(settings_commands, "_clear_tracker_data", lose_confirmation)

    result = _execute(client, headers, request, review, command_id).json()

    assert result == {
        "command_id": command_id,
        "status": "partial",
        "code": "settings_save_unconfirmed",
        "settings_revision": None,
        "snapshot": None,
    }
    assert calls == 1
    assert _execute(client, headers, request, review, command_id).json() == result
    assert calls == 1
    receipt = client.get(BASE + "/commands/" + command_id, headers=headers)
    assert receipt.status_code == 200 and receipt.json() == result
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT COUNT(*) FROM trackers").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
    connection.close()


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
            "knowledge",
            "memory_enabled",
            False,
            "tools_config.json",
            ("tools", "memory"),
        ),
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


def test_memory_switch_refreshes_the_existing_tool_registry(api, monkeypatch):
    from row_bot.tools import registry

    client, headers, _data, _service = api
    calls = []
    monkeypatch.setattr(registry, "reload_saved_config", lambda: calls.append(True))
    snapshot = client.get(BASE, headers=headers).json()
    request = {
        "settings_revision": snapshot["revision"],
        "page": "knowledge",
        "field": "memory_enabled",
        "value": False,
    }
    response = _execute(client, headers, request, _review(client, headers, request))
    assert response.status_code == 200 and response.json()["status"] == "completed"
    assert calls == [True]
