"""Deletion reaches Learning: deleting a conversation purges what Learning
recorded from it and marks what was derived from it; deleting a lesson's memory
marks the lesson; a failed Learning step never breaks the deletion and is swept
later."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime

import pytest

# Bound to the session's data folder at import, as in the app (not to a test's folder).
from row_bot import threads
from row_bot.learning import contracts
from row_bot.thread_cleanup import delete_thread
from row_bot.learning.store import (
    MARK_CHAT_DELETED,
    MARK_MEMORY_DELETED,
    LearningStore,
)
from tests.fixtures.learning import (
    attempt,
    decision,
    episode,
    lesson,
    release,
    save_learning_settings,
    signal,
)
from tests.fixtures.memory_stack import fresh_memory_stack

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

LEARNING_WARNING = "Some Learning data could not be removed."


def _conversation(title: str) -> str:
    return threads.create_thread(title, thread_id=f"learning-{uuid.uuid4().hex[:10]}", seed_default_skills=False)


def test_deleting_a_conversation_purges_its_learning_data_and_marks_what_was_derived(learning_on):
    gone = _conversation("Learning purge")
    store = LearningStore()
    seen = store.put_blob(b"what the model saw in the deleted chat")
    store.put_many([
        episode(gone),
        decision(gone, request_blob=seen, raw_output_blob=store.put_blob(b"its reply"), executed="raw"),
        signal(gone, note="Use the ID"),
        contracts.TaskFixture(id="fix_1", origin="replay", input_blob=seen, verifier="exact", created_at=1,
                              source_decision_id="dec_1", thread_id=gone),
        episode("still-here", "ep_kept"),
        lesson(memory_id="mem-1", status="in_use", source_thread_ids=(gone,)),
        release("rel_v3", label="Personal model v3", source_thread_ids=(gone, "still-here")),
        attempt("att_1", source_thread_ids=(gone,)),
    ])

    result = delete_thread(gone)

    assert result.deleted is True and LEARNING_WARNING not in result.warnings
    assert threads._thread_exists(gone) is False
    assert store.owned_by(gone) == []
    assert store.read_blob(seen) is None
    assert [item.id for item in store.find("episode")] == ["ep_kept"]
    # Lessons, releases and attempts stay, marked: "trained on deleted data" / "Retrain without it".
    assert store.get("release", "rel_v3") is not None and store.get("lesson", "les_1").status == "in_use"
    assert sorted((mark.kind, mark.id, mark.mark, mark.thread_id) for mark in store.marks()) == [
        ("attempt", "att_1", MARK_CHAT_DELETED, gone),
        ("lesson", "les_1", MARK_CHAT_DELETED, gone),
        ("release", "rel_v3", MARK_CHAT_DELETED, gone),
    ]


def test_a_learning_failure_never_breaks_the_deletion_and_is_reported(learning_on, monkeypatch):
    gone = _conversation("Learning purge fails")
    store = LearningStore(learning_on)
    store.put(episode(gone))

    def locked(self, thread_id, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(LearningStore, "purge_thread", locked)
    result = delete_thread(gone)

    assert result.deleted is True
    assert threads._thread_exists(gone) is False
    assert LEARNING_WARNING in result.warnings
    assert [item.id for item in store.owned_by(gone)] == ["ep_1"]  # left for the idle sweep


def _fresh_cleanup(tmp_path, reload_for_data_dir):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    threads, cleanup = reload_for_data_dir(
        data_dir,
        "row_bot.tasks",
        "row_bot.threads",
        "row_bot.thread_cleanup",
        "row_bot.agent_runs",
        "row_bot.designer.storage",
        "row_bot.designer.history",
        "row_bot.designer.publish",
        "row_bot.developer.storage",
        "row_bot.developer.todos",
        "row_bot.developer.sandbox_runtime",
    )[1:3]
    return data_dir, threads, cleanup


def test_idle_maintenance_sweeps_learning_data_of_conversations_that_are_gone(tmp_path, reload_for_data_dir):
    data_dir, fresh_threads, cleanup = _fresh_cleanup(tmp_path, reload_for_data_dir)
    save_learning_settings(data_dir, enabled=True)
    alive = fresh_threads.create_thread("Alive", thread_id="alive-chat", seed_default_skills=False)
    store = LearningStore(data_dir)
    long_ago = datetime(2020, 1, 1).timestamp()
    recent = datetime.now().timestamp() + 3600  # newer than the sweep's one-day grace
    store.put_many([
        episode(alive, "ep_alive", started_at=long_ago),
        episode("deleted-long-ago", "ep_gone", started_at=long_ago),
        episode("written-just-now", "ep_new", started_at=recent),
        lesson(source_thread_ids=("deleted-long-ago",)),
    ])

    stats = cleanup.sweep_orphaned_thread_artifacts(now=datetime.now())

    assert stats["learning_threads"] == 1
    assert sorted(item.id for item in store.find("episode")) == ["ep_alive", "ep_new"]
    assert [(mark.id, mark.thread_id) for mark in store.marks()] == [("les_1", "deleted-long-ago")]


def _revision(kg, entity_id: str) -> str:
    with closing(sqlite3.connect(kg.DB_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        row = dict(connection.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone())
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()


@pytest.fixture
def memories(tmp_path, monkeypatch, reload_for_data_dir):
    stack = fresh_memory_stack(tmp_path, monkeypatch, reload_for_data_dir)
    data_dir = tmp_path / "row-bot-data"
    save_learning_settings(data_dir, enabled=True)
    return stack["kg"], stack["memory"], LearningStore(data_dir)


def _lesson_memory(memory, store, number: int, *, source: str = "live") -> tuple[str, str]:
    saved = memory.save_memory(
        "self_knowledge", f"Documents: pick by ID {number}", "Choose by ID when titles repeat.", source=source,
    )
    lesson_id = f"les_{number}"
    store.put(lesson(lesson_id, memory_id=saved["id"], status="in_use"))
    return saved["id"], lesson_id


def test_deleting_a_lessons_memory_marks_the_lesson_deleted(memories):
    kg, memory, store = memories
    memory_id, lesson_id = _lesson_memory(memory, store, 1)
    other_id, other_lesson = _lesson_memory(memory, store, 2)
    fact = memory.save_memory("fact", "Unrelated", "Not a lesson.")

    assert memory.delete_memory(fact["id"]) is True
    assert store.marks() == []

    assert memory.delete_memory(memory_id) is True
    deleted = store.get("lesson", lesson_id)
    assert (deleted.status, deleted.memory_id) == ("deleted", memory_id)
    assert deleted.memory_deleted_at is not None
    assert [(mark.id, mark.mark) for mark in store.marks()] == [(lesson_id, MARK_MEMORY_DELETED)]
    assert store.get("lesson", other_lesson).status == "in_use"

    # The Knowledge view's reviewed delete marks it too.
    outcome = kg.delete_reviewed_entities({other_id: _revision(kg, other_id)}, validate=lambda: None)
    assert outcome["deleted"] == [other_id]
    assert store.get("lesson", other_lesson).status == "deleted"


def test_deleting_memories_by_source_or_all_of_them_marks_their_lessons(memories):
    kg, memory, store = memories
    _, by_source = _lesson_memory(memory, store, 1, source="learning:test")
    _, remaining = _lesson_memory(memory, store, 2)

    assert kg.delete_entities_by_source("learning:test") == 1
    assert store.get("lesson", by_source).status == "deleted"
    assert store.get("lesson", remaining).status == "in_use"

    kg.delete_all_entities()
    assert store.get("lesson", remaining).status == "deleted"


def test_a_memory_delete_still_works_when_learning_cannot_mark(memories, monkeypatch):
    kg, memory, store = memories
    memory_id, _ = _lesson_memory(memory, store, 1)

    def broken(self, ids):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(LearningStore, "mark_lesson_memories_deleted", broken)
    assert memory.delete_memory(memory_id) is True
    assert memory.get_memory(memory_id) is None


def test_deleting_a_conversation_removes_a_versions_kept_rows_that_came_from_it(learning_on):
    """C1 (A10): the rows a version kept for a later upload never outlive the chat they came from."""

    from row_bot.learning.training import kept_rows, releases

    gone = _conversation("Learning kept rows")
    LearningStore().create()
    folder = releases.version_dir(1, learning_on)
    data = learning_on / "rows-data"
    data.mkdir()
    rows = [{"messages": [{"role": "user", "content": "from the deleted chat"}]}]
    for name in (kept_rows.EXAMPLES, kept_rows.VALIDATION):
        (data / name).write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    manifest = {"version": 2, "recipe": "16gb-v1", "max_seq_length": 16384, "rendering": {"name": "served-v2"},
                "conversations": [gone]}
    manifest["sha256"] = kept_rows.digest(manifest, rows, rows)
    folder.mkdir(parents=True)
    (folder / releases.DATASET_FILE).write_text(json.dumps(manifest), encoding="utf-8")
    assert kept_rows.keep(data, folder, manifest)

    delete_thread(gone)

    assert not (folder / kept_rows.FOLDER).exists() and (folder / releases.DATASET_FILE).is_file()
