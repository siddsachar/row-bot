"""Private bindings (generative UI, G4): a surface's sources are read on this computer, for the client only."""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from tests.subsystem.client_protocol.test_protocol_application import _client, service
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

SPENDING = {"title": "October spending", "surfaceId": "spending", "components": [
    {"id": "root", "component": "Column", "children": ["rows", "now"]},
    {"id": "rows", "component": "Table", "columns": [{"key": "item"}, {"key": "cost", "type": "currency"}],
     "rows": {"@path": "/spend"}},
    {"id": "now", "component": "Text", "text": {"@path": "/now/weekday"}},
], "dataModel": {}, "sources": {
    "/spend": {"@call": "rowbot.file", "args": {"path": "bank/october.csv"}},
    "/now": {"@call": "rowbot.now"},
    "/secret": {"@call": "rowbot.file", "args": {"path": "../outside.txt"}},
    "/bad": {"@call": "rowbot.delete_everything"},
}}


@pytest.fixture
def drawn(service, tmp_path, monkeypatch):
    """A chat whose reply drew a surface bound to a file in the workspace (its id: "drew")."""
    from langchain_core.messages import AIMessage, HumanMessage
    from row_bot import approval_rules, threads

    workspace = tmp_path / "workspace"
    (workspace / "bank").mkdir(parents=True)
    (workspace / "bank" / "october.csv").write_text("item,cost\nRent,\"£1,200\"\nGroceries,512.40\n", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("not yours to show", encoding="utf-8")
    monkeypatch.setattr(approval_rules, "workspace_root", lambda: str(workspace))
    conversation = threads.create_thread("Spending", seed_default_skills=False)
    assert threads.append_checkpoint_messages(conversation, [
        HumanMessage(id=str(uuid4()), content="How did I spend October?"),
        AIMessage(id="drew", content=f"Here it is.\n\n```rowbot-ui\n{json.dumps(SPENDING)}\n```")])
    return conversation


def test_a_surface_s_sources_are_filled_in_for_the_client_and_never_enter_the_conversation(service, drawn):
    from row_bot import threads

    with _client(service) as client:
        _, headers = bootstrap(client)
        transcript = client.get(f"/api/v1/conversations/{drawn}/transcript", headers=headers).json()
        block = next(block for row in transcript["rows"] for block in row["blocks"] if block["type"] == "surface")
        tree = json.loads(block["surface_json"])
        # Only Row-Bot's read-only sources, with literal arguments, are kept.
        assert set(tree["sources"]) == {"/spend", "/now", "/secret"}
        read = client.post(f"/api/v1/conversations/{drawn}/surface-data", headers=headers,
                           json={"message_id": "drew", "surface_id": "spending"})
        assert read.status_code == 200, read.text
        data = read.json()
        assert data["values"]["/spend"] == [{"item": "Rent", "cost": 1200.0}, {"item": "Groceries", "cost": 512.4}]
        assert set(data["values"]["/now"]) == {"iso", "date", "time", "weekday"}
        # Outside the workspace is never read.
        assert "/secret" not in data["values"]
        assert data["problems"]["/secret"] == "That isn't in your workspace folder."
    # The model reads the conversation: the values are nowhere in it.
    saved = json.dumps([message.content for message in threads.get_latest_checkpoint_messages(drawn)])
    assert "1200" not in saved and "Groceries" not in saved and "not yours" not in saved


def test_surface_data_is_only_for_a_surface_a_reply_of_this_chat_drew(service, drawn):
    with _client(service) as client:
        _, headers = bootstrap(client)
        for body in ({"message_id": "no-such-reply", "surface_id": "spending"},
                     {"message_id": "drew", "surface_id": "another"}):
            refused = client.post(f"/api/v1/conversations/{drawn}/surface-data", headers=headers, json=body)
            assert refused.status_code in {400, 422}, refused.text


def test_a_link_is_a_fixed_address_never_one_built_from_the_data():
    from row_bot.application import surfaces

    def link(url: object) -> str:
        return json.dumps({"components": [
            {"id": "root", "component": "Button", "child": "label",
             "action": {"functionCall": {"@call": "openUrl", "args": {"url": url}}}},
            {"id": "label", "component": "Text", "text": "Open"}]})

    def opens(url: object) -> bool:
        parsed = surfaces.parse(link(url))
        return parsed is not None and "openUrl" in parsed[1]["surface_json"]

    assert opens("https://example.com")
    # A button whose link is built from the data model is left out: only its words remain.
    assert not opens({"@call": "formatString", "args": {"value": "https://example.com/?q=${/spend}"}})
    assert not opens({"@path": "/spend/0/item"})
