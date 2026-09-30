from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def case_dir(tmp_path):
    path = tmp_path / "case"
    path.mkdir()
    return path


def test_default_workflow_templates_are_disabled_manual_and_mixed_complexity(monkeypatch, case_dir):
    data_dir = case_dir
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))
    import row_bot.tasks as tasks

    tasks = importlib.reload(tasks)

    assert len(tasks._DEFAULT_TASKS) == 5
    assert sum(1 for t in tasks._DEFAULT_TASKS if t["complexity"] == "simple") == 3
    assert sum(1 for t in tasks._DEFAULT_TASKS if t["complexity"] == "advanced") == 2
    assert all(t.get("schedule") is None for t in tasks._DEFAULT_TASKS)
    assert all(t.get("enabled") is False for t in tasks._DEFAULT_TASKS)
    assert all(t.get("steps") for t in tasks._DEFAULT_TASKS)
    assert all(len(t.get("steps") or []) >= 2 for t in tasks._DEFAULT_TASKS)
    assert all(
        len(t.get("steps") or []) == 2
        for t in tasks._DEFAULT_TASKS
        if t["complexity"] == "simple"
    )
    assert any(
        step.get("type") == "approval"
        for t in tasks._DEFAULT_TASKS
        for step in t.get("steps", [])
    )
    assert any(
        step.get("type") == "condition"
        for t in tasks._DEFAULT_TASKS
        for step in t.get("steps", [])
    )
    assert not any(t.get("notify_only") for t in tasks._DEFAULT_TASKS)
    joined_templates = "\n".join(
        step.get("prompt", "") + step.get("message", "")
        for t in tasks._DEFAULT_TASKS
        for step in t.get("steps", [])
    )
    assert "<topic>" in joined_templates
    assert "<topic-or-decision>" in joined_templates
    assert "<product-or-project>" in joined_templates
    assert "<market-or-customer-segment>" in joined_templates
    assert "topic I provide" not in joined_templates

    tasks.seed_default_tasks()
    seeded = tasks.list_tasks()

    assert len(seeded) == 5
    assert all(t["enabled"] is False for t in seeded)
    assert all(t.get("schedule") in ("", None) for t in seeded)
    assert any(t.get("steps") for t in seeded)


def test_existing_users_are_not_reseeded_automatically(monkeypatch, case_dir):
    data_dir = case_dir
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))
    import row_bot.tasks as tasks

    tasks = importlib.reload(tasks)
    tasks.create_task(name="User workflow", prompts=["Do something"], enabled=False)
    tasks.seed_default_tasks()

    names = [t["name"] for t in tasks.list_tasks()]

    assert names == ["User workflow"]
    assert (data_dir / ".tasks_seeded").exists()
