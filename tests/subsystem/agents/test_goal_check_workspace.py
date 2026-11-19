"""R10: outside a code folder a goal's check, a watch's command and a wait's check run in the person's workspace.

That is the folder chosen in Settings (``filesystem.workspace_root``), the one the file tools and the shell use,
not Row-Bot's default workspace. The review names it (``~/…`` under the home folder), the approval is bound to
it, and a check refuses to run after the workspace changed. Processes are faked where they would start, so
nothing runs; every folder here is the test's own.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

CONVERSATION = "conversation-workspace"
_IDS = iter(range(1, 10_000))


def _valid() -> None:
    return None


@pytest.fixture
def chat(tmp_path, monkeypatch, reload_for_data_dir):
    """A plain chat on Ask, with a home folder, a default workspace and a chosen workspace of the test's own."""
    home = tmp_path / "home"
    chosen = home / "Work"
    chosen.mkdir(parents=True)
    default = tmp_path / "default-workspace"
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    monkeypatch.setenv("ROW_BOT_WORKSPACE", str(default))
    _tasks, _runs, goals, threads = reload_for_data_dir(
        tmp_path / "data", "row_bot.tasks", "row_bot.agent_runs", "row_bot.goals", "row_bot.threads")
    threads._save_thread_meta(CONVERSATION, "Workspace goal")
    threads._set_thread_approval_mode(CONVERSATION, "approve")
    from row_bot import goal_checks
    from row_bot.application import client_goal_profile_commands as commands
    from row_bot.tools import registry

    registry.set_tool_config("filesystem", "workspace_root", str(chosen))
    return SimpleNamespace(goals=goals, threads=threads, commands=commands, checks=goal_checks,
                           registry=registry, home=home, chosen=chosen, default=default)


def _request(chat, check: str) -> dict:
    current = chat.goals.get_current_goal(CONVERSATION, include_terminal=True)
    return {"conversation_id": CONVERSATION, "goal_id": current["id"] if current else None,
            "revision": str(current["revision"]) if current else "none", "operation": "start",
            "objective": "Write goal-retest.txt", "max_turns": None, "reason": None, "check_command": check}


def _start(chat, check: str = "findstr done goal-retest.txt") -> dict:
    command_id = str(UUID(int=next(_IDS)))
    receipt = chat.commands.execute_goal_command(
        owner_id="owner", key=command_id,
        command={"command_id": command_id, "type": "goal.control", "payload": _request(chat, check)},
        validate=_valid, validate_review=lambda _review: None)
    assert receipt["status"] == "completed", receipt
    goal = chat.goals.get_current_goal(CONVERSATION)
    chat.goals.request_completion(goal, source="goal_update", reason="Written.")
    return goal


def _started_in(monkeypatch) -> list[str]:
    """Where the check's process would start: the fake passes when goal-retest.txt there says done."""
    folders: list[str] = []

    def run(argv, *, cwd, timeout, **_kwargs):
        folders.append(cwd)
        found = Path(cwd, "goal-retest.txt")
        passed = found.is_file() and "done" in found.read_text(encoding="utf-8")
        return SimpleNamespace(returncode=0 if passed else 1, stdout="done\n" if passed else "",
                               stderr="" if passed else "Cannot open goal-retest.txt", timed_out=False,
                               cancelled=False)

    monkeypatch.setattr("row_bot.process_cancellation.run_cancellable_subprocess", run)
    return folders


def test_with_a_chosen_workspace_the_check_runs_there_and_passes(chat, monkeypatch):
    (chat.chosen / "goal-retest.txt").write_text("done\n", encoding="utf-8")
    started = _started_in(monkeypatch)
    goal = _start(chat)

    chat.checks.run_check(goal["id"])

    assert [Path(folder) for folder in started] == [chat.chosen]
    done = chat.goals.get_goal(goal["id"])
    assert (done["status"], done["check_state"]) == ("completed", "passed")
    assert not chat.default.exists(), "nothing ran in, or created, Row-Bot's default workspace"


def test_the_check_runs_where_the_shell_starts(chat):
    from row_bot.tools.shell_tool import ShellTool

    assert Path(chat.checks.place_for(CONVERSATION).folder) == Path(ShellTool()._get_workspace_root("chat-1"))
    assert Path(chat.checks.place_for(CONVERSATION).folder) == chat.chosen


def test_the_goal_form_and_the_confirm_name_the_chosen_folder(chat):
    review = chat.commands.review_goal_command(_request(chat, "npm test"), validate=_valid)

    assert review["check"]["runs_in"] == "Row-Bot's workspace (~/Work)"
    assert review["disclosures"] == [
        "Each time Row-Bot thinks the goal is done, it runs `npm test` in Row-Bot's workspace (~/Work). "
        "The goal counts as done only when it exits 0."]
    page = chat.commands.read_goals(CONVERSATION, validate=_valid)
    assert page["check_place"]["runs_in"] == "Row-Bot's workspace (~/Work)"


