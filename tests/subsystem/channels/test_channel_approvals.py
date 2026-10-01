from __future__ import annotations

import importlib

import pytest

from tests.fixtures.channels import FakeChannel
from tests.fixtures.tasks import fresh_tasks_module


pytestmark = pytest.mark.subsystem


def test_cross_channel_approval_resolution_updates_other_channels(tmp_path, monkeypatch) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    from row_bot.channels import registry

    registry._reset()
    source = FakeChannel(name="source")
    mirror = FakeChannel(name="mirror")
    source._running = True
    mirror._running = True
    registry.register(source)
    registry.register(mirror)

    token, approval_id = tasks.create_approval_request("run-1", "task-1", "approval_1", "Approve?")
    tasks._store_approval_channel_ref(approval_id, "source", "source-ref")
    tasks._store_approval_channel_ref(approval_id, "mirror", "mirror-ref")
    monkeypatch.setattr(tasks, "_resume_pipeline", lambda *_args, **_kwargs: None)

    assert tasks.respond_to_approval(token, True, source="source") is True

    assert source.approval_updates == []
    assert mirror.approval_updates == [("mirror-ref", "approved", "source")]


def test_channel_approval_helpers_round_trip_interrupt_text() -> None:
    from row_bot.channels.approval import extract_interrupt_ids, format_interrupt_text, is_approval_text

    interrupt_data = [
        {"__interrupt_id": "abc", "tool": "developer_apply_patch", "description": "Apply patch"},
        {"__interrupt_id": "def", "tool": "shell", "description": "Run command"},
    ]
    text = format_interrupt_text(interrupt_data)

    assert "Apply patch" in text
    assert extract_interrupt_ids(interrupt_data) == ["abc", "def"]
    assert extract_interrupt_ids([interrupt_data[0]]) == ["abc"]
    assert is_approval_text("approve") is True
    assert is_approval_text("deny") is False
    assert is_approval_text(text) is None


def test_child_agent_approval_routes_to_parent_channel(tmp_path, monkeypatch) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    from row_bot.channels import registry
    from row_bot.channels.thread_notifications import notify_agent_run_approval
    import row_bot.threads as threads
    import row_bot.agent_runner as agent_runner

    threads = importlib.reload(threads)

    registry._reset()
    source = FakeChannel(name="source")
    source._running = True
    registry.register(source)
    resumed: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        agent_runner,
        "resume_agent_run",
        lambda run_id, *, resume_token="", approved=True: resumed.append((run_id, approved)),
    )

    tasks.record_thread_channel_ref(
        "parent-thread",
        channel="source",
        target="conversation-1",
        external_conversation_id="conversation-1",
    )
    token, approval_id = tasks.create_approval_request(
        run_id="child-run",
        task_id="",
        step_id="agent_interrupt",
        message="Child needs approval.",
        agent_run_id="child-run",
        resume_kind="agent_run",
        source_label="Child Agent",
        source_thread_id="child-thread",
        parent_thread_id="parent-thread",
        approval_payload_json={
            "title": "Child Agent needs approval to run a command.",
            "reason": "Check the current branch.",
            "tool": "run_command",
            "raw_action": "git status",
            "source_label": "Child Agent",
        },
    )

    assert notify_agent_run_approval(approval_id) is True
    assert notify_agent_run_approval(approval_id) is True
    assert len(source.approvals) == 1
    sent = source.approvals[0]
    assert sent["target"] == "conversation-1"
    assert sent["config"]["approval_kind"] == "agent_run"
    assert sent["config"]["resume_token"] == token
    assert "Check the current branch." in sent["config"]["message"]
    approval_messages = [
            message
            for message in threads.get_latest_checkpoint_messages("parent-thread")
        if (
            getattr(message, "additional_kwargs", {})
            .get("row_bot_ui", {})
            .get("approval_request_id")
            == approval_id
        )
    ]
    assert len(approval_messages) == 1

    assert tasks.respond_to_approval(token, True, source="web") is True

    assert resumed == [("child-run", True)]
    assert source.approval_updates == [(sent["message_ref"], "approved", "web")]


def test_telegram_forgets_pending_approvals_after_an_hour(monkeypatch) -> None:
    import time

    from row_bot.channels import telegram

    now = 100_000.0
    monkeypatch.setattr(time, "time", lambda: now)
    monkeypatch.setattr(telegram, "_pending_interrupts", {1: {"_ts": now - 3601}, 2: {"_ts": now - 10}})
    monkeypatch.setattr(telegram, "_pending_task_approvals", {5: {"_ts": now - 3601}, 6: {"_ts": now}})
    monkeypatch.setattr(telegram, "_pending_skill_choices", {"old": {"_ts": now - 601}, "new": {"_ts": now}})

    telegram._cleanup_stale_pending()

    assert set(telegram._pending_interrupts) == {2}
    assert set(telegram._pending_task_approvals) == {6}
    assert set(telegram._pending_skill_choices) == {"new"}


_ADAPTERS = [
    ("telegram", True),
    ("slack", True),
    ("discord_channel", True),
    ("whatsapp", True),
    ("sms", False),
]


def _thread_in_block_mode(tmp_path, monkeypatch) -> dict:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    import row_bot.threads as threads

    threads = importlib.reload(threads)
    threads._save_thread_meta("channel-thread", "Channel thread")
    threads._set_thread_approval_mode("channel-thread", "block")
    return {"configurable": {"thread_id": "channel-thread"}}


@pytest.mark.parametrize("module,streams", _ADAPTERS)
def test_channel_turns_carry_the_thread_approval_mode(tmp_path, monkeypatch, module, streams) -> None:
    base = _thread_in_block_mode(tmp_path, monkeypatch)
    adapter = importlib.import_module(f"row_bot.channels.{module}")

    message = adapter.build_channel_runtime_config(base, "message")["configurable"]
    approval = adapter.build_channel_runtime_config(base, "approval")["configurable"]

    assert (message["runtime_surface"], message["runtime_mode"], message["channel_streaming"]) == (
        "channel",
        "auto",
        streams,
    )
    assert (approval["runtime_surface"], approval["runtime_mode"], approval["channel_streaming"]) == (
        "approval",
        "agent",
        False,
    )
    assert message["approval_mode"] == approval["approval_mode"] == "block"
    assert message["thread_id"] == approval["thread_id"] == "channel-thread"


def test_a_channel_approval_resumes_with_the_thread_approval_mode(tmp_path, monkeypatch) -> None:
    import sys
    import types

    import row_bot
    from row_bot.channels.approval import resume_agent_sync
    from row_bot.tools import registry

    base = _thread_in_block_mode(tmp_path, monkeypatch)
    seen = []

    def resume_stream_agent(enabled, config, approved, interrupt_ids=None):
        seen.append((config["configurable"], approved, interrupt_ids))
        return iter([("done", "denied")])

    fake_agent = types.ModuleType("row_bot.agent")
    fake_agent.resume_stream_agent = resume_stream_agent
    monkeypatch.setitem(sys.modules, "row_bot.agent", fake_agent)
    monkeypatch.setattr(row_bot, "agent", fake_agent, raising=False)
    monkeypatch.setattr(registry, "get_enabled_tools", lambda: [])

    assert resume_agent_sync(base, False, interrupt_ids=["interrupt-1"])[0] == "denied"

    [(configurable, approved, interrupt_ids)] = seen
    assert approved is False
    assert interrupt_ids == ["interrupt-1"]
    assert (configurable["runtime_surface"], configurable["runtime_mode"], configurable["approval_mode"]) == (
        "approval",
        "agent",
        "block",
    )
