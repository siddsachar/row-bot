from dataclasses import asdict
import json
import sqlite3
import threading

import pytest

from row_bot.application import task_execution as execution
from tests.subsystem.workflows.test_workflow_profile_runtime import (
    _fresh_modules, _install_fake_agent, _run_workflow_synchronously,
)

pytestmark = pytest.mark.subsystem
_REAL_THREAD = threading.Thread


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    tasks, threads, profiles, agent_runs, _ = _fresh_modules(tmp_path, monkeypatch)
    monkeypatch.setattr(execution, "tasks", tasks)
    monkeypatch.setattr(execution, "AgentProfileError", profiles.AgentProfileError)
    monkeypatch.setattr(tasks, "_canonicalize_workflow_model_override", lambda value: value)
    calls = []
    _install_fake_agent(monkeypatch, calls)
    _run_workflow_synchronously(monkeypatch, tasks)
    monkeypatch.setattr(tasks, "_fire_completion_triggers", lambda *_, **__: None)
    monkeypatch.setattr(tasks, "_deliver_to_channels", lambda *_, **__: ("", ""))
    from row_bot import notifications
    monkeypatch.setattr(notifications, "notify", lambda **kwargs: None)
    task_id = tasks.create_task("Synthetic reviewed run", prompts=["Original prompt"], channels=[], enabled=False)
    conversation_id = threads.create_thread("Synthetic run", thread_id="reviewed-thread", seed_default_skills=False)
    return tasks, profiles, agent_runs, task_id, conversation_id, calls


def review(runtime):
    return execution.get_task_run_review(runtime[3], enabled_tool_names=["filesystem", "shell"])


def start(runtime, reviewed=None, **overrides):
    reviewed = reviewed or review(runtime)
    kwargs = dict(expected_task_revision=reviewed.task_revision, expected_policy_revision=reviewed.policy_revision,
                  stable_run_id="reviewed-run", conversation_id=runtime[4], enabled_tool_names=["filesystem", "shell"],
                  validate=lambda: None)
    kwargs.update(overrides)
    return execution.start_reviewed_task_run(runtime[3], **kwargs)


@pytest.fixture
def delivery_runtime(tmp_path, monkeypatch):
    tasks, threads, profiles, agent_runs, _ = _fresh_modules(tmp_path, monkeypatch)
    monkeypatch.setattr(execution, "tasks", tasks)
    monkeypatch.setattr(execution, "AgentProfileError", profiles.AgentProfileError)
    calls = []
    _install_fake_agent(monkeypatch, calls)
    _run_workflow_synchronously(monkeypatch, tasks)
    task_id = tasks.create_task("Synthetic delivery", prompts=["Original prompt"], channels=[], enabled=False)
    thread_id = threads.create_thread("Synthetic delivery", thread_id="delivery-thread", seed_default_skills=False)
    from row_bot import notifications
    monkeypatch.setattr(notifications, "notify", lambda **kwargs: None)
    return tasks, profiles, agent_runs, task_id, thread_id, calls


@pytest.mark.parametrize("notify_only", [False, True])
def test_reviewed_run_rechecks_authority_between_external_destinations(delivery_runtime, monkeypatch, notify_only):
    from row_bot.channels import registry
    runtime = delivery_runtime
    tasks = runtime[0]
    tasks.update_task(runtime[3], notify_only=notify_only, channels=["first", "second"])
    sent = []
    revoked = False

    class Channel:
        def __init__(self, name):
            self.name = self.display_name = name

        def get_default_target(self):
            return "synthetic-target"

        def send_message(self, target, text):
            nonlocal revoked
            sent.append(self.name)
            revoked = True

    monkeypatch.setattr(registry, "running_channels", lambda: [Channel("first"), Channel("second")])
    monkeypatch.setattr(tasks, "_fire_completion_triggers", lambda *args, **kwargs: pytest.fail("trigger after revocation"))

    def validate():
        if revoked:
            raise PermissionError("synthetic capability revoked")

    try:
        start(runtime, validate=validate)
    except execution.TaskExecutionError as exc:
        assert exc.committed
    assert sent == ["first"]
    assert tasks.read_task_run("reviewed-run")["status"] == "failed"


