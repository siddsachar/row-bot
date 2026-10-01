"""Actual API, task row and durable command recovery without workflow execution."""

# ruff: noqa: F811 -- pytest fixtures intentionally share imported fixture names.
from dataclasses import asdict
from types import SimpleNamespace
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
    monkeypatch.setattr(
        tasks, "_canonicalize_agent_profile_reference", lambda value: value
    )
    monkeypatch.setattr(
        tasks,
        "run_task_background",
        lambda *a, **k: pytest.fail("Save started a workflow"),
    )
    return service, tasks


def body(headers, fields, *, task=None, revision=None):
    return {
        "command_id": str(uuid4()),
        "client_session_id": headers["X-Client-Session"],
        "type": "task.update" if task else "task.create",
        "expected_revision": "0",
        "payload": {
            "fields": asdict(fields),
            **({"task_id": task, "task_revision": revision} if task else {}),
        },
    }


def send(client, headers, command):
    return client.post(
        "/api/v1/tasks/commands",
        json=command,
        headers={**headers, "Idempotency-Key": command["command_id"]},
    )


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
        editor = client.get(
            f"/api/v1/tasks/{receipt['task_id']}/editing", headers=headers
        )
        assert editor.status_code == 200, editor.text
        state = editor.json()
        update = body(headers, fields, task=state["id"], revision=state["revision"])
        update["payload"]["fields"]["name"] = "Updated workflow"
        edited = send(client, headers, update)
        assert edited.status_code == 200, edited.text
        assert edited.json()["task_revision"] != state["revision"]
        stale = {**update, "command_id": str(uuid4())}
        rejected = send(client, headers, stale)
        assert (
            rejected.status_code == 409
            and rejected.json()["code"] == "task_revision_conflict"
        )
        assert tasks.get_task(state["id"])["name"] == "Updated workflow"
        assert len(tasks.list_tasks()) == 1 and tasks.get_recent_runs() == []
        assert service.list_conversations()["items"] == []


def test_revision_bound_delete_preserves_audit_ownership(task_api, fields):
    service, tasks = task_api
    with _client(service) as client:
        _, headers = bootstrap(client)
        created = send(client, headers, body(headers, fields)).json()
        command = {
            "command_id": str(uuid4()),
            "client_session_id": headers["X-Client-Session"],
            "type": "task.delete",
            "expected_revision": "0",
            "payload": {
                "task_id": created["task_id"],
                "task_revision": created["task_revision"],
            },
        }
        deleted = send(client, headers, command)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["task_deleted"] is True
        assert tasks.get_task(created["task_id"]) is None
        assert send(client, headers, command).json() == deleted.json()


def test_duplicate_is_a_revision_bound_copy_without_schedule_or_webhook(task_api, fields):
    """Parity row 18: Duplicate workflow makes "<name> (copy)" once, never runs it."""
    from dataclasses import replace

    service, tasks = task_api
    with _client(service) as client:
        _, headers = bootstrap(client)
        source = replace(fields, schedule="daily:09:30", channels=("telegram",))
        created = send(client, headers, body(headers, source)).json()
        tasks.update_task(created["task_id"], trigger={"type": "webhook", "secret": "s" * 32})
        revision = client.get(f"/api/v1/tasks/{created['task_id']}/editing", headers=headers).json()["revision"]
        command = {
            "command_id": str(uuid4()),
            "client_session_id": headers["X-Client-Session"],
            "type": "task.duplicate",
            "expected_revision": "0",
            "payload": {"task_id": created["task_id"], "task_revision": revision},
        }
        copied = send(client, headers, command)
        assert copied.status_code == 200, copied.text
        receipt = copied.json()
        assert receipt["task_created"] is True and receipt["task_id"] != created["task_id"]
        copy = tasks.get_task(receipt["task_id"])
        assert copy["name"] == f"{fields.name} (copy)"
        assert copy["prompts"] == list(fields.prompts) and copy["channels"] == ["telegram"]
        assert copy["schedule"] is None and copy["at"] is None and not copy.get("trigger")
        # B177: a copy starts switched off, whatever the original was.
        assert copy["enabled"] is False
        assert send(client, headers, command).json() == receipt
        assert len(tasks.list_tasks()) == 2 and tasks.get_recent_runs() == []
        stale = {**command, "command_id": str(uuid4()), "payload": {**command["payload"], "task_revision": "0" * 64}}
        refused = send(client, headers, stale)
        assert refused.status_code == 409 and refused.json()["code"] == "task_revision_conflict"
        assert len(tasks.list_tasks()) == 2


