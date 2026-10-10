"""Delete a design for good from its panel.

Real resource, designer, thread and admission owners on ``tmp_path``. Delete
leaves every conversation using the design first, then its files go; no
conversation is ever deleted, even one an old-style design owned.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.developer.test_conversation_creation import creation  # noqa: F401

pytestmark = pytest.mark.subsystem


def _command(service, target, kind, payload):  # noqa: F811
    identity = str(uuid4())
    revision = "0" if target == "resources" else str(service._metadata(target)["client_revision"])
    return service.execute(owner_id="fixture", idempotency_key=identity, command={
        "type": kind, "command_id": identity, "expected_revision": revision, "payload": payload,
    }, target=target)


def _design(service, conversation, name="QA test deck"):  # noqa: F811
    from row_bot.conversation_resources import list_bindings

    created = _command(service, conversation, "resource.setup", {
        "kind": "artifact", "intent": "create", "artifact": {"mode": "deck", "name": name}})
    assert created["status"] == "completed"
    binding = next(item for item in list_bindings(conversation).bindings
                   if item.binding_id == created["binding_id"])
    return binding


def _revision(design_id):
    from row_bot.designer.storage import get_project_metadata

    return get_project_metadata(design_id)["updated_at"]


def _saved_ids():
    from row_bot.designer.client_service import list_artifacts

    return [item.id for item in list_artifacts().items]


def _artifacts(conversation):
    from row_bot.conversation_resources import list_bindings

    return [item.resource_id for item in list_bindings(conversation).bindings if item.kind == "artifact"]


def _delete(service, conversation, binding, revision=None):  # noqa: F811
    return _command(service, conversation, "resource.delete", {
        "binding_id": binding.binding_id,
        "expected_resource_revision": revision or _revision(binding.resource_id)})


def test_delete_leaves_every_conversation_then_removes_the_design_and_keeps_the_conversations(
        creation, monkeypatch):  # noqa: F811
    from row_bot import threads
    from row_bot.conversation_resources import bind
    from row_bot.designer import storage

    service, first, _ = creation  # noqa: F811
    binding = _design(service, first)
    design = binding.resource_id
    other = threads.create_thread("Also uses the deck")
    bind(other, "artifact", design, expected_revision=int(service._metadata(other)["client_revision"]))
    # An old Designer conversation: linked by project_id only, and owning the
    # design the old way, which ``delete_project`` would delete with it.
    legacy = threads.create_thread("Old design chat")
    threads._set_thread_project_id(legacy, design)
    project = storage.load_project(design)
    project.thread_id, project.thread_ownership = legacy, "legacy"
    storage.save_project(project)
    storage.save_asset_bytes(design, "logo", "logo.png", b"png")
    unrelated = _design(service, first, "Keep me")
    published = []
    publish = service.projection.publish
    monkeypatch.setattr(service.projection, "publish",
                        lambda target, kind, data: (published.append((target, kind)), publish(target, kind, data)))
    assert set(_saved_ids()) == {design, unrelated.resource_id}

    receipt = _delete(service, first, binding)

    assert receipt["status"] == "completed" and receipt["resource_id"] == design
    assert receipt["revision"] == str(service._metadata(first)["client_revision"])
    assert storage.load_project(design) is None
    assert not (storage.ASSETS_DIR / design).exists()
    assert _saved_ids() == [unrelated.resource_id]
    for conversation in (first, other, legacy):
        assert threads._thread_exists(conversation), "the conversation stays"
        assert design not in _artifacts(conversation)
        assert (conversation, "resource.changed") in published
    assert _artifacts(first) == [unrelated.resource_id], "other designs stay bound"
    # Nothing names the deleted design, so the start-up sweep keeps them all.
    assert threads.sweep_orphan_project_ids() == 0
    assert all(threads._thread_exists(item) for item in (first, other, legacy))


def test_delete_is_refused_while_a_conversation_using_the_design_is_running(creation, monkeypatch):  # noqa: F811
    from row_bot import threads
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.conversation_resources import bind
    from row_bot.designer import storage

    service, first, _ = creation  # noqa: F811
    binding = _design(service, first)
    other = threads.create_thread("Busy with the deck")
    bind(other, "artifact", binding.resource_id,
         expected_revision=int(service._metadata(other)["client_revision"]))
    active = service.registry.active
    monkeypatch.setattr(service.registry, "active", lambda *args: args == (other,) or active(*args))
    with pytest.raises(ClientPlatformError, match="generation_active"):
        _delete(service, first, binding)
    assert storage.load_project(binding.resource_id) is not None
    assert _artifacts(first) == _artifacts(other) == [binding.resource_id]


def test_delete_needs_the_design_as_it_was_shown_and_only_deletes_designs(creation):  # noqa: F811
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.conversation_resources import list_bindings
    from row_bot.designer import storage
    from tests.subsystem.developer.test_conversation_creation import _draft

    service, first, _ = creation  # noqa: F811
    binding = _design(service, first)
    with pytest.raises(ClientPlatformError, match="resource_revision_conflict"):
        _delete(service, first, binding, revision="2000-01-01T00:00:00")
    assert storage.load_project(binding.resource_id) is not None
    _draft(service, first)
    folder = next(item for item in list_bindings(first).bindings if item.kind == "workspace")
    with pytest.raises(ClientPlatformError, match="invalid_resource"):
        _command(service, first, "resource.delete", {"binding_id": folder.binding_id,
                                                     "expected_resource_revision": "x"})
    assert [item.kind for item in list_bindings(first).bindings] == ["artifact", "workspace"]


def test_files_that_cannot_be_deleted_leave_the_design_in_open_saved_and_delete_again_finishes(
        creation, monkeypatch):  # noqa: F811
    from row_bot import threads
    from row_bot.designer import storage

    service, first, _ = creation  # noqa: F811
    binding = _design(service, first)
    design = binding.resource_id
    storage.save_asset_bytes(design, "logo", "logo.png", b"png")
    delete_assets = storage.delete_project_assets

    def locked(_project_id):
        raise PermissionError("a file is open elsewhere")

    monkeypatch.setattr(storage, "delete_project_assets", locked)
    receipt = _delete(service, first, binding)
    assert receipt["status"] == "partial" and receipt["code"] == "design_files_remain"
    assert design not in _artifacts(first) and threads._thread_exists(first)
    # Its record stays, so it can be found and opened again.
    assert _saved_ids() == [design]
    assert storage.load_project(design) is not None

    monkeypatch.setattr(storage, "delete_project_assets", delete_assets)
    again = _command(service, first, "resource.setup", {
        "kind": "artifact", "intent": "add", "resource_id": design,
        "expected_resource_revision": _revision(design)})
    assert again["status"] == "completed"
    reopened = next(item for item in _bindings(first) if item.resource_id == design)
    assert _delete(service, first, reopened)["status"] == "completed"
    assert _saved_ids() == [] and storage.load_project(design) is None
    assert not (storage.ASSETS_DIR / design).exists()


def _bindings(conversation):
    from row_bot.conversation_resources import list_bindings

    return list_bindings(conversation).bindings


def test_delete_travels_on_the_conversation_route_and_repeats_safely(creation):  # noqa: F811
    service, first, _ = creation  # noqa: F811
    binding = _design(service, first)
    with _client(service) as client:
        _, headers = bootstrap(client)

        def send(url, kind, payload, revision="0", command_id=None):
            body = {"command_id": command_id or str(uuid4()), "client_session_id": headers["X-Client-Session"],
                    "type": kind, "expected_revision": revision, "payload": payload}
            return client.post(url, json=body, headers={**headers, "Idempotency-Key": body["command_id"]})

        delete = {"binding_id": binding.binding_id, "expected_resource_revision": _revision(binding.resource_id)}
        assert send("/api/v1/resources/commands", "resource.delete", delete).status_code == 422
        revision, command_id = str(service._metadata(first)["client_revision"]), str(uuid4())
        deleted = send(f"/api/v1/conversations/{first}/commands", "resource.delete", delete, revision, command_id)
        assert deleted.status_code == 200, deleted.text
        # A repeat of the same request answers from its receipt; nothing runs twice.
        again = send(f"/api/v1/conversations/{first}/commands", "resource.delete", delete, revision, command_id)
        assert again.status_code == 200 and again.json() == deleted.json()
        listed = client.get("/api/v1/resources/library/artifact", headers=headers)
        assert listed.json()["items"] == []
