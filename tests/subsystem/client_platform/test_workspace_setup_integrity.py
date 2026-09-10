"""Workspace composition regressions using real metadata and domain owners."""
from __future__ import annotations

from contextlib import closing

import sqlite3
from uuid import UUID, uuid4, uuid5

import pytest

from tests.subsystem.client_protocol.test_protocol_application import _client, _command, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


@pytest.fixture
def workspace_service(service, tmp_path, monkeypatch):
    from row_bot.developer import storage
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(storage, "DEVELOPER_DIR", tmp_path / "developer")
    monkeypatch.setattr(storage, "WORKSPACES_PATH", tmp_path / "developer" / "workspaces.json")
    service.readiness_factory = lambda _: False
    return service


def _register(tmp_path, name="fixture"):
    from row_bot.developer.client_workspace import AuthorizedWorkspaceFolder, register_existing_folder
    folder = tmp_path / name
    folder.mkdir()
    (folder / "source.txt").write_bytes(b"Synthetic private source\n")
    return register_existing_folder(AuthorizedWorkspaceFolder(folder, tmp_path, "fixture-selection")).workspace


def _setup(client, headers, payload, *, target=None, revision="0", kind="resource.setup", key=None, command_id=None):
    endpoint = "/api/v1/resources/commands" if target is None else f"/api/v1/conversations/{target}/commands"
    return client.post(endpoint, headers={**headers, "Idempotency-Key": key or str(uuid4())}, json={
        "command_id": command_id or str(uuid4()), "client_session_id": headers["X-Client-Session"],
        "type": kind, "expected_revision": revision, "payload": payload,
    })


def _completed(response):
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "completed", result
    return result


def test_global_open_keeps_legacy_project_execution_ids_and_single_binding(workspace_service, tmp_path):
    from row_bot import threads, conversation_resources
    project, execution = _register(tmp_path, "project"), _register(tmp_path, "execution")
    conversation = threads.create_thread("Existing code history", thread_type="code",
        project_workspace_id=project.resource_id, developer_workspace_id=execution.resource_id,
        seed_default_skills=False)
    before = conversation_resources.list_bindings(conversation)
    with _client(workspace_service) as client:
        _, headers = bootstrap(client)
        result = _completed(_setup(client, headers, {"kind": "workspace", "intent": "open",
            "resource_id": project.resource_id, "expected_resource_revision": project.revision}))
        assert result["conversation_id"] == conversation
        assert result["binding_id"] == before.bindings[0].binding_id
        assert conversation_resources.list_bindings(conversation) == before
        metadata = workspace_service._metadata(conversation)
        assert metadata["project_workspace_id"] == project.resource_id
        assert metadata["developer_workspace_id"] == execution.resource_id
        assert len(workspace_service.list_conversations()["items"]) == 1


@pytest.mark.parametrize("origin_state", ["available", "missing", "unassociated"])
def test_new_workspace_conversation_preserves_origin_and_has_separate_empty_history(
    workspace_service, tmp_path, monkeypatch, origin_state,
):
    from langchain_core.messages import HumanMessage
    from row_bot import conversation_resources, threads
    from row_bot.application.conversation_search import history_window
    from row_bot.developer import storage
    from row_bot.developer.client_workspace import associate_workspace

    choice = _register(tmp_path)
    origin = None
    if origin_state != "unassociated":
        origin = threads.create_thread("Original workspace history", seed_default_skills=False)
        assert threads.append_checkpoint_messages(origin, [HumanMessage(id="original-message", content="Original private history")])
        choice = associate_workspace(choice.resource_id, origin, choice.revision, None)
        if origin_state == "missing":
            with closing(sqlite3.connect(threads.DB_PATH)) as connection, connection:
                connection.execute("DELETE FROM thread_meta WHERE thread_id=?", (origin,))
    saved = storage.get_workspace(choice.resource_id)

    def forbidden_generation(*args, **kwargs):
        pytest.fail("Creating a workspace conversation must not invoke generation")

    monkeypatch.setattr(workspace_service, "_start", forbidden_generation)
    with _client(workspace_service) as client:
        _, headers = bootstrap(client)
        result = _completed(_setup(client, headers, {
            "kind": "workspace", "intent": "new_conversation", "resource_id": choice.resource_id,
            "expected_resource_revision": choice.revision,
        }))
        conversation = result["conversation_id"]
        assert conversation != origin
        assert result["setup_intent"] == "new_conversation"
        assert result["association_required"] is False
        assert result["confirmed_stages"] == ["conversation", "bound"]
        assert storage.get_workspace(choice.resource_id) == saved
        bindings = conversation_resources.list_bindings(conversation).bindings
        assert len(bindings) == 1 and bindings[0].resource_id == choice.resource_id
        assert bindings[0].binding_id == result["binding_id"]
        assert history_window(workspace_service, conversation)["rows"] == []
        if origin_state == "available":
            assert "Original private history" in str(history_window(workspace_service, origin)["rows"])
            opened = _completed(_setup(client, headers, {
                "kind": "workspace", "intent": "open", "resource_id": choice.resource_id,
                "expected_resource_revision": choice.revision,
            }))
            assert opened["conversation_id"] == origin
        elif origin_state == "missing":
            denied = _setup(client, headers, {
                "kind": "workspace", "intent": "open", "resource_id": choice.resource_id,
                "expected_resource_revision": choice.revision,
            })
            assert denied.status_code == 409
            assert denied.json()["code"] == "origin_repair_required"
            assert storage.get_workspace(choice.resource_id) == saved
        assert (tmp_path / "fixture" / "source.txt").read_bytes() == b"Synthetic private source\n"
        assert [path.name for path in (tmp_path / "fixture").iterdir()] == ["source.txt"]


