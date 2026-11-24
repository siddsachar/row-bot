from __future__ import annotations

import asyncio

import pytest

from row_bot.channels.streaming import ChannelDeliveryResult
from tests.fixtures.channels import FakeChannel, approval_payload


pytestmark = pytest.mark.contract


def test_fake_channel_lifecycle_and_message_contract() -> None:
    channel = FakeChannel()

    assert channel.is_configured() is True
    assert channel.is_running() is False
    assert asyncio.run(channel.start()) is True
    assert channel.is_running() is True

    channel.send_message("target", "hello")
    channel.send_photo("target", "image.png", "caption")
    channel.send_document("target", "file.txt", None)

    assert channel.messages[0].text == "hello"
    assert channel.photos == [("target", "image.png", "caption")]
    assert channel.documents == [("target", "file.txt", None)]

    asyncio.run(channel.stop())
    assert channel.is_running() is False


def test_fake_channel_approval_contract_records_resolution() -> None:
    channel = FakeChannel(name="approval-fake")
    asyncio.run(channel.start())

    ref = channel.send_approval_request(
        "target",
        {"tool": "dangerous_tool"},
        {"task_name": "Contract", "resume_token": "resume-1", "message": "Approve?"},
    )
    channel.update_approval_message(ref or "", "approved", source="web")

    assert ref == "approval-fake-approval-1"
    assert channel.approvals[0]["config"]["resume_token"] == "resume-1"
    assert channel.approval_updates == [("approval-fake-approval-1", "approved", "web")]
    assert approval_payload("resume-1", True) == {"resume_token": "resume-1", "approved": True, "source": "fake"}


def test_generic_new_moves_a_chat_only_through_its_adapters_hook() -> None:
    from row_bot.channels import commands

    moved: list[str] = []

    refused = commands.dispatch("Fake Channel", "/new")
    started = commands.dispatch("Fake Channel", "/new", new_conversation=lambda: moved.append("chat"))
    custom = commands.dispatch("Fake Channel", "/new", new_conversation=lambda: "Reply in this thread.")

    assert refused == "Fake Channel can't start a new conversation from here."
    assert started == commands.NEW_CONVERSATION_STARTED
    assert custom == "Reply in this thread."
    assert moved == ["chat"]
    assert "`/new`" in commands.cmd_help("Fake Channel")


def test_channel_base_thread_id_contract() -> None:
    channel = FakeChannel(name="slack")

    assert channel.make_thread_id("abc123") == "slack_abc123"
    assert channel.get_default_target() == "fake-user"
    assert channel.config_fields[0].key == "fake_target"


class _SlackClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def chat_update(self, **_kwargs) -> dict:
        self.calls.append("update")
        return {"ok": True}

    async def chat_postMessage(self, **_kwargs) -> dict:
        self.calls.append("post")
        return {"ok": True}


class _DiscordMessage:
    id = 77

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def edit(self, **_kwargs) -> None:
        self.calls.append("edit")


class _DiscordChannel:
    id = 42

    async def send(self, *_args, **_kwargs) -> None:
        return None


def _slack_button(monkeypatch, kind: str):
    """Slack with one pending approval of ``kind``: a press(user) coroutine, whether it is still pending,
    and what a press touched."""
    from row_bot.channels import slack

    for name in ("_pending_interrupts", "_interrupt_by_message", "_interrupt_by_channel",
                 "_pending_task_approvals", "_approval_by_channel"):
        monkeypatch.setattr(slack, name, {})
    monkeypatch.setattr(slack, "get_channel_secret", lambda _channel, key: "U_OWNER" if key == "SLACK_USER_ID" else "")
    monkeypatch.setattr(slack.ch_auth, "is_user_approved", lambda _channel, user: user == "U_PAIRED")
    client, resolved = _SlackClient(), []
    if kind == "task":
        slack._pending_task_approvals["approval-ts"] = {"resume_token": "resume-1", "task_name": "Send report",
                                                        "dm_channel_id": "D1"}
        monkeypatch.setattr("row_bot.tasks.respond_to_approval",
                            lambda token, approved, **_kwargs: resolved.append((token, approved)) or True)
        action = slack._handle_approval_button
    else:
        slack._store_slack_interrupt(channel_id="D1", interrupt_data=[{"__interrupt_id": "i1", "tool": "shell"}],
                                     config={"configurable": {"thread_id": "t1"}}, thread_ts=None,
                                     message_ts="approval-ts")

        async def resume(_client, _channel, _config, approved, **_kwargs):
            resolved.append(("interrupt", approved))
            return "", None, [], [], ChannelDeliveryResult(delivered=True)

        monkeypatch.setattr(slack, "_stream_agent_resume_to_slack", resume)
        monkeypatch.setattr(slack.ch_runtime, "resolve_goal_approval_for_config", lambda *_args: None)
        monkeypatch.setattr(slack.ch_runtime, "thread_id_from_config", lambda _config: None)
        action = slack._handle_interrupt_button

    def press(user: str | None) -> None:
        body = {"channel": {"id": "D1"}, "message": {"ts": "approval-ts"}}
        if user is not None:
            body["user"] = {"id": user}
        asyncio.run(action(body, client, approved=True))

    def pending() -> bool:
        return bool(slack._pending_task_approvals or slack._pending_interrupts)

    return press, pending, resolved, client.calls


