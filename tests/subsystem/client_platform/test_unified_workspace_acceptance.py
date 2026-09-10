"""Independent Phase 3 acceptance checks against real application/API owners."""
from __future__ import annotations

from uuid import uuid4

import pytest

from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream, StreamBarrier, ToolMediaResult
from tests.subsystem.client_protocol.test_protocol_application import _client, _command, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap


pytestmark = pytest.mark.subsystem


@pytest.fixture
def resource_service(service, tmp_path, monkeypatch):
    from row_bot.designer import storage as artifacts
    from row_bot.developer import storage as workspaces

    monkeypatch.setattr(artifacts, "DESIGNER_DIR", tmp_path / "designer")
    monkeypatch.setattr(artifacts, "PROJECTS_DIR", tmp_path / "designer" / "projects")
    monkeypatch.setattr(workspaces, "DEVELOPER_DIR", tmp_path / "developer")
    monkeypatch.setattr(workspaces, "WORKSPACES_PATH", tmp_path / "developer" / "workspaces.json")
    service.readiness_factory = lambda _: False
    return service


def _setup(client, headers, payload, *, target=None, revision="0", command_id=None, key=None, kind="resource.setup"):
    body = {"command_id": command_id or str(uuid4()), "client_session_id": headers["X-Client-Session"],
            "type": kind, "expected_revision": revision, "payload": payload}
    endpoint = "/api/v1/resources/commands" if target is None else f"/api/v1/conversations/{target}/commands"
    response = client.post(endpoint, headers={**headers, "Idempotency-Key": key or str(uuid4())}, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_new_chat_without_model_creates_once_and_receipt_survives_response_loss(service):
    fake = ScriptedAgentStream()
    service.stream_factory = fake.stream
    with _client(service) as client:
        _, headers = bootstrap(client)
        command_id, key = str(uuid4()), str(uuid4())
        created = _command(client, headers, "conversation.create", {}, key=key, command_id=command_id)
        assert created.status_code == 200, created.text
        result = created.json()
        assert result["conversation_id"]
        receipt = client.get(f"/api/v1/commands/{command_id}", headers=headers)
        assert receipt.status_code == 200, receipt.text
        assert receipt.json() == result
        repeated = _command(client, headers, "conversation.create", {}, key=key, command_id=command_id)
        assert repeated.json() == result
        assert client.get(f"/api/v1/conversations/{result['conversation_id']}", headers=headers).status_code == 200
        history = client.get(f"/api/v1/conversations/{result['conversation_id']}/history", headers=headers)
        assert history.status_code == 200, history.text
        assert history.json()["rows"] == []
        assert not history.json()["has_more"]
        assert len(service.list_conversations()["items"]) == 1
        assert fake.calls == []


def test_pin_unpin_and_rename_preserve_durable_history_and_identity(service):
    from langchain_core.messages import HumanMessage
    from row_bot import threads

    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation = _command(client, headers, "conversation.create", {}).json()["conversation_id"]
        threads.append_checkpoint_messages(conversation, [HumanMessage(id="retained-message", content="Keep my history")])
        for revision, kind, payload in (
            ("0", "conversation.pin", {"pinned": True}),
            ("1", "conversation.rename", {"title": "Reviewed title"}),
            ("2", "conversation.pin", {"pinned": False}),
        ):
            result = _command(client, headers, kind, payload, target=conversation, revision=revision)
            assert result.status_code == 200, result.text
            assert result.json()["conversation_id"] == conversation
        view = client.get(f"/api/v1/conversations/{conversation}", headers=headers).json()
        assert view["title"] == "Reviewed title"
        assert view["revision"] == "3"
        rows = service.transcript(conversation)["rows"]
        assert len(rows) == 1
        assert rows[0]["message_id"] == "retained-message"
        assert rows[0]["blocks"] == [{"type": "text", "text": "Keep my history"}]


def test_one_client_disconnect_does_not_stop_producer_or_duplicate_final(service):
    from langchain_core.messages import AIMessage

    barrier = StreamBarrier()
    fake = ScriptedAgentStream((("token", "Independent observers"), barrier,
        CheckpointCommit((AIMessage(id="stable-output", content="Independent observers"),), "stable-output"),
        ("done", None)))
    service.stream_factory = fake.stream
    with _client(service) as client:
        _, first = bootstrap(client)
        _, second = bootstrap(client)
        conversation = _command(client, first, "conversation.create", {}).json()["conversation_id"]
        subscribers = [client.post(f"/api/v1/conversations/{conversation}/subscriptions", headers=h).json()
                       for h in (first, second)]
        admitted = _command(client, first, "conversation.submit", {"submission_id": str(uuid4()),
            "text": "Start synthetic response", "model_selection": {"provider_id": "fixture", "model_ref": "fixture::model"}},
            target=conversation)
        assert admitted.status_code == 202, admitted.text
        handle = service.registry.get(admitted.json()["execution_id"])
        assert barrier.entered.wait(5)
        try:
            removed = client.delete(f"/api/v1/subscriptions/{subscribers[0]['subscription_id']}", headers=first)
            assert removed.status_code == 200, removed.text
            assert not handle.cancel_scope.is_cancelled()
            observed = client.get("/api/v1/events/poll", headers=second,
                params={"subscription_id": subscribers[1]["subscription_id"], "cursor": subscribers[1]["cursor"]})
            assert observed.status_code == 200
            assert any(e["event"]["type"] == "transcript.delta" for e in observed.json()["events"])
        finally:
            barrier.release.set()
        assert handle.producer_done.wait(5)
        for _ in range(2):
            rows = service.transcript(conversation)["rows"]
            assert len([r for r in rows if r["message_id"] == "stable-output"]) == 1
        assert len(fake.calls) == 1


def test_stop_reports_request_until_uninterruptible_producer_releases(service):
    barrier = StreamBarrier(release_on_cancel=False)
    fake = ScriptedAgentStream((("token", "Pending external completion"), barrier, ("done", None)))
    service.stream_factory = fake.stream
    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation = _command(client, headers, "conversation.create", {}).json()["conversation_id"]
        admitted = _command(client, headers, "conversation.submit", {"submission_id": str(uuid4()),
            "text": "Start", "model_selection": {"provider_id": "fixture", "model_ref": "fixture::model"}}, target=conversation)
        assert admitted.status_code == 202, admitted.text
        handle = service.registry.get(admitted.json()["execution_id"])
        assert barrier.entered.wait(5)
        try:
            stopped = _command(client, headers, "conversation.stop", {}, target=conversation,
                               revision=service.get_conversation(conversation)["revision"])
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()["status"] == "cancel_requested"
            assert handle.view()["status"] == "stopping"
            assert not handle.producer_done.is_set()
            assert service.registry.active(conversation)
        finally:
            barrier.release.set()
        assert handle.producer_done.wait(5)
        assert not service.registry.active(conversation)
        assert len(fake.calls) == 1


def test_token_burst_and_acknowledgement_rate_limit_preserve_stop_control(service):
    from fastapi.testclient import TestClient
    from row_bot.api.v1.routes import create_client_platform_app
    from row_bot.api.v1.security import ClientSecurity

    barrier = StreamBarrier(release_on_cancel=True, timeout_seconds=20)
    fake = ScriptedAgentStream(tuple(("token", f"Token {index}. ") for index in range(160)) + (barrier,))
    service.stream_factory = fake.stream
    security = ClientSecurity("acknowledgement-fixture", clock=lambda: 100.0)
    app = create_client_platform_app(service, security=security, choices=lambda: {"models": [], "capabilities": []})
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        conversation = _command(client, headers, "conversation.create", {}).json()["conversation_id"]
        subscribed = client.post(f"/api/v1/conversations/{conversation}/subscriptions", headers=headers)
        assert subscribed.status_code == 200, subscribed.text
        sub = subscribed.json()
        admitted = _command(client, headers, "conversation.submit", {"submission_id": str(uuid4()),
            "text": "Burst", "model_selection": {"provider_id": "fixture", "model_ref": "fixture::model"}}, target=conversation)
        assert admitted.status_code == 202, admitted.text
        handle = service.registry.get(admitted.json()["execution_id"])
        assert barrier.entered.wait(5)
        try:
            events = client.get("/api/v1/events/poll", headers=headers,
                params={"subscription_id": sub["subscription_id"], "cursor": sub["cursor"]})
            assert events.status_code == 200, events.text
            assert sum(item["event"]["type"] == "transcript.delta" for item in events.json()["events"]) == 160
            statuses = [client.put(f"/api/v1/subscriptions/{sub['subscription_id']}/ack", headers=headers,
                json={"cursor": events.json()["cursor"]}).status_code for _ in range(32)]
            assert statuses == [200] * 30 + [429] * 2
            stopped = _command(client, headers, "conversation.stop", {}, target=conversation,
                revision=service.get_conversation(conversation)["revision"])
            assert stopped.status_code == 200, stopped.text
            assert handle.producer_done.wait(5)
            assert handle.view()["quiesced"] is True
        finally:
            barrier.release.set()


@pytest.mark.parametrize("media_step, expected_type", [
    (ToolMediaResult("tool-output", "tool-call", "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ZsAAAAASUVORK5CYII="), "media.available"),
    (ToolMediaResult("tool-output", "tool-call", "malformed synthetic image"), "media.error"),
    (("tool_done", {"tool_call_id": "tool-call", "message_id": "tool-output", "media_error": True}), "media.error"),
])
def test_tool_media_outcome_is_typed_and_does_not_discard_durable_final(service, media_step, expected_type):
    from langchain_core.messages import AIMessage, ToolMessage

    fake = ScriptedAgentStream((
        CheckpointCommit((AIMessage(id="tool-owner", content="", tool_calls=[
            {"id": "tool-call", "name": "synthetic_image", "args": {}}]),)),
        ("tool_call", {"tool_call_id": "tool-call", "message_id": "tool-output"}),
        CheckpointCommit((ToolMessage(id="tool-output", tool_call_id="tool-call", content="Synthetic media result"),)),
        media_step,
        ("token", "The tool completed."),
        CheckpointCommit((AIMessage(id="media-final", content="The tool completed."),), "media-final"),
        ("done", None),
    ))
    service.stream_factory = fake.stream
    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation = _command(client, headers, "conversation.create", {}).json()["conversation_id"]
        subscription = client.post(f"/api/v1/conversations/{conversation}/subscriptions", headers=headers).json()
        admitted = _command(client, headers, "conversation.submit", {
            "submission_id": str(uuid4()), "text": "Use the synthetic tool",
            "model_selection": {"provider_id": "fixture", "model_ref": "fixture::model"},
        }, target=conversation)
        assert admitted.status_code == 202, admitted.text
        handle = service.registry.get(admitted.json()["execution_id"])
        assert handle.producer_done.wait(5)
        observed = client.get("/api/v1/events/poll", headers=headers, params={
            "subscription_id": subscription["subscription_id"], "cursor": subscription["cursor"],
        })
        assert observed.status_code == 200, observed.text
        events = [item["event"] for item in observed.json()["events"]]
        media = [event for event in events if event["type"] == expected_type]
        assert len(media) == 1
        assert media[0]["payload"]["tool_call_id"] == "tool-call"
        assert media[0]["payload"]["message_id"] == "tool-output"
        assert len([row for row in service.transcript(conversation)["rows"]
                    if row["message_id"] == "media-final"]) == 1
        assert handle.view()["status"] == "completed"
        assert fake.external_call_count == 0


def test_blank_deck_creates_once_without_provider_and_global_open_reuses_in_chat_origin(resource_service):
    from row_bot.designer.client_service import read_artifact
    from row_bot.designer.storage import PROJECTS_DIR

    service = resource_service
    fake = ScriptedAgentStream()
    service.stream_factory = fake.stream
    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation = _command(client, headers, "conversation.create", {}).json()["conversation_id"]
        command_id, key = str(uuid4()), str(uuid4())
        payload = {"kind": "artifact", "intent": "create", "deck": {}}
        created = _setup(client, headers, payload, target=conversation, command_id=command_id, key=key)
        assert created["status"] == "completed"
        assert created["conversation_id"] == conversation
        assert created["confirmed_stages"] == ["created", "conversation", "associated", "bound"]
        repeated = _setup(client, headers, payload, target=conversation, command_id=command_id, key=key)
        assert repeated == created
        assert len(list(PROJECTS_DIR.glob("*.json"))) == 1
        project = read_artifact(created["resource_id"])
        assert project.mode == "deck"
        assert project.thread_id == conversation
        for _ in range(2):
            opened = _setup(client, headers, {"kind": "artifact", "intent": "open",
                "resource_id": project.id, "expected_resource_revision": project.updated_at})
            assert opened["status"] == "completed"
            assert opened["conversation_id"] == conversation
            assert opened["resource_id"] == project.id
            assert opened["binding_id"] == created["binding_id"]
        assert len(service.list_conversations()["items"]) == 1
        assert fake.calls == []


@pytest.mark.parametrize("intent", ["add", "open"])
def test_partial_existing_resource_bind_continues_without_repointing_origin(resource_service, monkeypatch, intent):
    from row_bot import conversation_resources
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.designer.client_service import read_artifact

    service = resource_service
    with _client(service) as client:
        _, headers = bootstrap(client)
        created = _setup(client, headers, {"kind": "artifact", "intent": "create", "deck": {"name": "Origin deck"}})
        origin, artifact = created["conversation_id"], created["resource_id"]
        target = _command(client, headers, "conversation.create", {}).json()["conversation_id"] if intent == "add" else None
        bind = conversation_resources.bind
        failed = False

        def fail_once(*args, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                raise ClientPlatformError("resource_limit")
            return bind(*args, **kwargs)

        monkeypatch.setattr(conversation_resources, "bind", fail_once)
        partial = _setup(client, headers, {"kind": "artifact", "intent": intent, "resource_id": artifact,
            "expected_resource_revision": read_artifact(artifact).updated_at}, target=target)
        assert partial["status"] == "partial"
        assert partial["code"] == "resource_limit"
        assert partial["resource_id"] == artifact
        assert read_artifact(artifact).thread_id == origin
        conversation = partial["conversation_id"]
        completed = _setup(client, headers, {
            "setup_command_id": partial["setup_command_id"],
            "expected_resource_revision": read_artifact(artifact).updated_at,
        }, target=conversation, revision=service.get_conversation(conversation)["revision"], kind="resource.continue")
        assert completed["status"] == "completed", completed
        assert completed["resource_id"] == artifact
        assert completed["conversation_id"] == (target or origin)
        assert read_artifact(artifact).thread_id == origin
        assert len([r for r in service.get_conversation(conversation)["resource_bindings"]
                    if r["resource_id"] == artifact]) == 1


def test_created_deck_survives_association_failure_and_continues_exact_identity(resource_service, monkeypatch):
    from row_bot.application import workspace_setup
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.designer.client_service import read_artifact
    from row_bot.designer.storage import PROJECTS_DIR

    service = resource_service
    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation = _command(client, headers, "conversation.create", {}).json()["conversation_id"]
        original = workspace_setup._associate
        with monkeypatch.context() as patch:
            patch.setattr(workspace_setup, "_associate", lambda *args, **kwargs: (_ for _ in ()).throw(ClientPlatformError("capability_revoked")))
            partial = _setup(client, headers, {"kind": "artifact", "intent": "create", "deck": {}}, target=conversation)
        assert partial["status"] == "partial"
        assert partial["confirmed_stages"] == ["created", "conversation"]
        assert workspace_setup._associate is original
        artifact = read_artifact(partial["resource_id"])
        assert not artifact.thread_id
        completed = _setup(client, headers, {"setup_command_id": partial["setup_command_id"],
            "expected_resource_revision": artifact.updated_at}, target=conversation, kind="resource.continue")
        assert completed["status"] == "completed", completed
        assert completed["resource_id"] == artifact.id
        assert read_artifact(artifact.id).thread_id == conversation
        assert len(list(PROJECTS_DIR.glob("*.json"))) == 1


def test_complete_search_crosses_thousand_conversations_and_ten_thousand_messages(service):
    from langchain_core.messages import HumanMessage
    from row_bot import threads
    from row_bot.application.conversation_search import search

    for index in range(1001):
        threads.create_thread(f"Library {index:04d}", thread_id=f"qa-library-{index:04d}", seed_default_skills=False)
    initial_page = service.list_conversations()
    initial_ids = {item["id"] for item in initial_page["items"]}
    target = next(f"qa-library-{index:04d}" for index in range(1001) if f"qa-library-{index:04d}" not in initial_ids)
    messages = [HumanMessage(id=f"history-{index:05d}", content=f"Synthetic history {index:05d}" +
                (" complete-history-needle" if index in {7, 9999} else "")) for index in range(10001)]
    assert threads.append_checkpoint_messages(target, messages)
    assert len(initial_page["items"]) <= 200
    assert target not in initial_ids
    cursor, hits, seen_cursors = None, [], set()
    for _ in range(80):
        result = search(service, "complete-history-needle", cursor=cursor, limit=1)
        assert len(result["items"]) <= 1
        assert result["scanned_messages"] <= 500
        hits.extend(result["items"])
        if not result["has_more"]:
            break
        cursor = result["next_cursor"]
        assert cursor and cursor not in seen_cursors
        seen_cursors.add(cursor)
    else:
        pytest.fail("Complete search did not terminate within the bounded fixture traversal")
    assert {(hit["conversation_id"], hit["message_id"]) for hit in hits} == {
        (target, "history-00007"), (target, "history-09999")}
    with _client(service) as client:
        _, headers = bootstrap(client)
        latest = client.get(f"/api/v1/conversations/{target}/history", headers=headers)
        assert latest.status_code == 200, latest.text
        assert len(latest.json()["rows"]) <= 100
        assert "history-00007" not in {row["message_id"] for row in latest.json()["rows"]}
        jumped = client.get(f"/api/v1/conversations/{target}/history", headers=headers,
                            params={"message_id": "history-00007", "limit": 25})
        assert jumped.status_code == 200, jumped.text
        assert len(jumped.json()["rows"]) <= 25
        assert "history-00007" in {row["message_id"] for row in jumped.json()["rows"]}
        assert jumped.json()["next_cursor"]
        assert len(hits) == 2


def test_search_continuation_rejects_changed_query_and_deleted_library_hit(service):
    from row_bot import threads
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.application.conversation_search import search

    for index in range(33):
        threads.create_thread("Ordinary history", thread_id=f"search-{index:03d}", seed_default_skills=False)
    threads.create_thread("Hidden library hit", thread_id="search-999", seed_default_skills=False)
    first = search(service, "Hidden library hit")
    assert not first["items"] and first["has_more"]
    with pytest.raises(ClientPlatformError, match="cursor_expired"):
        search(service, "different query", cursor=first["next_cursor"])
    with _client(service) as client:
        _, headers = bootstrap(client)
        deleted = _command(client, headers, "conversation.delete", {}, target="search-999")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["status"] == "DeleteCompleted"
    with pytest.raises(ClientPlatformError, match="cursor_expired"):
        search(service, "Hidden library hit", cursor=first["next_cursor"])
    assert not search(service, "Hidden library hit")["items"]


@pytest.mark.parametrize("content,query,expected", [
    ("x" * 510 + "Straße across a byte boundary", "STRASSE", True),
    ("x" * 511 + "🚣 Unicode boundary", "🚣 Unicode", True),
    ('Quoted "needle" and \\ local text', '"needle"', True),
    ([{"type": "text", "text": "Visible only"},
      {"type": "image_url", "image_url": {"url": "https://invalid.example/hidden-metadata"}}], "hidden-metadata", False),
    ("Ordinary words only", "[", False),
    ("Ordinary words only", "}", False),
])
def test_history_search_uses_public_text_across_unicode_chunks_without_json_syntax_hits(service, content, query, expected):
    from langchain_core.messages import HumanMessage
    from row_bot import threads

    threads.create_thread("Search fixture", thread_id="search-public", seed_default_skills=False)
    assert threads.append_checkpoint_messages("search-public", [HumanMessage(id="stable-search-row", content=content)])
    with _client(service) as client:
        _, headers = bootstrap(client)
        found = client.get("/api/v1/search", headers=headers,
                           params={"query": query, "conversation_id": "search-public"})
        assert found.status_code == 200, found.text
        assert bool(found.json()["items"]) is expected
        assert found.json()["has_more"] is False
        if expected:
            assert found.json()["items"][0]["message_id"] == "stable-search-row"


def test_explicit_folder_selection_reuses_identity_and_preserves_source_bytes(resource_service, tmp_path):
    from fastapi.testclient import TestClient
    from row_bot.api.v1.routes import create_client_platform_app
    from row_bot.application.folder_selections import FolderSelections
    from row_bot.developer.storage import get_workspace

    service = resource_service
    folder = tmp_path / "Selected workspace"
    folder.mkdir()
    (folder / "source.py").write_bytes(b"# Synthetic source\nvalue = 1\n")
    before = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    selections = []

    def picker():
        selections.append(folder.name)
        return folder

    app = create_client_platform_app(service, choices=lambda: {"models": [], "capabilities": []},
                                    folder_selections=FolderSelections(picker=picker))
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        confirmed = []
        for _ in range(2):
            selected = client.post("/api/v1/resources/folder-selection", headers=headers)
            assert selected.status_code == 200, selected.text
            assert selected.json()["status"] == "selected"
            assert selected.json()["name"] == folder.name
            assert str(folder) not in selected.text
            confirmed.append(_setup(client, headers, {"kind": "workspace", "intent": "create",
                             "folder_grant": selected.json()["grant_id"]}))
        assert all(result["status"] == "completed" for result in confirmed), confirmed
        assert confirmed[0]["resource_id"] == confirmed[1]["resource_id"]
        assert confirmed[0]["conversation_id"] == confirmed[1]["conversation_id"]
        workspace = get_workspace(confirmed[0]["resource_id"])
        assert workspace.name == folder.name
        assert len(selections) == 2
        after = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
        assert after == before
        assert not (folder / ".git").exists()


def test_cancelled_folder_picker_creates_nothing_and_grants_are_session_scoped(resource_service, tmp_path):
    from fastapi.testclient import TestClient
    from row_bot.api.v1.routes import create_client_platform_app
    from row_bot.application.folder_selections import FolderSelections
    from row_bot.developer.storage import WORKSPACES_PATH

    service = resource_service
    folder = tmp_path / "Synthetic selection"
    folder.mkdir()
    results = iter((None, folder))
    app = create_client_platform_app(service, choices=lambda: {"models": [], "capabilities": []},
                                    folder_selections=FolderSelections(picker=lambda: next(results)))
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, first = bootstrap(client)
        _, second = bootstrap(client)
        cancelled = client.post("/api/v1/resources/folder-selection", headers=first)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        assert not WORKSPACES_PATH.exists()
        assert service.list_conversations()["items"] == []
        selected = client.post("/api/v1/resources/folder-selection", headers=first)
        assert selected.status_code == 200, selected.text
        rejected = client.post("/api/v1/resources/commands", headers={**second, "Idempotency-Key": str(uuid4())}, json={
            "command_id": str(uuid4()), "client_session_id": second["X-Client-Session"],
            "type": "resource.setup", "expected_revision": "0", "payload": {
                "kind": "workspace", "intent": "create", "folder_grant": selected.json()["grant_id"],
            },
        })
        assert rejected.status_code in {403, 409}, rejected.text
        assert rejected.json()["code"] == "capability_revoked"
        assert not WORKSPACES_PATH.exists()
        assert service.list_conversations()["items"] == []


def test_receipt_recovers_deck_saved_before_first_confirmed_progress(resource_service, monkeypatch):
    from row_bot.designer import client_service
    from row_bot.designer.storage import PROJECTS_DIR
    from uuid import UUID, uuid5

    class SimulatedProcessLoss(BaseException):
        pass

    service = resource_service
    command_id, key = str(uuid4()), str(uuid4())
    actual_create = client_service.create_deck

    def save_then_lose_process(*args, **kwargs):
        actual_create(*args, **kwargs)
        raise SimulatedProcessLoss()

    command = {"command_id": command_id, "client_session_id": str(uuid4()), "type": "resource.setup",
               "expected_revision": "0", "payload": {"kind": "artifact", "intent": "create", "deck": {}}}
    with monkeypatch.context() as patch:
        patch.setattr(client_service, "create_deck", save_then_lose_process)
        with pytest.raises(SimulatedProcessLoss):
            service.execute(owner_id=service.instance_id, idempotency_key=key, command=command, target="resources")
    identity = str(uuid5(UUID(command_id), "deck"))
    assert client_service.read_artifact(identity).id == identity
    receipt = service.receipt(service.instance_id, command_id)
    assert receipt.get("resource_id") == identity, receipt
    assert "created" in receipt["confirmed_stages"]
    assert receipt["status"] in {"partial", "admitting"}
    assert len(list(PROJECTS_DIR.glob("*.json"))) == 1


def test_changed_write_target_is_rejected_before_delayed_producer_dispatch(resource_service, monkeypatch):
    import threading
    from row_bot.designer.client_service import read_artifact
    from row_bot.designer.storage import save_project

    service = resource_service
    service.readiness_factory = lambda _: True
    fake = ScriptedAgentStream((("token", "Unexpected stale-target dispatch"), ("done", None)))
    service.stream_factory = fake.stream
    entered, release = threading.Event(), threading.Event()
    launch = service.registry.launch

    def delayed_launch(handle, producer, **kwargs):
        def delayed():
            entered.set()
            assert release.wait(5), "Fixture never released the admitted worker"
            producer()
        return launch(handle, delayed, **kwargs)

    monkeypatch.setattr(service.registry, "launch", delayed_launch)
    with _client(service) as client:
        _, headers = bootstrap(client)
        created = _setup(client, headers, {"kind": "artifact", "intent": "create", "deck": {}})
        conversation = created["conversation_id"]
        view = service.get_conversation(conversation)
        binding = view["resource_bindings"][0]
        admitted = _command(client, headers, "conversation.submit", {
            "submission_id": str(uuid4()), "text": "Use the captured Deck", "attachment_refs": [],
            "model_selection": {"provider_id": "fixture", "model_ref": "fixture::model"},
            "write_targets": [{"kind": "artifact", "binding_id": binding["binding_id"],
                "resource_id": created["resource_id"], "binding_revision": binding["revision"],
                "resource_revision": created["resource_revision"]}],
        }, target=conversation, revision=view["revision"])
        assert admitted.status_code == 202, admitted.text
        handle = service.registry.get(admitted.json()["execution_id"])
        assert entered.wait(5)
        try:
            project = read_artifact(created["resource_id"])
            project.name = "Edited after target confirmation"
            save_project(project)
            assert read_artifact(project.id).updated_at != created["resource_revision"]
        finally:
            release.set()
        assert handle.producer_done.wait(5)
        assert fake.calls == []
        assert handle.view()["status"] == "interrupted"
