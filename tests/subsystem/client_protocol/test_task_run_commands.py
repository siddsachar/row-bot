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


def paused_run(client, headers, tasks, task, monkeypatch):
    from tests.subsystem.workflows.test_workflow_profile_runtime import _install_fake_agent
    from row_bot.runtime.executions import generation_registry
    calls = []
    _install_fake_agent(monkeypatch, calls)
    monkeypatch.setattr(tasks, "_push_approval_to_channels", lambda *a, **kw: None)
    monkeypatch.setattr(tasks, "_resolve_approval_on_channels", lambda *a, **kw: None)
    tasks.update_task(task, notify_only=False, steps=[
        {"id": "review", "type": "approval", "message": "Approve synthetic step?"},
        {"id": "after", "type": "prompt", "prompt": "Reviewed next step"},
    ])
    response = send(client, headers, command(client, headers, task))
    assert response.status_code == 200, response.text
    receipt = response.json()
    for handle in generation_registry.active(receipt["conversation_id"]):
        if not handle.producer_done.wait(5):
            import faulthandler
            faulthandler.dump_traceback()
            pytest.fail("Workflow producer did not quiesce within its 5 second budget")
    page = client.get(f"/api/v1/tasks/{task}/runs/{receipt['task_run_id']}/approvals", headers=headers)
    assert page.status_code == 200, page.text
    assert len(page.json()["items"]) == 1
    review = page.json()["items"][0]
    assert review["nonce"] and review["response_available"]
    body = {"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"], "type": "task.approval",
            "expected_revision": "0", "payload": {"task_id": task, "run_id": receipt["task_run_id"],
                "approval_id": review["id"], "approval_revision": review["revision"], "approved": True, "nonce": review["nonce"]}}
    return receipt, body, calls


def test_review_nonce_decision_and_replay_resume_only_once(run_api, monkeypatch):
    service, tasks, task, _ = run_api
    from row_bot.runtime.executions import generation_registry
    with _client(service) as client:
        _, headers = bootstrap(client)
        receipt, body, calls = paused_run(client, headers, tasks, task, monkeypatch)
        invalid = {**body, "payload": {**body["payload"], "nonce": "wrong"}}
        assert send(client, headers, invalid).status_code == 409
        result = send(client, headers, body)
        assert result.status_code == 200, result.text
        assert result.json()["task_approval_recorded"] and result.json()["task_approval_decision"] == "approved"
        for handle in generation_registry.active(receipt["conversation_id"]):
            assert handle.producer_done.wait(5)
        assert send(client, headers, body).json() == result.json()
        assert len(calls) == 1 and calls[0]["prompt"] == "Reviewed next step"
        conn = tasks._get_conn()
        serialized = conn.execute("SELECT result_json FROM client_commands WHERE command_id=?", (body["command_id"],)).fetchone()[0]
        conn.close()
        assert body["payload"]["nonce"] not in serialized


def test_recorded_approval_dispatch_loss_never_repeats(run_api, monkeypatch):
    service, tasks, task, _ = run_api
    with _client(service) as client:
        _, headers = bootstrap(client)
        _, body, calls = paused_run(client, headers, tasks, task, monkeypatch)
        def lose(*a, **kw):
            raise RuntimeError("synthetic lost resume")
        monkeypatch.setattr(tasks, "resume_reviewed_task_approval", lose)
        first = send(client, headers, body)
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "partial" and first.json()["task_approval_recorded"]
        monkeypatch.setattr(tasks, "resume_reviewed_task_approval", lambda *a, **kw: pytest.fail("Repeated approval effect"))
        assert send(client, headers, body).json()["status"] == "partial"
        assert not calls
