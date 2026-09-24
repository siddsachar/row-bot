"""Authenticated, path-free Wiki API over explicit local folder grants."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from row_bot.api.v1.routes import create_client_platform_app
from row_bot.api.v1.security import ClientSecurity
from row_bot.application.folder_selections import FolderSelections
from tests.integration.wiki_vault.conftest import wiki_stack  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_application import _isolated_service
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem


@pytest.fixture
def api(wiki_stack, monkeypatch):  # noqa: F811
    from row_bot import tasks
    from row_bot.runtime import admissions

    monkeypatch.setattr(tasks, "_DB_PATH", wiki_stack["data_dir"] / "tasks.db")
    monkeypatch.setattr(tasks, "_SCHEMA_READY_PATH", None)
    admissions.instance_identity()
    wiki_stack["wiki_vault"].set_enabled(True)
    clock = [100.0]
    service = _isolated_service()
    security = ClientSecurity(instance_id=service.instance_id, clock=lambda: clock[0])
    picked = [wiki_stack["vault"]]
    selections = FolderSelections(picker=lambda: picked[0], clock=lambda: clock[0])
    app = create_client_platform_app(service, security=security,
        choices=lambda: {"models": [], "capabilities": []}, folder_selections=selections)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        yield wiki_stack, client, headers, clock, picked


def grant(api) -> str:
    _stack, client, headers, _clock, _picked = api
    response = client.post("/api/v1/resources/folder-selection", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["grant_id"]


def review(api, folder_grant: str, action: str, payload: dict) -> dict:
    _stack, client, headers, _clock, _picked = api
    response = client.post("/api/v1/settings/wiki/review", headers=headers,
        json={"action": action, "folder_grant": folder_grant, "payload": payload})
    assert response.status_code == 200, response.text
    return response.json()


def command(api, folder_grant: str, action: str, payload: dict, reviewed: dict,
            *, identity: str | None = None):
    _stack, client, headers, _clock, _picked = api
    identity = identity or str(uuid4())
    return client.post("/api/v1/settings/wiki/commands",
        headers={**headers, "Idempotency-Key": identity}, json={
            "command_id": identity,
            "client_session_id": headers["X-Client-Session"],
            "type": action,
            "expected_revision": "0",
            "payload": {**payload, "review_id": reviewed["review_id"],
                        "folder_grant": folder_grant},
        })


def test_status_is_passive_and_requires_explicit_folder_scope(api):
    stack, client, headers, _clock, _picked = api
    unscoped = client.get("/api/v1/settings/wiki", headers=headers)
    assert unscoped.status_code == 200
    assert unscoped.json()["availability"] == "scope_required"
    selected = grant(api)
    scoped = client.get("/api/v1/settings/wiki", headers=headers,
        params={"folder_grant": selected})
    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["availability"] == "available"
    assert scoped.json()["scope_id"] and str(stack["vault"]) not in scoped.text


def test_local_owner_can_open_only_the_configured_folder_through_typed_result(api, monkeypatch):
    _stack, client, headers, _clock, _picked = api
    from row_bot.application import wiki_commands

    calls = []
    monkeypatch.setattr(
        wiki_commands,
        "open_configured_wiki_folder",
        lambda *, validate: (validate(), calls.append(True), {"status": "opened"})[-1],
    )
    response = client.post("/api/v1/settings/wiki/open-folder", headers=headers)
    assert response.status_code == 200 and response.json() == {"status": "opened"}
    assert calls == [True]


def test_remote_authenticated_owner_cannot_open_a_desktop_folder():
    client, _service, _active = client_app(remote=True)
    with client:
        _handshake, headers = bootstrap(client)
        response = client.post("/api/v1/settings/wiki/open-folder", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "action_denied"


def test_articles_open_without_import_or_path_disclosure(api, monkeypatch):
    stack, client, headers, _clock, _picked = api
    entity = stack["kg"].save_entity(
        "person", "Reviewed API article", "The saved database version is long enough for an article."
    )
    stack["wiki_vault"].export_entities_projection([entity])
    selected = grant(api)
    page = client.get("/api/v1/settings/wiki/articles", headers=headers,
        params={"folder_grant": selected})
    assert page.status_code == 200, page.text
    matching = [item for item in page.json()["items"] if item["entity_id"] == entity["id"]]
    assert matching, page.text
    item = matching[0]
    monkeypatch.setattr(stack["wiki_vault"], "import_from_vault",
        lambda *_a, **_k: pytest.fail("Passive open imported content"))
    opened = client.get(f"/api/v1/settings/wiki/articles/{item['article_id']}",
        headers=headers, params={"folder_grant": selected})
    assert opened.status_code == 200, opened.text
    assert opened.json()["entity_id"] == entity["id"]
    assert str(stack["vault"]) not in page.text + opened.text


def test_reviewed_rebuild_replays_original_receipt_without_republication(api, monkeypatch):
    stack, client, headers, _clock, _picked = api
    stack["kg"].save_entity("person", "Rebuild API article", "Saved database version")
    selected = grant(api)
    status = client.get("/api/v1/settings/wiki", headers=headers,
        params={"folder_grant": selected}).json()
    payload = {"revision": status["revision"]}
    reviewed = review(api, selected, "wiki.rebuild", payload)
    identity = str(uuid4())
    first = command(api, selected, "wiki.rebuild", payload, reviewed, identity=identity)
    assert first.status_code == 200 and first.json()["status"] == "completed", first.text
    monkeypatch.setattr(stack["wiki_vault"], "rebuild_vault",
        lambda **_k: pytest.fail("Original command replayed the rebuild"))
    duplicate = command(api, selected, "wiki.rebuild", payload, reviewed, identity=identity)
    receipt = client.get(f"/api/v1/settings/wiki/commands/{identity}", headers=headers,
        params={"folder_grant": selected})
    assert duplicate.status_code == receipt.status_code == 200
    assert duplicate.json() == receipt.json() == first.json()
    assert str(stack["vault"]) not in receipt.text


def test_expired_folder_grant_blocks_effect_before_admission(api):
    _stack, client, headers, clock, _picked = api
    selected = grant(api)
    status = client.get("/api/v1/settings/wiki", headers=headers,
        params={"folder_grant": selected}).json()
    payload = {"revision": status["revision"], "enabled": False}
    reviewed = review(api, selected, "wiki.configure", payload)
    clock[0] += 301
    response = command(api, selected, "wiki.configure", payload, reviewed)
    assert response.status_code in {403, 409}, response.text
    assert response.json()["code"] in {"capability_revoked", "approval_expired"}


def test_newly_selected_vault_can_be_configured_without_being_read_first(api, tmp_path):
    stack, client, headers, _clock, picked = api
    fresh = tmp_path / "fresh-vault"
    fresh.mkdir()
    # The picker changes explicitly; merely opening status never reconfigures it.
    picked[0] = fresh
    response = client.post("/api/v1/resources/folder-selection", headers=headers)
    assert response.status_code == 200, response.text
    selected = response.json()["grant_id"]
    status = client.get("/api/v1/settings/wiki", headers=headers,
        params={"folder_grant": selected})
    assert status.status_code == 200 and status.json()["availability"] == "available"
    assert Path(stack["wiki_vault"]._load_config()["vault_path"]) != fresh