def test_delivery_defaults_are_passive_revision_bound_and_idempotent(
    task_api, monkeypatch
):
    service, tasks = task_api
    from row_bot.channels import registry

    monkeypatch.setattr(
        registry,
        "configured_channels",
        lambda: [
            SimpleNamespace(name="desktop", display_name="Desktop"),
            SimpleNamespace(name="team", display_name="Team"),
        ],
    )
    with _client(service) as client:
        _, headers = bootstrap(client)
        snapshot = client.get("/api/v1/tasks/delivery-defaults", headers=headers).json()
        assert snapshot["web_app_always_on"] is True
        assert [item["id"] for item in snapshot["channels"]] == ["desktop", "team"]
        command = {
            "command_id": str(uuid4()),
            "client_session_id": headers["X-Client-Session"],
            "type": "task.delivery.update",
            "expected_revision": "0",
            "payload": {
                "delivery_revision": snapshot["revision"],
                "channels": ["team"],
            },
        }
        saved = send(client, headers, command)
        assert saved.status_code == 200, saved.text
        assert saved.json()["task_saved"] is True
        assert tasks.get_workflow_default_channels() == ["team"]
        assert send(client, headers, command).json() == saved.json()
        current = client.get("/api/v1/tasks/delivery-defaults", headers=headers).json()
        assert [item["id"] for item in current["channels"] if item["selected"]] == [
            "team"
        ]


@pytest.mark.parametrize("creating", [True, False])
def test_crash_after_commit_resumes_schedule_without_repeating_edit(
    task_api, fields, monkeypatch, creating
):
    service, tasks = task_api
    with _client(service) as client:
        _, headers = bootstrap(client)
        initial = send(client, headers, body(headers, fields)).json()
        command = (
            body(headers, fields)
            if creating
            else body(
                headers,
                fields,
                task=initial["task_id"],
                revision=initial["task_revision"],
            )
        )
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
        monkeypatch.setattr(
            tasks, operation, lambda *a, **k: pytest.fail("Repeated committed write")
        )
        reconciled = []
        monkeypatch.setattr(
            tasks,
            "sync_task_schedule",
            lambda identity, **kw: reconciled.append(identity),
        )
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


def test_same_identity_changed_fields_is_rejected_and_receipts_have_no_content(
    task_api, fields
):
    service, tasks = task_api
    with _client(service) as client:
        _, headers = bootstrap(client)
        command = body(headers, fields)
        assert send(client, headers, command).status_code == 200
        command["payload"]["fields"]["name"] = "Changed request"
        rejected = send(client, headers, command)
        assert (
            rejected.status_code == 409
            and rejected.json()["code"] == "idempotency_mismatch"
        )
        with admissions.transaction() as conn:
            stored = dict(
                conn.execute(
                    "SELECT * FROM client_commands WHERE command_id=?",
                    (command["command_id"],),
                ).fetchone()
            )
        assert fields.name not in str(stored) and fields.prompts[0] not in str(stored)
        assert len(tasks.list_tasks()) == 1


def test_task_commands_cannot_use_conversation_route(task_api, fields):
    service, tasks = task_api
    with _client(service) as client:
        _, headers = bootstrap(client)
        command = body(headers, fields)
        response = client.post(
            "/api/v1/conversations/tasks/commands",
            json=command,
            headers={**headers, "Idempotency-Key": command["command_id"]},
        )
        assert response.status_code == 422 and tasks.list_tasks() == []


def test_a_finished_past_once_workflow_can_be_switched_off(task_api):
    """B150: the enable switch of a one-off that already ran answered 503."""
    service, tasks = task_api
    import sqlite3
    from contextlib import closing

    row = {
        "id": "b150-once", "name": "[synthetic] finished once", "description": "", "icon": "⚡",
        "prompts": '["Reply with just the word NOTICE."]', "schedule": None, "at": "2026-09-28T13:11",
        "notify_only": 0, "notify_label": "", "enabled": 1, "last_run": "2026-09-28T13:11:08.920557",
        "created_at": "2026-09-28T13:04:46.526999", "sort_order": 0, "delivery_channel": None,
        "delivery_target": None, "model_override": None, "persistent_thread_id": None,
        "delete_after_run": 0, "allowed_commands": "[]", "allowed_recipients": "[]",
        "skills_override": None, "steps": "[]", "safety_mode": "block", "concurrency_group": None,
        "trigger": None, "tools_override": None, "channels": "[]", "advanced_mode": 0,
        "agent_profile_id": "builtin:row_bot_default", "profile_migration_status": "not_needed",
        "profile_migration_note": "Profile-first workflow.", "profile_migration_snapshot_json": "{}",
        "automation_kind": "workflow", "automation_subtype": "",
    }
    with closing(sqlite3.connect(tasks._DB_PATH)) as conn, conn:
        columns = {info[1] for info in conn.execute("PRAGMA table_info(tasks)")}
        row = {key: value for key, value in row.items() if key in columns}
        conn.execute(f"INSERT INTO tasks ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                     list(row.values()))
    with _client(service) as client:
        _, headers = bootstrap(client)
        editor = client.get("/api/v1/tasks/b150-once/editing", headers=headers)
        assert editor.status_code == 200, editor.text
        state = editor.json()
        command = {
            "command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
            "type": "task.update", "expected_revision": "0",
            "payload": {"fields": {**state["fields"], "enabled": False},
                        "task_id": "b150-once", "task_revision": state["revision"]},
        }
        switched = send(client, headers, command)
        assert switched.status_code == 200, switched.text
        assert tasks.get_task("b150-once")["enabled"] is False
