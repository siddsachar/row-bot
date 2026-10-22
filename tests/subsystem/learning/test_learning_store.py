"""learning.db and learning/blobs: created only on enable or the first write while
on, content-addressed blobs with garbage collection, versioned migrations, every
record type, the archive's lineage queries and delete-all."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing

import pytest

from row_bot.learning import contracts
from row_bot.learning.store import (
    BLOB_GRACE_SECONDS,
    MARK_CHAT_DELETED,
    LearningStore,
    LearningStoreError,
)
from tests.fixtures.learning import (
    attempt,
    decision,
    episode,
    learning_files,
    lesson,
    release,
    save_learning_settings,
    full_records,
    signal,
)

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


def test_reads_never_create_the_store(data_dir, clock):
    store = LearningStore(clock=clock)
    assert store.exists() is False
    assert store.get("episode", "ep_1") is None
    assert store.find("attempt") == []
    assert store.attempts(kept=True) == []
    assert store.ancestors("attempt", "att_1") == []
    assert store.marks() == []
    assert store.owned_by("chat-1") == []
    assert store.is_chat_opted_out("chat-1") is False
    assert store.opted_out_chats() == []
    assert store.read_blob("a" * 64) is None
    assert store.storage_bytes() == 0
    assert store.schema_version() is None
    assert store.purge_thread("chat-1").total == 0
    assert store.purge_missing_threads(set(), older_than=clock() + 1) == 0
    assert store.gc_blobs() == 0
    assert store.mark_lesson_memories_deleted(["mem-1"]) == 0
    assert store.mark_all_lesson_memories_deleted() == 0
    assert store.set_chat_opt_out("chat-1", False) is None
    assert learning_files(data_dir) == []


def test_nothing_is_written_or_created_while_learning_is_off(data_dir, clock):
    store = LearningStore(clock=clock)
    attempts = [
        lambda: store.put(episode("chat-1")),
        lambda: store.put(lesson()),
        lambda: store.put_blob(b"what the model saw"),
        lambda: store.set_chat_opt_out("chat-1", True),
    ]
    for write in attempts:
        with pytest.raises(LearningStoreError) as error:
            write()
        assert error.value.code == "learning_off"
    assert learning_files(data_dir) == []


def test_turning_learning_on_or_the_first_write_while_on_creates_the_store(learning_on, clock):
    store = LearningStore(clock=clock)
    assert store.exists() is False  # on, but nothing written yet
    assert store.put(episode("chat-1")) is True
    assert learning_files(learning_on) == ["learning", "learning.db", "learning/blobs", "learning_config.json"]
    assert store.schema_version() == 1

    other = learning_on.parent / "explicit"
    other.mkdir()
    LearningStore(other, clock=clock).create()  # what turning Learning on does
    assert sorted(path.name for path in other.iterdir()) == ["learning", "learning.db"]


def test_with_learning_off_kept_data_is_maintained_but_nothing_new_is_recorded(learning_on, clock):
    store = LearningStore(clock=clock)
    store.put_many([episode("chat-1"), lesson(memory_id="mem-1", status="in_use")])
    save_learning_settings(learning_on, enabled=False)

    for write in (lambda: store.put(signal("chat-1")), lambda: store.put_blob(b"more")):
        with pytest.raises(LearningStoreError):
            write()
    # Changes in use stay manageable: a lesson can still be updated, and purges still run.
    assert store.put(lesson(status="off", memory_id="mem-1", updated_at=clock() + 5)) is True
    assert store.get("lesson", "les_1").status == "off"
    assert store.purge_thread("chat-1").removed_of("episode") == 1


def test_every_record_type_is_stored_and_read_back(learning_on, clock):
    store = LearningStore(clock=clock)
    records = full_records()
    assert store.put_many(records) == len(records)
    for record in records:
        assert store.get(record.KIND, record.id) == record
    # Saving again replaces, it doesn't duplicate.
    assert store.put(records[0]) is True
    assert len(store.find("episode")) == 1


def test_blobs_are_content_addressed_deduplicated_and_written_atomically(learning_on, clock):
    store = LearningStore(clock=clock)
    first = store.put_blob(b"the request as sent")
    again = store.put_blob(b"the request as sent")
    other = store.put_blob(b"the raw reply")
    assert first == again != other
    assert len(first) == 64
    assert store.read_blob(first) == b"the request as sent"
    files = sorted(path.name for path in store.blobs_dir.rglob("*") if path.is_file())
    assert files == sorted([first, other])  # one file each, no temp files left behind

    # A damaged file is never returned as the content.
    path = next(path for path in store.blobs_dir.rglob(first))
    path.write_bytes(b"tampered")
    assert store.read_blob(first) is None
    assert store.read_blob("../../learning.db") is None


def test_unreferenced_blobs_are_collected_and_referenced_ones_kept(learning_on, clock):
    store = LearningStore(clock=clock)
    kept = store.put_blob(b"referenced")
    loose = store.put_blob(b"nothing names this")
    store.put(decision("chat-1", raw_output_blob=kept, executed="raw"))

    assert store.gc_blobs() == 0  # inside the grace period nothing is swept
    clock.advance(BLOB_GRACE_SECONDS + 1)
    assert store.gc_blobs() == 1
    assert store.read_blob(loose) is None
    assert store.read_blob(kept) == b"referenced"


def test_a_blob_shared_by_two_chats_is_freed_only_when_both_are_purged(learning_on, clock):
    store = LearningStore(clock=clock)
    shared = store.put_blob(b"the same tool schema prompt")
    store.put_many([
        decision("chat-1", "dec_1", request_blob=shared),
        decision("chat-2", "dec_2", request_blob=shared),
    ])
    assert store.purge_thread("chat-1").blobs_freed == 0
    assert store.read_blob(shared) is not None
    assert store.purge_thread("chat-2").blobs_freed == 1
    assert store.read_blob(shared) is None


def test_leftovers_of_an_interrupted_blob_write_are_swept(learning_on, clock):
    store = LearningStore(clock=clock)
    store.put_blob(b"something")
    leftover = store.blobs_dir / "tmp" / "abc.tmp"
    leftover.parent.mkdir(exist_ok=True)
    leftover.write_bytes(b"half written")
    orphan = store.blobs_dir / "ff" / ("f" * 64)
    orphan.parent.mkdir(exist_ok=True)
    orphan.write_bytes(b"file with no row")
    for path in store.blobs_dir.rglob("*"):
        if path.is_file():
            os.utime(path, (clock(), clock()))  # written "now" on the test's clock
    assert store.gc_blobs() == 0
    clock.advance(BLOB_GRACE_SECONDS + 10)
    assert store.gc_blobs() == 3  # the temp file, the row-less file, and the unreferenced blob
    assert not leftover.exists() and not orphan.exists()


def test_a_fresh_store_is_at_the_current_schema_and_reopening_changes_nothing(learning_on, clock):
    calls: list[int] = []

    def counted_v1(connection):
        calls.append(1)
        from row_bot.learning.store import MIGRATIONS

        MIGRATIONS[0](connection)

    store = LearningStore(clock=clock, migrations=[counted_v1])
    store.create()
    store.put(episode("chat-1"))
    store.find("episode")
    assert calls == [1]
    assert store.schema_version() == 1


def test_a_schema_upgrade_runs_once_and_keeps_the_data(learning_on, clock):
    from row_bot.learning.store import MIGRATIONS

    LearningStore(clock=clock).put(episode("chat-1"))

    def add_tag_table(connection):
        connection.execute("CREATE TABLE tags (id TEXT PRIMARY KEY)")

    upgraded = LearningStore(clock=clock, migrations=[*MIGRATIONS, add_tag_table])
    assert upgraded.get("episode", "ep_1") == episode("chat-1")
    assert upgraded.schema_version() == 2
    # The older app now refuses the newer store instead of changing it.
    older = LearningStore(clock=clock)
    with pytest.raises(LearningStoreError) as error:
        older.find("episode")
    assert error.value.code == "store_newer_than_app"
    with pytest.raises(LearningStoreError):
        older.put(episode("chat-2", "ep_2"))
    assert upgraded.find("episode") == [episode("chat-1")]


def test_a_failed_migration_leaves_the_store_as_it_was(learning_on, clock):
    from row_bot.learning.store import MIGRATIONS

    LearningStore(clock=clock).put(episode("chat-1"))

    def broken(connection):
        connection.execute("CREATE TABLE half_done (id TEXT)")
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        LearningStore(clock=clock, migrations=[*MIGRATIONS, broken]).find("episode")
    store = LearningStore(clock=clock)
    assert store.schema_version() == 1
    assert store.find("episode") == [episode("chat-1")]
    with closing(sqlite3.connect(store.path)) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "half_done" not in names


def test_the_archive_is_queryable_by_outcome_and_lineage(learning_on, clock):
    store = LearningStore(clock=clock)
    store.put_many([
        attempt("att_1", outcome="not_better", strategy_release_id="strat_1"),
        attempt("att_2", parent_ids=("att_1",), outcome="improved", kept=True, strategy_release_id="strat_1"),
        attempt("att_3", parent_ids=("att_1",), outcome="worse", change_kind="training"),
        attempt("att_4", parent_ids=("att_2", "att_3"), outcome="pending", kind_of_work="Weekly reports"),
    ])
    assert [item.id for item in store.attempts()] == ["att_1", "att_2", "att_3", "att_4"]
    assert [item.id for item in store.attempts(kept=True)] == ["att_2"]
    assert [item.id for item in store.attempts(kept=False)] == ["att_1", "att_3", "att_4"]
    assert [item.id for item in store.attempts(outcome="worse")] == ["att_3"]
    assert [item.id for item in store.attempts(change_kind="training")] == ["att_3"]
    assert [item.id for item in store.attempts(strategy_release_id="strat_1")] == ["att_1", "att_2"]
    assert [item.id for item in store.attempts(kind_of_work="Weekly reports")] == ["att_4"]
    assert [item.id for item in store.ancestors("attempt", "att_4")] == ["att_2", "att_3", "att_1"]
    assert [item.id for item in store.descendants("attempt", "att_1")] == ["att_2", "att_3", "att_4"]

    store.put_many([release("rel_1"), release("rel_2", version=2, parent_ids=("rel_1",))])
    assert [item.id for item in store.ancestors("release", "rel_2")] == ["rel_1"]
    assert [item.id for item in store.find("release", subkind="model")] == ["rel_1", "rel_2"]
    with pytest.raises(ValueError):
        store.ancestors("episode", "ep_1")


def test_a_purge_removes_what_a_chat_owns_and_marks_what_was_derived(learning_on, clock):
    store = LearningStore(clock=clock)
    request = store.put_blob(b"what the model saw")
    store.put_many([
        episode("chat-1"),
        decision("chat-1", request_blob=request),
        signal("chat-1"),
        episode("child-1", "ep_child", conversation_id="chat-1"),
        contracts.TaskFixture(id="fix_1", origin="replay", input_blob=request, verifier="v",
                              created_at=clock(), source_decision_id="dec_1", thread_id="chat-1"),
        episode("chat-2", "ep_2"),
        lesson(source_thread_ids=("chat-1",)),
        release(source_thread_ids=("chat-1", "chat-2")),
        attempt("att_1", source_thread_ids=("chat-1",)),
    ])
    report = store.purge_thread("chat-1")
    assert dict(report.removed) == {"decision": 1, "episode": 2, "fixture": 1, "signal": 1}
    assert report.blobs_freed == 1
    assert sorted(report.marked) == [("attempt", "att_1"), ("lesson", "les_1"), ("release", "rel_1")]
    assert store.owned_by("chat-1") == []
    assert [item.id for item in store.owned_by("chat-2")] == ["ep_2"]
    # Derived things stay, marked, for "Retrain without it".
    assert store.get("release", "rel_1") is not None
    assert {(mark.id, mark.mark, mark.thread_id) for mark in store.marks()} == {
        ("att_1", MARK_CHAT_DELETED, "chat-1"),
        ("les_1", MARK_CHAT_DELETED, "chat-1"),
        ("rel_1", MARK_CHAT_DELETED, "chat-1"),
    }
    assert store.purge_thread("chat-1").total == 0  # idempotent


def test_delete_all_removes_the_data_reports_trained_models_and_keeps_opt_outs(learning_on, clock):
    store = LearningStore(clock=clock)
    store.put_blob(b"content")
    store.put_many([
        episode("chat-1"),
        release("rel_model", label="Personal model v1"),
        release("rel_ways", kind="behaviour"),
    ])
    store.set_chat_opt_out("chat-9", True)

    report = store.delete_all()
    assert report.deleted is True and report.bytes_freed > 0
    assert [(item.id, item.label) for item in report.trained_releases] == [("rel_model", "Personal model v1")]
    assert report.kept_opt_outs == 1
    assert store.find("episode") == [] and store.find("release") == []
    assert store.opted_out_chats() == ["chat-9"]
    assert not any(path.is_file() for path in store.blobs_dir.rglob("*"))
    assert (learning_on / "learning_config.json").is_file()  # the settings stay


def test_delete_all_without_opt_outs_leaves_no_learning_data(learning_on, clock):
    store = LearningStore(clock=clock)
    store.put_blob(b"content")
    store.put(episode("chat-1"))
    assert store.delete_all().deleted is True
    assert learning_files(learning_on) == ["learning_config.json"]
    assert LearningStore(clock=clock).delete_all().deleted is False  # nothing left, nothing created
    assert learning_files(learning_on) == ["learning_config.json"]


@pytest.mark.skipif(os.name != "nt", reason="Windows refuses to move a file another handle has open")
def test_delete_all_while_the_file_is_open_elsewhere_deletes_nothing(learning_on, clock):
    store = LearningStore(clock=clock)
    store.put(episode("chat-1"))
    with closing(sqlite3.connect(store.path)) as other:
        other.execute("SELECT 1").fetchone()
        with pytest.raises(LearningStoreError) as error:
            store.delete_all()
    assert error.value.code == "learning_data_busy"
    assert store.find("episode") == [episode("chat-1")]
