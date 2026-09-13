from dataclasses import asdict, replace
import sqlite3
import threading

import pytest

from row_bot.application import task_controls as control
from tests.fixtures.tasks import fresh_tasks_module, sample_workflow_steps

pytestmark = pytest.mark.subsystem


@pytest.fixture
def owner(tmp_path, monkeypatch):
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    # Adjacent agent fixtures replace sys.modules entries. Bind the application
    # projection to this test's actual isolated canonical owner, not that prior module.
    monkeypatch.setattr(control, "tasks", tasks)
    monkeypatch.setattr(tasks, "_canonicalize_agent_profile_reference", lambda value: value)
    monkeypatch.setattr(tasks, "_canonicalize_workflow_model_override", lambda value: value)
    monkeypatch.setattr(tasks, "run_task_background", lambda *a, **k: pytest.fail("unexpected run"))
    monkeypatch.setattr(tasks, "_get_scheduler", lambda: pytest.fail("unexpected scheduler startup"))
    return tasks


@pytest.fixture
def fields():
    return control.TaskEditableFields(
        name="Synthetic workflow", description="Review before running", icon="",
        prompts=("Summarize synthetic text",), enabled=False, schedule=None, at=None,
        notify_only=False, notify_label="", channels=None,
    )


def create(fields):
    return control.create_saved_task(fields, stable_task_id="command-task-1", validate=lambda: None)


def test_create_exact_saved_fields_without_execution(owner, fields):
    result = create(fields)
    assert result.created and not result.replayed
    assert result.task.fields == fields
    assert result.task.id == "command-task-1"
    assert result.task.approval_mode == "block"
    assert result.task.conversation_id is None
    assert len(result.task.revision) == 64
    assert owner.get_recent_runs() == []
    assert len(owner.list_tasks()) == 1


@pytest.mark.parametrize("channels", [None, (), ("telegram", "slack")])
def test_channel_inheritance_distinction_saved_and_reloaded(owner, fields, channels):
    result = create(replace(fields, channels=channels))
    assert result.task.fields.channels == channels
    updated = control.update_saved_task(
        result.task.id, replace(result.task.fields, name="Edited"),
        expected_revision=result.task.revision, validate=lambda: None,
    )
    assert updated.task.fields.channels == channels


def test_crash_after_insert_replays_identity_without_rewriting(owner, fields, monkeypatch):
    original = owner.create_task
    def committed_then_lost(**kwargs):
        original(**kwargs)
        raise RuntimeError("synthetic lost response")
    monkeypatch.setattr(owner, "create_task", committed_then_lost)
    with pytest.raises(RuntimeError):
        create(fields)
    saved = owner.read_task_for_edit("command-task-1")
    monkeypatch.setattr(owner, "create_task", original)
    result = create(fields)
    assert result.replayed and not result.created
    assert owner.read_task_for_edit("command-task-1") == saved
    assert len(owner.list_tasks()) == 1


def test_retry_does_not_overwrite_a_concurrent_edit(owner, fields):
    initial = create(fields)
    owner.update_task(initial.task.id, name="Someone else's edit")
    with pytest.raises(control.TaskControlError, match="task_creation_conflict"):
        create(fields)
    assert owner.get_task(initial.task.id)["name"] == "Someone else's edit"


def test_duplicate_create_concurrent_admission(owner, fields):
    barrier = threading.Barrier(2)
    results, errors = [], []
    def submit():
        try:
            barrier.wait(timeout=5)
            results.append(create(fields))
        except Exception as exc:
            errors.append(exc)
    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert not errors
    assert sorted(result.created for result in results) == [False, True]
    assert len(owner.list_tasks()) == 1


def test_full_row_revision_rejects_hidden_metadata_change(owner, fields):
    initial = create(fields).task
    owner.update_task(initial.id, last_run="2026-09-01T12:00:00")
    with pytest.raises(control.TaskControlError, match="task_revision_conflict"):
        control.update_saved_task(initial.id, replace(fields, name="Overwrite"),
                                  expected_revision=initial.revision, validate=lambda: None)
    assert owner.get_task(initial.id)["name"] == fields.name


def test_racing_update_between_review_and_owner_cas_is_rejected(owner, fields, monkeypatch):
    initial = create(fields).task
    original = owner.update_task
    def race(task_id, **kwargs):
        original(task_id, description="Concurrent save")
        return original(task_id, **kwargs)
    monkeypatch.setattr(owner, "update_task", race)
    with pytest.raises(control.TaskControlError, match="task_revision_conflict"):
        control.update_saved_task(initial.id, replace(fields, name="Overwrite"),
                                  expected_revision=initial.revision, validate=lambda: None)
    assert owner.get_task(initial.id)["description"] == "Concurrent save"


