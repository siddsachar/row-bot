"""The person's ticks and sent choices on answer components (F3): kept beside the chat, revision-checked."""
from __future__ import annotations

import pytest

from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

LIST = "block:" + "a" * 24
CHOICES = "block:" + "b" * 24


@pytest.fixture
def answer_service(service, tmp_path, monkeypatch):
    from row_bot import threads
    folder = tmp_path / "thread-ui"
    folder.mkdir()
    monkeypatch.setattr(threads, "_THREAD_UI_DIR", folder)
    return service


def test_ticks_and_a_choice_sync_between_two_clients_and_never_touch_the_transcript(answer_service):
    from row_bot import threads
    conversation = threads.create_thread("Answers", seed_default_skills=False)
    checkpoint = threads.get_latest_checkpoint_revision(conversation)
    endpoint = f"/api/v1/conversations/{conversation}/answer-state"
    with _client(answer_service) as client:
        _, phone = bootstrap(client)
        _, desktop = bootstrap(client)
        empty = client.get(endpoint, headers=phone)
        assert empty.status_code == 200, empty.text
        assert empty.json()["ticks"] == {} and empty.json()["choices"] == {}

        ticked = client.put(endpoint, headers=phone, json={
            "expected_revision": empty.json()["revision"], "block_id": LIST, "item_id": "book", "done": True})
        assert ticked.status_code == 200, ticked.text
        assert ticked.json()["ticks"] == {LIST: {"book": True}}
        # The other device reads the same state, and a change from the old revision is refused.
        assert client.get(endpoint, headers=desktop).json() == ticked.json()
        stale = client.put(endpoint, headers=desktop, json={
            "expected_revision": empty.json()["revision"], "block_id": LIST, "item_id": "book", "done": False})
        assert stale.status_code == 409 and stale.json()["code"] == "answer_state_revision_conflict"

        chosen = client.put(endpoint, headers=desktop, json={
            "expected_revision": ticked.json()["revision"], "block_id": CHOICES, "choice": "Subscriptions"})
        assert chosen.status_code == 200, chosen.text
        assert chosen.json()["choices"] == {CHOICES: "Subscriptions"}
        # One choice per block: another one is refused, the same one again is fine.
        other = client.put(endpoint, headers=phone, json={
            "expected_revision": chosen.json()["revision"], "block_id": CHOICES, "choice": "Groceries"})
        assert other.status_code == 409 and other.json()["code"] == "answer_choice_taken"
        again = client.put(endpoint, headers=phone, json={
            "expected_revision": chosen.json()["revision"], "block_id": CHOICES, "choice": "Subscriptions"})
        assert again.status_code == 200 and again.json()["choices"] == {CHOICES: "Subscriptions"}

    # The state is the person's own: the conversation's saved messages didn't change.
    assert threads.get_latest_checkpoint_revision(conversation) == checkpoint


@pytest.mark.parametrize("change", [
    {"block_id": "block:nothex", "item_id": "a", "done": True},
    {"block_id": LIST, "item_id": "has space", "done": True},
    {"block_id": LIST, "item_id": "a"},
    {"block_id": LIST, "choice": "x" * 41},
    {"block_id": LIST, "item_id": "a", "done": True, "colour": "red"},
])
def test_a_malformed_change_is_refused(answer_service, change):
    from row_bot import threads
    conversation = threads.create_thread("Answers", seed_default_skills=False)
    endpoint = f"/api/v1/conversations/{conversation}/answer-state"
    with _client(answer_service) as client:
        _, headers = bootstrap(client)
        revision = client.get(endpoint, headers=headers).json()["revision"]
        refused = client.put(endpoint, headers=headers, json={"expected_revision": revision, **change})
        assert refused.status_code in {400, 422}, refused.text
        assert client.get(endpoint, headers=headers).json()["revision"] == revision


def test_the_state_stays_bounded(answer_service):
    from row_bot import threads
    from row_bot.application.answer_state import MAX_STATE_BYTES, read_answer_state, save_answer_state
    from row_bot.application.client_platform import ClientPlatformError
    conversation = threads.create_thread("Answers", seed_default_skills=False)
    state = read_answer_state(answer_service, conversation)
    # Half the limit fits; a second half, on another block, doesn't (a surface's data model is the large part).
    half = {"notes": "x" * (MAX_STATE_BYTES // 2)}
    state = save_answer_state(answer_service, conversation, {
        "expected_revision": state["revision"], "block_id": LIST, "surface_data": half})
    with pytest.raises(ClientPlatformError) as full:
        save_answer_state(answer_service, conversation, {
            "expected_revision": state["revision"], "block_id": CHOICES, "surface_data": half})
    assert full.value.code == "answer_state_full"
    assert len((threads._THREAD_UI_DIR / f"{conversation}.answers.json").read_bytes()) <= MAX_STATE_BYTES


def test_deleting_the_chat_removes_its_answer_state(answer_service):
    from row_bot import threads
    from row_bot.application.answer_state import read_answer_state, save_answer_state
    from row_bot.thread_cleanup import delete_thread
    conversation = threads.create_thread("Answers", seed_default_skills=False)
    state = read_answer_state(answer_service, conversation)
    save_answer_state(answer_service, conversation, {
        "expected_revision": state["revision"], "block_id": LIST, "item_id": "a", "done": True})
    path = threads._THREAD_UI_DIR / f"{conversation}.answers.json"
    assert path.exists()
    delete_thread(conversation)
    assert not path.exists()