def test_new_workspace_conversation_response_receipt_and_duplicate_command_reuse_history(workspace_service, tmp_path):
    from row_bot import conversation_resources
    from row_bot.developer import storage

    choice = _register(tmp_path)
    saved = storage.get_workspace(choice.resource_id)
    command_id, key = str(uuid4()), str(uuid4())
    payload = {"kind": "workspace", "intent": "new_conversation", "resource_id": choice.resource_id,
               "expected_resource_revision": choice.revision}
    with _client(workspace_service) as client:
        _, headers = bootstrap(client)
        first = _completed(_setup(client, headers, payload, key=key, command_id=command_id))
        # A client losing the mutation response reconciles the durable receipt.
        receipt = client.get(f"/api/v1/commands/{command_id}", headers=headers)
        assert receipt.status_code == 200, receipt.text
        assert receipt.json() == first
        _, second_headers = bootstrap(client)
        replay = _completed(_setup(client, second_headers, payload, key=key, command_id=command_id))
        assert replay == first
        assert first["conversation_id"] == str(uuid5(UUID(command_id), "conversation"))
        assert len(workspace_service.list_conversations()["items"]) == 1
        assert len(conversation_resources.list_bindings(first["conversation_id"]).bindings) == 1
        another = _completed(_setup(client, headers, payload))
        assert another["conversation_id"] != first["conversation_id"]
        assert len(workspace_service.list_conversations()["items"]) == 2
        assert storage.get_workspace(choice.resource_id) == saved


def test_new_workspace_conversation_partial_bind_continues_without_duplicate(workspace_service, tmp_path, monkeypatch):
    from row_bot import conversation_resources, threads
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.developer import storage
    from row_bot.developer.client_workspace import associate_workspace

    origin = threads.create_thread("Canonical origin", seed_default_skills=False)
    choice = _register(tmp_path)
    choice = associate_workspace(choice.resource_id, origin, choice.revision, None)
    saved = storage.get_workspace(choice.resource_id)
    with _client(workspace_service) as client:
        _, headers = bootstrap(client)
        with monkeypatch.context() as patch:
            def fail_bind(*args, **kwargs):
                raise ClientPlatformError("resource_limit")
            patch.setattr(conversation_resources, "bind", fail_bind)
            response = _setup(client, headers, {
                "kind": "workspace", "intent": "new_conversation", "resource_id": choice.resource_id,
                "expected_resource_revision": choice.revision,
            })
        assert response.status_code == 200, response.text
        partial = response.json()
        assert partial["status"] == "partial" and partial["code"] == "resource_limit"
        target = partial["conversation_id"]
        assert target != origin
        assert partial["confirmed_stages"] == ["conversation"]
        assert len(workspace_service.list_conversations()["items"]) == 2
        command_id, key = str(uuid4()), str(uuid4())
        payload = {"setup_command_id": partial["setup_command_id"], "expected_resource_revision": choice.revision}
        result = _completed(_setup(client, headers, payload, target=target, kind="resource.continue", key=key, command_id=command_id))
        assert result["setup_command_id"] == partial["setup_command_id"]
        assert result["conversation_id"] == target
        assert result["association_required"] is False
        assert result["confirmed_stages"] == ["conversation", "bound"]
        assert _completed(_setup(client, headers, payload, target=target, kind="resource.continue", key=key, command_id=command_id)) == result
        assert len(workspace_service.list_conversations()["items"]) == 2
        assert len(conversation_resources.list_bindings(target).bindings) == 1
        assert storage.get_workspace(choice.resource_id) == saved


