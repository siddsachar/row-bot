"""Phase 5 multi-client recovery and collision checks over canonical owners."""
# ruff: noqa: F811 -- imported isolated fixture is intentionally reused.
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from tests.helpers.client_platform_fakes import (
    CheckpointCommit,
    ScriptedAgentStream,
    StreamBarrier,
)
from tests.subsystem.client_protocol.test_protocol_application import (
    _client,
    _command,
    _isolated_service,
    service,  # noqa: F401 - shared isolated service fixture
)
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


@pytest.fixture
def resource_service(service, tmp_path, monkeypatch):
    """Keep every resource effect inside this test's isolated data roots."""
    from row_bot.designer import storage as artifacts
    from row_bot.developer import storage as workspaces

    monkeypatch.setattr(artifacts, "DESIGNER_DIR", tmp_path / "designer")
    monkeypatch.setattr(
        artifacts, "PROJECTS_DIR", tmp_path / "designer" / "projects"
    )
    monkeypatch.setattr(workspaces, "DEVELOPER_DIR", tmp_path / "developer")
    monkeypatch.setattr(
        workspaces,
        "WORKSPACES_PATH",
        tmp_path / "developer" / "workspaces.json",
    )
    service.readiness_factory = lambda _: False
    return service


def _resource_command(
    service,
    *,
    key: str,
    command_id: str,
    payload: dict,
) -> dict:
    return service.execute(
        owner_id=service.instance_id,
        idempotency_key=key,
        target="resources",
        command={
            "command_id": command_id,
            "client_session_id": str(uuid4()),
            "type": "resource.setup",
            "expected_revision": "0",
            "payload": payload,
        },
    )


def test_background_client_and_reconnected_client_observe_one_exact_generation(
    service,
) -> None:
    """A suspended observer and a fresh session converge on one producer identity."""
    from langchain_core.messages import AIMessage

    barrier = StreamBarrier()
    fake = ScriptedAgentStream(
        (
            ("token", "One shared result"),
            barrier,
            CheckpointCommit(
                (AIMessage(id="phase5-shared-output", content="One shared result"),),
                "phase5-shared-output",
            ),
            ("done", None),
        )
    )
    service.stream_factory = fake.stream

    with _client(service) as client:
        _, desktop = bootstrap(client)
        _, phone = bootstrap(client)
        conversation = _command(
            client, desktop, "conversation.create", {"title": "Shared generation"}
        ).json()["conversation_id"]
        subscriptions = {
            name: client.post(
                f"/api/v1/conversations/{conversation}/subscriptions", headers=headers
            ).json()
            for name, headers in (("desktop", desktop), ("phone", phone))
        }
        command_id = str(uuid4())
        key = str(uuid4())
        payload = {
            "submission_id": str(uuid4()),
            "text": "Run exactly once",
            "attachment_refs": [],
            "model_selection": {
                "provider_id": "fixture",
                "model_ref": "fixture::model",
            },
        }
        admitted = _command(
            client,
            desktop,
            "conversation.submit",
            payload,
            target=conversation,
            key=key,
            command_id=command_id,
        )
        assert admitted.status_code == 202, admitted.text
        identity = {
            field: admitted.json()[field]
            for field in ("execution_id", "generation_id")
        }
        submission_id = admitted.json()["submission_id"]
        handle = service.registry.get(identity["execution_id"])
        assert barrier.entered.wait(5)
        try:
            desktop_events = client.get(
                "/api/v1/events/poll",
                headers=desktop,
                params={
                    "subscription_id": subscriptions["desktop"]["subscription_id"],
                    "cursor": subscriptions["desktop"]["cursor"],
                },
            )
            assert desktop_events.status_code == 200, desktop_events.text
            running = [
                item["event"]["payload"]
                for item in desktop_events.json()["events"]
                if item["event"]["type"] == "generation.state"
            ]
            assert any(
                all(event[field] == identity[field] for field in identity)
                for event in running
            )
        finally:
            barrier.release.set()

        assert handle.producer_done.wait(5)
        # The phone resumes from its original cut after the whole run completed.
        phone_events = client.get(
            "/api/v1/events/poll",
            headers=phone,
            params={
                "subscription_id": subscriptions["phone"]["subscription_id"],
                "cursor": subscriptions["phone"]["cursor"],
            },
        )
        assert phone_events.status_code == 200, phone_events.text
        terminal = [
            item["event"]["payload"]
            for item in phone_events.json()["events"]
            if item["event"]["type"] == "generation.state"
            and item["event"]["payload"]["quiesced"]
        ]
        assert len(terminal) == 1
        assert all(terminal[0][field] == identity[field] for field in identity)

        _, reconnected = bootstrap(client)
        reopened = client.post(
            f"/api/v1/conversations/{conversation}/subscriptions",
            headers=reconnected,
        )
        assert reopened.status_code == 200, reopened.text
        generation = reopened.json()["snapshot"]["generation"]
        assert generation["quiesced"] is True
        assert all(generation[field] == identity[field] for field in identity)

        replay = _command(
            client,
            reconnected,
            "conversation.submit",
            payload,
            target=conversation,
            key=key,
            command_id=command_id,
        )
        assert replay.json() == admitted.json()
        assert len(fake.calls) == 1
        rows = service.transcript(conversation)["rows"]
        assert [row["message_id"] for row in rows].count(submission_id) == 1
        assert [row["message_id"] for row in rows].count("phase5-shared-output") == 1