def test_a_folder_outside_the_home_folder_is_named_whole(chat, tmp_path):
    elsewhere = tmp_path / "projects"
    elsewhere.mkdir()
    chat.registry.set_tool_config("filesystem", "workspace_root", str(elsewhere))

    page = chat.commands.read_goals(CONVERSATION, validate=_valid)
    assert page["check_place"]["runs_in"] == f"Row-Bot's workspace ({elsewhere})"


def test_a_confirm_after_the_workspace_changed_is_stale(chat, tmp_path):
    request = _request(chat, "npm test")
    reviewed = chat.commands.review_goal_command(request, validate=_valid)["action_digest"]
    other = tmp_path / "other"
    other.mkdir()
    chat.registry.set_tool_config("filesystem", "workspace_root", str(other))

    assert chat.commands.review_goal_command(request, validate=_valid)["action_digest"] != reviewed


def test_a_check_approved_for_one_workspace_refuses_to_run_in_another(chat, tmp_path, monkeypatch):
    started = _started_in(monkeypatch)
    goal = _start(chat)
    other = tmp_path / "other"
    other.mkdir()
    (other / "goal-retest.txt").write_text("done\n", encoding="utf-8")
    chat.registry.set_tool_config("filesystem", "workspace_root", str(other))

    chat.checks.run_check(goal["id"])

    assert started == [], "nothing ran"
    paused = chat.goals.get_goal(goal["id"])
    assert (paused["status"], paused["check_state"]) == ("paused", "error")
    assert paused["last_reason"] == "Row-Bot's workspace folder changed since you set the check. Set the check again."


def test_with_no_workspace_chosen_the_check_runs_in_the_default_one(chat, monkeypatch):
    chat.registry.set_tool_config("filesystem", "workspace_root", "")
    started = _started_in(monkeypatch)
    goal = _start(chat)

    chat.checks.run_check(goal["id"])

    assert [Path(folder) for folder in started] == [chat.default]
    assert chat.default.is_dir(), "the default workspace is made, as the shell makes it"
    assert chat.goals.check_approval(goal)["runs_in"] == f"Row-Bot's workspace ({chat.default})"


def test_a_chosen_workspace_that_is_gone_falls_back_to_the_default_one(chat):
    chat.registry.set_tool_config("filesystem", "workspace_root", str(chat.home / "Gone"))

    assert Path(chat.checks.place_for(CONVERSATION).folder) == chat.default


def test_a_watch_command_runs_in_the_chosen_workspace_and_refuses_after_it_changed(chat, tmp_path, monkeypatch):
    """``/watch 5m `git status``` is approved by the same review and runs in the same folder."""
    from row_bot import waits

    ran: list[str] = []

    def execute(command, place, **_kwargs):
        ran.append(place.folder)
        return chat.checks.CheckRun(0, "clean")

    monkeypatch.setattr(chat.checks, "execute_check", execute)
    approval = waits.command_approval(CONVERSATION, "git status", "read_only")
    assert approval["runs_in"] == "Row-Bot's workspace (~/Work)"
    record = waits.create(CONVERSATION, "watch", "git status",
                          spec={"mode": "command", "command": "git status", "approval": approval, "then": "",
                                "focus": "", "ends_when": ""}, every_minutes=5)

    assert waits._look(record).text is not None
    assert [Path(folder) for folder in ran] == [chat.chosen]

    other = tmp_path / "other"
    other.mkdir()
    chat.registry.set_tool_config("filesystem", "workspace_root", str(other))
    seen = waits._look(record)
    assert seen.refused and seen.why == ("Row-Bot's workspace folder changed since you set the check. "
                                         "Set the check again.")
    assert len(ran) == 1


def test_a_wait_check_command_is_set_and_runs_in_the_chosen_workspace(chat, monkeypatch):
    import subprocess

    from row_bot import waits
    from row_bot.tools import waits_tool

    monkeypatch.setattr(waits_tool, "_here", lambda: CONVERSATION)
    said = waits_tool._wait_for(kind="check", label="the tree is clean", command="git status", text="clean")
    assert said.startswith("Waiting for"), said
    [record] = waits.waiting(CONVERSATION)
    assert Path(record["spec"]["folder"]) == chat.chosen

    ran: list[str] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, *, cwd, **_kwargs: ran.append(cwd) or SimpleNamespace(
        stdout="clean", stderr="", returncode=0))
    assert waits._check_command(CONVERSATION, record["spec"]).met
    assert [Path(folder) for folder in ran] == [chat.chosen]
