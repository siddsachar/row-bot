"""Hands-off (A0): chosen by the person once on this install, never by an agent or a migration, and only
for the person's own chats."""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.subsystem


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    folder = tmp_path / "data"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(folder))
    return folder


def test_turning_it_on_needs_the_one_time_confirmation_then_never_asks_again(data_dir):
    from row_bot import full_autonomy

    assert full_autonomy.is_confirmed() is False
    with pytest.raises(full_autonomy.FullAutonomyError) as refused:
        full_autonomy.require_choice("full")
    assert refused.value.code == "full_autonomy_unconfirmed"
    assert full_autonomy.require_choice("auto") == "auto"  # Other levels never ask.

    assert full_autonomy.require_choice("full", confirmed=True) == "full"
    # Remembered on this install: the next time needs no dialog.
    assert full_autonomy.is_confirmed() is True
    assert full_autonomy.require_choice("full") == "full"
    saved = json.loads((data_dir / "approval_settings.json").read_text(encoding="utf-8"))
    assert saved == {"full_autonomy_confirmed_version": full_autonomy.NOTICE_VERSION}


def test_an_agent_never_chooses_full_autonomy(data_dir):
    from row_bot import full_autonomy

    assert full_autonomy.agent_settable("auto") == "auto"
    assert full_autonomy.agent_settable("allow_all") == "auto"
    with pytest.raises(full_autonomy.FullAutonomyError):
        full_autonomy.agent_settable("full")


def test_an_agent_cannot_create_or_switch_a_workflow_to_full_autonomy(tmp_path, reload_for_data_dir):
    tasks, task_tool = reload_for_data_dir(tmp_path / "data", "row_bot.tasks", "row_bot.tools.task_tool")

    refused = task_tool._task_create("Nightly tidy", prompts=["Tidy the notes"], safety_mode="full")
    assert refused.startswith("Hands-off can only be turned on by the person")
    assert tasks.list_tasks() == []

    task_tool._task_create("Nightly tidy", prompts=["Tidy the notes"], approval_mode="auto")
    [task] = tasks.list_tasks()
    assert task["safety_mode"] == "auto"
    assert task_tool._task_update(task["id"], safety_mode="full").startswith("Hands-off can only")
    assert tasks.get_task(task["id"])["safety_mode"] == "auto"


def test_workflows_saved_with_the_old_auto_become_reviewed_auto_never_full(tmp_path, reload_for_data_dir):
    (tasks,) = reload_for_data_dir(tmp_path / "data", "row_bot.tasks")
    ids = [tasks.create_task(f"Saved {index}", prompts=["Run"], channels=[], enabled=False) for index in range(4)]
    conn = tasks._get_conn()
    for task_id, stored in zip(ids, ("allow_all", "agent_run", None, "approve")):
        conn.execute("UPDATE tasks SET safety_mode=? WHERE id=?", (stored, task_id))
    conn.commit()
    conn.close()

    tasks.ensure_task_schema(force=True)

    assert [tasks.get_task(task_id)["safety_mode"] for task_id in ids] == ["auto", "auto", "auto", "approve"]


def test_a_profile_narrows_its_parents_level_and_never_widens_it():
    from row_bot.agent_profiles import _effective_approval

    assert _effective_approval("auto", "full")[0] == "auto"
    assert _effective_approval("full", "full")[0] == "full"
    assert _effective_approval("full", "auto")[0] == "auto"
    assert _effective_approval("full", "inherit")[0] == "full"
    assert _effective_approval("approve", "allow_all")[0] == "approve"
    assert _effective_approval("full", "allow_all")[0] == "auto"  # A profile saved with the old Auto.


@pytest.fixture
def channel_threads(tmp_path, reload_for_data_dir, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    tasks, threads = reload_for_data_dir(tmp_path / "data", "row_bot.tasks", "row_bot.threads")
    for thread_id in ("slack-thread", "slack-dm"):
        threads._save_thread_meta(thread_id, thread_id)
    tasks.record_thread_channel_ref("slack-thread", channel="slack", target="C1:1700000000.000100",
                                    external_conversation_id="C1:1700000000.000100", shared=True)
    tasks.record_thread_channel_ref("slack-dm", channel="slack", target="D1", external_conversation_id="D1",
                                    shared=False)
    return tasks, threads


def test_a_conversation_other_people_share_runs_on_auto_at_most(channel_threads):
    """User decision 4: a shared Slack or Discord thread never runs on Hands-off."""
    from row_bot import full_autonomy

    tasks, threads = channel_threads
    threads._set_thread_approval_mode("slack-thread", "full")
    threads._set_thread_approval_mode("slack-dm", "full")
    assert threads._get_thread_approval_mode("slack-thread") == "auto"
    assert threads._get_thread_approval_mode("slack-dm") == "full"
    assert full_autonomy.cap_for_conversation("slack-thread", "full") == "auto"
    assert full_autonomy.cap_for_conversation("slack-thread", "approve") == "approve"
    # A later message that doesn't say keeps what was recorded.
    tasks.record_thread_channel_ref("slack-thread", channel="slack", target="C1:1700000000.000100")
    assert full_autonomy.is_shared_conversation("slack-thread") is True
    assert full_autonomy.is_shared_conversation("a-chat-in-the-app") is False


def test_full_autonomy_is_never_turned_on_from_a_channel(channel_threads, monkeypatch):
    from row_bot import agent
    from row_bot.channels.commands import cmd_approval

    _tasks, threads = channel_threads
    monkeypatch.setattr(agent, "clear_agent_cache", lambda: None)

    for name in ("full", "hands-off", "Hands off"):
        reply = cmd_approval("Slack", name, thread_id="slack-dm")
        assert reply.startswith("Hands-off can only be turned on in the Row-Bot app")
        assert threads._get_thread_approval_mode("slack-dm") == "approve"

    assert "Auto" in cmd_approval("Slack", "auto", thread_id="slack-dm")
    assert threads._get_thread_approval_mode("slack-dm") == "auto"
    assert "allow_all" not in cmd_approval("Slack", "", thread_id="slack-dm")


@pytest.mark.parametrize(("adapter", "shared"), [
    ("slack_dm", False), ("slack_thread", True), ("whatsapp_chat", False), ("whatsapp_group", True),
    ("telegram_chat", False), ("telegram_group", True),
])
def test_each_channel_says_which_of_its_conversations_are_shared(adapter, shared):
    if adapter.startswith("slack"):
        from row_bot.channels import slack

        conversation = (slack._conversation("D123") if adapter == "slack_dm"
                        else slack._conversation("C123", "1700000000.000100"))
        assert conversation.shared is shared
    elif adapter.startswith("whatsapp"):
        from row_bot.channels import whatsapp

        chat = "447700900123@c.us" if adapter == "whatsapp_chat" else "120363000000000000@g.us"
        assert whatsapp._conversation(chat).shared is shared
    else:
        from row_bot.channels import telegram

        assert telegram._is_group_chat(123456 if adapter == "telegram_chat" else -100123456) is shared
