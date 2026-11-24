"""The Home Board (generative UI, G5): pinned surfaces, each the latest drawing in its chat."""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from tests.subsystem.client_protocol.test_protocol_application import _client, service
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


def _reply(message_id: str, heading: str) -> object:
    from langchain_core.messages import AIMessage

    surface = {"surfaceId": "day", "title": "My day", "components": [
        {"id": "root", "component": "Text", "text": heading}], "sources": {"/now": {"@call": "rowbot.now"}}}
    return AIMessage(id=message_id, content=f"```rowbot-ui\n{json.dumps(surface)}\n```")


@pytest.fixture
def chat(service, tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage
    from row_bot import data_paths, threads

    monkeypatch.setattr(data_paths, "get_row_bot_data_dir", lambda create=True: tmp_path)
    conversation = threads.create_thread("Mornings", seed_default_skills=False)
    assert threads.append_checkpoint_messages(conversation, [
        HumanMessage(id=str(uuid4()), content="Show me my day"), _reply("first", "Tuesday")])
    return conversation


def test_a_pinned_surface_is_the_latest_drawing_in_its_chat(service, chat):
    from langchain_core.messages import HumanMessage
    from row_bot import threads

    with _client(service) as client:
        _, headers = bootstrap(client)
        assert client.get("/api/v1/boards", headers=headers).json() == {"items": []}
        pinned = client.post("/api/v1/boards/pins", headers=headers,
                             json={"conversation_id": chat, "message_id": "first", "surface_id": "day"})
        assert pinned.status_code == 200, pinned.text
        [item] = pinned.json()["items"]
        assert (item["title"], item["message_id"], item["available"]) == ("My day", "first", True)
        assert item["conversation_title"] == "Mornings"
        # The chat (or a workflow running in it) draws the surface again: the Board has the new one.
        assert threads.append_checkpoint_messages(chat, [
            HumanMessage(id=str(uuid4()), content="Again"), _reply("second", "Wednesday")])
        [item] = client.get("/api/v1/boards", headers=headers).json()["items"]
        assert item["message_id"] == "second" and "Wednesday" in item["surface_json"]
        # Its sources are read for the Board as for the chat: on this computer, with no model call.
        data = client.post(f"/api/v1/conversations/{chat}/surface-data", headers=headers,
                           json={"message_id": "second", "surface_id": "day"})
        assert data.status_code == 200 and "/now" in data.json()["values"]
        gone = client.delete(f"/api/v1/boards/pins/{item['pin_id']}", headers=headers)
        assert gone.status_code == 200 and gone.json() == {"items": []}


def test_only_a_surface_a_reply_drew_can_be_pinned(service, chat):
    with _client(service) as client:
        _, headers = bootstrap(client)
        for body in ({"conversation_id": chat, "message_id": "first", "surface_id": "other"},
                     {"conversation_id": chat, "message_id": "no_such_reply", "surface_id": "day"}):
            refused = client.post("/api/v1/boards/pins", headers=headers, json=body)
            assert refused.status_code in {400, 422}, refused.text
        assert client.get("/api/v1/boards", headers=headers).json() == {"items": []}


def test_a_deleted_chat_takes_its_pins_with_it(service, chat, tmp_path):
    from row_bot.thread_cleanup import delete_thread

    with _client(service) as client:
        _, headers = bootstrap(client)
        client.post("/api/v1/boards/pins", headers=headers,
                    json={"conversation_id": chat, "message_id": "first", "surface_id": "day"})
        delete_thread(chat)
        assert client.get("/api/v1/boards", headers=headers).json() == {"items": []}
    # Not even its title stays behind.
    assert "My day" not in (tmp_path / "boards.json").read_text(encoding="utf-8")


def test_asking_for_a_pinned_interface_by_name_shows_it_in_the_chat(service, chat):
    from row_bot.application.conversation_traces import specialize_tool_result
    from row_bot.tools.pages_tool import _board_show

    assert _board_show("my day").startswith("Nothing is pinned")
    with _client(service) as client:
        _, headers = bootstrap(client)
        [item] = client.post("/api/v1/boards/pins", headers=headers, json={
            "conversation_id": chat, "message_id": "first", "surface_id": "day"}).json()["items"]
    shown = _board_show("show my day board")
    assert shown.startswith(f"Pinned interface ({item['pin_id']}): My day")
    # Its handle line becomes the card that shows the pinned interface itself.
    card = specialize_tool_result({"name": "board_show", "content": shown})
    assert (card.kind, card.pin_id) == ("pinned_surface", item["pin_id"])
    missing = _board_show("tax return")
    assert missing == "No pinned interface is called that. Pinned: My day."
    assert specialize_tool_result({"name": "board_show", "content": missing}) is None