@pytest.mark.parametrize("stage", ["conversation", "binding"])
def test_new_workspace_conversation_process_loss_receipt_continues_original_identity(
    workspace_service, tmp_path, monkeypatch, stage,
):
    from row_bot import conversation_resources, threads
    from row_bot.developer import storage

    choice = _register(tmp_path)
    saved = storage.get_workspace(choice.resource_id)
    command_id, key = str(uuid4()), str(uuid4())
    identity = str(uuid5(UUID(command_id), "conversation"))
    owner, function = (threads, "create_thread") if stage == "conversation" else (conversation_resources, "bind")
    actual = getattr(owner, function)

    def save_then_lose_process(*args, **kwargs):
        actual(*args, **kwargs)
        raise SimulatedProcessLoss()

    command = {"command_id": command_id, "client_session_id": str(uuid4()), "type": "resource.setup",
               "expected_revision": "0", "payload": {"kind": "workspace", "intent": "new_conversation",
               "resource_id": choice.resource_id, "expected_resource_revision": choice.revision}}
    with monkeypatch.context() as patch:
        patch.setattr(owner, function, save_then_lose_process)
        with pytest.raises(SimulatedProcessLoss):
            workspace_service.execute(owner_id=workspace_service.instance_id, idempotency_key=key,
                                      command=command, target="resources")
    assert threads._thread_exists(identity)
    before = conversation_resources.list_bindings(identity)
    receipt = workspace_service.receipt(workspace_service.instance_id, command_id)
    assert receipt["status"] == "partial"
    assert receipt["setup_intent"] == "new_conversation"
    assert receipt["association_required"] is False
    assert "associated" not in receipt["confirmed_stages"]
    # Receipt reconciliation is observational, even after a durable stage commit.
    assert conversation_resources.list_bindings(identity) == before
    assert len(workspace_service.list_conversations()["items"]) == 1
    with _client(workspace_service) as client:
        _, headers = bootstrap(client)
        result = _completed(_setup(client, headers, {
            "setup_command_id": command_id, "expected_resource_revision": choice.revision,
        }, target=receipt.get("conversation_id"), revision=before.revision, kind="resource.continue"))
        assert result["conversation_id"] == identity
        assert result["setup_command_id"] == command_id
        assert result["confirmed_stages"] == ["conversation", "bound"]
        assert len(conversation_resources.list_bindings(identity).bindings) == 1
        if stage == "binding":
            assert result["binding_id"] == before.bindings[0].binding_id
        assert len(workspace_service.list_conversations()["items"]) == 1
        assert storage.get_workspace(choice.resource_id) == saved


@pytest.mark.parametrize("invalid", ["conversation_target", "artifact", "missing_id", "missing_revision",
                                     "empty_revision", "deck", "folder_grant", "expected_origin_id"])
