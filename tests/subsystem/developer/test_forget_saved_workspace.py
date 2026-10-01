"""Remove a saved code folder from Add resource › Open saved, and Undo.

Real Developer registry, bindings and admissions on ``tmp_path``: the entry
is hidden like NiceGUI's "Remove from Developer recents". Files, history and
every conversation using the folder stay as they are.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.developer.test_conversation_creation import _draft, creation  # noqa: F401

pytestmark = pytest.mark.subsystem


def _forget(service, resource_id, revision, *, restore=None, target="resources"):  # noqa: F811
    identity = str(uuid4())
    payload = {"kind": "workspace", "resource_id": resource_id, "expected_resource_revision": revision}
    if restore is not None:
        payload["restore"] = restore
    return service.execute(owner_id="fixture", idempotency_key=identity, command={
        "type": "resource.forget", "command_id": identity, "expected_revision": "0", "payload": payload,
    }, target=target)


def _saved_ids():
    from row_bot.developer.client_workspace import list_workspace_choices

    return [item.resource_id for item in list_workspace_choices().items]


def _bound(service, conversation):  # noqa: F811
    from row_bot.conversation_resources import list_bindings
    from row_bot.developer.storage import get_workspace

    _draft(service, conversation)
    binding = list_bindings(conversation).bindings[0]
    return binding, get_workspace(binding.resource_id)


def test_removing_a_saved_folder_hides_it_and_keeps_files_history_and_bindings(creation):  # noqa: F811
    from row_bot.application.workspace_setup import conversation_workspace
    from row_bot.conversation_resources import describe

    service, conversation, _ = creation
    binding, workspace = _bound(service, conversation)
    folder = Path(workspace.path)
    (folder / "index.html").write_text("<p>date</p>", encoding="utf-8")
    assert _saved_ids() == [workspace.id]

    result = _forget(service, workspace.id, workspace.updated_at)
    assert result == {"command_id": result["command_id"], "status": "completed", "resource_id": workspace.id,
                      "resource_kind": "workspace", "resource_revision": workspace.updated_at}
    assert _saved_ids() == []
    assert (folder / "index.html").read_text(encoding="utf-8") == "<p>date</p>"
    # The conversation keeps working with its folder, at the same revision.
    descriptor = describe(binding)
    assert descriptor.available and descriptor.resource_revision == workspace.updated_at
    [resource] = conversation_workspace(service, conversation)["resources"]
    assert resource["available"] and resource["resource_revision"] == workspace.updated_at


def test_removing_twice_is_harmless_and_undo_puts_it_back(creation):  # noqa: F811
    service, conversation, _ = creation
    _, workspace = _bound(service, conversation)
    _forget(service, workspace.id, workspace.updated_at)
    again = _forget(service, workspace.id, workspace.updated_at)
    assert again["status"] == "completed" and _saved_ids() == []

    restored = _forget(service, workspace.id, again["resource_revision"], restore=True)
    assert restored["status"] == "completed"
    assert _saved_ids() == [workspace.id]
    assert _forget(service, workspace.id, workspace.updated_at, restore=True)["status"] == "completed"
    assert _saved_ids() == [workspace.id]


def test_a_changed_or_unknown_folder_is_refused(creation):  # noqa: F811
    from row_bot.application.client_platform import ClientPlatformError

    service, conversation, _ = creation
    _, workspace = _bound(service, conversation)
    with pytest.raises(ClientPlatformError, match="resource_revision_conflict"):
        _forget(service, workspace.id, "2000-01-01T00:00:00")
    with pytest.raises(ClientPlatformError, match="resource_unavailable"):
        _forget(service, "dev_0000000000000000", workspace.updated_at)
    with pytest.raises(ClientPlatformError, match="invalid_command"):
        _forget(service, workspace.id, workspace.updated_at, target=conversation)
    assert _saved_ids() == [workspace.id]


def test_undo_never_lists_a_worktree(creation, monkeypatch):  # noqa: F811
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.developer import storage, worktrees
    from row_bot.developer.state import DeveloperWorkspace

    service, _, root = creation
    checkout = root / "worktree-checkout"
    checkout.mkdir(parents=True)
    worktree = storage.save_workspace(DeveloperWorkspace(id="dev_worktree", name="Worktree",
                                                         path=str(checkout), hidden=True))
    monkeypatch.setattr(worktrees, "get_worktree_for_workspace",
                        lambda workspace_id: {"worktree_workspace_id": workspace_id})
    with pytest.raises(ClientPlatformError, match="action_denied"):
        _forget(service, worktree.id, worktree.updated_at, restore=True)
    assert storage.get_workspace(worktree.id).hidden is True


def test_forget_is_a_resources_command_on_the_wire(creation):  # noqa: F811
    service, conversation, _ = creation
    _, workspace = _bound(service, conversation)
    with _client(service) as client:
        _, headers = bootstrap(client)

        def send(url, restore=False):
            body = {"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
                    "type": "resource.forget", "expected_revision": "0",
                    "payload": {"kind": "workspace", "resource_id": workspace.id,
                                "expected_resource_revision": workspace.updated_at, "restore": restore}}
            return client.post(url, json=body, headers={**headers, "Idempotency-Key": body["command_id"]})

        removed = send("/api/v1/resources/commands")
        assert removed.status_code == 200, removed.text
        assert removed.json()["resource_id"] == workspace.id
        listed = client.get("/api/v1/resources/library/workspace", headers=headers)
        assert listed.json()["items"] == []
        assert send(f"/api/v1/conversations/{conversation}/commands").status_code == 422
        assert send("/api/v1/resources/commands", restore=True).status_code == 200
        listed = client.get("/api/v1/resources/library/workspace", headers=headers)
        assert [item["resource_id"] for item in listed.json()["items"]] == [workspace.id]