def test_notify_only_revocation_after_desktop_notification_blocks_channels(delivery_runtime, monkeypatch):
    from row_bot import notifications
    runtime = delivery_runtime
    tasks = runtime[0]
    tasks.update_task(runtime[3], notify_only=True)
    revoked = False
    sent = []

    def notify(**kwargs):
        nonlocal revoked
        revoked = True

    def validate():
        if revoked:
            raise PermissionError("synthetic capability revoked")

    monkeypatch.setattr(notifications, "notify", notify)
    monkeypatch.setattr(tasks, "_deliver_to_channels", lambda *args, **kwargs: sent.append("sent") or ("", ""))
    try:
        start(runtime, validate=validate)
    except execution.TaskExecutionError as exc:
        assert exc.committed
    assert sent == []
    assert tasks.read_task_run("reviewed-run")["status"] == "failed"


@pytest.mark.parametrize("notify_only", [False, True])
def test_reviewed_auto_delete_preserves_conversation_and_other_run_recovery(delivery_runtime, monkeypatch, notify_only):
    from row_bot import threads, thread_cleanup
    runtime = delivery_runtime
    tasks, _, _, task_id, thread_id, _ = runtime
    tasks.update_task(task_id, notify_only=notify_only, delete_after_run=True)
    monkeypatch.setattr(thread_cleanup, "delete_thread", lambda *_: pytest.fail("Ordinary conversation deleted"))
    monkeypatch.setattr(tasks, "_fire_completion_triggers", lambda *args, **kwargs: None)
    removed = []
    monkeypatch.setattr(tasks, "_remove_job", removed.append)
    conn = tasks._get_conn()
    conn.execute("INSERT INTO pipeline_state(run_id,task_id,thread_id,status) VALUES(?,?,?,'paused')",
                 ("other-run", task_id, thread_id))
    conn.execute("INSERT INTO approval_requests(id,run_id,task_id,step_id,resume_token,status) VALUES(?,?,?,?,?,'pending')",
                 ("other-approval", "other-run", task_id, "approval_1", "other-token"))
    conn.commit()
    conn.close()
    result = start(runtime)
    assert result.run.status == "completed"
    assert tasks.get_task(task_id) is None
    assert tasks.read_task_run("reviewed-run") is not None
    assert threads._thread_exists(thread_id)
    assert removed == [task_id]
    conn = tasks._get_conn()
    assert conn.execute("SELECT status FROM pipeline_state WHERE run_id='other-run'").fetchone()[0] == "paused"
    assert conn.execute("SELECT status FROM approval_requests WHERE id='other-approval'").fetchone()[0] == "pending"
    conn.close()