def test_new_workspace_conversation_rejects_invalid_wire_and_direct_service_without_mutation(
    workspace_service, tmp_path, monkeypatch, invalid,
):
    from row_bot import conversation_resources, threads
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.developer import storage

    choice = _register(tmp_path)
    payload = {"kind": "workspace", "intent": "new_conversation", "resource_id": choice.resource_id,
               "expected_resource_revision": choice.revision}
    target = None
    if invalid == "conversation_target":
        target = threads.create_thread("Existing target", seed_default_skills=False)
    elif invalid == "artifact":
        payload["kind"] = "artifact"
    elif invalid == "missing_id":
        del payload["resource_id"]
    elif invalid == "missing_revision":
        del payload["expected_resource_revision"]
    elif invalid == "empty_revision":
        payload["expected_resource_revision"] = ""
    else:
        payload[invalid] = {} if invalid == "deck" else "synthetic-inappropriate-value"
    if invalid == "folder_grant":
        from row_bot.application.folder_selections import FolderSelections

        def forbidden_grant_resolution(*args, **kwargs):
            pytest.fail("An invalid new-conversation payload must not resolve a folder grant")

        monkeypatch.setattr(FolderSelections, "resolve", forbidden_grant_resolution)
    saved = storage.list_workspaces()
    conversations = workspace_service.list_conversations()
    with _client(workspace_service) as client:
        _, headers = bootstrap(client)
        response = _setup(client, headers, payload, target=target)
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "invalid_command"
    command = {"command_id": str(uuid4()), "client_session_id": str(uuid4()), "type": "resource.setup",
               "expected_revision": "0", "payload": payload}
    with pytest.raises(ClientPlatformError, match="invalid_command"):
        workspace_service.execute(owner_id=workspace_service.instance_id, idempotency_key=str(uuid4()),
                                  command=command, target=target or "resources")
    assert storage.list_workspaces() == saved
    assert workspace_service.list_conversations() == conversations
    if target:
        assert conversation_resources.list_bindings(target).bindings == ()


@pytest.mark.parametrize("target_mode", ["new", "current"])
def test_missing_origin_requires_explicit_repair_and_resumes_repaired_identity(workspace_service, tmp_path, target_mode):
    from row_bot import threads
    from row_bot.developer import storage
    from row_bot.developer.client_workspace import associate_workspace
    choice = _register(tmp_path)
    missing = threads.create_thread("Removed origin", seed_default_skills=False)
    choice = associate_workspace(choice.resource_id, missing, choice.revision, None)
    with closing(sqlite3.connect(threads.DB_PATH)) as connection, connection:
        connection.execute("DELETE FROM thread_meta WHERE thread_id=?", (missing,))
    target = threads.create_thread("Current chat", seed_default_skills=False) if target_mode == "current" else None
    with _client(workspace_service) as client:
        _, headers = bootstrap(client)
        denied = _setup(client, headers, {"kind": "workspace", "intent": "open",
            "resource_id": choice.resource_id, "expected_resource_revision": choice.revision})
        assert denied.status_code == 409, denied.text
        assert denied.json()["code"] == "origin_repair_required"
        assert storage.get_workspace(choice.resource_id).origin_conversation_id == missing
        repaired = _completed(_setup(client, headers, {"kind": "workspace", "intent": "repair",
            "resource_id": choice.resource_id, "expected_resource_revision": choice.revision,
            "expected_origin_id": missing}, target=target))
        assert repaired["conversation_id"] != missing
        if target:
            assert repaired["conversation_id"] == target
        saved = storage.get_workspace(choice.resource_id)
        assert saved.origin_conversation_id == repaired["conversation_id"]
        opened = _completed(_setup(client, headers, {"kind": "workspace", "intent": "open",
            "resource_id": saved.id, "expected_resource_revision": saved.updated_at}))
        assert opened["conversation_id"] == repaired["conversation_id"]
        assert opened["binding_id"] == repaired["binding_id"]


def test_add_partial_continuation_retains_origin_and_exact_resource(workspace_service, tmp_path, monkeypatch):
    from row_bot import threads, conversation_resources
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.developer import storage
    from row_bot.developer.client_workspace import associate_workspace
    origin = threads.create_thread("Origin", seed_default_skills=False)
    target = threads.create_thread("Current", seed_default_skills=False)
    choice = _register(tmp_path)
    choice = associate_workspace(choice.resource_id, origin, choice.revision, None)
    with _client(workspace_service) as client:
        _, headers = bootstrap(client)
        with monkeypatch.context() as patch:
            def fail_bind(*args, **kwargs):
                raise ClientPlatformError("resource_limit")
            patch.setattr(conversation_resources, "bind", fail_bind)
            partial = _setup(client, headers, {"kind": "workspace", "intent": "add",
                "resource_id": choice.resource_id, "expected_resource_revision": choice.revision}, target=target).json()
        assert partial["status"] == "partial", partial
        assert partial["code"] == "resource_limit"
        assert storage.get_workspace(choice.resource_id).origin_conversation_id == origin
        result = _completed(_setup(client, headers, {"setup_command_id": partial["setup_command_id"],
            "expected_resource_revision": choice.revision}, target=target, kind="resource.continue"))
        assert result["resource_id"] == choice.resource_id
        assert result["conversation_id"] == target
        assert storage.get_workspace(choice.resource_id).origin_conversation_id == origin
        assert len(conversation_resources.list_bindings(target).bindings) == 1


