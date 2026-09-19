"""Real v1 empty-workspace setup with isolated stores and explicit fake grants."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


@pytest.fixture
def workspace_api(service, tmp_path, monkeypatch):
    from row_bot.api.v1.routes import create_client_platform_app
    from row_bot.application.folder_selections import FolderSelections
    from row_bot.developer import storage
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(storage, "DEVELOPER_DIR", tmp_path / "developer")
    monkeypatch.setattr(storage, "WORKSPACES_PATH", tmp_path / "developer" / "workspaces.json")
    service.readiness_factory = lambda _: False
    def forbidden(*args, **kwargs):
        pytest.fail("Empty creation invoked implicit Developer execution or changed policy")
    for name in ("clone_repository", "create_workspace_thread", "ensure_workspace_thread",
                 "create_thread_worktree", "detect_git_summary", "set_workspace_approval_mode",
                 "set_workspace_execution_settings", "remember_clone_parent_folder"):
        monkeypatch.setattr(storage, name, forbidden)
    monkeypatch.setattr(service, "_start", forbidden)
    parent = tmp_path / "selected-parent"
    parent.mkdir()
    clock = [0.0]
    picked = [parent]
    selections = FolderSelections(picker=lambda: picked[0], clock=lambda: clock[0])
    app = create_client_platform_app(service, choices=lambda: {"models": [], "capabilities": []},
                                    folder_selections=selections)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        yield service, storage, parent, client, headers, clock, picked


def _grant(client, headers):
    response = client.post("/api/v1/resources/folder-selection", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["grant_id"]


def _command(client, headers, payload, *, kind="resource.setup", target=None,
             revision="0", command_id=None, key=None):
    return client.post("/api/v1/resources/commands" if target is None else
                       f"/api/v1/conversations/{target}/commands",
                       headers={**headers, "Idempotency-Key": key or str(uuid4())}, json={
                           "command_id": command_id or str(uuid4()),
                           "client_session_id": headers["X-Client-Session"], "type": kind,
                           "expected_revision": revision, "payload": payload})


def _payload(grant, name="new-project"):
    return {"kind": "workspace", "intent": "create", "folder_grant": grant,
            "empty_workspace": {"folder_name": name}}


def _public(response, parent):
    assert response.status_code == 200, response.text
    assert str(parent) not in response.text
    assert not any(secret in response.text for secret in
                   ("_empty_workspace", "parent_identity", "directory_identity"))
    return response.json()


@contextmanager
def _registration_failure(monkeypatch, storage):
    with monkeypatch.context() as patch:
        def fail(_):
            raise OSError("private registration detail")
        patch.setattr(storage, "save_workspace", fail)
        yield


@pytest.mark.parametrize("in_chat", [False, True])
def test_create_replay_receipt_open_preserve_one_resource_and_origin(workspace_api, in_chat):
    from row_bot import threads, conversation_resources
    from row_bot.runtime import admissions
    service, storage, parent, client, headers, _, _ = workspace_api
    target = threads.create_thread("Existing ordinary chat", seed_default_skills=False) if in_chat else None
    command_id, key = str(uuid4()), str(uuid4())
    payload = _payload(_grant(client, headers))
    first = _public(_command(client, headers, payload, target=target, command_id=command_id, key=key), parent)
    assert first["status"] == "completed"
    assert first["confirmed_stages"] == ["created", "conversation", "associated", "bound"]
    assert first["folder_reselection_required"] is False
    assert target is None or first["conversation_id"] == target
    duplicate = _public(_command(client, headers, payload, target=target, command_id=command_id, key=key), parent)
    receipt = _public(client.get(f"/api/v1/commands/{command_id}", headers=headers), parent)
    assert duplicate == receipt == first
    raw = admissions.receipt(service.instance_id, command_id)
    assert raw["_empty_workspace"]["command_id"] == command_id
    workspace = storage.get_workspace(first["resource_id"])
    assert workspace.origin_conversation_id == first["conversation_id"]
    assert workspace.path == str(parent / "new-project")
    assert list((parent / "new-project").iterdir()) == []
    assert len(storage.list_workspaces()) == len(service.list_conversations()["items"]) == 1
    assert len(conversation_resources.list_bindings(first["conversation_id"]).bindings) == 1
    opened = _public(_command(client, headers, {"kind": "workspace", "intent": "open",
        "resource_id": workspace.id, "expected_resource_revision": workspace.updated_at}), parent)
    assert opened["conversation_id"] == first["conversation_id"]
    assert opened["binding_id"] == first["binding_id"]


@pytest.mark.parametrize("kind", ["empty", "nonempty", "file"])
def test_collision_never_registers_existing_destination(workspace_api, kind):
    service, storage, parent, client, headers, _, _ = workspace_api
    target = parent / "new-project"
    if kind == "file":
        target.write_text("retained")
    else:
        target.mkdir()
        if kind == "nonempty":
            (target / "saved.txt").write_text("retained")
    response = _command(client, headers, _payload(_grant(client, headers)))
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "workspace_destination_exists"
    assert storage.list_workspaces() == []
    assert service.list_conversations()["items"] == []
    assert list(parent.iterdir()) == [target]
    if kind != "empty":
        assert (target if kind == "file" else target / "saved.txt").read_text() == "retained"


@pytest.mark.parametrize("name", ["../escape", "CON.txt", "trail.", "C:\\absolute", "a/b"])
def test_portable_name_error_has_no_persistent_effect(workspace_api, name):
    service, storage, parent, client, headers, _, _ = workspace_api
    response = _command(client, headers, _payload(_grant(client, headers), name))
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "workspace_name_invalid"
    assert list(parent.iterdir()) == []
    assert storage.list_workspaces() == []
    assert service.list_conversations()["items"] == []


def test_registration_failure_recovers_with_renewed_grant_and_original_command(workspace_api, monkeypatch):
    from row_bot.runtime import admissions
    service, storage, parent, client, headers, clock, _ = workspace_api
    command_id, key = str(uuid4()), str(uuid4())
    grant = _grant(client, headers)
    payload = _payload(grant)
    with _registration_failure(monkeypatch, storage):
        partial = _public(_command(client, headers, payload, command_id=command_id, key=key), parent)
    assert partial["status"] == "partial" and partial["code"] == "workspace_registration_failed"
    assert partial["confirmed_stages"] == [] and partial["folder_reselection_required"]
    assert storage.list_workspaces() == []
    original_stat = (parent / "new-project").stat()
    assert _public(_command(client, headers, payload, command_id=command_id, key=key), parent) == partial
    assert _public(client.get(f"/api/v1/commands/{command_id}", headers=headers), parent) == partial
    assert admissions.receipt(service.instance_id, command_id)["_empty_workspace"]["command_id"] == command_id
    clock[0] = 301
    expired = _command(client, headers, {"setup_command_id": command_id, "folder_grant": grant}, kind="resource.continue")
    assert expired.json()["code"] == "capability_revoked"
    continued = _public(_command(client, headers, {"setup_command_id": command_id,
        "folder_grant": _grant(client, headers)}, kind="resource.continue"), parent)
    assert continued["status"] == "completed"
    assert continued["setup_command_id"] == command_id
    assert continued["resource_id"] == partial["resource_id"]
    assert (parent / "new-project").stat().st_ino == original_stat.st_ino
    assert len(storage.list_workspaces()) == len(service.list_conversations()["items"]) == 1


@pytest.mark.parametrize("failure", ["wrong_target", "wrong_parent", "foreign_session", "forged_recovery"])
def test_continuation_cannot_redirect_or_forge_confirmed_creation(workspace_api, monkeypatch, failure, tmp_path):
    from row_bot import threads
    service, storage, parent, client, headers, _, picked = workspace_api
    command_id = str(uuid4())
    with _registration_failure(monkeypatch, storage):
        partial = _public(_command(client, headers, _payload(_grant(client, headers)), command_id=command_id), parent)
    target = None
    if failure == "wrong_parent":
        other = tmp_path / "other-parent"
        other.mkdir()
        picked[0] = other
    elif failure == "wrong_target":
        target = threads.create_thread("Unrelated", seed_default_skills=False)
    payload = {"setup_command_id": command_id, "folder_grant": _grant(client, headers)}
    if failure == "foreign_session":
        _, headers = bootstrap(client)
    elif failure == "forged_recovery":
        payload["_empty_workspace"] = {"resource_id": "fake", "directory_identity": "fake"}
    response = _command(client, headers, payload, target=target, kind="resource.continue")
    assert response.status_code in {403, 409, 422}, response.text
    assert response.json()["code"] == {"wrong_target": "action_denied", "wrong_parent": "workspace_recovery_conflict",
        "foreign_session": "capability_revoked", "forged_recovery": "invalid_command"}[failure]
    assert storage.list_workspaces() == []
    assert list((parent / "new-project").iterdir()) == []
    assert len(list(parent.iterdir())) == 1
    assert partial["resource_id"]


def test_changed_directory_after_partial_never_gets_registered(workspace_api, monkeypatch):
    _, storage, parent, client, headers, _, _ = workspace_api
    command_id = str(uuid4())
    with _registration_failure(monkeypatch, storage):
        _public(_command(client, headers, _payload(_grant(client, headers)), command_id=command_id), parent)
    target = parent / "new-project"
    target.rename(parent / "original-retained")
    target.mkdir()
    response = _command(client, headers, {"setup_command_id": command_id,
        "folder_grant": _grant(client, headers)}, kind="resource.continue")
    result = _public(response, parent)
    assert result["status"] == "partial" and result["code"] == "workspace_recovery_conflict"
    assert storage.list_workspaces() == []
    assert len(list(parent.iterdir())) == 2


def test_binding_failure_continues_original_conversation_without_duplicate(workspace_api, monkeypatch):
    from row_bot import conversation_resources
    from row_bot.application.client_platform import ClientPlatformError
    service, storage, parent, client, headers, _, _ = workspace_api
    command_id = str(uuid4())
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise ClientPlatformError("resource_limit")
        patch.setattr(conversation_resources, "bind", fail)
        partial = _public(_command(client, headers, _payload(_grant(client, headers)), command_id=command_id), parent)
    assert partial["status"] == "partial"
    assert partial["confirmed_stages"] == ["created", "conversation", "associated"]
    workspace = storage.get_workspace(partial["resource_id"])
    assert workspace.origin_conversation_id == partial["conversation_id"]
    continued = _public(_command(client, headers, {"setup_command_id": command_id,
        "folder_grant": _grant(client, headers), "expected_resource_revision": workspace.updated_at},
        kind="resource.continue", target=partial["conversation_id"]), parent)
    assert continued["status"] == "completed"
    assert continued["resource_id"] == partial["resource_id"]
    assert continued["conversation_id"] == partial["conversation_id"]
    assert continued["confirmed_stages"] == ["created", "conversation", "associated", "bound"]
    assert len(storage.list_workspaces()) == len(service.list_conversations()["items"]) == 1


def test_stale_destination_continuation_rejects_before_registering_or_associating(workspace_api, monkeypatch):
    from row_bot import threads
    service, storage, parent, client, headers, _, _ = workspace_api
    target = threads.create_thread("Original target", seed_default_skills=False)
    command_id = str(uuid4())
    with _registration_failure(monkeypatch, storage):
        partial = _public(_command(client, headers, _payload(_grant(client, headers)),
                                  target=target, command_id=command_id), parent)
    assert partial["status"] == "partial" and storage.list_workspaces() == []
    renamed = _command(client, headers, {"title": "Changed target"}, kind="conversation.rename", target=target)
    assert renamed.status_code == 200, renamed.text
    response = _command(client, headers, {"setup_command_id": command_id,
        "folder_grant": _grant(client, headers)}, kind="resource.continue", target=target, revision="0")
    assert response.status_code == 409 and response.json()["code"] == "revision_conflict", response.text
    assert storage.list_workspaces() == []
    assert list((parent / "new-project").iterdir()) == []
    assert len(service.list_conversations()["items"]) == 1


def test_process_interruption_after_directory_creation_is_not_replayed(workspace_api, monkeypatch):
    from row_bot.api.v1.schemas import Command
    from row_bot.developer.client_workspace import AuthorizedWorkspaceFolder
    service, storage, parent, client, headers, _, _ = workspace_api
    class ProcessLost(BaseException):
        pass
    original = os.mkdir
    def create_then_interrupt(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if (Path(path) == parent / "new-project" or
                path == "new-project" and kwargs.get("dir_fd") is not None):
            raise ProcessLost()
        return result
    grant = _grant(client, headers)
    command_id, key = str(uuid4()), str(uuid4())
    import json
    wire = Command.model_validate_json(json.dumps({"command_id": command_id,
        "client_session_id": headers["X-Client-Session"], "type": "resource.setup",
        "expected_revision": "0", "payload": _payload(grant)})).model_dump(mode="json")
    with monkeypatch.context() as patch:
        patch.setattr(os, "mkdir", create_then_interrupt)
        with pytest.raises(ProcessLost):
            service.execute(owner_id=service.instance_id, idempotency_key=key, command=wire,
                            target="resources", authorized_folder=AuthorizedWorkspaceFolder(parent, parent, grant))
    observed = _public(client.get(f"/api/v1/commands/{command_id}", headers=headers), parent)
    assert observed["status"] == "admitting"
    assert not observed.get("resource_id") and "created" not in observed["confirmed_stages"]
    replay = _command(client, headers, _payload(grant), command_id=command_id, key=key)
    assert replay.json()["code"] == "operation_uncertain"
    new_intent = _command(client, headers, _payload(grant))
    assert new_intent.json()["code"] == "workspace_destination_exists"
    assert storage.list_workspaces() == []
    assert len(list(parent.iterdir())) == 1


@pytest.mark.parametrize("bad", ["no_grant", "artifact", "existing_id", "wrong_intent", "deck"])
def test_invalid_empty_setup_is_rejected_before_domain_write(workspace_api, bad):
    service, storage, parent, client, headers, _, _ = workspace_api
    payload = _payload(_grant(client, headers))
    if bad == "no_grant":
        del payload["folder_grant"]
    elif bad == "artifact":
        payload["kind"] = "artifact"
    elif bad == "existing_id":
        payload["resource_id"] = "saved-workspace"
    elif bad == "wrong_intent":
        payload["intent"] = "add"
    else:
        payload["deck"] = {}
    response = _command(client, headers, payload)
    assert response.status_code == 422 and response.json()["code"] == "invalid_command", response.text
    assert storage.list_workspaces() == [] and list(parent.iterdir()) == []
    assert service.list_conversations()["items"] == []


def test_grant_revoked_between_resolution_and_dispatch_creates_nothing(workspace_api, monkeypatch):
    from row_bot.application.folder_selections import FolderSelections
    service, storage, parent, client, headers, clock, _ = workspace_api
    grant = _grant(client, headers)
    resolve = FolderSelections.resolve
    calls = []
    def revoke_after_resolving(owner, *args, **kwargs):
        result = resolve(owner, *args, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            clock[0] = 301
        return result
    monkeypatch.setattr(FolderSelections, "resolve", revoke_after_resolving)
    response = _command(client, headers, _payload(grant))
    assert response.json()["code"] == "capability_revoked", response.text
    assert len(calls) == 1
    assert list(parent.iterdir()) == [] and storage.list_workspaces() == []
    assert service.list_conversations()["items"] == []