@pytest.mark.parametrize("operation", ["create", "update"])
def test_revocation_before_commit_rolls_back_all_fields(owner, fields, operation):
    initial = create(fields).task if operation == "update" else None
    count = 0
    def validate():
        nonlocal count
        count += 1
        if count == 3:  # service check, writer admission, immediately before commit
            raise PermissionError("synthetic revoked")
    with pytest.raises(PermissionError):
        if initial is None:
            control.create_saved_task(fields, stable_task_id="command-task-1", validate=validate)
        else:
            control.update_saved_task(initial.id, replace(fields, name="Unsaved", enabled=True),
                                      expected_revision=initial.revision, validate=validate)
    if initial is None:
        assert owner.get_task("command-task-1") is None
    else:
        assert control.get_task_editor(initial.id) == initial


@pytest.mark.parametrize("schedule", ["garbage", "daily:25:00", "daily:12:00:30", "weekly:no:10:00",
                                     "interval:nan", "interval:inf", "interval:-1",
                                     "interval_minutes:0.5", "cron:nope"])
def test_invalid_schedule_never_saves(owner, fields, schedule):
    with pytest.raises(control.TaskControlError, match="invalid_task_schedule"):
        create(replace(fields, schedule=schedule))
    assert owner.list_tasks() == []


@pytest.mark.parametrize("schedule", ["daily:09:30", "weekly:monday:12:01", "interval:0.5",
                                     "interval_minutes:5", "cron:0 8 * * mon"])
def test_canonical_schedule_forms_remain_supported(owner, fields, schedule):
    assert create(replace(fields, schedule=schedule)).task.fields.schedule == schedule


@pytest.mark.parametrize("at", ["bad", "2026-09-11T15:30:00+01:00"])
def test_invalid_local_date_never_saves(owner, fields, at):
    with pytest.raises(control.TaskControlError, match="invalid_task_schedule"):
        create(replace(fields, at=at))
    assert not owner.list_tasks()


def test_advanced_graph_approval_and_origin_survive_basic_edit(owner, fields):
    task_id = owner.create_task(name=fields.name, steps=sample_workflow_steps(),
                               persistent_thread_id="conversation-synthetic", enabled=False)
    before = owner.get_task(task_id)
    snapshot = control.get_task_editor(task_id)
    assert snapshot.advanced
    result = control.update_saved_task(task_id, replace(snapshot.fields, name="Renamed", channels=()),
                                      expected_revision=snapshot.revision, validate=lambda: None)
    after = owner.get_task(task_id)
    for key in ("steps", "safety_mode", "agent_profile_id", "persistent_thread_id", "trigger"):
        assert after[key] == before[key]
    assert result.task.conversation_id == "conversation-synthetic"
    with pytest.raises(control.TaskControlError, match="task_advanced_edit_required"):
        control.update_saved_task(task_id, replace(result.task.fields, prompts=("Replace graph",)),
                                  expected_revision=result.task.revision, validate=lambda: None)


def test_legacy_destination_is_private_and_preserved(owner, fields):
    initial = create(fields).task
    conn = owner._get_conn()
    conn.execute("UPDATE tasks SET delivery_channel='telegram', delivery_target='PRIVATE' WHERE id=?", (initial.id,))
    conn.commit()
    conn.close()
    snapshot = control.get_task_editor(initial.id)
    assert snapshot.legacy_delivery
    assert "PRIVATE" not in str(asdict(snapshot))
    with pytest.raises(control.TaskControlError, match="task_delivery_review_required"):
        control.update_saved_task(initial.id, replace(fields, channels=()),
                                  expected_revision=snapshot.revision, validate=lambda: None)
    result = control.update_saved_task(initial.id, replace(fields, name="Renamed"),
                                      expected_revision=snapshot.revision, validate=lambda: None)
    assert result.task.legacy_delivery
    assert owner.get_task(initial.id)["delivery_target"] == "PRIVATE"


def test_post_commit_scheduler_failure_is_recoverable_without_row_rewrite(owner, fields, monkeypatch):
    monkeypatch.setattr(owner, "_scheduler", object())
    calls = []
    def sync(row):
        calls.append(row["id"])
        if len(calls) == 1:
            raise RuntimeError("synthetic scheduler failure")
    monkeypatch.setattr(owner, "_sync_job", sync)
    with pytest.raises(control.TaskControlError) as failure:
        create(fields)
    assert failure.value.code == "task_schedule_unconfirmed"
    assert failure.value.committed
    saved = owner.read_task_for_edit("command-task-1")
    result = create(fields)
    assert result.replayed and calls == ["command-task-1", "command-task-1"]
    assert owner.read_task_for_edit("command-task-1") == saved


