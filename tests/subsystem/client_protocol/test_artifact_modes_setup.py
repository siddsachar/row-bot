"""Five-mode resource setup through real protocol, receipts and domain stores."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from uuid import uuid4

import pytest

from row_bot.designer import client_service as artifacts, storage
from row_bot.designer.state import DESIGNER_MODES
from tests.subsystem.client_platform.test_workspace_setup_integrity import _completed, _setup
from tests.subsystem.client_protocol.test_protocol_application import _client, _command, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem
MODES = tuple(DESIGNER_MODES)


@pytest.fixture
def artifact_service(service, tmp_path, monkeypatch):  # noqa: F811 - reused pytest fixture
    from row_bot import threads
    import row_bot.designer.session as session

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(threads, "_THREAD_UI_DIR", tmp_path / "thread-ui")
    for key, path in (("DESIGNER_DIR", tmp_path / "designer"),
                      ("PROJECTS_DIR", tmp_path / "designer" / "projects"),
                      ("ASSETS_DIR", tmp_path / "designer" / "assets"),
                      ("REFERENCES_DIR", tmp_path / "designer" / "references")):
        monkeypatch.setattr(storage, key, path)
    service.readiness_factory = lambda _: False

    def forbidden(*args, **kwargs):
        pytest.fail("Resource setup must not invoke generation or select a Studio session")

    monkeypatch.setattr(service, "_start", forbidden)
    monkeypatch.setattr(session, "set_active_project", forbidden)
    return service


def _create(client, headers, mode, **kwargs):
    return _setup(client, headers, {"kind": "artifact", "intent": "create", "artifact": {"mode": mode}}, **kwargs)


def _existing(project, intent="open"):
    return {"kind": "artifact", "intent": intent, "resource_id": project.id,
            "expected_resource_revision": project.updated_at}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("destination", ["global", "current"])
def test_all_modes_minimum_setup_create_preview_and_canonical_reopen(artifact_service, mode, destination):
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        target = (_command(client, headers, "conversation.create", {"title": "Ordinary chat"}).json()["conversation_id"]
                  if destination == "current" else None)
        options = client.get(f"/api/v1/resources/setup/artifact/{mode}", headers=headers)
        assert options.status_code == 200, options.text
        defaults = options.json()
        assert defaults["mode"] == mode
        assert defaults["default_template"] in {item["id"] for item in defaults["templates"]}
        assert defaults["default_canvas"] in {item["id"] for item in defaults["canvases"]}
        created = _completed(_create(client, headers, mode, target=target))
        conversation = created["conversation_id"]
        if target:
            assert conversation == target
        project = artifacts.read_artifact(created["resource_id"])
        assert project.mode == mode
        assert project.template_id == defaults["default_template"]
        assert project.aspect_ratio == defaults["default_canvas"]
        assert project.name == defaults["default_name"] and project.brief is None
        assert project.thread_id == conversation and project.thread_ownership == "resume"
        preview_url = f"/api/v1/conversations/{conversation}/artifacts/{created['binding_id']}/preview"
        preview = client.get(preview_url, headers=headers)
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["mode"] == mode and body["html"] and not body["unchanged"]
        assert body["scripts_allowed"] is (mode in {"landing", "app_mockup", "storyboard"})
        unchanged = client.get(preview_url, headers=headers, params={"known_revision": body["preview_revision"]})
        assert unchanged.status_code == 200, unchanged.text
        assert unchanged.json()["unchanged"] and unchanged.json()["html"] is None
        for _ in range(2):
            reopened = _completed(_setup(client, headers, _existing(project)))
            assert reopened["conversation_id"] == conversation
            assert reopened["binding_id"] == created["binding_id"]
        assert len(artifact_service.list_conversations()["items"]) == 1
        library = client.get("/api/v1/resources/library/artifact", headers=headers)
        assert library.status_code == 200, library.text
        assert library.json()["items"][0]["available"]


@pytest.mark.parametrize("mode", MODES)
def test_create_response_loss_and_cross_session_retry_keep_one_identity(artifact_service, mode):
    command_id, key = str(uuid4()), str(uuid4())
    with _client(artifact_service) as client:
        _, first_headers = bootstrap(client)
        first = _completed(_create(client, first_headers, mode, key=key, command_id=command_id))
        receipt = client.get(f"/api/v1/commands/{command_id}", headers=first_headers)
        assert receipt.status_code == 200 and receipt.json() == first
        _, other_headers = bootstrap(client)
        replay = _completed(_create(client, other_headers, mode, key=key, command_id=command_id))
        assert replay == first
        assert len(list(storage.PROJECTS_DIR.glob("*.json"))) == 1
        assert len(artifact_service.list_conversations()["items"]) == 1


@pytest.mark.parametrize("mode", MODES)
def test_add_existing_preserves_origin_history_draft_and_conversation_controls(artifact_service, mode):
    from langchain_core.messages import HumanMessage
    from row_bot import threads
    from row_bot.application.conversation_search import history_window

    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        first = _completed(_create(client, headers, mode))
        project = artifacts.read_artifact(first["resource_id"])
        origin = first["conversation_id"]
        target = _command(client, headers, "conversation.create", {"title": "Keep current chat"}).json()["conversation_id"]
        assert threads.append_checkpoint_messages(target, [HumanMessage(id="existing-row", content="Existing conversation")])
        threads._set_thread_model_override(target, "fixture::chosen-model")
        threads._set_thread_approval_mode(target, "block")
        threads.save_thread_draft(target, "Keep my unsent draft", source="fixture")
        saved_bytes = (storage.PROJECTS_DIR / f"{project.id}.json").read_bytes()
        metadata = artifact_service._metadata(target)
        draft = client.get(f"/api/v1/conversations/{target}/draft", headers=headers).json()
        added = _completed(_setup(client, headers, _existing(project, "add"), target=target,
                                  revision=str(metadata["client_revision"])))
        assert added["conversation_id"] == target and added["resource_id"] == project.id
        assert (storage.PROJECTS_DIR / f"{project.id}.json").read_bytes() == saved_bytes
        assert artifacts.read_artifact(project.id).thread_id == origin
        assert client.get(f"/api/v1/conversations/{target}/draft", headers=headers).json() == draft
        after = artifact_service._metadata(target)
        for field in ("model_override", "approval_mode", "agent_profile_id", "client_runtime_mode"):
            assert after[field] == metadata[field]
        assert "Existing conversation" in str(history_window(artifact_service, target)["rows"])
        assert _completed(_setup(client, headers, _existing(project)))["conversation_id"] == origin
        assert len(artifact_service.list_conversations()["items"]) == 2


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("stage", ["association", "binding"])
def test_partial_create_continues_only_failed_stage_with_original_resource(artifact_service, monkeypatch, mode, stage):
    from row_bot import conversation_resources
    from row_bot.application.client_platform import ClientPlatformError

    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        target = _command(client, headers, "conversation.create", {"title": "Captured chat"}).json()["conversation_id"]

        def fail(*args, **kwargs):
            raise ClientPlatformError("resource_limit")

        with monkeypatch.context() as patch:
            patch.setattr(artifacts if stage == "association" else conversation_resources,
                          "associate_origin" if stage == "association" else "bind", fail)
            response = _create(client, headers, mode, target=target)
        assert response.status_code == 200, response.text
        partial = response.json()
        assert partial["status"] == "partial" and partial["code"] == "resource_limit"
        project = artifacts.read_artifact(partial["resource_id"])
        assert project.mode == mode
        assert partial["conversation_id"] == target
        assert "created" in partial["confirmed_stages"]
        assert ("associated" in partial["confirmed_stages"]) is (stage == "binding")
        with monkeypatch.context() as patch:
            patch.setattr(artifacts, "create_artifact", lambda *a, **kw: pytest.fail("Continuation recreated artifact"))
            result = _completed(_setup(client, headers, {
                "setup_command_id": partial["setup_command_id"], "expected_resource_revision": project.updated_at,
            }, target=target, kind="resource.continue"))
        assert result["resource_id"] == project.id and result["conversation_id"] == target
        assert result["confirmed_stages"] == ["created", "conversation", "associated", "bound"]
        assert len(list(storage.PROJECTS_DIR.glob("*.json"))) == 1
        assert len(artifact_service.list_conversations()["items"]) == 1
        assert len(conversation_resources.list_bindings(target).bindings) == 1


@pytest.mark.parametrize("mode", MODES)
def test_missing_origin_requires_explicit_repair_without_losing_artifact(artifact_service, mode):
    from row_bot import threads

    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        created = _completed(_create(client, headers, mode))
        project = artifacts.read_artifact(created["resource_id"])
        missing = project.thread_id
        with closing(sqlite3.connect(threads.DB_PATH)) as connection, connection:
            connection.execute("DELETE FROM thread_meta WHERE thread_id=?", (missing,))
        before = (storage.PROJECTS_DIR / f"{project.id}.json").read_bytes()
        denied = _setup(client, headers, _existing(project))
        assert denied.status_code == 409 and denied.json()["code"] == "origin_repair_required"
        assert (storage.PROJECTS_DIR / f"{project.id}.json").read_bytes() == before
        repaired = _completed(_setup(client, headers, {**_existing(project, "repair"), "expected_origin_id": missing}))
        assert repaired["conversation_id"] != missing
        saved = artifacts.read_artifact(project.id)
        assert saved.mode == mode and saved.thread_id == repaired["conversation_id"]
        assert saved.thread_ownership == "resume"
        reopened = _completed(_setup(client, headers, _existing(saved)))
        assert reopened["conversation_id"] == repaired["conversation_id"]
        assert not threads._thread_exists(missing)


@pytest.mark.parametrize("payload,code", [
    ({"artifact": {"mode": "app"}}, "invalid_command"),
    ({"artifact": {"mode": "future"}}, "invalid_command"),
    ({"artifact": {"mode": "document", "unknown": True}}, "invalid_command"),
    ({"artifact": {"mode": "document"}, "deck": {}}, "invalid_command"),
    ({"artifact": {"mode": "document"}, "folder_grant": "fake-grant"}, "invalid_command"),
    ({"artifact": {"mode": "document"}, "resource_id": "existing"}, "invalid_command"),
    ({"artifact": {"mode": "document"}, "kind": "workspace"}, "invalid_command"),
    ({"artifact": {"mode": "document"}, "intent": "open"}, "invalid_command"),
    ({"artifact": {"mode": "document", "template_id": "blank_deck"}}, "invalid_template"),
    ({"artifact": {"mode": "document", "aspect_ratio": "phone"}}, "invalid_canvas"),
])
def test_invalid_typed_setup_is_actionable_and_has_no_resource_effect(artifact_service, payload, code):
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        rejected = _setup(client, headers, {"kind": "artifact", "intent": "create", **payload})
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["code"] == code
        assert not storage.PROJECTS_DIR.exists()
        assert artifact_service.list_conversations()["items"] == []


def test_unknown_saved_mode_is_unavailable_and_legacy_deck_contract_is_retained(artifact_service):
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        old_options = client.get("/api/v1/resources/setup/deck", headers=headers)
        new_options = client.get("/api/v1/resources/setup/artifact/deck", headers=headers)
        assert old_options.status_code == 200 and old_options.json() == new_options.json()
        unknown_options = client.get("/api/v1/resources/setup/artifact/future", headers=headers)
        assert unknown_options.status_code == 404
        assert unknown_options.json()["code"] == "artifact_type_unavailable"
        created = _completed(_setup(client, headers, {"kind": "artifact", "intent": "create", "deck": {}}))
        path = storage.PROJECTS_DIR / f"{created['resource_id']}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["mode"] = "future"
        path.write_text(json.dumps(data), encoding="utf-8")
        library = client.get("/api/v1/resources/library/artifact", headers=headers)
        assert library.status_code == 200 and not library.json()["items"][0]["available"]
        preview = client.get(f"/api/v1/conversations/{created['conversation_id']}/artifacts/{created['binding_id']}/preview",
                             headers=headers)
        assert preview.status_code == 404 and preview.json()["code"] == "artifact_type_unavailable"