@pytest.mark.parametrize("notify_only", [False, True])
def test_reviewed_auto_delete_refuses_concurrent_user_edit(delivery_runtime, monkeypatch, notify_only):
    import sys
    from row_bot import notifications
    runtime = delivery_runtime
    tasks, _, _, task_id, _, _ = runtime
    tasks.update_task(task_id, notify_only=notify_only, delete_after_run=True)
    monkeypatch.setattr(tasks, "_fire_completion_triggers", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_remove_job", lambda *_: pytest.fail("Changed task timer removed"))
    monkeypatch.setattr(notifications, "notify", lambda **kwargs: tasks.update_task(task_id, description="New user edit"))
    if not notify_only:
        # Explicit client runs suppress the completion desktop notification.
        # A user edit while the fake provider returns is the actual normal path.
        original = sys.modules["row_bot.agent"].invoke_agent

        def invoke(*args, **kwargs):
            result = original(*args, **kwargs)
            tasks.update_task(task_id, description="New user edit")
            return result

        monkeypatch.setattr(sys.modules["row_bot.agent"], "invoke_agent", invoke)
    try:
        start(runtime)
    except execution.TaskExecutionError as exc:
        assert exc.committed
    assert tasks.get_task(task_id)["description"] == "New user edit"
    conn = tasks._get_conn()
    row = conn.execute("SELECT status,status_message FROM task_runs WHERE id='reviewed-run'").fetchone()
    assert row["status"] == "failed"
    assert row["status_message"] == "task_delete_revision_conflict"
    conn.close()


@pytest.mark.parametrize("after_admission", [False, True])
def test_reviewed_delete_rechecks_writer_admission_and_precommit(delivery_runtime, monkeypatch, after_admission):
    tasks, _, _, task_id, _, _ = delivery_runtime
    revision = tasks.read_task_for_edit(task_id)[1]
    original = tasks._get_conn
    revoked = False

    class Connection:
        def __init__(self):
            self.conn = original()

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def execute(self, sql, *args):
            nonlocal revoked
            result = self.conn.execute(sql, *args)
            if sql == ("BEGIN IMMEDIATE" if after_admission else "DELETE FROM tasks WHERE id = ?"):
                revoked = True
            return result

    monkeypatch.setattr(tasks, "_get_conn", Connection)
    monkeypatch.setattr(tasks, "_remove_job", lambda *_: pytest.fail("Timer removed before authorized commit"))

    def validate():
        if revoked:
            raise PermissionError("synthetic revocation")

    with pytest.raises(PermissionError):
        tasks.delete_task(task_id, expected_revision=revision, validate=validate, preserve_conversations=True)
    assert tasks.read_task_for_edit(task_id)[1] == revision


def test_completion_triggers_recheck_between_independent_launches(delivery_runtime, monkeypatch):
    from row_bot.tools import registry
    tasks, _, _, task_id, _, _ = delivery_runtime
    for index in range(2):
        tasks.create_task(f"Triggered {index}", prompts=["synthetic"], enabled=True,
                          trigger={"type": "task_complete", "target_task": task_id})
    monkeypatch.setattr(registry, "get_enabled_tools", lambda: [])
    prepared, launched = [], []
    revoked = False
    monkeypatch.setattr(tasks, "_prepare_task_thread", lambda task: prepared.append(task["id"]) or "synthetic-child")

    def launch(task_id, *args, **kwargs):
        nonlocal revoked
        launched.append(task_id)
        revoked = True

    def validate():
        if revoked:
            raise PermissionError("synthetic revocation")

    monkeypatch.setattr(tasks, "run_task_background", launch)
    with pytest.raises(tasks._WorkflowEffectDenied):
        tasks._fire_completion_triggers(task_id, validate=validate)
    assert len(launched) == 1 and prepared == launched


@pytest.mark.parametrize("operation", ["push", "resolve"])
def test_approval_channel_effects_recheck_each_destination(delivery_runtime, monkeypatch, operation):
    from row_bot.channels import registry
    tasks = delivery_runtime[0]
    effects = []
    revoked = False

    class Channel:
        def __init__(self, name):
            self.name = name

        def get_default_target(self):
            return "synthetic-target"

        def is_running(self):
            return True

        def send_approval_request(self, *args):
            nonlocal revoked
            effects.append(self.name)
            revoked = True
            return f"ref-{self.name}"

        def update_approval_message(self, *args, **kwargs):
            return self.send_approval_request()

    channels = [Channel("first"), Channel("second")]
    monkeypatch.setattr(tasks, "get_task_channels", lambda *_: channels)
    monkeypatch.setattr(registry, "get", lambda name: next(ch for ch in channels if ch.name == name))
    if operation == "resolve":
        for channel in channels:
            tasks._store_approval_channel_ref("synthetic-approval", channel.name, f"ref-{channel.name}")

    def validate():
        if revoked:
            raise PermissionError("synthetic revocation")

    with pytest.raises(tasks._WorkflowEffectDenied):
        if operation == "push":
            tasks._push_approval_to_channels({"name": "Synthetic"}, "synthetic-approval", "token", "Review", validate=validate)
        else:
            tasks._resolve_approval_on_channels("synthetic-approval", "denied", validate=validate)
    assert effects == ["first"]


def test_inline_subtask_retains_authority_through_post_provider_notification(delivery_runtime, monkeypatch):
    import sys
    from row_bot import notifications
    tasks, _, _, _, thread_id, _ = delivery_runtime
    child_id = tasks.create_task("Synthetic subtask", steps=[{"type": "prompt", "prompt": "Read"},
        {"type": "notify", "message": "Done", "channel": "desktop"}], enabled=False, channels=[])
    revoked = False
    effects = []

    def invoke(*args, **kwargs):
        nonlocal revoked
        effects.append("provider")
        revoked = True
        return "Synthetic result"

    def validate():
        if revoked:
            raise PermissionError("synthetic revocation")

    monkeypatch.setattr(sys.modules["row_bot.agent"], "invoke_agent", invoke)
    monkeypatch.setattr(notifications, "notify", lambda **kwargs: effects.append("notify"))
    with pytest.raises(tasks._WorkflowEffectDenied):
        tasks._run_subtask_sync(tasks.get_task(child_id), thread_id, [], {"configurable": {}},
                                threading.Event(), validate=validate)
    assert effects == ["provider"]


def test_review_does_not_execute_or_create_run_and_policy_hash_is_stable(runtime):
    first = review(runtime)
    second = review(runtime)
    assert first == second
    assert first.approval_mode == "block" and first.steps_total == 1
    assert runtime[0].get_run_history(runtime[3]) == []
    assert not runtime[5]
    assert "Original prompt" not in json.dumps(asdict(first))


def test_explicit_run_retains_stable_identity_and_mirrored_policy(runtime):
    result = start(runtime)
    assert result.run.id == "reviewed-run" and not result.replayed
    assert result.run.status == "completed"
    assert len(runtime[5]) == 1
    assert runtime[5][0]["prompt"] == "Original prompt"
    mirrored = runtime[2].get_agent_run("reviewed-run")
    assert mirrored["approval_mode"] == "block"
    assert mirrored["status"] == "completed"


def test_duplicate_run_command_reads_saved_result_without_dispatch(runtime):
    reviewed = review(runtime)
    first = start(runtime, reviewed)
    second = start(runtime, reviewed)
    assert second.replayed and second.run == first.run
    assert len(runtime[5]) == 1
    assert len(runtime[0].get_run_history(runtime[3])) == 1


def test_revision_change_prevents_run_before_any_effect(runtime):
    reviewed = review(runtime)
    runtime[0].update_task(runtime[3], prompts=["Changed"])
    with pytest.raises(execution.TaskExecutionError, match="task_revision_conflict"):
        start(runtime, reviewed)
    assert runtime[0].get_run_history(runtime[3]) == []
    assert not runtime[5]


def test_changed_enabled_tool_policy_requires_new_review(runtime):
    reviewed = review(runtime)
    with pytest.raises(execution.TaskExecutionError, match="task_policy_revision_conflict"):
        start(runtime, reviewed, enabled_tool_names=["filesystem"])
    assert not runtime[5]


def test_captured_prompt_and_policy_survive_edit_after_claim(runtime, monkeypatch):
    tasks = runtime[0]
    original = tasks.run_task_background
    def race(*args, **kwargs):
        tasks.update_task(runtime[3], prompts=["Unreviewed replacement"], safety_mode="allow_all")
        return original(*args, **kwargs)
    monkeypatch.setattr(tasks, "run_task_background", race)
    result = start(runtime)
    assert result.run.status == "completed"
    assert runtime[5][0]["prompt"] == "Original prompt"
    assert runtime[5][0]["config"]["configurable"]["approval_mode"] == "block"


def test_dispatch_uncertainty_never_relaunches_reserved_run(runtime, monkeypatch):
    reviewed = review(runtime)
    calls = []
    def interrupted(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("synthetic launch uncertainty")
    monkeypatch.setattr(runtime[0], "run_task_background", interrupted)
    with pytest.raises(execution.TaskExecutionError) as error:
        start(runtime, reviewed)
    assert error.value.committed and error.value.run_id == "reviewed-run"
    retried = start(runtime, reviewed)
    assert retried.replayed and retried.run.status == "starting"
    assert calls == [1]


def test_revocation_before_run_commit_rolls_back_both_existing_rows(runtime):
    count = 0
    def validate():
        nonlocal count
        count += 1
        if count == 3:
            raise PermissionError("synthetic revocation")
    with pytest.raises(PermissionError):
        start(runtime, validate=validate)
    conn = runtime[0]._get_conn()
    assert conn.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM pipeline_state").fetchone()[0] == 0
    conn.close()
    assert not runtime[5]


def test_receipt_proof_is_atomic_with_run_reservation(runtime):
    def record(conn, run_id):
        assert conn.in_transaction
        assert conn.execute("SELECT status FROM task_runs WHERE id=?", (run_id,)).fetchone()[0] == "starting"
        raise RuntimeError("synthetic receipt publication failure")
    with pytest.raises(RuntimeError):
        start(runtime, record_commit=record)
    assert runtime[0].get_run_history(runtime[3]) == []
    assert not runtime[5]


def test_wrong_conversation_cannot_replay_existing_run(runtime):
    reviewed = review(runtime)
    start(runtime, reviewed)
    with pytest.raises(execution.TaskExecutionError, match="task_run_identity_conflict"):
        start(runtime, reviewed, conversation_id="different-thread")
    assert len(runtime[5]) == 1


def test_history_pages_do_not_expose_private_status_messages_and_expire_on_progress(runtime):
    tasks, _, _, task_id, thread_id, _ = runtime
    conn = tasks._get_conn()
    conn.executemany(
        "INSERT INTO task_runs(id,task_id,thread_id,started_at,status,steps_total,steps_done,status_message) VALUES(?,?,?,?,?,?,?,?)",
        [(f"run-{i:03}", task_id, thread_id, f"2026-01-{1+i//24:02}T{i%24:02}:00:00", "running", 2, 0, "PRIVATE_PATH_TOKEN") for i in range(105)],
    )
    conn.commit()
    conn.close()
    page = execution.list_task_runs(task_id, limit=100)
    assert len(page.items) == 100 and page.total == 105 and page.next_cursor
    final = execution.list_task_runs(task_id, limit=100, cursor=page.next_cursor)
    assert len(final.items) == 5 and final.next_cursor is None
    assert "PRIVATE" not in json.dumps(asdict(page))
    tasks._update_run_progress(page.items[0].id, 1)
    with pytest.raises(execution.TaskExecutionError, match="cursor_expired"):
        execution.list_task_runs(task_id, limit=100, cursor=page.next_cursor)


def test_same_snapshot_profile_resolution_never_rereads_snapshot_owner(runtime, monkeypatch):
    profiles = runtime[1]
    monkeypatch.setattr(profiles, "snapshot_agent_profile", lambda *_: pytest.fail("profile reread"))
    assert review(runtime).approval_mode == "block"


def test_profile_row_and_task_row_share_writer_admission(runtime, monkeypatch):
    tasks, profiles = runtime[:2]
    profile = profiles.save_agent_profile(slug="reviewed-profile", display_name="Reviewed", tool_policy_json={"capability": "read_only"})
    tasks.update_task(runtime[3], agent_profile_id=profile["id"])
    original = profiles._profile_from_row
    def guarded(row):
        other = sqlite3.connect(tasks._DB_PATH, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("UPDATE agent_profiles SET revision=revision+1 WHERE id=?", (profile["id"],))
        finally:
            other.close()
        return original(row)
    monkeypatch.setattr(profiles, "_profile_from_row", guarded)
    assert review(runtime).agent_profile_id == profile["id"]


def test_concurrent_duplicate_reservations_dispatch_once(runtime, monkeypatch):
    tasks = runtime[0]
    reviewed = review(runtime)
    calls = []
    monkeypatch.setattr(tasks, "run_task_background", lambda *a, **k: calls.append(k["reviewed_capture"]["run_id"]))
    barrier = threading.Barrier(2)
    results, errors = [], []
    def submit():
        try:
            barrier.wait(timeout=5)
            results.append(start(runtime, reviewed))
        except Exception as exc:
            errors.append(exc)
    workers = [_REAL_THREAD(target=submit) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert not worker.is_alive()
    assert not errors
    assert calls == ["reviewed-run"] and sorted(result.replayed for result in results) == [False, True]


def pause(runtime):
    tasks = runtime[0]
    tasks.update_task(runtime[3], steps=[
        {"id": "review", "type": "approval", "message": "Approve synthetic step?"},
        {"id": "after", "type": "prompt", "prompt": "Reviewed next step"},
    ])
    started = start(runtime)
    assert started.run.status == "paused"
    page = execution.list_task_approvals(runtime[3], started.run.id)
    assert len(page.items) == 1
    return page.items[0]


def test_approval_resumes_captured_steps_policy_once_after_later_task_edit(runtime, monkeypatch):
    approval = pause(runtime)
    assert approval.response_available and approval.approval_mode == "block"
    assert "resume_token" not in json.dumps(asdict(approval))
    runtime[0].update_task(runtime[3], steps=[{"id": "new", "type": "prompt", "prompt": "Unreviewed replacement"}], safety_mode="allow_all")
    result = execution.respond_task_approval(runtime[3], "reviewed-run", approval.id,
        expected_revision=approval.revision, approved=True, validate=lambda: None)
    assert result.run.status == "completed"
    assert runtime[5][0]["prompt"] == "Reviewed next step"
    assert runtime[5][0]["config"]["configurable"]["approval_mode"] == "block"
    with pytest.raises(execution.TaskExecutionError):
        execution.respond_task_approval(runtime[3], "reviewed-run", approval.id,
            expected_revision=approval.revision, approved=True, validate=lambda: None)
    assert len(runtime[5]) == 1


def test_approval_denial_retains_existing_stop_semantics(runtime):
    approval = pause(runtime)
    result = execution.respond_task_approval(runtime[3], "reviewed-run", approval.id,
        expected_revision=approval.revision, approved=False, validate=lambda: None)
    assert result.run.status == "stopped" and not runtime[5]
    assert execution.list_task_approvals(runtime[3], "reviewed-run").total == 0


def test_approval_full_pipeline_revision_rejects_changed_config(runtime):
    approval = pause(runtime)
    conn = runtime[0]._get_conn()
    conn.execute("UPDATE pipeline_state SET current_step_index=99 WHERE run_id='reviewed-run'")
    conn.commit()
    conn.close()
    with pytest.raises(execution.TaskExecutionError, match="task_approval_revision_conflict"):
        execution.respond_task_approval(runtime[3], "reviewed-run", approval.id,
            expected_revision=approval.revision, approved=True, validate=lambda: None)
    assert not runtime[5]
    assert runtime[0].get_pending_approvals(approval_id=approval.id)


def test_approval_receipt_failure_rolls_back_claim_and_pipeline(runtime):
    approval = pause(runtime)
    def broken(conn, identity):
        assert conn.in_transaction and identity == approval.id
        raise RuntimeError("synthetic receipt failure")
    with pytest.raises(RuntimeError):
        execution.respond_task_approval(runtime[3], "reviewed-run", approval.id,
            expected_revision=approval.revision, approved=True, validate=lambda: None, record_commit=broken)
    assert execution.list_task_approvals(runtime[3], "reviewed-run").items[0].revision == approval.revision


def test_approval_dispatch_uncertainty_never_repeats_claim(runtime, monkeypatch):
    approval = pause(runtime)
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("synthetic resume failure")
    monkeypatch.setattr(runtime[0], "resume_reviewed_task_approval", fail)
    with pytest.raises(execution.TaskExecutionError) as error:
        execution.respond_task_approval(runtime[3], "reviewed-run", approval.id,
            expected_revision=approval.revision, approved=True, validate=lambda: None)
    assert error.value.code == "task_approval_unconfirmed" and error.value.committed
    with pytest.raises(execution.TaskExecutionError):
        execution.respond_task_approval(runtime[3], "reviewed-run", approval.id,
            expected_revision=approval.revision, approved=True, validate=lambda: None)
    assert calls == [1]


def test_other_approval_owner_is_explicitly_unavailable(runtime):
    approval = pause(runtime)
    conn = runtime[0]._get_conn()
    conn.execute("UPDATE approval_requests SET resume_kind='agent_run' WHERE id=?", (approval.id,))
    conn.commit()
    conn.close()
    current = execution.list_task_approvals(runtime[3], "reviewed-run").items[0]
    assert not current.response_available
    with pytest.raises(execution.TaskExecutionError, match="task_approval_owner_required"):
        execution.respond_task_approval(runtime[3], "reviewed-run", current.id,
            expected_revision=current.revision, approved=True, validate=lambda: None)


def test_stop_before_dispatch_prevents_late_provider_effect_and_not_other_conversation_work(runtime, monkeypatch):
    tasks = runtime[0]
    original = tasks.run_task_background
    pending = []
    monkeypatch.setattr(tasks, "run_task_background", lambda *a, **k: pending.append((a, k)))
    result = start(runtime)
    assert result.run.status == "starting"
    from row_bot.runtime.executions import generation_registry
    unrelated = generation_registry.register(runtime[4], domain="conversation", domain_id="unrelated")
    try:
        stopped = execution.stop_task_run(runtime[3], result.run.id, validate=lambda: None)
        assert stopped.stop_requested and not stopped.quiesced and stopped.run.status == "stopping"
        assert not unrelated.cancel_scope.is_cancelled()
        args, kwargs = pending[0]
        # The actual queued producer acknowledges the durable stop on entry.
        try:
            original(*args, **kwargs)
        except InterruptedError:
            pass
        assert execution.get_task_run(runtime[3], result.run.id).status == "stopped"
        assert not runtime[5]
    finally:
        generation_registry.finish(unrelated)


def test_stop_paused_run_cancels_pending_approval_and_acknowledges_current_owner(runtime):
    approval = pause(runtime)
    stopped = execution.stop_task_run(runtime[3], "reviewed-run", validate=lambda: None)
    assert stopped.stop_requested and stopped.quiesced and stopped.run.status == "stopped"
    assert not runtime[0].get_pending_approvals(approval_id=approval.id)


def test_tool_catalog_rechecked_inside_writer_admission(runtime):
    reviewed = review(runtime)
    with pytest.raises(execution.TaskExecutionError, match="task_policy_revision_conflict"):
        start(runtime, reviewed, refresh_enabled_tool_names=lambda: [])
    assert not runtime[5] and not runtime[0].get_run_history(runtime[3])


def test_pending_approval_waits_for_actual_producer_quiescence_for_both_clients(runtime):
    approval = pause(runtime)
    from row_bot.runtime.executions import generation_registry
    handle = generation_registry.register(runtime[4], domain="workflow", domain_id="reviewed-run")
    token = runtime[0].get_pending_approvals(approval_id=approval.id)[0]["resume_token"]
    try:
        assert not execution.list_task_approvals(runtime[3], "reviewed-run").items[0].response_available
        with pytest.raises(execution.TaskExecutionError, match="task_run_draining"):
            execution.respond_task_approval(runtime[3], "reviewed-run", approval.id,
                expected_revision=approval.revision, approved=True, validate=lambda: None)
        assert runtime[0].respond_to_approval(token, True) is False
        assert runtime[0].get_pending_approvals(approval_id=approval.id)
    finally:
        generation_registry.finish(handle)
    assert execution.respond_task_approval(runtime[3], "reviewed-run", approval.id,
        expected_revision=approval.revision, approved=True, validate=lambda: None).run.status == "completed"


def test_expired_and_oversized_approval_reviews_never_resume(runtime):
    approval = pause(runtime)
    conn = runtime[0]._get_conn()
    conn.execute("UPDATE approval_requests SET timeout_at='2000-01-01T00:00:00' WHERE id=?", (approval.id,))
    conn.commit()
    conn.close()
    current = execution.list_task_approvals(runtime[3], "reviewed-run").items[0]
    with pytest.raises(execution.TaskExecutionError, match="task_approval_expired"):
        execution.respond_task_approval(runtime[3], "reviewed-run", approval.id,
            expected_revision=current.revision, approved=True, validate=lambda: None)
    conn = runtime[0]._get_conn()
    conn.execute("UPDATE approval_requests SET timeout_at=NULL,message=? WHERE id=?", ("x" * 16385, approval.id))
    conn.commit()
    conn.close()
    current = execution.list_task_approvals(runtime[3], "reviewed-run").items[0]
    assert current.message_truncated and len(current.message) == 16384 and not current.response_available
    with pytest.raises(execution.TaskExecutionError, match="task_approval_review_incomplete"):
        execution.respond_task_approval(runtime[3], "reviewed-run", approval.id,
            expected_revision=current.revision, approved=True, validate=lambda: None)
    assert not runtime[5]


def test_approval_pages_bound_escaped_unicode_bytes_and_reach_every_item(runtime):
    approval = pause(runtime)
    tasks = runtime[0]
    conn = tasks._get_conn()
    template = dict(conn.execute("SELECT * FROM approval_requests WHERE id=?", (approval.id,)).fetchone())
    conn.execute("DELETE FROM approval_requests WHERE id=?", (approval.id,))
    for index in range(17):
        row = {**template, "id": f"approval-{index:03}", "resume_token": f"private-token-{index:03}", "message": "\U0001f642" * 16384}
        columns = list(row)
        conn.execute(f"INSERT INTO approval_requests ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", list(row.values()))
    conn.commit()
    conn.close()
    cursor, ids, revisions = None, [], set()
    while True:
        page = execution.list_task_approvals(runtime[3], "reviewed-run", cursor=cursor)
        assert len(json.dumps(asdict(page), ensure_ascii=True).encode()) <= 240 * 1024
        assert page.total == 17 and 1 <= len(page.items) <= 8
        assert all(not item.message_truncated for item in page.items)
        ids.extend(item.id for item in page.items)
        revisions.add(page.revision)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert ids == [f"approval-{i:03}" for i in range(17)] and len(revisions) == 1


def test_approval_cursor_binds_filters_and_full_pipeline_revision(runtime):
    approval = pause(runtime)
    conn = runtime[0]._get_conn()
    row = dict(conn.execute("SELECT * FROM approval_requests WHERE id=?", (approval.id,)).fetchone())
    row.update(id="approval-second", resume_token="private-second")
    conn.execute(f"INSERT INTO approval_requests ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", list(row.values()))
    conn.commit()
    conn.close()
    page = execution.list_task_approvals(runtime[3], "reviewed-run", limit=1)
    assert page.next_cursor
    with pytest.raises(execution.TaskExecutionError, match="cursor_expired"):
        execution.list_task_approvals(runtime[3], "reviewed-run", limit=2, cursor=page.next_cursor)
    conn = runtime[0]._get_conn()
    conn.execute("UPDATE pipeline_state SET step_outputs='{} ',updated_at='changed' WHERE run_id='reviewed-run'")
    conn.commit()
    conn.close()
    with pytest.raises(execution.TaskExecutionError, match="cursor_expired"):
        execution.list_task_approvals(runtime[3], "reviewed-run", limit=1, cursor=page.next_cursor)


def test_graph_interrupt_resume_keeps_captured_policy_and_one_owner_through_remaining_steps(runtime, monkeypatch):
    import sys
    fake = sys.modules["row_bot.agent"]
    calls, resumed = [], []
    def invoke(prompt, tools, config, stop_event=None):
        calls.append((prompt, config["configurable"]["approval_mode"]))
        if len(calls) == 1:
            return {"type": "interrupt", "interrupts": [{"tool": "filesystem", "description": "Review synthetic write"}]}
        return "Finished tail"
    def resume(tools, config, approved=True, stop_event=None):
        from row_bot.runtime.executions import generation_registry
        resumed.append((approved, config["configurable"]["approval_mode"], len(generation_registry.active(runtime[4]))))
        return "Approved synthetic output"
    monkeypatch.setattr(fake, "invoke_agent", invoke)
    monkeypatch.setattr(fake, "resume_invoke_agent", resume)
    runtime[0].update_task(runtime[3], prompts=["First graph prompt", "Captured tail"], safety_mode="approve")
    assert start(runtime).run.status == "paused"
    card = execution.list_task_approvals(runtime[3], "reviewed-run").items[0]
    runtime[0].update_task(runtime[3], prompts=["Replacement"], safety_mode="allow_all")
    result = execution.respond_task_approval(runtime[3], "reviewed-run", card.id,
        expected_revision=card.revision, approved=True, validate=lambda: None)
    assert result.run.status == "completed"
    assert calls == [("First graph prompt", "approve"), ("Captured tail", "approve")]
    assert resumed == [(True, "approve", 1)]


def test_stop_active_provider_retains_exact_scope_until_it_returns(runtime, monkeypatch):
    import sys
    from row_bot.runtime.executions import generation_registry
    entered, release = threading.Event(), threading.Event()
    fake = sys.modules["row_bot.agent"]
    scopes = []
    def invoke(prompt, tools, config, stop_event=None):
        scopes.append(stop_event)
        entered.set()
        assert release.wait(timeout=10)
        raise fake.TaskStoppedError("synthetic stop acknowledgment")
    monkeypatch.setattr(fake, "invoke_agent", invoke)
    monkeypatch.setattr(runtime[0].threading, "Thread", _REAL_THREAD)
    result = start(runtime)
    assert entered.wait(timeout=10)
    handles = [h for h in generation_registry.active(runtime[4]) if h.domain_id == result.run.id]
    assert len(handles) == 1
    try:
        stopped = execution.stop_task_run(runtime[3], result.run.id, validate=lambda: None)
        assert stopped.stop_requested and not stopped.quiesced
        assert scopes[0].is_set() and not handles[0].producer_done.is_set()
    finally:
        release.set()
        assert handles[0].producer_done.wait(timeout=10)
    assert execution.get_task_run(runtime[3], result.run.id).status == "stopped"
