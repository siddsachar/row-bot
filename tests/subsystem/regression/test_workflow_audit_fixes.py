from __future__ import annotations

import pytest

from tests.fixtures.channels import FakeChannel, SentMessage
from tests.fixtures.tasks import fresh_tasks_module


pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


def test_shell_command_classification_regressions() -> None:
    from row_bot.tools.shell_tool import _strip_quoted, classify_command

    assert classify_command('echo "hello > world"') == "safe"
    assert classify_command("echo 'hello | world'") == "safe"
    assert classify_command("echo hello > /tmp/out") == "needs_approval"
    assert classify_command("ls | grep foo") == "needs_approval"
    assert classify_command("echo safe\nrm -rf /") == "blocked"
    assert classify_command("ls\npwd\nwhoami") == "safe"
    assert classify_command("ls\npip install foo") == "needs_approval"
    assert ">" not in _strip_quoted('echo "hello \\" > world"')
    assert isinstance(_strip_quoted('echo "unterminated'), str)
    assert _strip_quoted("") == ""
    assert _strip_quoted("ls -la") == "ls -la"


def test_delete_task_and_finish_run_clean_up_pipeline_state(tmp_path, monkeypatch) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)

    task_id = tasks.create_task(name="cleanup test", prompts=["test"], apply_default_skills=False)
    conn = tasks._get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO pipeline_state "
        "(run_id, task_id, thread_id, current_step_index, step_outputs, status, config, created_at, updated_at) "
        "VALUES (?, ?, 'thread', 0, '{}', 'paused', '{}', datetime('now'), datetime('now'))",
        ("run-cleanup", task_id),
    )
    conn.execute(
        "INSERT OR REPLACE INTO approval_requests "
        "(id, run_id, task_id, step_id, resume_token, message, status, requested_at) "
        "VALUES ('request-cleanup', 'run-cleanup', ?, 'step_1', 'token-cleanup', 'test', 'pending', datetime('now'))",
        (task_id,),
    )
    conn.commit()
    conn.close()

    tasks.delete_task(task_id)

    conn = tasks._get_conn()
    pipeline_state = conn.execute("SELECT * FROM pipeline_state WHERE task_id = ?", (task_id,)).fetchone()
    approval = conn.execute("SELECT * FROM approval_requests WHERE id = 'request-cleanup'").fetchone()
    conn.close()
    assert pipeline_state is None
    assert approval is not None
    assert dict(approval)["status"] == "cancelled"

    task_id_2 = tasks.create_task(name="finish cleanup test", prompts=["test"], apply_default_skills=False)
    run_id = "run-finish-cleanup"
    conn = tasks._get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO task_runs (id, task_id, thread_id, started_at, status) "
        "VALUES (?, ?, 'thread-2', datetime('now'), 'running')",
        (run_id, task_id_2),
    )
    conn.execute(
        "INSERT OR REPLACE INTO pipeline_state "
        "(run_id, task_id, thread_id, current_step_index, step_outputs, status, config, created_at, updated_at) "
        "VALUES (?, ?, 'thread-2', 0, '{}', 'running', '{}', datetime('now'), datetime('now'))",
        (run_id, task_id_2),
    )
    conn.commit()
    conn.close()

    tasks._finish_run(run_id, "completed", "done")

    conn = tasks._get_conn()
    finished_state = conn.execute("SELECT * FROM pipeline_state WHERE run_id = ?", (run_id,)).fetchone()
    conn.close()
    assert finished_state is None


def test_get_task_channels_distinguishes_none_from_empty_list(tmp_path, monkeypatch) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    from row_bot.channels import registry

    class RunningChannel:
        name = "slack"
        display_name = "Slack"

        def is_running(self) -> bool:
            return True

    registry._reset()
    registry.register(RunningChannel())
    tasks.set_workflow_default_channels(["slack", "not-running"])

    default_task = tasks.create_task(name="default channels", prompts=["test"], apply_default_skills=False)
    no_delivery_task = tasks.create_task(
        name="no channels",
        prompts=["test"],
        channels=[],
        apply_default_skills=False,
    )

    inherited_channels = tasks.get_task_channels(tasks.get_task(default_task))
    assert [channel.name for channel in inherited_channels] == ["slack"]
    assert tasks.get_effective_task_channel_names(tasks.get_task(default_task)) == ["slack", "not-running"]
    assert tasks.get_task_channels(tasks.get_task(no_delivery_task)) == []
    registry._reset()


def test_a_channel_without_a_target_is_a_failed_delivery_not_a_send(tmp_path, monkeypatch) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    from row_bot.channels import registry

    untargeted = FakeChannel(name="untargeted", display_name="Untargeted", default_target="")
    targeted = FakeChannel(name="targeted", display_name="Targeted", default_target="channel-1")
    untargeted._running = targeted._running = True
    registry._reset()
    try:
        registry.register(untargeted)
        assert tasks._deliver_to_channels({"name": "T", "channels": ["untargeted"]}, "done") == (
            "delivery_failed",
            "Untargeted (no target configured)",
        )

        registry.register(targeted)
        status, detail = tasks._deliver_to_channels({"name": "T", "channels": ["untargeted", "targeted"]}, "done")
        assert status == "delivered"
        assert "Untargeted (no target configured)" in detail
        assert untargeted.messages == []
        assert targeted.messages == [SentMessage("channel-1", "📋 T\n\ndone")]

        # The task's own target wins over the channel default.
        tasks._deliver_to_channels({"name": "T", "channels": ["targeted"], "delivery_target": "chosen"}, "x")
        assert targeted.messages[-1].target == "chosen"
    finally:
        registry._reset()
