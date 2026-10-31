"""Run now: a reviewed, idempotent start of one run in the workflow's own conversation (F31).

What a run then does is a chat turn (test_workflow_runs_as_chat); here the turn's start is
recorded instead, so only the command's contract is under test.
"""
from dataclasses import asdict
import json
import threading

import pytest

from row_bot.application import task_execution as execution

pytestmark = pytest.mark.subsystem
_REAL_THREAD = threading.Thread


@pytest.fixture
def runtime(tmp_path, monkeypatch, reload_for_data_dir):
    tasks, threads, profiles = reload_for_data_dir(
        tmp_path / "data", "row_bot.tasks", "row_bot.threads", "row_bot.agent_profiles")
    from row_bot import notifications, workflow_turns
    monkeypatch.setattr(execution, "tasks", tasks)
    monkeypatch.setattr(execution, "AgentProfileError", profiles.AgentProfileError)
    monkeypatch.setattr(notifications, "notify", lambda **kwargs: None)
    monkeypatch.setattr(tasks, "_deliver_to_channels", lambda *_, **__: ("", ""))
    started: list[dict] = []
    monkeypatch.setattr(workflow_turns, "start", lambda task, thread_id, run_id, trigger="Run": started.append(
        {"messages": list(task["prompts"]), "thread_id": thread_id, "run_id": run_id, "trigger": trigger}) or True)
    task_id = tasks.create_task("Synthetic reviewed run", prompts=["Original prompt"], channels=[], enabled=False)
    conversation_id = tasks._prepare_task_thread(tasks.get_task(task_id))
    return tasks, task_id, conversation_id, started


def review(runtime):
    return execution.get_task_run_review(runtime[1], enabled_tool_names=["filesystem", "shell"])


def start(runtime, reviewed=None, **overrides):
    reviewed = reviewed or review(runtime)
    kwargs = dict(expected_task_revision=reviewed.task_revision, expected_policy_revision=reviewed.policy_revision,
                  stable_run_id="reviewed-run", conversation_id=runtime[2], enabled_tool_names=["filesystem", "shell"],
                  validate=lambda: None)
    kwargs.update(overrides)
    return execution.start_reviewed_task_run(runtime[1], **kwargs)


def test_review_does_not_run_and_its_policy_is_stable(runtime):
    first, second = review(runtime), review(runtime)
    assert first == second
    assert (first.approval_mode, first.steps_total, first.conversation_id) == ("auto", 1, runtime[2])
    assert runtime[0].get_run_history(runtime[1]) == []
    assert not runtime[3]
    assert "Original prompt" not in json.dumps(asdict(first))


def test_run_now_starts_one_turn_in_the_workflows_conversation(runtime):
    result = start(runtime)
    assert result.run.id == "reviewed-run" and not result.replayed
    assert result.run.conversation_id == runtime[2]
    assert runtime[3] == [{"messages": ["Original prompt"], "thread_id": runtime[2], "run_id": "reviewed-run",
                           "trigger": "Run"}]


def test_a_repeated_run_command_reads_the_saved_run_without_starting_another(runtime):
    reviewed = review(runtime)
    first = start(runtime, reviewed)
    second = start(runtime, reviewed)
    assert second.replayed and second.run == first.run
    assert len(runtime[3]) == 1


def test_an_edit_after_review_prevents_the_run_before_anything_happens(runtime):
    reviewed = review(runtime)
    runtime[0].update_task(runtime[1], prompts=["Changed"])
    with pytest.raises(execution.TaskExecutionError, match="task_revision_conflict"):
        start(runtime, reviewed)
    assert runtime[0].get_run_history(runtime[1]) == []
    assert not runtime[3]


def test_a_changed_approval_level_needs_a_new_review(runtime):
    reviewed = review(runtime)
    runtime[0].update_task(runtime[1], safety_mode="approve")
    with pytest.raises(execution.TaskExecutionError, match="task_revision_conflict|task_policy_revision_conflict"):
        start(runtime, reviewed)
    assert not runtime[3]


def test_the_messages_reviewed_are_the_ones_sent_after_a_later_edit(runtime, monkeypatch):
    tasks = runtime[0]
    original = tasks.run_task_background

    def race(*args, **kwargs):
        tasks.update_task(runtime[1], prompts=["Unreviewed replacement"])
        return original(*args, **kwargs)

    monkeypatch.setattr(tasks, "run_task_background", race)
    start(runtime)
    assert runtime[3][0]["messages"] == ["Original prompt"]


def test_an_uncertain_start_never_starts_the_reserved_run_again(runtime, monkeypatch):
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


def test_a_receipt_failure_leaves_no_run(runtime):
    def record(conn, run_id):
        assert conn.in_transaction
        assert conn.execute("SELECT status FROM task_runs WHERE id=?", (run_id,)).fetchone()[0] == "starting"
        raise RuntimeError("synthetic receipt publication failure")

    with pytest.raises(RuntimeError):
        start(runtime, record_commit=record)
    assert runtime[0].get_run_history(runtime[1]) == []
    assert not runtime[3]


def test_another_conversation_cannot_replay_the_run(runtime):
    reviewed = review(runtime)
    start(runtime, reviewed)
    with pytest.raises(execution.TaskExecutionError, match="task_run_identity_conflict"):
        start(runtime, reviewed, conversation_id="different-thread")
    assert len(runtime[3]) == 1


def test_two_identical_run_commands_at_once_start_one_run(runtime, monkeypatch):
    reviewed = review(runtime)
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
    assert len(runtime[3]) == 1 and sorted(result.replayed for result in results) == [False, True]


def test_history_pages_hide_private_status_messages_and_expire_on_progress(runtime):
    tasks, task_id, thread_id, _ = runtime
    conn = tasks._get_conn()
    conn.executemany(
        "INSERT INTO task_runs(id,task_id,thread_id,started_at,status,steps_total,steps_done,status_message) "
        "VALUES(?,?,?,?,?,?,?,?)",
        [(f"run-{i:03}", task_id, thread_id, f"2026-01-{1 + i // 24:02}T{i % 24:02}:00:00", "running", 2, 0,
          "PRIVATE_PATH_TOKEN") for i in range(105)],
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


def test_a_one_time_run_keeps_its_answer_after_the_task_goes(runtime, tmp_path, monkeypatch):
    # "Look again in 2 minutes" made as a one-time task: once it has run, the task goes, but the
    # conversation with its answer stays where the person can read it.
    tasks, task_id, conversation_id, _ = runtime
    from row_bot import thread_cleanup
    deleted = []
    monkeypatch.setattr(thread_cleanup, "delete_thread", deleted.append)
    tasks.update_task(task_id, delete_after_run=True)

    tasks.after_run_finished(tasks.get_task(task_id), "run-1", conversation_id, "completed",
                             "supplier-notes.txt is still there", "", "")

    assert tasks.get_task(task_id) is None
    assert deleted == []


def test_a_reminder_run_now_notifies_and_finishes_without_a_turn(runtime, monkeypatch):
    tasks, task_id, _, started = runtime
    from row_bot import notifications
    notices = []
    monkeypatch.setattr(notifications, "notify", lambda **kwargs: notices.append(kwargs))
    tasks.update_task(task_id, notify_only=True, notify_label="Stretch")
    result = start(runtime)
    assert result.run.status == "completed"
    assert [notice["message"] for notice in notices] == ["Stretch"]
    assert not started