def test_scheduler_recovery_captures_current_metadata_after_writer_admission(owner, fields, monkeypatch):
    initial = create(fields).task
    owner.update_task(initial.id, schedule="daily:11:30")
    monkeypatch.setattr(owner, "_scheduler", object())
    captured = []
    def sync(row):
        captured.append(row)
        # A different SQLite writer cannot replace the reviewed schedule
        # during the local scheduler effect, even in another process.
        other = sqlite3.connect(owner._DB_PATH, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("UPDATE tasks SET schedule='daily:12:00' WHERE id=?", (initial.id,))
        finally:
            other.close()
    monkeypatch.setattr(owner, "_sync_job", sync)
    owner.sync_task_schedule(initial.id, validate=lambda: None)
    assert captured[0]["schedule"] == "daily:11:30"


def test_invalid_stored_schedule_can_be_reviewed_and_repaired(owner, fields):
    initial = create(fields).task
    owner.update_task(initial.id, schedule="invalid-legacy")
    snapshot = control.get_task_editor(initial.id)
    assert snapshot.fields.schedule == "invalid-legacy"
    result = control.update_saved_task(initial.id, replace(snapshot.fields, schedule=None),
                                      expected_revision=snapshot.revision, validate=lambda: None)
    assert result.task.fields.schedule is None


@pytest.mark.parametrize("change", [
    {"name": " "}, {"name": "x" * 257}, {"description": "x" * 4097},
    {"enabled": 1}, {"prompts": ("",)}, {"prompts": ("x" * 16385,)},
    {"prompts": tuple("x" * 16000 for _ in range(5))},
    {"channels": ("slack", "slack")}, {"channels": ("../private",)},
    {"schedule": "daily:10:00", "at": "2027-01-01T10:00"},
])
def test_invalid_fields_never_enter_task_writer(owner, fields, monkeypatch, change):
    monkeypatch.setattr(owner, "create_task", lambda **kwargs: pytest.fail("invalid input reached writer"))
    with pytest.raises(control.TaskControlError):
        create(replace(fields, **change))


def test_reminder_needs_no_fake_prompt(owner, fields):
    result = create(replace(fields, notify_only=True, prompts=(), notify_label="Review later"))
    assert result.task.fields.prompts == ()
    assert owner.get_task(result.task.id)["notify_only"]


def test_post_commit_read_failure_retains_recovery_identity(owner, fields, monkeypatch):
    original = control.get_task_editor
    monkeypatch.setattr(control, "get_task_editor", lambda task_id: (_ for _ in ()).throw(sqlite3.OperationalError("synthetic")))
    with pytest.raises(control.TaskControlError) as failure:
        create(fields)
    assert failure.value.code == "task_saved_read_unconfirmed"
    assert failure.value.task_id == "command-task-1" and failure.value.committed
    monkeypatch.setattr(control, "get_task_editor", original)
    assert create(fields).replayed
    assert len(owner.list_tasks()) == 1


def test_revoked_replay_cannot_read_or_reconcile(owner, fields, monkeypatch):
    create(fields)
    monkeypatch.setattr(owner, "sync_task_schedule", lambda *a, **k: pytest.fail("revoked reconciliation"))
    monkeypatch.setattr(control, "get_task_editor", lambda *a: pytest.fail("revoked detail read"))
    with pytest.raises(PermissionError):
        control.create_saved_task(fields, stable_task_id="command-task-1",
                                  validate=lambda: (_ for _ in ()).throw(PermissionError("revoked")))


def test_oversized_legacy_metadata_is_rejected_before_materialization_without_changes(owner, fields, monkeypatch):
    initial = create(fields).task
    conn = owner._get_conn()
    conn.execute("UPDATE tasks SET profile_migration_snapshot_json=? WHERE id=?",
                 ('{"synthetic":"' + 'x' * (2 * 1024 * 1024) + '"}', initial.id))
    conn.commit()
    conn.close()
    monkeypatch.setattr(owner, "_row_to_dict", lambda row: pytest.fail("oversized row materialized"))
    with pytest.raises(control.TaskControlError, match="task_metadata_too_large"):
        control.get_task_editor(initial.id)
    with pytest.raises(owner.TaskMutationError, match="task_metadata_too_large"):
        owner.update_task(initial.id, expected_revision=initial.revision, name="Overwrite")
    conn = owner._get_conn()
    assert conn.execute("SELECT name FROM tasks WHERE id=?", (initial.id,)).fetchone()[0] == fields.name
    conn.close()
