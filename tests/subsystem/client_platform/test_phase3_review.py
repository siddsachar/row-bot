"""Independent Phase 3 review regressions, using real isolated domain owners."""
from __future__ import annotations

import pytest

from tests.subsystem.client_platform.test_workspace_setup_integrity import workspace_service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


@pytest.mark.parametrize("scoped", [False, True])
def test_search_deletion_after_worker_prevents_hit_delivery(workspace_service, monkeypatch, scoped):
    from row_bot import threads
    from row_bot.application import conversation_search
    from row_bot.runtime import admissions
    conversation = threads.create_thread("Search deletion fixture", seed_default_skills=False)
    actual = conversation_search.search
    captured = []
    def close_after_search(*args, **kwargs):
        result = actual(*args, **kwargs)
        captured.append(result)
        admissions.close_admission(conversation)
        return result
    monkeypatch.setattr(conversation_search, "search", close_after_search)
    client = _client(workspace_service)
    try:
        _, headers = bootstrap(client)
        params = {"query": "Search deletion fixture"}
        if scoped:
            params["conversation_id"] = conversation
        response = client.get("/api/v1/search", headers=headers, params=params)
        assert captured[0]["items"]
        if scoped:
            assert response.status_code == 409
            assert response.json()["code"] == "conversation_deleting"
        else:
            assert response.status_code == 200
            assert response.json()["items"] == []
            assert response.json()["scanned_messages"] == captured[0]["scanned_messages"]
            assert response.json()["next_cursor"] == captured[0]["next_cursor"]
    finally:
        client.close()


@pytest.mark.parametrize("query", ["workspace", "open", "history", "draft"])
def test_deletion_after_workspace_composition_prevents_return(workspace_service, monkeypatch, query):
    from row_bot import threads
    from row_bot.application import workspace_setup, conversation_open, conversation_search, conversation_drafts
    from row_bot.runtime import admissions
    conversation = threads.create_thread("Composition deletion", seed_default_skills=False)
    owner, name = {"workspace": (workspace_setup, "conversation_workspace"), "open": (conversation_open, "read_open"),
                   "history": (conversation_search, "history_window"), "draft": (conversation_drafts, "read_draft")}[query]
    actual = getattr(owner, name)
    def close_after_composition(*args, **kwargs):
        result = actual(*args, **kwargs)
        admissions.close_admission(conversation)
        return result
    monkeypatch.setattr(owner, name, close_after_composition)
    client = _client(workspace_service)
    try:
        _, headers = bootstrap(client)
        result = client.get(f"/api/v1/conversations/{conversation}/{query}", headers=headers)
        assert result.status_code == 409
        assert result.json()["code"] == "conversation_deleting"
        assert "controls" not in result.json()
    finally:
        client.close()


@pytest.mark.parametrize("query", ["directory", "file"])
def test_deletion_started_during_workspace_read_prevents_return(workspace_service, tmp_path, monkeypatch, query):
    from row_bot import threads, conversation_resources
    from row_bot.developer import client_workspace
    from row_bot.runtime import admissions
    root = tmp_path / "selected"
    root.mkdir()
    (root / "private.txt").write_text("Fixture content should not return after deletion starts")
    choice = client_workspace.register_existing_folder(
        client_workspace.AuthorizedWorkspaceFolder(root, root, "review-selection")).workspace
    conversation = threads.create_thread("Deletion review", seed_default_skills=False)
    binding = conversation_resources.bind(conversation, "workspace", choice.resource_id, expected_revision="0").bindings[0]
    name = "list_workspace_directory" if query == "directory" else "read_workspace_file"
    actual = getattr(client_workspace, name)
    def begin_deletion_after_read(*args, **kwargs):
        result = actual(*args, **kwargs)
        admissions.close_admission(conversation)
        return result
    monkeypatch.setattr(client_workspace, name, begin_deletion_after_read)
    # Read-only API dispatch needs no producer startup/recovery lifecycle.
    client = _client(workspace_service)
    try:
        _, headers = bootstrap(client)
        response = client.get(f"/api/v1/conversations/{conversation}/workspaces/{binding.binding_id}/{query}",
            headers=headers, params={"path": "private.txt"} if query == "file" else {})
        assert response.status_code in {403, 409}, response.text
        assert "Fixture content" not in response.text
        assert "private.txt" not in response.text
    finally:
        client.close()
