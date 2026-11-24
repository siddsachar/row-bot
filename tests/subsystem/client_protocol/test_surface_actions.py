"""A press on a surface's button (generative UI): a labelled message Row-Bot writes, never approval."""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream
from tests.subsystem.client_protocol.test_protocol_application import _client, _command, service
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

MODEL = {"provider_id": "fixture", "model_ref": "fixture::model"}
SURFACE = {"title": "Lisbon trip", "surfaceId": "trip", "components": [
    {"id": "root", "component": "Column", "children": ["budget", "book"]},
    {"id": "budget", "component": "Slider", "label": "Budget", "max": 3000, "value": {"@path": "/budget"}},
    {"id": "book", "component": "Button", "child": "label",
     "action": {"event": {"name": "book_hotel", "context": {"budget": {"@path": "/budget"}}}}},
    {"id": "label", "component": "Text", "text": "Book the hotel"},
], "dataModel": {"budget": 1500}}


@pytest.fixture
def drawn(service, tmp_path, monkeypatch):
    """A chat whose last reply drew a surface (its id: "drew")."""
    from langchain_core.messages import AIMessage, HumanMessage
    from row_bot import threads

    folder = tmp_path / "thread-ui"
    folder.mkdir()
    monkeypatch.setattr(threads, "_THREAD_UI_DIR", folder)
    conversation = threads.create_thread("Trip", seed_default_skills=False)
    assert threads.append_checkpoint_messages(conversation, [
        HumanMessage(id=str(uuid4()), content="Plan Lisbon"),
        AIMessage(id="drew", content=f"Here's a plan.\n\n```rowbot-ui\n{json.dumps(SURFACE)}\n```")])
    return conversation


def _press(client, headers, service, conversation, **action):
    return _command(client, headers, "conversation.submit", {
        "submission_id": str(uuid4()), "text": "Book the hotel", "attachment_refs": [], "model_selection": MODEL,
        "surface_action": {"message_id": "drew", "surface_id": "trip", "name": "book_hotel",
                           "source_component_id": "book", "context": {"budget": 1800}, **action}},
        target=conversation, revision=service.get_conversation(conversation)["revision"])


def test_a_press_sends_a2ui_s_action_and_the_chat_shows_the_button_s_words(service, drawn):
    from langchain_core.messages import AIMessage
    from row_bot import approval_review, threads

    fake = ScriptedAgentStream((CheckpointCommit((AIMessage(id="answer", content="Booked."),), "answer"),
                                ("done", None)))
    service.stream_factory = fake.stream
    with _client(service) as client:
        _, headers = bootstrap(client)
        sent = _press(client, headers, service, drawn)
        assert sent.status_code == 202, sent.text
        assert service.registry.get(sent.json()["execution_id"]).producer_done.wait(5)
        human = [message for message in threads.get_latest_checkpoint_messages(drawn) if message.type == "human"][-1]
        # The model reads A2UI's action message, written by Row-Bot from the surface (never the client's words).
        action = json.loads(human.content.split("```json\n", 1)[1].rsplit("\n```", 1)[0])["action"]
        assert (action["name"], action["surfaceId"], action["context"]) == ("book_hotel", "trip", {"budget": 1800})
        assert human.additional_kwargs["platform_note"] == "surface"
        # Never the person's request: a press can't approve a tool call for the Auto reviewer.
        assert approval_review.person_messages([human]) == ()
        transcript = client.get(f"/api/v1/conversations/{drawn}/transcript", headers=headers).json()
        surface = next(block for row in transcript["rows"] for block in row["blocks"] if block["type"] == "surface")
        assert surface["surface_id"] == "trip"
        row = [item for item in transcript["rows"] if item["role"] == "user"][-1]
        assert row["note"] == "surface"
        assert row["blocks"][0]["text"].splitlines() == ["Book the hotel · Lisbon trip", "Budget: 1,800"]


@pytest.mark.parametrize("action", [
    {"message_id": "no-such-reply"},
    {"surface_id": "another"},
    {"name": "delete_everything"},
    {"context": {"card_number": "4242"}},
])
def test_a_press_the_surface_does_not_declare_is_refused(service, drawn, action):
    from row_bot import threads

    before = len(threads.get_latest_checkpoint_messages(drawn))
    with _client(service) as client:
        _, headers = bootstrap(client)
        refused = _press(client, headers, service, drawn, **action)
        assert refused.status_code in {400, 422}, refused.text
    assert len(threads.get_latest_checkpoint_messages(drawn)) == before


def test_what_the_person_entered_is_kept_beside_the_chat_not_in_it(service, drawn):
    from row_bot import threads

    checkpoint = threads.get_latest_checkpoint_revision(drawn)
    endpoint = f"/api/v1/conversations/{drawn}/answer-state"
    block = "block:" + "c" * 24
    with _client(service) as client:
        _, phone = bootstrap(client)
        _, desktop = bootstrap(client)
        revision = client.get(endpoint, headers=phone).json()["revision"]
        saved = client.put(endpoint, headers=phone, json={
            "expected_revision": revision, "block_id": block, "surface_data": {"budget": 2200}})
        assert saved.status_code == 200, saved.text
        assert client.get(endpoint, headers=desktop).json()["surfaces"] == {block: {"budget": 2200}}
    assert threads.get_latest_checkpoint_revision(drawn) == checkpoint