@pytest.mark.parametrize("query", ["directory", "file"])
def test_binding_revoked_during_real_query_does_not_return_read_data(workspace_service, tmp_path, monkeypatch, query):
    from row_bot import threads, conversation_resources
    from row_bot.developer import client_workspace
    choice = _register(tmp_path)
    conversation = threads.create_thread("Reader", seed_default_skills=False)
    snapshot = conversation_resources.bind(conversation, "workspace", choice.resource_id, expected_revision="0")
    binding = snapshot.bindings[0]
    name = "list_workspace_directory" if query == "directory" else "read_workspace_file"
    actual = getattr(client_workspace, name)
    calls = []
    def revoke_after_read(*args, **kwargs):
        result = actual(*args, **kwargs)
        calls.append(result)
        conversation_resources.unbind(conversation, binding.binding_id, expected_revision=snapshot.revision)
        return result
    monkeypatch.setattr(client_workspace, name, revoke_after_read)
    with _client(workspace_service) as client:
        _, headers = bootstrap(client)
        response = client.get(f"/api/v1/conversations/{conversation}/workspaces/{binding.binding_id}/{query}",
            headers=headers, params={"path": "source.txt"} if query == "file" else {})
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "resource_binding_revoked"
        assert calls
        assert "Synthetic private source" not in response.text
        assert "source.txt" not in response.text
        assert str(tmp_path) not in response.text


@pytest.mark.parametrize("query", ["directory", "file"])
def test_traversal_query_is_denied_with_safe_actionable_error(workspace_service, tmp_path, query):
    from row_bot import threads, conversation_resources
    choice = _register(tmp_path)
    (tmp_path / "private.txt").write_text("Never publish this fixture secret")
    conversation = threads.create_thread("Reader", seed_default_skills=False)
    binding = conversation_resources.bind(conversation, "workspace", choice.resource_id, expected_revision="0").bindings[0]
    with _client(workspace_service) as client:
        _, headers = bootstrap(client)
        response = client.get(f"/api/v1/conversations/{conversation}/workspaces/{binding.binding_id}/{query}",
            headers=headers, params={"path" if query == "file" else "directory": "../private.txt"})
        assert "Never publish" not in response.text
        assert str(tmp_path) not in response.text
        if query == "file":
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "denied"
            assert response.json()["text"] == ""
        else:
            assert response.status_code in {403, 422}, response.text
            assert response.json()["retryable"] is False


class SimulatedProcessLoss(BaseException):
    pass


def test_blank_conversation_receipt_recovers_creation_before_completion(workspace_service, monkeypatch):
    from row_bot import threads
    actual = threads.create_thread
    command_id, key = str(uuid4()), str(uuid4())
    def create_then_lose_process(*args, **kwargs):
        actual(*args, **kwargs)
        raise SimulatedProcessLoss()
    command = {"command_id": command_id, "client_session_id": str(uuid4()), "type": "conversation.create",
        "expected_revision": "0", "payload": {}}
    with monkeypatch.context() as patch:
        patch.setattr(threads, "create_thread", create_then_lose_process)
        with pytest.raises(SimulatedProcessLoss):
            workspace_service.execute(owner_id=workspace_service.instance_id, idempotency_key=key,
                command=command, target="conversations")
    identity = str(uuid5(UUID(command_id), "conversation"))
    assert threads._thread_exists(identity)
    receipt = workspace_service.receipt(workspace_service.instance_id, command_id)
    assert receipt.get("conversation_id") == identity, receipt
    assert receipt["status"] == "completed", receipt
    replay = workspace_service.execute(owner_id=workspace_service.instance_id, idempotency_key=key,
        command=command, target="conversations")
    assert replay == receipt
    assert len(workspace_service.list_conversations()["items"]) == 1


