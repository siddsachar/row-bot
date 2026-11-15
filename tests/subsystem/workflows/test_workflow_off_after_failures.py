"""D5: a workflow that starts by itself and fails five runs in a row is switched off, says so once, and comes
back cleanly when it is turned back on. Nothing here starts a scheduler, a model or a channel."""

from __future__ import annotations

import importlib
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.subsystem

OFF_WORDS = "It failed 5 times in a row, so Row-Bot switched it off. Open it to see why, then turn it back on."


@pytest.fixture
def rails(tmp_path, monkeypatch, reload_for_data_dir):
    (tasks,) = reload_for_data_dir(tmp_path / "data", "row_bot.tasks")
    from row_bot import notifications
    from row_bot.proactive import changes

    notices: list[dict] = []
    buddy: list[dict] = []
    queued: list[str] = []
    monkeypatch.setattr(notifications, "notify", lambda **notice: notices.append(notice))
    monkeypatch.setattr(tasks, "_emit_buddy_workflow_event",
                        lambda status, **event: buddy.append({"status": status, **event}))
    monkeypatch.setattr(changes, "workflow_finished", lambda task, run_id, status, notice: queued.append(run_id))
    return SimpleNamespace(tasks=tasks, notices=notices, buddy=buddy, queued=queued, day=[0])


def _workflow(tasks, **values) -> str:
    values.setdefault("schedule", "daily:09:00")
    return tasks.create_task("Price watch", prompts=["Check the price and tell me if it dropped."], **values)


def _saved_run(rails, task_id: str, status: str, *, started_at: str | None = None) -> str:
    """A run of the workflow that ended *status*, a day after the one before."""
    rails.day[0] += 1
    run_id = f"run{rails.day[0]:04d}"
    started = started_at or f"2026-10-{rails.day[0]:02d}T09:00:00"
    conn = rails.tasks._get_conn()
    try:
        conn.execute("INSERT INTO task_runs (id, task_id, thread_id, started_at, finished_at, status) "
                     "VALUES (?, ?, 'thread-1', ?, ?, ?)", (run_id, task_id, started, started, status))
        conn.commit()
    finally:
        conn.close()
    return run_id


def _ended(rails, task_id: str, status: str = "failed", **saved) -> str:
    """A run ended *status* and what follows a finished run happened."""
    run_id = _saved_run(rails, task_id, status, **saved)
    rails.tasks.after_run_finished(rails.tasks.get_task(task_id), run_id, "thread-1", status,
                                   "The run didn't finish: the provider is unavailable.", "", "")
    return run_id


def _off_notices(rails) -> list[dict]:
    return [notice for notice in rails.notices if notice["title"] == "Price watch is off"]


def test_an_older_task_database_gains_the_reason_columns(tmp_path, reload_for_data_dir):
    data = tmp_path / "data"
    data.mkdir()
    with sqlite3.connect(data / "tasks.db") as conn:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, name TEXT NOT NULL, prompts TEXT NOT NULL, "
                     "created_at TEXT NOT NULL, enabled INTEGER DEFAULT 1)")
        conn.execute("INSERT INTO tasks (id, name, prompts, created_at, enabled) "
                     "VALUES ('oldtask', 'Old one', '[\"hello\"]', '2026-01-01T00:00:00', 0)")
    (tasks,) = reload_for_data_dir(data, "row_bot.tasks")
    from row_bot.application import task_controls

    saved = tasks.get_task("oldtask")

    assert (saved["disabled_reason"], saved["re_enabled_at"]) == ("", "")
    assert task_controls.get_task_editor("oldtask").disabled_reason == ""
    assert tasks.failures_in_a_row("oldtask") == 0


def test_five_failed_runs_in_a_row_switch_a_scheduled_workflow_off_with_one_notice(rails):
    task_id = _workflow(rails.tasks)

    first_four = [_ended(rails, task_id) for _ in range(4)]

    assert rails.tasks.get_task(task_id)["enabled"] is True
    assert [notice["title"] for notice in rails.notices] == ["Price watch: didn't finish"] * 4
    assert rails.queued == first_four

    fifth = _ended(rails, task_id)

    task = rails.tasks.get_task(task_id)
    assert (task["enabled"], task["disabled_reason"]) == (False, "failures")
    assert len(rails.notices) == 5, "the off notice replaces the fifth run's own"
    [off] = _off_notices(rails)
    assert (off["message"], off["toast_type"], off["source"], off["workflow_id"]) == (
        OFF_WORDS, "warning", "workflow", task_id)
    assert rails.buddy[-1] == {"status": "error", "task_id": task_id, "thread_id": "thread-1",
                               "label": "Price watch is off", "error": OFF_WORDS}
    assert fifth not in rails.queued, "no later 'didn't finish' moment for a workflow already said to be off"
    # Run now still works on it: a run that fails then is reported as usual and switches nothing again.
    _ended(rails, task_id)
    assert len(_off_notices(rails)) == 1
    assert rails.notices[-1]["title"] == "Price watch: didn't finish"


