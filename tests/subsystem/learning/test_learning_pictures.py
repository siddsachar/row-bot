"""Pictures kept for the long run (P2, O128): 5.3's page picture after a browser or computer action is stored
as its own bytes, once, read back exactly as the model saw it, never touched by redaction, and kept within a
size cap that never drops a vault task's picture. A8: every arm is asked in the shape it is served (a text-only
arm without the picture, as training leaves it out; a vision arm with it), the vault keeps the picture, and a
task's text is the person's words. These run in the pull-request lane."""

from __future__ import annotations

import base64
import json
import sqlite3
from contextlib import closing

import pytest
from langchain_core.messages import AIMessage, message_to_dict

from row_bot.learning import capture, evaluation, redaction
from row_bot.learning.capture import Capture
from row_bot.learning.evaluation import Arm, collect_replay
from row_bot.learning.store import MARK_HELD_OUT, LearningStore
from tests.fixtures.learning import jpeg_bytes, picture_turn
from tests.fixtures.learning_eval import ScriptedClient

pytestmark = pytest.mark.subsystem


@pytest.fixture(autouse=True)
def sees_images(monkeypatch):
    """The main model sees images, so 5.3 sends it the page's picture."""

    from row_bot.browser import model_view

    monkeypatch.setattr(model_view, "model_sees_images", lambda model=None: True)


def _blob_files(root) -> dict[str, bytes]:
    folder = root / "learning" / "blobs"
    return {path.name: path.read_bytes() for path in folder.rglob("*") if path.is_file()}


def _decision(store: LearningStore, decision_id: str):
    return store.get("decision", decision_id)


def test_a_page_picture_is_stored_once_as_its_bytes_and_read_back_exactly(writer, chats, learning_on, clock):
    picture = jpeg_bytes("settings")
    sent, (_first, second) = picture_turn(writer, chats, "chat-1", picture)
    picture_turn(writer, chats, "chat-2", picture, question="Open it again")  # the same picture, another chat

    store = LearningStore(learning_on, clock=clock)
    files = _blob_files(learning_on)
    encoded = base64.b64encode(picture)
    assert sum(1 for data in files.values() if data == picture) == 1  # its original bytes, once
    assert not any(encoded[:64] in data or b"base64," in data for data in files.values())  # no base64 anywhere
    decision = _decision(store, second)
    assert decision.fidelity == "exact" and not decision.untrusted
    request = capture.read_request(store, decision)
    assert request["messages"] == [message_to_dict(message) for message in sent]  # exactly as the model saw it
    assert request["pictures_dropped"] == 0
    stats = store.picture_stats()
    assert (stats.stored, stats.bytes, stats.dropped) == (1, len(picture), 0)


def test_redaction_never_reads_picture_data_and_still_redacts_the_words_around_it(writer, chats, learning_on,
                                                                                   clock):
    # Base64 that holds a Google-key shape after a "+": before P2 redaction replaced it and the picture broke.
    shaped = "/9j/4AAQ+AIza" + "B" * 35 + "AAA"
    picture = base64.b64decode(shaped + "A" * (-len(shaped) % 4))
    assert redaction.redact_text("AIza" + "B" * 35)[1]  # the shape is a secret in text
    _sent, (_first, second) = picture_turn(writer, chats, "chat-1", picture)
    key = "AKIA" + "Q" * 16
    sent, (_, worded) = picture_turn(writer, chats, "chat-2", picture, caption=f"The page shows {key}.", number=2)

    store = LearningStore(learning_on, clock=clock)
    clean = _decision(store, second)
    assert clean.fidelity == "exact"  # a picture never makes a decision "redacted"
    request = capture.read_request(store, clean)
    url = request["messages"][-1]["data"]["content"][1]["image_url"]["url"]
    assert base64.b64decode(url.split(",", 1)[1]) == picture  # not corrupted
    marked = _decision(store, worded)
    assert marked.fidelity == "redacted"  # the caption's key was found and replaced
    text = json.dumps(capture.read_request(store, marked)["messages"])
    assert key not in text and redaction.REDACTED in text
    inline = f"see data:image/jpeg;base64,{shaped}"
    assert redaction.redact_text(inline) == (inline, False)  # image data left in text is skipped too
    assert sent  # the caption went to the model as written