def test_reserved_blank_identity_is_not_claimed_created_before_domain_save(workspace_service, monkeypatch):
    from row_bot import threads
    from row_bot.application.client_platform import ClientPlatformError
    command_id, key = str(uuid4()), str(uuid4())
    def lose_before_create(*args, **kwargs):
        raise SimulatedProcessLoss()
    command = {"command_id": command_id, "client_session_id": str(uuid4()), "type": "conversation.create",
        "expected_revision": "0", "payload": {}}
    with monkeypatch.context() as patch:
        patch.setattr(threads, "create_thread", lose_before_create)
        with pytest.raises(SimulatedProcessLoss):
            workspace_service.execute(owner_id=workspace_service.instance_id, idempotency_key=key,
                command=command, target="conversations")
    receipt = workspace_service.receipt(workspace_service.instance_id, command_id)
    assert receipt["status"] == "admitting"
    assert not threads._thread_exists(receipt["conversation_id"])
    with pytest.raises(ClientPlatformError, match="operation_uncertain"):
        workspace_service.execute(owner_id=workspace_service.instance_id, idempotency_key=key,
            command=command, target="conversations")
    assert workspace_service.list_conversations()["items"] == []


def test_global_workspace_saved_before_progress_recovers_and_continues(workspace_service, tmp_path, monkeypatch):
    from row_bot.developer import client_workspace, storage
    folder = tmp_path / "selected"
    folder.mkdir()
    actual = client_workspace.register_existing_folder
    command_id, key = str(uuid4()), str(uuid4())
    def save_then_lose_process(*args, **kwargs):
        actual(*args, **kwargs)
        raise SimulatedProcessLoss()
    command = {"command_id": command_id, "client_session_id": str(uuid4()), "type": "resource.setup",
        "expected_revision": "0", "payload": {"kind": "workspace", "intent": "create", "folder_grant": "fixture"}}
    with monkeypatch.context() as patch:
        patch.setattr(client_workspace, "register_existing_folder", save_then_lose_process)
        with pytest.raises(SimulatedProcessLoss):
            workspace_service.execute(owner_id=workspace_service.instance_id, idempotency_key=key, command=command,
                target="resources", authorized_folder=client_workspace.AuthorizedWorkspaceFolder(folder, tmp_path, "fixture"))
    receipt = workspace_service.receipt(workspace_service.instance_id, command_id)
    identity = storage._workspace_id_for_path(folder)
    assert receipt["resource_id"] == identity
    assert receipt["status"] == "partial"
    assert receipt["confirmed_stages"] == ["created"]
    assert workspace_service.list_conversations()["items"] == []
    with _client(workspace_service) as client:
        _, headers = bootstrap(client)
        result = _completed(_setup(client, headers, {"setup_command_id": command_id,
            "expected_resource_revision": receipt["resource_revision"]}, kind="resource.continue"))
        assert result["resource_id"] == identity
        assert len(storage.list_workspaces()) == 1
        assert len(workspace_service.list_conversations()["items"]) == 1
        assert storage.get_workspace(identity).origin_conversation_id == result["conversation_id"]


def test_origin_saved_before_setup_progress_continues_without_duplicate_history(workspace_service, tmp_path, monkeypatch):
    from row_bot.application import workspace_setup
    from row_bot.developer import client_workspace, storage
    folder = tmp_path / "selected"
    folder.mkdir()
    actual = workspace_setup._associate
    command_id, key = str(uuid4()), str(uuid4())
    def associate_then_lose_process(*args, **kwargs):
        actual(*args, **kwargs)
        raise SimulatedProcessLoss()
    command = {"command_id": command_id, "client_session_id": str(uuid4()), "type": "resource.setup",
        "expected_revision": "0", "payload": {"kind": "workspace", "intent": "create", "folder_grant": "fixture"}}
    with monkeypatch.context() as patch:
        patch.setattr(workspace_setup, "_associate", associate_then_lose_process)
        with pytest.raises(SimulatedProcessLoss):
            workspace_service.execute(owner_id=workspace_service.instance_id, idempotency_key=key, command=command,
                target="resources", authorized_folder=client_workspace.AuthorizedWorkspaceFolder(folder, tmp_path, "fixture"))
    receipt = workspace_service.receipt(workspace_service.instance_id, command_id)
    saved = storage.get_workspace(receipt["resource_id"])
    assert saved.origin_conversation_id == receipt["conversation_id"]
    with _client(workspace_service) as client:
        _, headers = bootstrap(client)
        result = _completed(_setup(client, headers, {"setup_command_id": command_id,
            "expected_resource_revision": saved.updated_at}, target=receipt["conversation_id"], kind="resource.continue"))
        assert result["conversation_id"] == receipt["conversation_id"]
        assert result["resource_id"] == saved.id
        assert len(workspace_service.list_conversations()["items"]) == 1
