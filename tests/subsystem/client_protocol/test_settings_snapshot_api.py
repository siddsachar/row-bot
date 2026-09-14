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
            generation TEXT,
            vector_error TEXT
        );
        INSERT INTO knowledge_projection_state VALUES (1, 'synthetic', NULL);
        CREATE TABLE knowledge_projection_work (
            entity_id TEXT PRIMARY KEY,
            semantic_pending INTEGER NOT NULL
        );
        INSERT INTO knowledge_projection_work VALUES ('a', 1);
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