def test_two_clients_racing_distinct_commands_have_one_revision_winner(service) -> None:
    """The canonical revision owner deterministically rejects one stale mutation."""
    from row_bot import threads
    from row_bot.application.client_platform import ClientPlatformError

    conversation = threads.create_thread("Before race", seed_default_skills=False)
    start = Barrier(2)
    attempts = [
        {
            "key": str(uuid4()),
            "command": {
                "command_id": str(uuid4()),
                "client_session_id": str(uuid4()),
                "type": "conversation.rename",
                "expected_revision": "0",
                "payload": {"title": title},
            },
        }
        for title in ("Desktop winner", "Phone winner")
    ]

    def rename(attempt: dict) -> dict | tuple[str, str | None]:
        start.wait(timeout=5)
        try:
            return service.execute(
                owner_id=service.instance_id,
                idempotency_key=attempt["key"],
                command=attempt["command"],
                target=conversation,
            )
        except ClientPlatformError as exc:
            return exc.code, exc.current_revision

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(rename, attempts))

    winners = [outcome for outcome in outcomes if isinstance(outcome, dict)]
    conflicts = [outcome for outcome in outcomes if isinstance(outcome, tuple)]
    assert len(winners) == 1
    assert conflicts == [("revision_conflict", "1")]
    winner = winners[0]
    winning_attempt = attempts[outcomes.index(winner)]
    assert service.get_conversation(conversation)["revision"] == "1"
    assert service.get_conversation(conversation)["title"] == winning_attempt[
        "command"
    ]["payload"]["title"]
    assert (
        service.execute(
            owner_id=service.instance_id,
            idempotency_key=winning_attempt["key"],
            command=winning_attempt["command"],
            target=conversation,
        )
        == winner
    )
    assert service.get_conversation(conversation)["revision"] == "1"


def test_backend_restart_reopens_exact_conversation_and_replays_create(
    service, tmp_path, monkeypatch
) -> None:
    """A fresh protocol epoch recovers durable identity without a blind create retry."""
    from row_bot import threads

    draft_root = tmp_path / "thread-ui"
    draft_root.mkdir()
    monkeypatch.setattr(threads, "_THREAD_UI_DIR", draft_root)
    command_id = str(uuid4())
    key = str(uuid4())

    with _client(service) as client:
        first_handshake, first = bootstrap(client)
        created = _command(
            client,
            first,
            "conversation.create",
            {"title": "Restart-safe identity"},
            key=key,
            command_id=command_id,
        )
        assert created.status_code == 200, created.text
        original = created.json()
        draft_url = f"/api/v1/conversations/{original['conversation_id']}/draft"
        draft = client.get(draft_url, headers=first).json()
        draft_root.mkdir(exist_ok=True)
        saved = client.put(
            draft_url,
            headers=first,
            json={
                "expected_revision": draft["revision"],
                "text": "Unsent text survives the restart",
                "attachment_refs": [],
            },
        )
        assert saved.status_code == 200, saved.text

    restarted = _isolated_service()
    try:
        with _client(restarted) as client:
            second_handshake, second = bootstrap(client)
            assert second_handshake["server_epoch"] != first_handshake["server_epoch"]
            opened = client.get(
                f"/api/v1/conversations/{original['conversation_id']}/open",
                headers=second,
            )
            assert opened.status_code == 200, opened.text
            assert opened.json()["conversation"]["id"] == original["conversation_id"]
            assert opened.json()["draft"]["text"] == "Unsent text survives the restart"
            replay = _command(
                client,
                second,
                "conversation.create",
                {"title": "Restart-safe identity"},
                key=key,
                command_id=command_id,
            )
            assert replay.status_code == 200, replay.text
            assert replay.json() == original
            assert len(restarted.list_conversations()["items"]) == 1
    finally:
        restarted.registry.shutdown()


def test_concurrent_global_open_reuses_origin_binding_and_resource(
    resource_service,
) -> None:
    """Two devices opening one artifact cannot fork its canonical history."""
    from row_bot import conversation_resources
    from row_bot.designer.client_service import read_artifact
    from row_bot.designer.storage import PROJECTS_DIR

    service = resource_service
    with _client(service) as client:
        _, headers = bootstrap(client)
        created = _resource_command(
            service,
            key=str(uuid4()),
            command_id=str(uuid4()),
            payload={"kind": "artifact", "intent": "create", "deck": {}},
        )
        artifact = read_artifact(created["resource_id"])
        payload = {
            "kind": "artifact",
            "intent": "open",
            "resource_id": artifact.id,
            "expected_resource_revision": artifact.updated_at,
        }
        start = Barrier(2)
        attempts = [(str(uuid4()), str(uuid4())) for _ in range(2)]

        def open_resource(identity: tuple[str, str]) -> dict:
            start.wait(timeout=5)
            return _resource_command(
                service, key=identity[0], command_id=identity[1], payload=payload
            )

        with ThreadPoolExecutor(max_workers=2) as workers:
            opened = list(workers.map(open_resource, attempts))

        assert all(result["status"] == "completed" for result in opened)
        assert {result["conversation_id"] for result in opened} == {
            created["conversation_id"]
        }
        assert {result["resource_id"] for result in opened} == {artifact.id}
        assert {result["binding_id"] for result in opened} == {
            created["binding_id"]
        }
        assert len(service.list_conversations()["items"]) == 1
        assert len(
            conversation_resources.list_bindings(created["conversation_id"]).bindings
        ) == 1
        assert len(list(PROJECTS_DIR.glob("*.json"))) == 1
        # Opening is observational: the selected artifact revision never changes.
        assert read_artifact(artifact.id).updated_at == artifact.updated_at
