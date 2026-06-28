from __future__ import annotations

import pytest

from tests.fixtures.tasks import fresh_tasks_module, sample_workflow_steps


pytestmark = pytest.mark.subsystem


def test_workflow_steps_are_canonicalized_and_renderable(tmp_path, monkeypatch) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    steps = sample_workflow_steps()

    task_id = tasks.create_task(
        "Subsystem Workflow",
        steps=steps,
        model_override="model:openai:gpt-4o-mini",
        channels=["fake"],
        safety_mode="approve",
        apply_default_skills=False,
    )
    task = tasks.get_task(task_id)
    mermaid = tasks.generate_pipeline_mermaid(task["steps"])

    assert [step["id"] for step in task["steps"]] == ["prompt_1", "approval_1", "notify_1"]
    assert task["model_override"] == "model:openai:gpt-4o-mini"
    assert task["agent_profile_id"] == tasks.DEFAULT_WORKFLOW_AGENT_PROFILE_ID
    assert task["tools_override"] is None
    assert task["skills_override"] is None
    assert task["channels"] == ["fake"]
    assert task["safety_mode"] == "approve"
    assert "graph TD" in mermaid
    assert "approval_1" in mermaid


def test_delegate_agent_steps_keep_helper_profile_and_render(tmp_path, monkeypatch) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    steps = [
        {
            "type": "delegate_agent",
            "objective": "Fix failing tests",
            "profile": "develop",
            "agent_profile_id": "develop",
            "editing_safety": "worktree",
            "use_worktree": True,
        },
        {"type": "wait_for_agents", "timeout_seconds": 30},
    ]

    task_id = tasks.create_task(
        "Delegate Workflow",
        steps=steps,
        channels=[],
        apply_default_skills=False,
    )
    task = tasks.get_task(task_id)
    mermaid = tasks.generate_pipeline_mermaid(task["steps"])

    assert [step["id"] for step in task["steps"]] == ["delegate_agent_1", "wait_for_agents_1"]
    assert task["steps"][0]["agent_profile_id"] == "develop"
    assert task["steps"][0]["profile"] == "develop"
    assert "Agent Fix failing tests" in mermaid
    assert "Wait for Agents" in mermaid


def test_new_and_seeded_workflows_default_to_default_profile_without_old_overrides(
    tmp_path,
    monkeypatch,
) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)

    task_id = tasks.create_task("Profile default", prompts=["say hi"])
    task = tasks.get_task(task_id)

    assert task["agent_profile_id"] == tasks.DEFAULT_WORKFLOW_AGENT_PROFILE_ID
    assert task["tools_override"] is None
    assert task["skills_override"] is None

    created = tasks.add_default_workflow_templates()
    seeded = tasks.list_tasks()

    assert created > 0
    assert seeded
    assert {item["agent_profile_id"] for item in seeded} == {
        tasks.DEFAULT_WORKFLOW_AGENT_PROFILE_ID
    }
    assert all(item["tools_override"] is None for item in seeded)
    assert all(item["skills_override"] is None for item in seeded)


def test_workflow_drafts_are_isolated_from_saved_tasks(tmp_path, monkeypatch) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    payload = {"name": "Draft", "steps": sample_workflow_steps()}

    tasks.save_workflow_draft(None, payload)
    draft = tasks.get_workflow_draft(None)

    assert draft is not None
    assert draft["mode"] == "new"
    assert draft["payload"]["name"] == "Draft"

    tasks.delete_workflow_draft(None)
    assert tasks.get_workflow_draft(None) is None


def test_subtask_cycle_detection_uses_current_unsaved_steps(tmp_path, monkeypatch) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    parent_id = tasks.create_task("Parent", steps=[], apply_default_skills=False)
    child_id = tasks.create_task(
        "Child",
        steps=[{"type": "subtask", "task_id": parent_id}],
        apply_default_skills=False,
    )

    cycle = tasks.detect_circular_subtasks(parent_id, [{"type": "subtask", "task_id": child_id}])

    assert cycle[0] == parent_id
    assert parent_id in cycle
    assert child_id in cycle