def test_past_the_cap_the_oldest_pictures_go_but_never_a_vault_tasks_and_the_decision_stays(
        learning_on, clock, chats):
    pictures = [jpeg_bytes(f"page-{number}") for number in range(4)]
    cap = 3 * len(pictures[0])
    writer = Capture(clock=clock, start_thread=False, lookup=chats,
                     store_factory=lambda root: LearningStore(root, clock=clock, picture_cap=cap))
    try:
        ids = []
        for number, picture in enumerate(pictures):
            if number == 1:
                held = ids[0][1]
                LearningStore(learning_on, clock=clock).add_marks([("decision", held, "chat-0")], MARK_HELD_OUT)
            _sent, found = picture_turn(writer, chats, f"chat-{number}", picture, number=number)
            ids.append(found)
            clock.advance(60)
    finally:
        writer.shutdown()

    store = LearningStore(learning_on, clock=clock, picture_cap=cap)
    files = list(_blob_files(learning_on).values())
    assert pictures[0] in files  # the oldest, but a vault task's: kept
    assert pictures[1] not in files and pictures[2] in files and pictures[3] in files  # the oldest other went
    stats = store.picture_stats()
    assert (stats.stored, stats.dropped, stats.bytes) == (3, 1, cap)
    database = sum(path.stat().st_size for path in (store.path, store.path.with_name(store.path.name + "-wal"))
                   if path.is_file())
    assert store.storage_bytes() == database + sum(len(data) for data in _blob_files(learning_on).values())
    dropped = _decision(store, ids[1][1])
    assert dropped is not None  # the decision stays
    request = capture.read_request(store, dropped)
    assert request["pictures_dropped"] == 1
    assert request["messages"][-1]["data"]["content"][0]["text"] == "The browser page after that action."
    replay = collect_replay(store, refresh=False, include_untrusted=True)
    assert replay.skipped["picture_dropped"] == 1  # its starting point can't be rebuilt (§8.6)
    assert ids[1][1] not in {case.decision_id for case in replay.cases}


def _replay_case(store: LearningStore, decision_id: str):
    found = collect_replay(store, refresh=False, include_untrusted=True)
    return next(case for case in found.cases if case.decision_id == decision_id)


def _images(messages) -> list[str]:
    urls = []
    for message in messages:
        content = getattr(message, "content", "")
        for part in content if isinstance(content, list) else ():
            if isinstance(part, dict) and part.get("type") == "image_url":
                urls.append(part["image_url"]["url"])
    return urls


def test_each_arm_is_asked_in_the_shape_it_is_served(writer, chats, learning_on, clock):
    picture = jpeg_bytes("dashboard")
    _sent, (_first, second) = picture_turn(writer, chats, "chat-1", picture, question="Is the dashboard green?")
    case = _replay_case(LearningStore(learning_on, clock=clock), second)
    assert case.has_picture and case.text == "Is the dashboard green?"  # the person's words, not image data

    client = ScriptedClient(lambda *_: AIMessage(content="The settings page is open."), local=True)
    arms = [Arm("start", model_ref="start"), Arm("personal", model_ref="personal", serve_context=32_768),
            Arm("main", model_ref="main", sees_images=True)]
    results = {arm.name: evaluation.run_replay_case(case, arm, client=client) for arm in arms}

    sent = {call["model_ref"]: call["messages"] for call in client.calls}
    assert _images(sent["start"]) == [] and _images(sent["personal"]) == []  # text only, as training leaves it out
    assert _images(sent["main"]) == [f"data:image/jpeg;base64,{base64.b64encode(picture).decode()}"]
    assert {name: result.status for name, result in results.items()} == {
        "start": "passed", "personal": "passed", "main": "passed"}  # every arm is counted, none "unverified"


def test_the_vault_keeps_the_picture_and_counts_tasks_with_and_without_one(writer, chats, learning_on, clock):
    picture = jpeg_bytes("inbox")
    picture_turn(writer, chats, "chat-1", picture, question="Check my inbox", untrusted=True, clock=clock)
    clock.advance(60)
    picture_turn(writer, chats, "chat-2", picture, question="Check it again", untrusted=True, number=2, clock=clock)
    store = LearningStore(learning_on, clock=clock)

    evaluation.refresh_audit(store=store, asked_by="test")  # every 4th eligible call: chat-2's picture call

    with closing(sqlite3.connect(learning_on / "learning" / "audit.db")) as connection:
        [(content,)] = connection.execute("SELECT content FROM tasks").fetchall()
    held = json.loads(content)
    assert held["text"] == "Check it again" and held["untrusted"] is True
    assert base64.b64encode(picture).decode() in held["messages"][-1]["data"]["content"][1]["image_url"]["url"]
    counts = evaluation.audit_counts(asked_by="test", store=store)
    assert (counts["with_picture"], counts["without_picture"], counts["untrusted"], counts["trusted"]) == (1, 0, 1, 0)


def test_a_task_held_before_a8_is_read_with_the_persons_words_not_the_pictures_data(writer, chats, learning_on,
                                                                                   clock):
    picture = jpeg_bytes("held-before")
    _sent, (_first, second) = picture_turn(writer, chats, "chat-1", picture, question="Did the upload finish?")
    content = _replay_case(LearningStore(learning_on, clock=clock), second).to_content()
    # What the vault held before A8: the picture message's JSON, base64 and all, as the task's text.
    content["text"] = json.dumps(content["messages"][-1]["data"]["content"])[:4_000]

    case = evaluation.ReplayCase.from_content({"id": second, **content})

    assert case.text == "Did the upload finish?"