@pytest.mark.parametrize("starts", [
    {"trigger": {"type": "webhook", "secret": "s3cret-value"}},
    {"trigger": {"type": "task_complete", "target_task": "other"}},
    {"at": "2026-12-01T09:00:00"},
])
def test_a_webhook_a_chain_or_a_set_time_counts_as_starting_by_itself(rails, starts):
    task_id = _workflow(rails.tasks, schedule=None, **starts)

    for _ in range(5):
        _ended(rails, task_id)

    assert rails.tasks.get_task(task_id)["disabled_reason"] == "failures"


def test_a_webhook_call_to_a_switched_off_workflow_is_refused(rails):
    task_id = _workflow(rails.tasks, schedule=None, trigger={"type": "webhook", "secret": "s3cret-value"})
    for _ in range(5):
        _ended(rails, task_id)

    assert rails.tasks.handle_webhook(task_id, "s3cret-value") == {"status": "error", "message": "Task is disabled"}


def test_a_manual_workflow_is_never_switched_off(rails):
    task_id = _workflow(rails.tasks, schedule=None)

    for _ in range(7):
        _ended(rails, task_id)

    task = rails.tasks.get_task(task_id)
    assert (task["enabled"], task["disabled_reason"]) == (True, "")
    assert not _off_notices(rails)
    assert len(rails.notices) == 7


def test_blocked_runs_count_and_stopped_skipped_or_expired_runs_neither_count_nor_reset(rails):
    task_id = _workflow(rails.tasks)
    for status in ("failed", "stopped", "blocked", "skipped", "failed", "expired", "failed"):
        _saved_run(rails, task_id, status)

    assert rails.tasks.failures_in_a_row(task_id) == 4

    _ended(rails, task_id, "blocked")

    assert rails.tasks.get_task(task_id)["disabled_reason"] == "failures"


@pytest.mark.parametrize("worked", ["completed", "completed_delivery_failed", "unsure", "denied"])
def test_a_run_that_worked_or_that_the_person_answered_ends_the_streak(rails, worked):
    task_id = _workflow(rails.tasks)
    for status in ("failed", "failed", "failed", worked, "failed"):
        _saved_run(rails, task_id, status)

    assert rails.tasks.failures_in_a_row(task_id) == 1

    for _ in range(3):
        _ended(rails, task_id)

    assert rails.tasks.get_task(task_id)["enabled"] is True, "four failures since the run that worked"


def _switched_off(rails) -> str:
    task_id = _workflow(rails.tasks)
    for _ in range(5):
        _ended(rails, task_id)
    assert rails.tasks.get_task(task_id)["disabled_reason"] == "failures"
    return task_id


def _turn_on_in_the_editor(task_id: str) -> None:
    from row_bot.application import task_controls

    snapshot = task_controls.get_task_editor(task_id)
    assert snapshot.disabled_reason == "failures"
    saved = task_controls.update_saved_task(task_id, replace(snapshot.fields, enabled=True),
                                            expected_revision=snapshot.revision, validate=lambda: None)
    assert saved.task.disabled_reason == ""


def _turn_on_by_the_agent(task_id: str) -> None:
    from row_bot.tools import task_tool

    assert "Enabled: True" in task_tool._task_update(task_id, enabled=True)


@pytest.mark.parametrize("turn_on", [_turn_on_in_the_editor, _turn_on_by_the_agent])
def test_turning_it_back_on_clears_the_reason_and_starts_a_new_streak(rails, turn_on):
    task_id = _switched_off(rails)

    turn_on(task_id)

    task = rails.tasks.get_task(task_id)
    assert (task["enabled"], task["disabled_reason"]) == (True, "")
    assert rails.tasks.failures_in_a_row(task_id) == 0
    # The next run starts once it is on again.
    _ended(rails, task_id, started_at=task["re_enabled_at"])
    assert rails.tasks.get_task(task_id)["enabled"] is True, "one failure after turning it on isn't five"
    assert rails.notices[-1]["title"] == "Price watch: didn't finish"


