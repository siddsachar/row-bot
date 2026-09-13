"""Real HTML export, scoped download and interrupted receipt reconciliation."""
# ruff: noqa: F811 -- reused pytest fixtures.
from uuid import uuid4

import pytest

from row_bot.runtime import admissions
from row_bot.designer import export
from tests.subsystem.client_protocol.test_artifact_modes_setup import artifact_service, _create  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_application import _client, _command, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.client_protocol.test_artifact_edit_commands import editing, target
from tests.subsystem.client_platform.test_workspace_setup_integrity import _completed

pytestmark = pytest.mark.subsystem


def prepare(client, headers, mode):
    created = _completed(_create(client, headers, mode))
    state = editing(client, headers, created)
    payload = {"target": target(client, headers, created, state["resource_revision"]), "format": "html", "pages": "all"}
    return created, payload


def url(created, identity):
    return f"/api/v1/conversations/{created['conversation_id']}/artifacts/{created['binding_id']}/exports/{identity}"


@pytest.mark.parametrize("mode", ["deck", "document", "landing", "app_mockup", "storyboard"])
def test_all_modes_explicit_export_and_scoped_download(artifact_service, mode, monkeypatch):
    monkeypatch.setattr(export, "_launch_playwright_browser", lambda *a: pytest.fail("HTML export launched a browser"))
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        created, payload = prepare(client, headers, mode)
        identity, key = str(uuid4()), str(uuid4())
        args = dict(target=created["conversation_id"], revision=created["revision"], command_id=identity, key=key)
        response = _command(client, headers, "artifact.export", payload, **args)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "completed" and response.json()["export_id"] == identity
        assert _command(client, headers, "artifact.export", payload, **args).json() == response.json()
        metadata = client.get(url(created, identity), headers=headers)
        assert metadata.status_code == 200, metadata.text
        value = metadata.json()
        assert value["resource_id"] == created["resource_id"] and value["format"] == "html"
        download = client.get(url(created, identity) + "/download", headers=headers)
        assert download.status_code == 200, download.text
        assert len(download.content) == value["size_bytes"]
        assert download.headers["Content-Disposition"].startswith("attachment;")
        assert download.headers["Content-Security-Policy"].startswith("sandbox;")
        assert download.headers["Cache-Control"] == "no-store"
        assert b"<html" in download.content.lower()
        revoked = client.get(url({**created, "binding_id": "other-binding"}, identity), headers=headers)
        assert revoked.status_code in {403, 404}
        assert len(artifact_service.list_conversations()["items"]) == 1


def test_incomplete_export_receipt_does_not_infer_completion_from_existing_conversation(artifact_service, monkeypatch):
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        created, payload = prepare(client, headers, "deck")
        def broken(*args, **kwargs):
            raise RuntimeError("synthetic renderer failure")
        monkeypatch.setattr(export, "export_html", broken)
        identity = str(uuid4())
        response = _command(client, headers, "artifact.export", payload, target=created["conversation_id"],
                            revision=created["revision"], command_id=identity, key=identity)
        assert response.status_code == 200 and response.json()["status"] == "partial", response.text
        receipt = client.get(f"/api/v1/commands/{identity}", headers=headers)
        assert receipt.status_code == 200 and receipt.json()["status"] == "partial", receipt.text
        assert client.get(url(created, identity), headers=headers).status_code == 409
        assert "_command_type" not in receipt.json()


def test_ready_export_recovers_lost_receipt_without_rerender(artifact_service, monkeypatch):
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        created, payload = prepare(client, headers, "deck")
        identity = str(uuid4())
        original = admissions.complete_command
        def lost(*args, **kwargs):
            raise RuntimeError("synthetic receipt publication loss")
        monkeypatch.setattr(admissions, "complete_command", lost)
        args = dict(target=created["conversation_id"], revision=created["revision"], command_id=identity, key=identity)
        first = _command(client, headers, "artifact.export", payload, **args)
        assert first.status_code == 503
        receipt = client.get(f"/api/v1/commands/{identity}", headers=headers)
        assert receipt.json()["status"] == "admitting", receipt.text
        monkeypatch.setattr(admissions, "complete_command", original)
        monkeypatch.setattr(export, "export_html", lambda *a, **k: pytest.fail("Repeated export effect"))
        replay = _command(client, headers, "artifact.export", payload, **args)
        assert replay.status_code == 200 and replay.json()["status"] == "completed", replay.text


def test_detached_binding_cannot_download_old_export(artifact_service):
    from row_bot.conversation_resources import unbind
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        created, payload = prepare(client, headers, "deck")
        identity = str(uuid4())
        response = _command(client, headers, "artifact.export", payload, target=created["conversation_id"],
                            revision=created["revision"], command_id=identity, key=identity)
        assert response.status_code == 200, response.text
        # Use existing owner to retire only this synthetic binding.
        from row_bot.conversation_resources import list_bindings
        current = list_bindings(created["conversation_id"])
        unbind(created["conversation_id"], created["binding_id"], expected_revision=current.bindings_revision)
        assert client.get(url(created, identity) + "/download", headers=headers).status_code in {403, 404}
