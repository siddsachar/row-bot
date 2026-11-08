"""Actual task run API admission, immutable identities and failure recovery."""
# ruff: noqa: F811 -- imported pytest fixture names.
from uuid import uuid4

import pytest

from row_bot.application import task_execution
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.client_protocol.test_task_edit_commands import send

pytestmark = pytest.mark.subsystem


@pytest.fixture
def run_api(service, monkeypatch):
    from row_bot import tasks, notifications
    monkeypatch.setattr(task_execution, "tasks", tasks)
    tasks._init_db()
    monkeypatch.setattr(tasks, "_scheduler", None)
    effects = []
    monkeypatch.setattr(notifications, "notify", lambda **kw: effects.append("notification"))
    monkeypatch.setattr(tasks, "_deliver_to_channels", lambda *a, **kw: ("", ""))
    monkeypatch.setattr(tasks, "_fire_completion_triggers", lambda *a, **kw: None)
    task = tasks.create_task("Synthetic reminder", notify_only=True, channels=[], enabled=False)
    return service, tasks, task, effects


def command(client, headers, task):
    result = client.get(f"/api/v1/tasks/{task}/run-review", headers=headers)
    assert result.status_code == 200, result.text
    review = result.json()
    return {"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
            "type": "task.run", "expected_revision": "0", "payload": {
                "task_id": task, "task_revision": review["task_revision"], "policy_revision": review["policy_revision"]}}


def test_passive_review_explicit_run_replay_history_and_stop(run_api):
    service, tasks, task, effects = run_api
    with _client(service) as client:
        _, headers = bootstrap(client)
        body = command(client, headers, task)
        assert not effects and not tasks.get_recent_runs() and not service.list_conversations()["items"]
        started = send(client, headers, body)
        assert started.status_code == 200, started.text
        receipt = started.json()
        assert receipt["status"] == "completed" and receipt["task_run_reserved"]
        assert effects == ["notification"]
        assert send(client, headers, body).json() == receipt
        assert effects == ["notification"]
        history = client.get(f"/api/v1/tasks/{task}/runs", headers=headers).json()
        assert history["total"] == 1 and history["items"][0]["id"] == receipt["task_run_id"]
        run = client.get(f"/api/v1/tasks/{task}/runs/{receipt['task_run_id']}", headers=headers).json()
        assert run["status"] == "completed" and run["conversation_id"] == receipt["conversation_id"]
        stopped = send(client, headers, {**body, "command_id": str(uuid4()), "type": "task.stop", "payload": {
            "task_id": task, "run_id": receipt["task_run_id"]}})
        assert stopped.status_code == 200, stopped.text
        assert not stopped.json()["task_stop_requested"]


@pytest.mark.parametrize("change", ["task", "policy"])
def test_stale_review_has_no_execution_or_new_conversation(run_api, monkeypatch, change):
    service, tasks, task, effects = run_api
    with _client(service) as client:
        _, headers = bootstrap(client)
        body = command(client, headers, task)
        if change == "task":
            tasks.update_task(task, name="Concurrent edit")
        else:
            body["payload"]["policy_revision"] = "0" * 64
        rejected = send(client, headers, body)
        assert rejected.status_code == 409, rejected.text
        assert not effects and not tasks.get_recent_runs() and not service.list_conversations()["items"]


@pytest.fixture
def saved_workflow(run_api, monkeypatch):
    """A workflow with one message, saved and never run: its first run makes its conversation."""
    service, tasks, _, _ = run_api
    from row_bot import workflow_turns
    # The faked start never finishes its run: keep its in-flight entry out of the shared registry.
    monkeypatch.setattr(tasks, "_active_runs", {})
    started = []
    monkeypatch.setattr(workflow_turns, "start", lambda task, thread_id, run_id, trigger="Run": started.append(
        (list(task["prompts"]), thread_id)) or True)
    task = tasks.create_task("Backup check", prompts=["Check the backup"], channels=[], enabled=False)
    return service, tasks, task, started


