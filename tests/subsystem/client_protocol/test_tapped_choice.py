"""A reply button's tap (F3): the person's own message, marked with the reply that offered it."""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

from __future__ import annotations

from uuid import uuid4

import pytest

from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream
from tests.subsystem.client_protocol.test_protocol_application import _client, _command, service
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

MODEL = {"provider_id": "fixture", "model_ref": "fixture::model"}


@pytest.fixture
def offered(service):
    """A chat whose last reply offered choices (its id: "offer")."""
    from langchain_core.messages import AIMessage, HumanMessage
    from row_bot import threads

    conversation = threads.create_thread("Budget", seed_default_skills=False)
    assert threads.append_checkpoint_messages(conversation, [
        HumanMessage(id=str(uuid4()), content="Summarise October"),
        AIMessage(id="offer", content="What next?\n\n```rowbot-choices\n{}\n```")])
    return conversation


def _submit(client, headers, service, conversation, **extra):
    return _command(client, headers, "conversation.submit", {
        "submission_id": str(uuid4()), "text": "List my subscriptions", "attachment_refs": [],
        "model_selection": MODEL, **extra}, target=conversation,
        revision=service.get_conversation(conversation)["revision"])


def test_a_tap_is_the_person_s_own_message_marked_with_the_reply_that_offered_it(service, offered):
    from langchain_core.messages import AIMessage
    from row_bot import approval_review, threads

    fake = ScriptedAgentStream((CheckpointCommit((AIMessage(id="answer", content="Three subscriptions."),),
                                                 "answer"), ("done", None)))
    service.stream_factory = fake.stream
    with _client(service) as client:
        _, headers = bootstrap(client)
        sent = _submit(client, headers, service, offered,
                       tapped_choice={"message_id": "offer", "label": "Subscriptions"})
        assert sent.status_code == 202, sent.text
        assert service.registry.get(sent.json()["execution_id"]).producer_done.wait(5)
        human = [message for message in threads.get_latest_checkpoint_messages(offered)
                 if message.type == "human"][-1]
        # The model reads the button's words, unchanged; the marker only names the reply.
        assert human.content == "List my subscriptions"
        assert human.additional_kwargs["tapped_choice"] == {"message_id": "offer", "label": "Subscriptions"}
        assert not {"platform_note", "row_bot_note", "platform_public_content"} & set(human.additional_kwargs)
        # Still the person's request: the Auto reviewer reads it as theirs.
        assert approval_review.person_messages([human]) == ("List my subscriptions",)
        transcript = client.get(f"/api/v1/conversations/{offered}/transcript", headers=headers).json()
        row = [item for item in transcript["rows"] if item["role"] == "user"][-1]
        assert not row.get("note")


@pytest.mark.parametrize("tapped", [
    {"message_id": "no-such-reply"},
    {"message_id": "offer", "label": "x" * 41},
    {"message_id": "offer", "colour": "red"},
])
def test_a_tap_naming_no_reply_of_this_chat_is_refused(service, offered, tapped):
    from row_bot import threads

    before = len(threads.get_latest_checkpoint_messages(offered))
    with _client(service) as client:
        _, headers = bootstrap(client)
        refused = _submit(client, headers, service, offered, tapped_choice=tapped)
        assert refused.status_code in {400, 422}, refused.text
    assert len(threads.get_latest_checkpoint_messages(offered)) == before



@pytest.mark.parametrize("action", ["comment", "swap", "new_take", "make"])
def test_a_video_panel_request_is_the_person_s_own_message_marked_with_its_button(service, offered, action):
    """C1: a comment, swap, new take or make sent from a storyboard is the person's words, marked with which of the
    Video panel's buttons sent it, as a reply button's tap is."""
    from langchain_core.messages import AIMessage
    from row_bot import approval_review, threads

    fake = ScriptedAgentStream((CheckpointCommit((AIMessage(id="answer", content="Done."),), "answer"),
                                ("done", None)))
    service.stream_factory = fake.stream
    with _client(service) as client:
        _, headers = bootstrap(client)
        sent = _submit(client, headers, service, offered, video_panel={"action": action})
        assert sent.status_code == 202, sent.text
        assert service.registry.get(sent.json()["execution_id"]).producer_done.wait(5)
        human = [message for message in threads.get_latest_checkpoint_messages(offered)
                 if message.type == "human"][-1]
        assert human.content == "List my subscriptions"
        assert human.additional_kwargs["video_panel"] == {"action": action}
        assert not {"platform_note", "row_bot_note", "tapped_choice"} & set(human.additional_kwargs)
        assert approval_review.person_messages([human]) == ("List my subscriptions",)


@pytest.mark.parametrize("panel", [{"action": "delete"}, {"action": "comment", "scene": "s000000"}])
def test_a_video_panel_request_with_another_action_is_refused(service, offered, panel):
    from row_bot import threads

    before = len(threads.get_latest_checkpoint_messages(offered))
    with _client(service) as client:
        _, headers = bootstrap(client)
        refused = _submit(client, headers, service, offered, video_panel=panel)
        assert refused.status_code in {400, 422}, refused.text
    assert len(threads.get_latest_checkpoint_messages(offered)) == before
