"""Where a goal's check runs (B3): a code folder's Docker sandbox through the Developer runner, or a real
shell in Row-Bot's workspace."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from tests.fixtures.developer import fake_workspace

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.fixture
def owners(tmp_path, monkeypatch, reload_for_data_dir):
    workspace = tmp_path / "default-workspace"
    workspace.mkdir()
    monkeypatch.setenv("ROW_BOT_WORKSPACE", str(workspace))
    _tasks, _runs, goals, threads = reload_for_data_dir(
        tmp_path / "data", "row_bot.tasks", "row_bot.agent_runs", "row_bot.goals", "row_bot.threads")
    from row_bot import goal_checks

    return goals, threads, goal_checks


def _claim_done(goals, thread):
    goals.update_goal_progress(thread_id=thread, status="completed", progress="Done.")


def test_a_check_in_a_code_folder_runs_in_its_docker_sandbox(owners, tmp_path, monkeypatch):
    from row_bot.developer import sandbox_runtime, storage

    goals, threads, goal_checks = owners
    folder = fake_workspace(tmp_path, execution_mode="docker")
    (tmp_path / "workspace" / "package.json").write_text('{"scripts": {"test": "node --test"}}',
                                                         encoding="utf-8")
    monkeypatch.setattr(storage, "get_workspace", lambda workspace_id: folder if workspace_id == folder.id else None)
    ran = []
    monkeypatch.setattr(sandbox_runtime, "run_docker_sandbox_command", lambda _ws, command, **kwargs: (
        ran.append((command, kwargs["timeout"])) or SimpleNamespace(
            cwd="/workspace", returncode=0, stdout="12 passed", stderr="", changed_files=[],
            sandbox_backend="docker", pending_change_id="")))
    thread = threads.create_thread("Docs", developer_workspace_id=folder.id, approval_mode="approve")

    place = goal_checks.place_for(thread)
    assert place.runs_in == "Fixture Workspace (Docker sandbox, network off)"
    assert goal_checks.suggestions(place) == ["npm test"]
    with pytest.raises(goal_checks.CheckRefused, match="network"):
        goal_checks.review_check(thread, "curl https://example.com")

    goal = goals.start_goal(thread, "Make the tests pass", max_turns=0,
                            check=goal_checks.review_check(thread, "npm test"))
    _claim_done(goals, thread)
    goal_checks.run_check(goal["id"])
    assert ran == [("npm test", 600)]
    assert goals.get_goal(goal["id"])["status"] == "completed"


@pytest.mark.slow
def test_a_check_runs_a_real_command_in_row_bots_workspace(owners):
    goals, threads, goal_checks = owners
    thread = threads.create_thread("Checks", approval_mode="approve")
    python = f'& "{sys.executable}"' if os.name == "nt" else f'"{sys.executable}"'
    for code, state in ((3, "failed"), (0, "passed")):
        command = f'{python} -c "print(\'checked\'); raise SystemExit({code})"'
        goal = goals.start_goal(thread, f"Exit {code}", max_turns=0,
                                check=goal_checks.review_check(thread, command))
        _claim_done(goals, thread)
        goal_checks.run_check(goal["id"])
        latest = goals.get_goal(goal["id"])
        assert latest["check_state"] == state
        assert goals.check_result(latest)["exit_code"] == code
        assert "checked" in goals.check_result(latest)["output_tail"]
