"""B311, B314: workflow steps that can't run as written are refused when saved, and fail when run."""

from __future__ import annotations

import json

import pytest

from tests.subsystem.workflows.test_workflow_profile_runtime import (
    _fresh_modules, _install_fake_agent, _run_workflow_synchronously,
)

pytestmark = pytest.mark.subsystem

REORDER = "Does step prompt_1's output contain at least one item below its reorder level?"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    tasks, threads, _profiles, _runs, _skills = _fresh_modules(tmp_path, monkeypatch)
    from row_bot.tools import task_tool

    monkeypatch.setattr(task_tool, "tasks_db", tasks)
    return tasks, threads, task_tool


def _steps(condition: str = "contains:LOW", prompt: str = "Check stock levels") -> list[dict]:
    return [
        {"type": "prompt", "prompt": prompt},
        {"type": "condition", "condition": condition, "if_true": "prompt_2", "if_false": "notify_1"},
        {"type": "prompt", "prompt": "Draft purchase orders"},
        {"type": "notify", "message": "Morning report ready"},
    ]


def test_a_condition_in_plain_words_is_saved_as_a_question_for_the_model(runtime):
    tasks, _threads, task_tool = runtime

    reply = task_tool._task_create(name="Morning shop report", steps=_steps(REORDER))

    assert reply.startswith("Task created successfully")
    saved = tasks.get_task(reply.split("ID: ")[1].split()[0])
    assert saved["steps"][1]["condition"] == f"llm:{REORDER}"


@pytest.mark.parametrize("condition", ["gt:many", "matches:([", "and:[contains:a,", "json:onlypath"])
def test_a_condition_with_an_operator_that_cant_be_read_is_refused_with_the_syntax(runtime, condition):
    tasks, _threads, task_tool = runtime

    reply = task_tool._task_create(name="Broken", steps=_steps(condition))

    assert reply.startswith("Error creating task: Step 2's condition can't be read")
    assert "llm:<a yes/no question" in reply
    assert tasks.list_tasks() == []


def test_an_empty_prompt_step_is_refused_and_points_to_a_notify_step(runtime):
    tasks, _threads, task_tool = runtime

    reply = task_tool._task_create(name="Report", steps=_steps(prompt="  "))

    assert reply == ("Error creating task: Step 1 (prompt) needs a prompt. "
                     "To tell the person something, use a notify step with a message.")
    task_id = tasks.create_task("Report", steps=_steps())
    with pytest.raises(ValueError, match=r"Step 4 \(notify\) needs a message"):
        tasks.update_task(task_id, steps=[*_steps()[:3], {"type": "notify", "message": ""}])


def test_a_saved_condition_that_cant_be_read_fails_the_run_instead_of_completing(runtime, monkeypatch):
    tasks, threads, _task_tool = runtime
    _install_fake_agent(monkeypatch, [])
    _run_workflow_synchronously(monkeypatch, tasks)
    monkeypatch.setattr(tasks, "_deliver_to_channels", lambda *_, **__: ("", ""))
    task_id = tasks.create_task("Old workflow", steps=_steps(), channels=[], apply_default_skills=False)
    # A workflow saved before conditions were checked.
    old = tasks.get_task(task_id)["steps"]
    old[1]["condition"] = REORDER
    with tasks._get_conn() as conn:
        conn.execute("UPDATE tasks SET steps=? WHERE id=?", (json.dumps(old), task_id))
    thread_id = threads.create_thread("Run", thread_id="old-workflow-run", seed_default_skills=False)

    tasks.run_task_background(task_id, thread_id, [], notification=False)

    run = tasks.get_run_history(task_id, limit=1)[0]
    assert run["status"] == "failed"
    assert "can't be read" in run["status_message"]