def test_saving_it_while_it_stays_off_keeps_the_reason(rails):
    task_id = _switched_off(rails)
    from row_bot.application import task_controls

    snapshot = task_controls.get_task_editor(task_id)
    saved = task_controls.update_saved_task(task_id, replace(snapshot.fields, name="Price watch (fixed)"),
                                            expected_revision=snapshot.revision, validate=lambda: None)

    assert (saved.task.fields.enabled, saved.task.disabled_reason) == (False, "failures")


def test_monitor_lists_it_as_switched_off_until_it_is_back_on(rails):
    task_id = _switched_off(rails)
    from row_bot.application.client_monitor import read_attention

    def problem():
        return [item for item in read_attention(include_update=False)["problems"]
                if item["id"] == f"workflow:{task_id}"]

    [listed] = problem()
    assert (listed["title"], listed["detail"]) == (
        "Price watch was switched off", "It failed 5 times in a row. Open it to see why, then turn it back on.")
    assert listed["fix"]["href"] == f"/?tab=workflows&workflow={task_id}"

    _turn_on_by_the_agent(task_id)
    _saved_run(rails, task_id, "completed", started_at=rails.tasks.get_task(task_id)["re_enabled_at"])

    assert problem() == []


def test_the_library_and_the_agent_say_why_it_is_off(rails):
    task_id = _switched_off(rails)
    other = rails.tasks.create_task("Morning brief", prompts=["Brief me."], schedule="daily:08:00", enabled=False)
    from row_bot import task_views
    from row_bot.tools import task_tool

    reasons = {item.id: item.disabled_reason for item in task_views.list_saved_tasks().items}
    listed = task_tool._task_list()

    assert reasons == {task_id: "failures", other: ""}
    assert '"off":"failed 5 times in a row"' in listed
    assert listed.count('"off"') == 1, "a workflow the person switched off says nothing more"
    assert '"off":"failed 5 times in a row"' in task_tool._task_list(task_id=task_id)


@pytest.fixture
def finishing(rails, monkeypatch):
    """The end of a run's last turn, delivering to the workflow's channels (recorded, never sent)."""
    from row_bot import workflow_turns

    delivered: list[str] = []
    monkeypatch.setattr(rails.tasks, "_deliver_to_channels", lambda task, text, **_: (delivered.append(text) or ("delivered", "")))
    monkeypatch.setattr(workflow_turns, "_service", lambda: SimpleNamespace(conversation_changed=lambda *a, **k: None))

    def finish(task_id: str) -> None:
        run_id = _saved_run(rails, task_id, "running")
        run = rails.tasks.read_task_run(run_id)
        workflow_turns._finish(run, rails.tasks.get_task(task_id), "failed", "", "The run didn't finish.")

    return SimpleNamespace(finish=finish, delivered=delivered)


def test_the_delivery_of_the_run_that_switches_it_off_carries_one_more_line(rails, finishing):
    task_id = _workflow(rails.tasks, channels=["telegram"])

    for _ in range(5):
        finishing.finish(task_id)

    assert len(finishing.delivered) == 5, "no extra message"
    assert finishing.delivered[3] == "Didn't finish · The run didn't finish."
    assert finishing.delivered[4] == ("Didn't finish · The run didn't finish.\n"
                                      "Row-Bot switched this workflow off after 5 failed runs in a row.")
    assert rails.tasks.get_task(task_id)["disabled_reason"] == "failures"


@pytest.fixture
def folders(tmp_path, monkeypatch, rails):
    data_dir = tmp_path / "data"
    storage = importlib.import_module("row_bot.developer.storage")
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "DEVELOPER_DIR", data_dir / "developer")
    monkeypatch.setattr(storage, "WORKSPACES_PATH", data_dir / "developer" / "workspaces.json")
    from row_bot import workflow_turns
    from row_bot.developer.state import DeveloperWorkspace

    monkeypatch.setattr(workflow_turns, "start", lambda *a, **k: pytest.fail("a blocked run starts no turn"))
    path = tmp_path / "landing"
    path.mkdir()
    storage.save_workspace(DeveloperWorkspace(id="dev_landing", name="landing", path=str(path)))
    return storage


def test_a_workflow_its_removed_code_folder_blocks_is_switched_off_too(rails, folders):
    task_id = _workflow(rails.tasks, workspace_id="dev_landing", workspace_name="landing")
    folders.remove_workspace("dev_landing")
    thread_id = rails.tasks._prepare_task_thread(rails.tasks.get_task(task_id))

    for _ in range(5):
        rails.tasks.run_task_background(task_id, thread_id, [])

    assert rails.tasks.get_task(task_id)["disabled_reason"] == "failures"
    assert [event["label"] for event in rails.buddy] == ["Workflow needs attention"] * 4 + ["Price watch is off"]
    assert len(_off_notices(rails)) == 1