def test_a_just_saved_workflow_runs_on_the_first_click(saved_workflow):
    service, tasks, task, started = saved_workflow
    with _client(service) as client:
        _, headers = bootstrap(client)
        started_run = send(client, headers, command(client, headers, task))
        assert started_run.status_code == 200, started_run.text
        receipt = started_run.json()
        assert receipt["status"] == "completed" and receipt["task_run_reserved"]
        assert started == [(["Check the backup"], receipt["conversation_id"])]
        # The run made the workflow's one conversation, which later runs reuse.
        assert tasks.get_task(task)["persistent_thread_id"] == receipt["conversation_id"]


def test_an_edit_while_a_first_run_starts_still_stops_it(saved_workflow, monkeypatch):
    service, tasks, task, started = saved_workflow
    prepare = tasks._prepare_task_thread
    def edited_meanwhile(workflow):
        conversation = prepare(workflow)
        tasks.update_task(task, name="Concurrent edit")
        return conversation
    monkeypatch.setattr(tasks, "_prepare_task_thread", edited_meanwhile)
    with _client(service) as client:
        _, headers = bootstrap(client)
        rejected = send(client, headers, command(client, headers, task))
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["code"] == "task_revision_conflict"
        assert not started and not tasks.get_recent_runs()


def test_crash_after_reservation_never_reexecutes_or_infers_completion(run_api, monkeypatch):
    service, tasks, task, effects = run_api
    with _client(service) as client:
        _, headers = bootstrap(client)
        body = command(client, headers, task)
        def fail(*args, **kwargs):
            raise RuntimeError("synthetic lost dispatch")
        monkeypatch.setattr(tasks, "run_task_background", fail)
        first = send(client, headers, body)
        assert first.status_code == 200, first.text
        receipt = first.json()
        assert receipt["status"] == "partial" and receipt["task_run_reserved"]
        observed = client.get(f"/api/v1/commands/{body['command_id']}", headers=headers)
        assert observed.status_code == 200, observed.text
        assert observed.json()["status"] == "partial"
        monkeypatch.setattr(tasks, "run_task_background", lambda *a, **kw: pytest.fail("Repeated reserved run"))
        recovered = send(client, headers, body)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["task_run_id"] == receipt["task_run_id"] and recovered.json()["status"] == "partial"
        assert len(tasks.get_recent_runs()) == 1 and not effects
        changed = {**body, "payload": {**body["payload"], "policy_revision": "0" * 64}}
        assert send(client, headers, changed).status_code == 409


def test_lost_completed_receipt_does_not_repeat_effect(run_api, monkeypatch):
    service, tasks, task, effects = run_api
    with _client(service) as client:
        _, headers = bootstrap(client)
        body = command(client, headers, task)
        complete = admissions.complete_command
        monkeypatch.setattr(admissions, "complete_command", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("lost receipt")))
        first = send(client, headers, body)
        assert first.status_code == 200 and first.json()["status"] == "partial", first.text
        monkeypatch.setattr(admissions, "complete_command", complete)
        assert send(client, headers, body).json()["status"] == "partial"
        assert effects == ["notification"]


def test_atomic_marker_failure_rolls_back_run_and_retains_empty_conversation(run_api, monkeypatch):
    service, tasks, task, effects = run_api
    original = task_execution.start_reviewed_task_run
    def fail_marker(*args, **kwargs):
        def bad(conn, run):
            raise task_execution.TaskExecutionError("action_denied")
        return original(*args, **{**kwargs, "record_commit": bad})
    monkeypatch.setattr(task_execution, "start_reviewed_task_run", fail_marker)
    with _client(service) as client:
        _, headers = bootstrap(client)
        result = send(client, headers, command(client, headers, task))
        assert result.status_code == 403, result.text
        assert not effects and not tasks.get_recent_runs()
        assert len(service.list_conversations()["items"]) == 1


def test_task_commands_reject_conversation_route(run_api):
    service, _, task, effects = run_api
    with _client(service) as client:
        _, headers = bootstrap(client)
        body = command(client, headers, task)
        result = client.post("/api/v1/conversations/unused/commands", json=body,
                             headers={**headers, "Idempotency-Key": body["command_id"]})
        assert result.status_code == 422 and not effects
