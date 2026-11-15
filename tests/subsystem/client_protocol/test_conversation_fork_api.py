"""conversation.fork through the client API (B5): Fork from here, and Edit and resend through the normal send."""
from __future__ import annotations

from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tests.subsystem.client_protocol.test_protocol_application import _client, _command, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

MODEL = {"provider_id": "fixture", "model_ref": "fixture::model"}


def _view(client, headers, conversation: str) -> dict:
    return client.get(f"/api/v1/conversations/{conversation}", headers=headers).json()


def _chat(client, headers, *, approval_mode: str = "approve") -> str:
    from row_bot import threads

    conversation = _command(client, headers, "conversation.create", {"title": "Trip"}).json()["conversation_id"]
    threads._set_thread_approval_mode(conversation, approval_mode)
    assert threads.append_checkpoint_messages(conversation, [
        HumanMessage(id="h1", content="Plan a trip"), AIMessage(id="a1", content="Where to?"),
        HumanMessage(id="h2", content="Lisbon"),
        AIMessage(id="a2-call", content="", tool_calls=[{"id": "call-1", "name": "web_search", "args": {}}]),
        ToolMessage(id="t2", tool_call_id="call-1", name="web_search", content="Warm in May."),
        AIMessage(id="a2", content="May is good.")])
    return conversation


def _fork(client, headers, conversation: str, message: str, mode: str, **options):
    return _command(client, headers, "conversation.fork", {"at_message_id": message, "mode": mode},
                    target=conversation, revision=_view(client, headers, conversation)["revision"], **options)


def test_fork_from_here_opens_a_new_conversation_that_says_where_it_came_from(service):
    from row_bot import threads

    with _client(service) as client:
        _, headers = bootstrap(client)
        source = _chat(client, headers)

        forked = _fork(client, headers, source, "a1", "fork")

        assert forked.status_code == 200, forked.text
        receipt = forked.json()
        fork = receipt["forked_conversation_id"]
        assert receipt["status"] == "completed" and receipt["conversation_id"] == source and fork != source
        assert [message.id for message in threads.get_latest_checkpoint_messages(fork)] == ["h1", "a1"]
        assert _view(client, headers, fork)["forked_from"] == {"id": source, "title": "Trip"}
        assert _view(client, headers, source)["forked_from"] is None
        listed = client.get("/api/v1/conversations", headers=headers).json()["items"]
        assert {row["id"] for row in listed} >= {source, fork}


def test_edit_and_resend_sends_the_new_text_through_the_normal_admission(service):
    from row_bot import threads

    asked: list[tuple[str, str]] = []

    def stream(text, _enabled, config, *, stop_event=None):
        asked.append((text, config["configurable"]["approval_mode"]))
        yield "done", "Porto it is."

    service.stream_factory = stream
    with _client(service) as client:
        _, headers = bootstrap(client)
        source = _chat(client, headers, approval_mode="approve")
        before = threads.get_latest_checkpoint_messages(source)

        forked = _fork(client, headers, source, "h2", "edit")
        assert forked.status_code == 200, forked.text
        fork = forked.json()["forked_conversation_id"]
        assert forked.json()["draft_attachments"] == []
        submitted = _command(client, headers, "conversation.submit", {
            "submission_id": str(uuid4()), "text": "Porto", "model_selection": MODEL},
            target=fork, revision=_view(client, headers, fork)["revision"])
        assert submitted.status_code == 202, submitted.text
        assert service.registry.get(submitted.json()["execution_id"]).producer_done.wait(5)

        assert asked == [("Porto", "approve")]
        history = threads.get_latest_checkpoint_messages(fork)
        assert [message.content for message in history[:3]] == ["Plan a trip", "Where to?", "Porto"]
        assert threads.get_latest_checkpoint_messages(source) == before


def test_a_fork_is_refused_while_the_reply_is_under_way(service, monkeypatch):
    from row_bot import threads

    with _client(service) as client:
        _, headers = bootstrap(client)
        source = _chat(client, headers)
        revision = _view(client, headers, source)["revision"]

        with monkeypatch.context() as replying:
            replying.setattr(service.registry, "active", lambda conversation_id: (object(),))
            refused = _command(client, headers, "conversation.fork", {"at_message_id": "a1", "mode": "fork"},
                               target=source, revision=revision)

        assert refused.status_code == 409 and refused.json()["code"] == "generation_active"
        assert len(threads._list_threads()) == 1


@pytest.mark.parametrize(("payload", "status", "code"), [
    ({"at_message_id": "a1", "mode": "rewind"}, 422, "invalid_command"),
    ({"at_message_id": "", "mode": "fork"}, 422, "invalid_command"),
    ({"at_message_id": "missing", "mode": "fork"}, 404, "not_found"),
    ({"at_message_id": "t2", "mode": "fork"}, 422, "invalid_command"),
    ({"at_message_id": "a1", "mode": "edit"}, 422, "invalid_command"),
])
def test_only_a_known_message_of_the_right_kind_forks(service, payload, status, code):
    from row_bot import threads

    with _client(service) as client:
        _, headers = bootstrap(client)
        source = _chat(client, headers)

        refused = _command(client, headers, "conversation.fork", payload, target=source,
                           revision=_view(client, headers, source)["revision"])

        assert refused.status_code == status, refused.text
        assert refused.json()["code"] == code
        assert len(threads._list_threads()) == 1


def test_a_fork_needs_the_callers_own_session(service):
    from row_bot import threads

    with _client(service) as client:
        _, headers = bootstrap(client)
        _, other = bootstrap(client)
        source = _chat(client, headers)

        borrowed = client.post(f"/api/v1/conversations/{source}/commands", headers={
            **headers, "Idempotency-Key": str(uuid4())}, json={
            "command_id": str(uuid4()), "client_session_id": other["X-Client-Session"], "type": "conversation.fork",
            "expected_revision": _view(client, headers, source)["revision"],
            "payload": {"at_message_id": "a1", "mode": "fork"}})

        assert borrowed.status_code == 403
        assert len(threads._list_threads()) == 1


def test_a_replayed_fork_returns_the_same_conversation(service):
    from row_bot import threads

    with _client(service) as client:
        _, headers = bootstrap(client)
        source = _chat(client, headers)
        key, command = str(uuid4()), str(uuid4())

        first = _fork(client, headers, source, "a1", "fork", key=key, command_id=command)
        again = _fork(client, headers, source, "a1", "fork", key=key, command_id=command)

        assert first.status_code == again.status_code == 200
        assert again.json()["forked_conversation_id"] == first.json()["forked_conversation_id"]
        assert len(threads._list_threads()) == 2
