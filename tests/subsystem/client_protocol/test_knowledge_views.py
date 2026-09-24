"""Authenticated saved knowledge projections without mutation or live readiness."""

from __future__ import annotations

import json
import sqlite3

import pytest

from row_bot import knowledge_views as views
from tests.subsystem.knowledge_graph.test_client_read_views import saved, document_store  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem


@pytest.fixture
def api_store(kind, request, monkeypatch):
    store = request.getfixturevalue("saved" if kind == "entities" else "document_store")
    from row_bot.providers import runtime

    monkeypatch.setattr(
        runtime, "provider_status", lambda *a, **k: {"configured": False}
    )
    return store


def read(client, headers, kind, **params):
    response = client.get(f"/api/v1/knowledge/{kind}", headers=headers, params=params)
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    return response.json()


def test_knowledge_graph_requires_auth_and_returns_bounded_snapshot(saved):
    client, service, _ = client_app()
    with client:
        assert client.get("/api/v1/knowledge/graph").status_code == 401
        _, headers = bootstrap(client)
        response = client.get(
            "/api/v1/knowledge/graph", headers=headers, params={"limit": 25}
        )
    assert response.status_code == 200, response.text
    graph = response.json()
    assert len(graph["nodes"]) == 25
    assert graph["total_entities"] == 205 and graph["truncated"] is True
    assert service.commands == []


@pytest.mark.parametrize("kind", ["entities", "documents"])
def test_saved_knowledge_requires_current_session_origin_and_auth(
    api_store, kind, monkeypatch
):
    endpoint = f"/api/v1/knowledge/{kind}"
    client, service, active = client_app(remote=True)
    with client:
        assert client.get(endpoint).status_code == 401
        _, headers = bootstrap(client)
        assert (
            client.get(
                endpoint, headers={"X-Client-Session": headers["X-Client-Session"]}
            ).status_code
            == 403
        )
        assert (
            client.get(
                endpoint, headers={**headers, "Origin": "http://foreign.invalid"}
            ).status_code
            == 403
        )
        assert read(client, headers, kind)["availability"] == "available"
        active["value"] = False
        monkeypatch.setattr(
            views, "_read", lambda *a, **k: pytest.fail("revoked GET reached store")
        )
        assert client.get(endpoint, headers=headers).status_code == 401
        assert service.commands == []


@pytest.mark.parametrize("kind", ["entities", "documents"])
def test_revocation_during_snapshot_does_not_deliver_saved_rows(
    api_store, kind, monkeypatch
):
    client, _, active = client_app(remote=True)
    original = views._read

    def revoke(*args, **kwargs):
        result = original(*args, **kwargs)
        active["value"] = False
        return result

    with client:
        _, headers = bootstrap(client)
        monkeypatch.setattr(views, "_read", revoke)
        response = client.get(f"/api/v1/knowledge/{kind}", headers=headers)
        assert response.status_code == 401
        assert "items" not in response.json()


@pytest.mark.parametrize("kind", ["entities", "documents"])
def test_saved_metadata_is_bounded_private_and_truthful(api_store, kind):
    client, service, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        limit = 80 if kind == "entities" else 2
        page = read(client, headers, kind, limit=limit)
        revision = page["revision"]
        items = page["items"]
        while page["next_cursor"]:
            page = read(client, headers, kind, limit=limit, cursor=page["next_cursor"])
            assert page["revision"] == revision
            items.extend(page["items"])
        assert len(items) == (205 if kind == "entities" else 4)
        assert len({item["id"] for item in items}) == len(items)
        assert all(
            item["semantic_state" if kind == "entities" else "searchability"]
            == "unknown"
            for item in items
        )
        response = client.get(f"/api/v1/knowledge/{kind}", headers=headers)
        assert "private" not in response.text and "properties" not in response.text
        if kind == "entities":
            assert all(
                len(item["description"]) <= 1000 and item["truncated"] for item in items
            )
            filtered = read(client, headers, kind, query="late needle")
            assert (
                filtered["total"] == 1 and filtered["items"][0]["id"] == "entity-0204"
            )
        else:
            assert {item["record_state"] for item in items} == {
                "partial",
                "record_only",
                "job_only",
            }
            assert read(client, headers, kind, status="completed")["total"] == 1
        assert service.commands == []


@pytest.mark.parametrize("kind", ["documents"])
def test_document_api_includes_masked_legacy_markers(api_store, kind):
    del kind
    service, _ = api_store
    (service.data_dir / "processed_files.json").write_text(
        '["/private/legacy/reference.md"]', encoding="utf-8"
    )
    client, command_service, _ = client_app()

    with client:
        _, headers = bootstrap(client)
        page = read(client, headers, "documents", query="reference")

    assert page["total"] == 1
    assert page["items"][0]["name"] == "reference.md"
    assert page["items"][0]["id"].startswith("legacy:")
    assert page["items"][0]["record_state"] == "record_only"
    assert "private" not in json.dumps(page)
    assert command_service.commands == []


@pytest.mark.parametrize("kind", ["entities", "documents"])
def test_changed_metadata_expires_cursor(api_store, kind):
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        page = read(client, headers, kind, limit=2)
        path = api_store.DB_PATH if kind == "entities" else api_store[0].db_path
        with sqlite3.connect(path) as conn:
            conn.execute(
                "UPDATE entities SET subject='Changed' WHERE id='entity-0000'"
                if kind == "entities"
                else "UPDATE document_jobs SET original_name='Changed'"
            )
        response = client.get(
            f"/api/v1/knowledge/{kind}",
            headers=headers,
            params={"limit": 2, "cursor": page["next_cursor"]},
        )
        assert (
            response.status_code == 410 and response.json()["code"] == "cursor_expired"
        )


@pytest.mark.parametrize("kind", ["entities", "documents"])
@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"query": "x" * 257}])
def test_invalid_saved_query_is_rejected(api_store, kind, params):
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        response = client.get(
            f"/api/v1/knowledge/{kind}", headers=headers, params=params
        )
        assert (
            response.status_code == 422
            and response.json()["code"] == "invalid_knowledge_query"
        )


@pytest.mark.parametrize("kind", ["entities", "documents"])
def test_missing_store_does_not_bootstrap_from_get(
    api_store, kind, monkeypatch, tmp_path
):
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        missing = tmp_path / "uncreated"
        monkeypatch.setattr(
            views, "get_memory_db_path", lambda **k: missing / "memory.db"
        )
        monkeypatch.setattr(views, "get_row_bot_data_dir", lambda **k: missing)
        page = read(client, headers, kind)
        assert (
            page["availability"] == "missing"
            and page["total"] is None
            and page["items"] == []
        )
        assert not missing.exists()
