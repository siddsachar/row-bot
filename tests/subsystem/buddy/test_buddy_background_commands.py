"""Buddy shows a long command carrying on in the background as work; a server it started is not (F6)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.platform


@pytest.fixture(autouse=True)
def _isolate_buddy_durable_activity(monkeypatch):
    import row_bot.agent_orchestrator as orchestrator
    import row_bot.buddy.brain as brain_mod
    import row_bot.tasks as tasks

    monkeypatch.setattr(tasks, "get_pending_approvals", lambda: [])
    monkeypatch.setattr(tasks, "get_running_tasks", lambda: {})
    monkeypatch.setattr(orchestrator, "get_thread_orchestration_activity", lambda: {})
    monkeypatch.setattr(brain_mod, "get_buddy_config",
                        lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"})


def test_a_background_command_reads_as_work_until_it_ends(monkeypatch):
    import row_bot.background_processes as processes
    import row_bot.buddy.brain as brain_mod
    from row_bot.buddy.brain import BuddyBrain

    now = 1000.0
    monkeypatch.setattr(brain_mod.time, "time", lambda: now)
    running = [SimpleNamespace(process_id="proc-1a2b3c4d")]
    monkeypatch.setattr(processes, "running_commands", lambda: list(running))
    brain = BuddyBrain()

    now = 1003.0  # After the idle grace.
    working = brain.resolve(None)
    assert (working.message, working.animation) == ("Command running", "step_check")

    running.clear()
    now = 1010.0
    assert brain.resolve(None).message == "Idle"


def test_servers_are_not_work(monkeypatch, tmp_path):
    import row_bot.background_processes as processes

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    server = processes.BackgroundProcess(process_id="proc-00000001", conversation_id="c", command="npm run dev",
                                         folder=str(tmp_path), kind="process",
                                         log=processes.ProcessLog(tmp_path / "server"))
    build = processes.BackgroundProcess(process_id="proc-00000002", conversation_id="c", command="npm run build",
                                        folder=str(tmp_path), kind="command",
                                        log=processes.ProcessLog(tmp_path / "build"))
    monkeypatch.setattr(processes, "_RECORDS", {server.process_id: server, build.process_id: build})

    assert processes.running_commands() == [build]
