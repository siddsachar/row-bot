"""The folder cards' commands (B277): Add resource's reviewed path, from the chat.

A card pauses the turn on an approval; the person's pick sends the same
``resource.setup`` command Add resource sends (local owner only, with the
picker's grant), then the approval resumes the turn, which works in the
folder at once. Stores are isolated and the model is a scripted stream.
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import _native_proof, bootstrap, client_app

pytestmark = pytest.mark.subsystem

REPO = "https://github.com/example/demo.git"


def _post(client, headers, target, kind, payload, revision="0"):
    identity = str(uuid4())
    path = "/api/v1/resources/commands" if target is None else f"/api/v1/conversations/{target}/commands"
    return client.post(path, headers={**headers, "Idempotency-Key": identity}, json={
        "command_id": identity, "client_session_id": headers["X-Client-Session"], "type": kind,
        "expected_revision": revision, "payload": payload})


def test_a_desktop_pick_for_a_clone_is_accepted_for_that_clone(tmp_path):
    """The desktop picker's grant for "Choose where" reaches the reviewed clone setup."""
    client, service, _ = client_app()
    with client:
        proof, headers = _native_proof(client)
        selected = client.post("/api/v1/native/selections/complete", headers={"Origin": "http://localhost"}, json={
            **proof, "selection_kind": "folder", "intent_id": str(uuid4()), "intent": "resource_setup",
            "conversation_id": "conversation-a", "destination": "workspace:clone_repository",
            "path": str(tmp_path)})
        assert selected.status_code == 200, selected.text
        response = _post(client, headers, "conversation-a", "resource.setup", {
            "kind": "workspace", "intent": "create", "folder_grant": selected.json()["reference"],
            "clone_workspace": {"repo_url": REPO}})
        assert response.status_code == 200, response.text
        assert service.commands[-1]["authorized_folder"].path == tmp_path
        assert service.commands[-1]["command"]["payload"]["clone_workspace"] == {"repo_url": REPO}


def test_a_card_pick_is_local_owner_only_and_needs_the_pickers_grant():
    remote, remote_service, _ = client_app(remote=True)
    with remote:
        _, headers = bootstrap(remote)
        denied = _post(remote, headers, "conversation-a", "resource.setup",
                       {"kind": "workspace", "intent": "create", "folder_grant": "a" * 43})
        assert denied.status_code == 403 and denied.json()["code"] == "action_denied"
    assert remote_service.commands == []
    local, local_service, _ = client_app()
    with local:
        _, headers = bootstrap(local)
        forged = _post(local, headers, "conversation-a", "resource.setup",
                       {"kind": "workspace", "intent": "create", "folder_grant": "a" * 43})
        assert forged.status_code == 409 and forged.json()["code"] == "capability_revoked"
    assert local_service.commands == []


def test_the_picked_folder_is_bound_and_the_paused_turn_goes_on_in_it(service, tmp_path, monkeypatch):  # noqa: F811
    import langgraph.types
    from langchain_core.messages import AIMessage
    from row_bot import threads
    from row_bot.api.v1.routes import create_client_platform_app
    from row_bot.application.folder_selections import FolderSelections
    from row_bot.conversation_resources import current_execution_context
    from row_bot.developer import storage
    from row_bot.tools import registry
    from row_bot.tools.conversation_setup_tool import use_code_folder

    monkeypatch.setattr(storage, "DEVELOPER_DIR", tmp_path / "developer")
    monkeypatch.setattr(storage, "WORKSPACES_PATH", tmp_path / "developer" / "workspaces.json")
    monkeypatch.setattr(registry, "is_enabled", lambda name: name == "developer")
    picked = tmp_path / "tide-app"
    picked.mkdir()
    (picked / "index.html").write_text("<p>tides</p>", encoding="utf-8")
    seen: list = []

    class Paused(Exception):
        pass

    def stream(_text, _enabled, _config, *, stop_event):
        card: dict = {}

        def pause(payload):
            card.update(payload)
            raise Paused

        with monkeypatch.context() as patch:
            patch.setattr(langgraph.types, "interrupt", pause)
            with pytest.raises(Paused):
                use_code_folder("tide app")
        yield "interrupt", [{"__interrupt_id": "folder-card", **card}]

    def resume(_enabled, _config, approved, *, interrupt_ids=None, stop_event=None):
        # The tool runs again from the top when its turn resumes.
        with monkeypatch.context() as patch:
            patch.setattr(langgraph.types, "interrupt", lambda _payload: pytest.fail("the card came back"))
            result = json.loads(use_code_folder("tide app"))
        seen.append((approved, result, [item.resource_id for item in current_execution_context().bindings]))
        identity = str(uuid4())
        threads.append_checkpoint_messages(conversation, [AIMessage(id=identity, content="Working in it.")])
        yield "output_binding", {"native_message_id": identity,
                                 "checkpoint_revision": threads.get_latest_checkpoint_revision(conversation)}
        yield "done", "Working in it."

    service.stream_factory, service.resume_factory = stream, resume
    app = create_client_platform_app(service, choices=lambda: {"models": [], "capabilities": []},
                                     folder_selections=FolderSelections(picker=lambda: picked))
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        created =client.post("/api/v1/conversations/commands", headers={**headers, "Idempotency-Key": str(uuid4())},
                              json={"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
                                    "type": "conversation.create", "expected_revision": "0",
                                    "payload": {"title": "Tides"}})
        conversation = created.json()["conversation_id"]
        submitted = _post(client, headers, conversation, "conversation.submit", {
            "submission_id": str(uuid4()), "text": "Work on my tide app",
            "model_selection": {"provider_id": "fixture", "model_ref": "fixture::model"}})
        assert submitted.status_code == 202, submitted.text
        handle = service.registry.get(submitted.json()["execution_id"])
        assert handle.producer_done.wait(5)
        view = client.get(f"/api/v1/approvals/{handle.approval_id}", headers=headers)
        assert view.status_code == 200, view.text
        assert view.json()["setup"] == {"kind": "folder", "label": "Use an existing folder", "folders": []}
        revision = str(service._metadata(conversation)["client_revision"])
        # Nothing is registered without the person's pick.
        ungranted = _post(client, headers, conversation, "resource.setup",
                          {"kind": "workspace", "intent": "create"}, revision)
        assert ungranted.status_code == 409 and ungranted.json()["code"] == "capability_revoked"
        assert storage.list_workspaces() == []
        # Choose folder: the picker's grant, then Add resource's own setup command.
        grant = client.post("/api/v1/resources/folder-selection", headers=headers).json()["grant_id"]
        setup = _post(client, headers, conversation, "resource.setup",
                      {"kind": "workspace", "intent": "create", "folder_grant": grant}, revision)
        assert setup.status_code == 200, setup.text
        assert setup.json()["status"] == "completed" and str(picked) not in setup.text
        resolved = client.post(f"/api/v1/approvals/{handle.approval_id}/commands",
                               headers={**headers, "Idempotency-Key": str(uuid4())}, json={
                                   "command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
                                   "type": "approval.resolve", "expected_revision": view.json()["revision"],
                                   "payload": {"decision": "approve", "nonce": view.json()["nonce"]}})
        assert resolved.status_code == 202, resolved.text
        assert service.registry.get(resolved.json()["execution_id"]).producer_done.wait(5)
    approved, result, captured = seen[0]
    assert approved is True and result["kind"] == "resource_bound" and result["name"] == "tide-app"
    assert captured == [result["resource_id"]], "the resumed turn works in the picked folder"
    assert "ready now" in result["next"]
    assert (picked / "index.html").read_text(encoding="utf-8") == "<p>tides</p>"
