"""Actual API, task row and durable command recovery without workflow execution."""
# ruff: noqa: F811 -- pytest fixtures intentionally share imported fixture names.
from dataclasses import asdict
from uuid import uuid4

import pytest

from row_bot.application import task_controls, task_commands
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.workflows.test_task_controls import fields  # noqa: F401

pytestmark = pytest.mark.subsystem


@pytest.fixture
def task_api(service, monkeypatch):
    from row_bot import tasks
    from row_bot.application import task_graph_controls, task_settings_controls

    monkeypatch.setattr(task_controls, "tasks", tasks)
    monkeypatch.setattr(task_graph_controls, "tasks", tasks)
    monkeypatch.setattr(task_settings_controls, "tasks", tasks)
    tasks._init_db()
    monkeypatch.setattr(tasks, "_scheduler", None)
    monkeypatch.setattr(tasks, "_canonicalize_agent_profile_reference", lambda value: value)
    monkeypatch.setattr(tasks, "run_task_background", lambda *a, **k: pytest.fail("Save started a workflow"))
    return service, tasks


def body(headers, fields, *, task=None, revision=None):
    return {"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
            "type": "task.update" if task else "task.create", "expected_revision": "0",
            "payload": {"fields": asdict(fields), **({"task_id": task, "task_revision": revision} if task else {})}}


def send(client, headers, command):
    return client.post("/api/v1/tasks/commands", json=command,
                       headers={**headers, "Idempotency-Key": command["command_id"]})


def test_create_update_and_receipt_replay(task_api, fields):
    service, tasks = task_api
    with _client(service) as client:
        _, headers = bootstrap(client)
        command = body(headers, fields)
        first = send(client, headers, command)
        assert first.status_code == 200, first.text
        receipt = first.json()
        assert receipt["status"] == "completed" and receipt["task_saved"]
        assert send(client, headers, command).json() == receipt
        editor = client.get(f"/api/v1/tasks/{receipt['task_id']}/editing", headers=headers)
        assert editor.status_code == 200, editor.text
        state = editor.json()
        update = body(headers, fields, task=state["id"], revision=state["revision"])
        update["payload"]["fields"]["name"] = "Updated workflow"
        edited = send(client, headers, update)
        assert edited.status_code == 200, edited.text
        assert edited.json()["task_revision"] != state["revision"]
        stale = {**update, "command_id": str(uuid4())}
        rejected = send(client, headers, stale)
        assert rejected.status_code == 409 and rejected.json()["code"] == "task_revision_conflict"
        assert tasks.get_task(state["id"])["name"] == "Updated workflow"
        assert len(tasks.list_tasks()) == 1 and tasks.get_recent_runs() == []
        assert service.list_conversations()["items"] == []


@pytest.mark.parametrize("creating", [True, False])
def test_crash_after_commit_resumes_schedule_without_repeating_edit(task_api, fields, monkeypatch, creating):
    service, tasks = task_api
    with _client(service) as client:
        _, headers = bootstrap(client)
        initial = send(client, headers, body(headers, fields)).json()
        command = body(headers, fields) if creating else body(headers, fields, task=initial["task_id"], revision=initial["task_revision"])
        operation = "create_task" if creating else "update_task"
        original = getattr(tasks, operation)
        def commit_then_lose(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("synthetic crash after SQL commit")
        monkeypatch.setattr(tasks, operation, commit_then_lose)
        partial = send(client, headers, command)
        assert partial.status_code == 200, partial.text
        assert partial.json()["status"] == "partial" and partial.json()["task_saved"]
        saved_id = partial.json()["task_id"]
        # Another editor wins after the original commit. Recovery keeps that edit.
        original_update = tasks.update_task if creating else original
        original_update(saved_id, name="Concurrent edit")
        monkeypatch.setattr(tasks, operation, lambda *a, **k: pytest.fail("Repeated committed write"))
        reconciled = []
        monkeypatch.setattr(tasks, "sync_task_schedule", lambda identity, **kw: reconciled.append(identity))
        completed = send(client, headers, command)
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "completed"
        assert reconciled == [saved_id]
        assert tasks.get_task(saved_id)["name"] == "Concurrent edit"


def test_receipt_marker_failure_rolls_back_task_row(task_api, fields, monkeypatch):
    service, tasks = task_api
    original = task_commands.create_saved_task
    def reject_marker(*args, **kwargs):
        def fail(conn, identity):
            raise task_controls.TaskControlError("invalid_task_fields")
        kwargs["record_commit"] = fail
        return original(*args, **kwargs)
    monkeypatch.setattr(task_commands, "create_saved_task", reject_marker)
    with _client(service) as client:
        _, headers = bootstrap(client)
        rejected = send(client, headers, body(headers, fields))
        assert rejected.status_code == 422, rejected.text
        assert tasks.list_tasks() == []


def test_same_identity_changed_fields_is_rejected_and_receipts_have_no_content(task_api, fields):
    service, tasks = task_api
    with _client(service) as client:
        _, headers = bootstrap(client)
        command = body(headers, fields)
        assert send(client, headers, command).status_code == 200
        command["payload"]["fields"]["name"] = "Changed request"
        rejected = send(client, headers, command)
        assert rejected.status_code == 409 and rejected.json()["code"] == "idempotency_mismatch"
        with admissions.transaction() as conn:
            stored = dict(conn.execute("SELECT * FROM client_commands WHERE command_id=?", (command["command_id"],)).fetchone())
        assert fields.name not in str(stored) and fields.prompts[0] not in str(stored)
        assert len(tasks.list_tasks()) == 1


def test_task_commands_cannot_use_conversation_route(task_api, fields):
    service, tasks = task_api
    with _client(service) as client:
        _, headers = bootstrap(client)
        command = body(headers, fields)
        response = client.post("/api/v1/conversations/tasks/commands", json=command,
                               headers={**headers, "Idempotency-Key": command["command_id"]})
        assert response.status_code == 422 and tasks.list_tasks() == []
