"""Every blob Learning writes belongs to its chat (P2, the privacy fix). A request names its messages, tools
and pictures only inside its manifest, so before P2 "Don't learn from this chat" and deleting a chat freed
the decision's own blobs and left the person's words and pictures on disk. Now an opt-out, a deletion and
"Delete learning data" leave no blob of that chat behind, and a one-off sweep on the first start removes
what earlier builds left while keeping everything still referenced. These run in the pull-request lane."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from row_bot import learning
from row_bot.learning import capture, past_chats, scope
from row_bot.learning.store import BLOB_GRACE_SECONDS, LearningStore
from tests.fixtures.learning import CapturedWork, jpeg_bytes, picture_turn, save_learning_settings

pytestmark = pytest.mark.subsystem

SECRET_WORDS = "my sister's address is 4 Elm Row"


@pytest.fixture(autouse=True)
def sees_images(monkeypatch):
    from row_bot.browser import model_view

    monkeypatch.setattr(model_view, "model_sees_images", lambda model=None: True)


class PastChats:
    """Saved conversations for the 30-day import: one chat, inside the window."""

    def __init__(self, thread_id: str, created_at: float, messages: list) -> None:
        self.thread_id, self.created_at, self.saved = thread_id, created_at, messages

    def conversations(self, since: float) -> list[tuple[str, float]]:
        return [(self.thread_id, self.created_at)]

    def messages(self, thread_id: str) -> list:
        return list(self.saved)

    def fork_source(self, thread_id: str) -> str:
        return ""

    def window_start(self, thread_id: str, since: float, created_at: float) -> tuple[int, float]:
        return 0, created_at


def _blobs(root) -> dict[str, bytes]:
    folder = root / "learning" / "blobs"
    return {path.name: path.read_bytes() for path in folder.rglob("*") if path.is_file()} if folder.is_dir() else {}


def _requests(store: LearningStore) -> dict[str, object]:
    return {item.id: capture.read_request(store, item)
            for item in store.find("decision", subkind="model_call", limit=10_000)}


@pytest.fixture
def learning_on(learning_on):
    """Learning on, with "Also learn from the last 30 days of chats" ticked (off by default, O195)."""

    save_learning_settings(learning_on, sources={"past_chats": True})
    return learning_on


@pytest.fixture
def two_chats(writer, chats, learning_on, clock):
    """Two chats with captured work, a page picture each, and an imported past chat; chat-a holds words and a
    picture nothing else has."""

    work = CapturedWork(writer, chats, clock)
    work.turn("chat-a", f"Remember: {SECRET_WORDS}", "Noted.")
    picture_turn(writer, chats, "chat-a", jpeg_bytes("chat-a-only"), question=f"Find {SECRET_WORDS}", number=1)
    work.turn("chat-b", "What is 6 * 7?", "42.")
    picture_turn(writer, chats, "chat-b", jpeg_bytes("chat-b-only"), number=2)
    store = LearningStore(learning_on, clock=clock)
    chats.chat("chat-past")
    past = [HumanMessage(content="Which train is first?", id="h-past"), AIMessage(content="The 7:04.", id="a-past")]
    past_chats.run_import(store=store, source=PastChats("chat-past", clock() - 86400.0, past), lookup=chats,
                          clock=clock, busy=lambda: False, wait=lambda _seconds: None)
    return store


@pytest.mark.parametrize("how", ["opt_out", "delete"])
def test_an_opt_out_or_a_deletion_leaves_no_blob_of_that_chat_behind(two_chats, learning_on, how):
    store = two_chats
    kept_before = {key: value for key, value in _requests(store).items()
                   if store.get("decision", key).conversation_id != "chat-a"}

    if how == "opt_out":
        scope.set_chat_opt_out("chat-a", True, store=store)  # "Don't learn from this chat"
    else:
        store.purge_thread("chat-a")  # what deleting the chat runs

    files = _blobs(learning_on)
    text = b"\n".join(files.values())
    assert SECRET_WORDS.encode() not in text and jpeg_bytes("chat-a-only") not in files.values()
    assert jpeg_bytes("chat-b-only") in files.values()  # the other chat's picture stays
    assert _requests(store) == kept_before  # everything else still reads back as it was
    # Nothing is left that no record references.
    assert store.sweep_orphans(remove=False, grace_seconds=0).files == 0


def test_a_deleted_past_chat_takes_its_imported_conversation_with_it(two_chats, learning_on):
    store = two_chats
    assert b"Which train is first?" in b"\n".join(_blobs(learning_on).values())

    store.purge_thread("chat-past")

    assert b"Which train is first?" not in b"\n".join(_blobs(learning_on).values())
    assert store.sweep_orphans(remove=False, grace_seconds=0).files == 0


def test_delete_learning_data_removes_every_blob_pictures_included(two_chats, learning_on):
    assert two_chats.picture_stats().stored == 2

    two_chats.delete_all()

    assert _blobs(learning_on) == {}


def _plant(store: LearningStore, root, clock) -> dict[str, bytes]:
    """What an earlier build left behind: an opted-out chat's message blob, a file no row knows and an
    unfinished write; and, as earlier builds wrote, no owners for anything."""

    words = json.dumps({"type": "human", "data": {"content": SECRET_WORDS, "type": "human"}}).encode()
    store.put_blob(words)
    stray = jpeg_bytes("stray")
    folder = root / "learning" / "blobs"
    import hashlib

    stray_sha = hashlib.sha256(stray).hexdigest()
    (folder / stray_sha[:2]).mkdir(exist_ok=True)
    (folder / stray_sha[:2] / stray_sha).write_bytes(stray)
    (folder / "tmp").mkdir(exist_ok=True)
    (folder / "tmp" / "cut-short.tmp").write_bytes(b"half a blob")
    old = time.time() - 2 * BLOB_GRACE_SECONDS
    for path in folder.rglob("*"):
        if path.is_file():
            os.utime(path, (old, old))
    with closing(sqlite3.connect(root / "learning.db")) as connection, connection:
        connection.execute("DELETE FROM blob_owners")
    clock.advance(2 * BLOB_GRACE_SECONDS)
    return {"words": words, "stray": stray}


def test_the_one_off_sweep_removes_planted_orphans_and_keeps_everything_referenced(two_chats, learning_on, clock):
    store = two_chats
    before = _requests(store)
    planted = _plant(store, learning_on, clock)

    report = store.sweep_once()

    # The planted three, and the finished import's plan (the job forgets it when the import is done).
    sizes = {kind: (files, size) for kind, files, size in report.kinds}
    assert report.files == 4 and set(sizes) == {"message", "picture", "unfinished_write", "past_chat_plan"}
    assert (sizes["message"], sizes["picture"], sizes["unfinished_write"]) == (
        (1, len(planted["words"])), (1, len(planted["stray"])), (1, len(b"half a blob")))
    files = _blobs(learning_on)
    assert planted["words"] not in files.values() and planted["stray"] not in files.values()
    assert not any(b"past_chat_plan" in data for data in files.values())
    assert _requests(store) == before  # every request still reads back, pictures and past chats included
    assert store.sweep_result()["files"] == 4 and store.sweep_once() is None  # once per store
    # What earlier builds wrote now belongs to its chat: an opt-out frees it.
    scope.set_chat_opt_out("chat-a", True, store=store)
    assert SECRET_WORDS.encode() not in b"\n".join(_blobs(learning_on).values())


def test_the_sweep_starts_once_in_the_background_on_start_up(learning_on, data_dir):
    assert learning.start_blob_sweep() is None  # no store: one path check
    LearningStore(learning_on).create()

    learning.start_blob_sweep().join(10)

    assert LearningStore(learning_on).sweep_result() == {"at": pytest.approx(time.time(), abs=60), "files": 0,
                                                          "bytes": 0, "kinds": {}}


def test_a_turn_still_running_when_its_chat_is_switched_off_leaves_nothing_after(writer, chats, learning_on, clock):
    from langchain_core.messages import SystemMessage

    chats.chat("chat-a")
    turn = writer.begin_turn(thread_id="chat-a", surface="normal_chat")
    asked = [SystemMessage(content="You are Row-Bot.", id="sys"), HumanMessage(content="Hello", id="h1")]
    writer.model_call(turn, messages=asked, response=AIMessage(content="", id="a1"), model_ref="gpt-6.1-sol",
                      provider_id="codex")
    writer.drain()  # the turn's scope is decided: allowed
    store = LearningStore(learning_on, clock=clock)
    scope.set_chat_opt_out("chat-a", True, store=store)

    later = HumanMessage(content=f"Also, {SECRET_WORDS}", id="h2")
    writer.model_call(turn, messages=[*asked, later], response=AIMessage(content="Noted.", id="a2"),
                      model_ref="gpt-6.1-sol", provider_id="codex")
    writer.end_turn(turn, "completed")
    writer.drain()

    assert SECRET_WORDS.encode() not in b"\n".join(_blobs(learning_on).values())
    assert store.owned_by("chat-a") == []
