from __future__ import annotations

import pytest

from tests.fixtures.channels import FakeChannel, SentMessage
from tests.fixtures.tasks import fresh_tasks_module


pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


def test_shell_command_classification_regressions() -> None:
    from row_bot.tools.shell_tool import classify_command

    assert classify_command('echo "hello > world"') == "safe"
    assert classify_command("echo 'hello | world'") == "safe"
    assert classify_command("echo hello > /tmp/out") == "needs_approval"
    assert classify_command("ls | grep foo") == "safe"  # Both only read (5.3.1).
    assert classify_command("ls | sh") == "needs_approval"
    assert classify_command("echo safe\nrm -rf /") == "blocked"
    assert classify_command("ls\npwd\nwhoami") == "safe"
    assert classify_command("ls\npip install foo") == "needs_approval"
    # PowerShell ends the string at \" and would redirect, so this asks.
    assert classify_command('echo "hello \\" > world"') == "needs_approval"
    assert classify_command('echo "unterminated') == "needs_approval"
    assert classify_command("ls -la") == "safe"


def test_deleting_a_workflow_cancels_its_waiting_approval(tmp_path, reload_for_data_dir) -> None:
    tasks = fresh_tasks_module(tmp_path, reload_for_data_dir)

    task_id = tasks.create_task(name="cleanup test", prompts=["test"], apply_default_skills=False)
    conn = tasks._get_conn()
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
    approval = conn.execute("SELECT * FROM approval_requests WHERE id = 'request-cleanup'").fetchone()
    conn.close()
    assert approval is not None
    assert dict(approval)["status"] == "cancelled"


def test_get_task_channels_distinguishes_none_from_empty_list(tmp_path, reload_for_data_dir) -> None:
    tasks = fresh_tasks_module(tmp_path, reload_for_data_dir)
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


def test_a_channel_without_a_target_is_a_failed_delivery_not_a_send(tmp_path, reload_for_data_dir) -> None:
    tasks = fresh_tasks_module(tmp_path, reload_for_data_dir)
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