def _discord_button(monkeypatch, kind: str):
    from row_bot.channels import discord_channel

    for name in ("_pending_interrupts", "_interrupt_by_message", "_interrupt_by_channel",
                 "_pending_task_approvals", "_task_approval_by_channel"):
        monkeypatch.setattr(discord_channel, name, {})
    monkeypatch.setattr(discord_channel, "get_channel_secret",
                        lambda _channel, key: "1001" if key == "DISCORD_USER_ID" else "")
    monkeypatch.setattr(discord_channel.ch_auth, "is_user_approved", lambda _channel, user: user == "2002")
    channel, message, resolved = _DiscordChannel(), _DiscordMessage(), []
    if kind == "task":
        discord_channel._pending_task_approvals[77] = {"resume_token": "resume-1", "task_name": "Send report",
                                                       "dm_channel_id": 42}
        monkeypatch.setattr("row_bot.tasks.respond_to_approval",
                            lambda token, approved, **_kwargs: resolved.append((token, approved)) or True)
        action = discord_channel._handle_discord_task_approval_button
    else:
        discord_channel._store_discord_interrupt(channel_id=42, interrupt_data=[{"__interrupt_id": "i1"}],
                                                 config={"configurable": {"thread_id": "t1"}}, message_id=77)

        async def resume(_channel, _config, approved, **_kwargs):
            resolved.append(("interrupt", approved))
            return "", None, [], [], ChannelDeliveryResult(delivered=True)

        monkeypatch.setattr(discord_channel, "_stream_agent_resume_to_discord", resume)
        monkeypatch.setattr(discord_channel.ch_runtime, "resolve_goal_approval_for_config", lambda *_args: None)
        monkeypatch.setattr(discord_channel.ch_runtime, "thread_id_from_config", lambda _config: None)
        action = discord_channel._handle_discord_interrupt_button

    def press(user: str | None) -> None:
        asyncio.run(action(channel, message, approved=True, user_id=None if user is None else int(user)))

    def pending() -> bool:
        return bool(discord_channel._pending_task_approvals or discord_channel._pending_interrupts)

    return press, pending, resolved, message.calls


CHANNEL_BUTTONS = {
    # channel: (pending approval, owner, a DM-paired user, someone else in the channel)
    "slack": (_slack_button, "U_OWNER", "U_PAIRED", "U_STRANGER"),
    "discord": (_discord_button, "1001", "2002", "3003"),
}


@pytest.mark.parametrize("kind", ["task", "interrupt"])
@pytest.mark.parametrize("channel_name", sorted(CHANNEL_BUTTONS))
def test_an_approval_button_pressed_by_anyone_but_the_owner_changes_nothing(monkeypatch, channel_name, kind):
    """Anyone in a channel can press the bot's buttons; only the paired owner's press answers an approval."""
    setup, owner, _paired, stranger = CHANNEL_BUTTONS[channel_name]
    press, pending, resolved, touched = setup(monkeypatch, kind)

    press(stranger)
    press(None)

    assert pending() and resolved == [] and touched == []

    press(owner)

    assert not pending() and len(resolved) == 1


@pytest.mark.parametrize("channel_name", sorted(CHANNEL_BUTTONS))
def test_a_dm_paired_user_answers_like_the_configured_owner(monkeypatch, channel_name):
    setup, _owner, paired, _stranger = CHANNEL_BUTTONS[channel_name]
    press, pending, resolved, _touched = setup(monkeypatch, "task")

    press(paired)

    assert not pending() and resolved == [("resume-1", True)]


@pytest.mark.parametrize("module_name", ["telegram", "whatsapp"])
def test_voice_note_channels_offer_the_voice_reply_switch_on_by_default(module_name):
    """Telegram and WhatsApp answer a voice note with one (VD9) unless the person turns it off."""
    import importlib

    from row_bot.channels import voice_replies

    module = importlib.import_module(f"row_bot.channels.{module_name}")
    channel = next(value for value in vars(module).values() if getattr(value, "name", None) == module_name
                   and hasattr(value, "config_fields"))
    (field,) = [field for field in channel.config_fields if field.key == voice_replies.VOICE_REPLIES_KEY]

    assert channel.capabilities.voice_in is True
    assert (field.label, field.field_type, field.storage, field.default) == (
        "Reply to voice notes with a voice note", "toggle", "config", True,
    )
    assert "Kokoro" in field.help_text
