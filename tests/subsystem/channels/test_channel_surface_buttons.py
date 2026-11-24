"""An interface's buttons in chat apps (generative UI, G7).

Telegram, Slack and Discord show an interface as its words and then its buttons. A tap is the person's press
on that interface, checked against the drawing the conversation keeps: the model reads A2UI's action message,
the chat shows the button's words, and it never counts as approving a tool call. Values Row-Bot fills in on
this computer never go to the conversation from a chat app.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from row_bot.channels.streaming import ChannelDeliveryResult

pytestmark = pytest.mark.subsystem

TRIP = {"title": "Lisbon trip", "surfaceId": "trip", "components": [
    {"id": "root", "component": "Column", "children": ["hotels", "book", "agree", "site"]},
    {"id": "hotels", "component": "List", "children": {"path": "/hotels", "componentId": "hotel"}},
    {"id": "hotel", "component": "Button", "child": "hotel_name",
     "action": {"event": {"name": "pick_hotel", "context": {"area": {"@path": "area"}, "price": {"@path": "price"}}}}},
    {"id": "hotel_name", "component": "Text", "text": {"@path": "name"}},
    {"id": "book", "component": "Button", "child": "book_words",
     "action": {"event": {"name": "book", "context": {"budget": {"@path": "/budget"},
                                                       "spent": {"@path": "/spend/total"}}}}},
    {"id": "book_words", "component": "Text", "text": "Book the trip"},
    {"id": "agree", "component": "Button", "child": "agree_words", "@checks": [
        {"condition": {"@path": "/agreed"}, "message": "Tick the box first"}],
     "action": {"event": {"name": "confirm"}}},
    {"id": "agree_words", "component": "Text", "text": "Confirm"},
    {"id": "site", "component": "Button", "child": "site_words",
     "action": {"functionCall": {"@call": "openUrl", "args": {"url": "https://example.com"}}}},
    {"id": "site_words", "component": "Text", "text": "Open the site"},
], "dataModel": {"budget": 1500, "hotels": [{"name": "Baixa House", "area": "Baixa", "price": 140},
                                            {"name": "Alfama Rooms", "area": "Alfama", "price": 110}]},
    "sources": {"/spend": {"@call": "rowbot.file", "args": {"path": "bank/oct.csv"}}}}

DELIVERED = ChannelDeliveryResult(delivered=True, streamed=True, finalized=True, fallback_sent=False,
                                  transport="fake", final_text="Booked.")


def _reply(surface: dict) -> str:
    return f"Here's the plan.\n\n```rowbot-ui\n{json.dumps(surface)}\n```"


@pytest.fixture
def chat(tmp_path, reload_for_data_dir, monkeypatch):
    """A conversation whose latest reply drew the trip interface, and fresh button tokens."""
    reload_for_data_dir(tmp_path / "data", "row_bot.tasks", "row_bot.threads")
    from langchain_core.messages import AIMessage, HumanMessage

    from row_bot import threads
    from row_bot.channels import surface_buttons

    monkeypatch.setattr(surface_buttons, "_tokens", {})
    monkeypatch.setattr(surface_buttons, "_answering", set())
    thread_id = threads.create_thread("Trip", seed_default_skills=False)
    assert threads.append_checkpoint_messages(thread_id, [
        HumanMessage(id=str(uuid4()), content="Plan Lisbon"), AIMessage(id="drew", content=_reply(TRIP))])
    return SimpleNamespace(thread_id=thread_id, buttons=surface_buttons, threads=threads)


def _offered(chat, channel: str = "telegram") -> dict[str, str]:
    (offer,) = chat.buttons.offers(channel, chat.thread_id)
    assert offer.title == "Lisbon trip"
    return dict(offer.buttons)


def test_a_chat_app_gets_a_button_per_action_each_with_what_the_interface_holds(chat) -> None:
    from row_bot.application import surfaces

    found = surfaces.chat_app_buttons(surfaces.read_surface(json.dumps(TRIP)))

    # One button per hotel in the list; the box-dependent Confirm and the link are left out.
    assert [(button["label"], button["name"], button["context"]) for button in found] == [
        ("Baixa House", "pick_hotel", {"area": "Baixa", "price": 140}),
        ("Alfama Rooms", "pick_hotel", {"area": "Alfama", "price": 110}),
        # What Row-Bot fills in on this computer (the bank file) never goes from a chat app.
        ("Book the trip", "book", {"budget": 1500}),
    ]


def test_a_tap_is_the_persons_press_on_the_interface_they_saw(chat) -> None:
    press = chat.buttons.take(_offered(chat)["Alfama Rooms"], channel="telegram", thread_id=chat.thread_id)

    action = json.loads(press.model_text.split("```json\n", 1)[1].rsplit("\n```", 1)[0])["action"]
    assert (action["name"], action["surfaceId"], action["context"]) == (
        "pick_hotel", "trip", {"area": "Alfama", "price": 110})
    assert "not approval of any tool call" in press.model_text
    assert press.label == "Alfama Rooms"
    assert press.shown.splitlines()[1:] == ["Area: Alfama", "Price: 110"]


def test_a_press_from_a_chat_app_never_approves_a_tool_call(chat) -> None:
    from row_bot import agent, approval_review

    press = chat.buttons.take(_offered(chat)["Book the trip"], channel="telegram", thread_id=chat.thread_id)
    config = chat.buttons.press_config({"configurable": {"thread_id": chat.thread_id}}, press)

    _config, graph_input = agent._new_agent_graph_input(press.model_text, config)
    (human,) = graph_input["messages"]
    assert human.content == press.model_text
    assert human.additional_kwargs == {"platform_note": "surface", "platform_public_content": press.shown}
    assert approval_review.person_messages([human]) == ()


def test_a_tap_that_doesnt_fit_this_chat_says_why(chat) -> None:
    from langchain_core.messages import AIMessage, HumanMessage

    token = _offered(chat)["Book the trip"]
    with pytest.raises(chat.buttons.Refused, match="expired"):
        chat.buttons.take("made-up", channel="telegram", thread_id=chat.thread_id)
    with pytest.raises(chat.buttons.Refused, match="expired"):
        chat.buttons.take(token, channel="slack", thread_id=chat.thread_id)
    with pytest.raises(chat.buttons.Refused, match="earlier conversation"):
        chat.buttons.take(token, channel="telegram", thread_id="another-conversation")

    # Later replies that draw nothing offer no buttons; the earlier interface's still work.
    assert chat.threads.append_checkpoint_messages(chat.thread_id, [
        HumanMessage(id=str(uuid4()), content="thanks"), AIMessage(id="plain", content="Any time.")])
    assert chat.buttons.offers("telegram", chat.thread_id) == []
    assert chat.buttons.take(token, channel="telegram", thread_id=chat.thread_id).label == "Book the trip"


def test_a_redrawn_interface_offers_its_latest_drawing(chat) -> None:
    from langchain_core.messages import AIMessage, HumanMessage

    redrawn = {**TRIP, "dataModel": {**TRIP["dataModel"], "budget": 1800}}
    assert chat.threads.append_checkpoint_messages(chat.thread_id, [
        HumanMessage(id=str(uuid4()), content="raise it"), AIMessage(id="again", content=_reply(redrawn))])

    press = chat.buttons.take(_offered(chat)["Book the trip"], channel="telegram", thread_id=chat.thread_id)
    assert press.shown.splitlines()[1:] == ["Budget: 1,800"]


# ── Telegram ─────────────────────────────────────────────────────────────────


class _TelegramChat:
    id = 55

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send_message(self, text: str, **kwargs: Any) -> SimpleNamespace:
        self.sent.append((text, kwargs))
        return SimpleNamespace(message_id=len(self.sent))

    async def send_action(self, *_args: Any) -> None:
        return None


@pytest.fixture
def telegram(chat, monkeypatch):
    from row_bot.channels import telegram

    turns: list[tuple[str, dict]] = []

    async def streamed_turn(_chat, text: str, config: dict, **_kwargs: Any):
        turns.append((text, config))
        return "Booked.", None, [], [], DELIVERED

    config = {"configurable": {"thread_id": chat.thread_id}}
    monkeypatch.setattr(telegram, "_is_authorised", lambda update: True)
    monkeypatch.setattr(telegram, "_pending_interrupts", {})
    monkeypatch.setattr(telegram, "_current_thread_config", lambda chat_id, context: config)
    monkeypatch.setattr(telegram, "_refresh_thread_model_override", lambda config: config)
    monkeypatch.setattr(telegram, "_stream_agent_turn_to_telegram", streamed_turn)
    return SimpleNamespace(module=telegram, chat=_TelegramChat(), turns=turns)


def _telegram_tap(telegram, data: str) -> None:
    query = SimpleNamespace(data=data, message=SimpleNamespace(message_id=9, message_thread_id=None))

    async def answer() -> None:
        return None

    query.answer = answer
    update = SimpleNamespace(callback_query=query, effective_chat=telegram.chat, effective_user=SimpleNamespace(id=1))
    asyncio.run(telegram.module._handle_callback(update, SimpleNamespace(chat_data={})))


def test_telegram_sends_the_buttons_and_a_tap_runs_the_press(chat, telegram) -> None:
    asyncio.run(telegram.module._send_surface_buttons(telegram.chat, chat.thread_id))
    text, kwargs = telegram.chat.sent[-1]
    assert text == "<b>Lisbon trip</b>"
    buttons = {row[0].text: row[0].callback_data for row in kwargs["reply_markup"].inline_keyboard}
    assert list(buttons) == ["Baixa House", "Alfama Rooms", "Book the trip"]
    assert all(len(data.encode()) <= 64 for data in buttons.values())  # Telegram's callback limit

    _telegram_tap(telegram, buttons["Baixa House"])

    ((model_text, config),) = telegram.turns
    assert '"name": "pick_hotel"' in model_text
    assert config["configurable"]["platform_task_note"]["kind"] == "surface"
    assert "👉 <b>Baixa House</b>" in [text for text, _ in telegram.chat.sent]


def test_telegram_keeps_a_waiting_approval_first(chat, telegram) -> None:
    (offer,) = chat.buttons.offers("telegram", chat.thread_id)
    telegram.module._pending_interrupts[telegram.chat.id] = {"data": [{"tool": "send_email"}], "config": {}}

    _telegram_tap(telegram, f"ui:{offer.buttons[0][1]}")

    assert telegram.turns == []
    assert telegram.chat.sent[-1][0] == "⏸️ Answer the waiting approval or question first."


# ── Slack ────────────────────────────────────────────────────────────────────


class _SlackClient:
    def __init__(self) -> None:
        self.posts: list[dict] = []

    async def chat_postMessage(self, **kwargs: Any) -> dict:
        self.posts.append(kwargs)
        return {"channel": kwargs["channel"], "ts": "9.1"}


@pytest.fixture
def slack(chat, monkeypatch):
    from row_bot.channels import slack

    turns: list[tuple[str, dict, Any]] = []

    async def streamed_turn(_client, channel_id, text, config, *, thread_ts=None):
        turns.append((text, config, thread_ts))
        return "Booked.", None, [], [], DELIVERED

    monkeypatch.setattr(slack, "_is_authorised", lambda user_id: user_id == "UPERSON")
    monkeypatch.setattr(slack, "_get_or_create_thread", lambda channel_id, thread_ts=None, **_: chat.thread_id)
    monkeypatch.setattr(slack, "_stream_agent_turn_to_slack", streamed_turn)
    for name in ("_pending_interrupts", "_interrupt_by_message", "_interrupt_by_channel"):
        monkeypatch.setattr(slack, name, {})
    return SimpleNamespace(module=slack, client=_SlackClient(), turns=turns)


def _slack_tap(slack, value: str, *, user: str = "UPERSON", thread_ts: str | None = "100.1") -> None:
    body = {"user": {"id": user}, "channel": {"id": "C1"}, "actions": [{"action_id": "rowbot_ui_0", "value": value}],
            "message": {"ts": "101.1", **({"thread_ts": thread_ts} if thread_ts else {})}}
    asyncio.run(slack.module._handle_surface_button(body, slack.client))


def test_slack_posts_the_buttons_in_the_thread_and_a_tap_runs_the_press(chat, slack) -> None:
    asyncio.run(slack.module._send_slack_surface_buttons(slack.client, "C1", chat.thread_id, thread_ts="100.1"))
    post = slack.client.posts[-1]
    assert (post["text"], post["thread_ts"]) == ("Lisbon trip", "100.1")
    elements = post["blocks"][1]["elements"]
    assert [element["text"]["text"] for element in elements] == ["Baixa House", "Alfama Rooms", "Book the trip"]
    assert len({element["action_id"] for element in elements}) == 3  # unique in its message, as Slack needs

    _slack_tap(slack, elements[2]["value"])

    ((model_text, config, thread_ts),) = slack.turns
    assert '"name": "book"' in model_text and thread_ts == "100.1"
    assert config["configurable"]["platform_task_note"]["kind"] == "surface"


def test_slack_ignores_a_tap_by_someone_else_in_the_channel(chat, slack) -> None:
    (offer,) = chat.buttons.offers("slack", chat.thread_id)

    _slack_tap(slack, offer.buttons[0][1], user="USTRANGER")

    assert slack.turns == [] and slack.client.posts == []


# ── Discord ──────────────────────────────────────────────────────────────────


class _DiscordChannel:
    id = 501

    def __init__(self) -> None:
        self.sent: list[tuple[Any, dict]] = []

    async def send(self, content: Any = None, **kwargs: Any) -> SimpleNamespace:
        self.sent.append((content, kwargs))
        return SimpleNamespace(id=len(self.sent))


@pytest.fixture
def discord_bot(chat, monkeypatch):
    discord = pytest.importorskip("discord")
    from row_bot.channels import discord_channel

    turns: list[tuple[str, dict]] = []

    async def streamed_turn(_channel, text: str, config: dict):
        turns.append((text, config))
        return "Booked.", None, [], [], DELIVERED

    monkeypatch.setattr(discord_channel, "_is_authorised", lambda user_id: user_id == 4242)
    monkeypatch.setattr(discord_channel, "_get_or_create_thread", lambda channel_id, **_: chat.thread_id)
    monkeypatch.setattr(discord_channel, "_stream_agent_turn_to_discord", streamed_turn)
    for name in ("_pending_interrupts", "_interrupt_by_message", "_interrupt_by_channel"):
        monkeypatch.setattr(discord_channel, name, {})
    channel = _DiscordChannel()
    channel.type = discord.ChannelType.private
    return SimpleNamespace(module=discord_channel, channel=channel, turns=turns)


def test_discord_sends_a_view_of_buttons_and_a_tap_runs_the_press(chat, discord_bot) -> None:
    asyncio.run(discord_bot.module._send_discord_surface_buttons(discord_bot.channel, chat.thread_id))
    content, kwargs = discord_bot.channel.sent[-1]
    assert content == "**Lisbon trip**"
    items = kwargs["view"].children
    assert [item.label for item in items] == ["Baixa House", "Alfama Rooms", "Book the trip"]

    token = items[0].custom_id.removeprefix("rowbot_ui:")
    asyncio.run(discord_bot.module._handle_discord_surface_button(discord_bot.channel, token, user_id=99))
    assert discord_bot.turns == []  # someone other than the paired owner: ignored

    asyncio.run(discord_bot.module._handle_discord_surface_button(discord_bot.channel, token, user_id=4242))
    ((model_text, config),) = discord_bot.turns
    assert '"area": "Baixa"' in model_text
    assert config["configurable"]["platform_task_note"]["kind"] == "surface"
    assert "👉 **Baixa House**" in [content for content, _ in discord_bot.channel.sent]
