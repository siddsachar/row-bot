"""Real HTML export, scoped save offers and interrupted receipt reconciliation."""
# ruff: noqa: F811 -- reused pytest fixtures.
from uuid import uuid4

import pytest

from row_bot.runtime import admissions
from row_bot.designer import export
from tests.subsystem.client_protocol.test_artifact_modes_setup import artifact_service, _create  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_application import (  # noqa: F401
    _client,
    _command,
    _remote_client,
    service,
)
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


def offer(client, headers, created, identity):
    """Ask to save one export, the one way every download saves (5.3.3)."""
    return client.post("/api/v1/saves", headers=headers, json={"source": {
        "kind": "design_export", "conversation_id": created["conversation_id"],
        "binding_id": created["binding_id"], "export_id": identity}})


@pytest.mark.slow
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
        offered = offer(client, headers, created, identity)
        assert offered.status_code == 200, offered.text
        download = client.get(f"/api/v1/attachments/{offered.json()['reference']}", headers=headers)
        assert download.status_code == 200, download.text
        assert len(download.content) == value["size_bytes"]
        assert download.headers["Content-Disposition"].startswith("attachment;")
        # An HTML export is a file to save, never a page served from the app.
        assert download.headers["Content-Type"] == "application/octet-stream"
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
        assert offer(client, headers, created, identity).status_code in {403, 404}


def test_an_export_saves_once_and_into_exports_while_the_desktop_window_reconnects(artifact_service, monkeypatch, tmp_path):
    from row_bot.application import conversation_creation

    root = (tmp_path / "Row-Bot")
    root.mkdir()
    monkeypatch.setattr(conversation_creation, "configured_workspace_root", lambda: root.resolve())
    monkeypatch.setattr(export, "_launch_playwright_browser", lambda *a: pytest.fail("HTML export launched a browser"))
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        created, payload = prepare(client, headers, "deck")
        identity = str(uuid4())
        made = _command(client, headers, "artifact.export", payload, target=created["conversation_id"],
                        revision=created["revision"], command_id=identity, key=str(uuid4()))
        assert made.status_code == 200, made.text
        offered = offer(client, headers, created, identity)
        assert offered.status_code == 200, offered.text
        reference = offered.json()["reference"]
        saved = client.post(f"/api/v1/attachments/{reference}/save", headers=headers, json={})
        assert saved.status_code == 200, saved.text
        assert saved.json()["folder"] == "Row-Bot › Exports"
        assert (root / "Exports" / saved.json()["file_name"]).is_file()
        assert str(root) not in saved.text
        # An offer is read once.
        assert client.get(f"/api/v1/attachments/{reference}", headers=headers).status_code == 404
