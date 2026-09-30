"""Synthetic ownership, interruption and revision races for the canonical wiki owner."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.subsystem]


def article(stack, subject="Alice"):
    stack["wiki_vault"].set_enabled(True)
    return stack["kg"].save_entity(
        "person", subject, f"{subject} has enough synthetic content for an article.", source="test",
    )


def test_readable_names_follow_renames_and_never_collide(wiki_stack):
    wiki = wiki_stack["wiki_vault"]
    first = article(wiki_stack, "A/B")
    second = article(wiki_stack, "A:B")
    path = wiki.export_entity(first)
    other = wiki.export_entity(second)
    assert path.name == "A_B.md"
    assert other.name.startswith("A_B (") and other.name != path.name
    renamed = wiki_stack["kg"].update_entity(first["id"], first["description"], subject="Renamed")
    moved = wiki.export_entity(renamed)
    assert moved == path.with_name("Renamed.md")
    assert "# Renamed" in moved.read_text(encoding="utf-8")
    assert not path.exists()  # the old name is retired, kept in recovery
    recovery = wiki_stack["vault"] / "wiki" / ".row-bot-recovery"
    assert any("# A/B" in kept.read_text(encoding="utf-8") for kept in recovery.glob("*.retained"))
    assert wiki.read_article(first["id"]) == wiki.read_article("Renamed")
    assert "A:B" in other.read_text(encoding="utf-8")


def test_unowned_files_survive_export_rebuild_and_clear_and_a_clash_adds_a_short_id(wiki_stack):
    wiki = wiki_stack["wiki_vault"]
    entity = article(wiki_stack)
    wiki.tidy_vault()  # later files are never adopted, even generated-looking ones
    own = wiki_stack["vault"] / "wiki" / "person" / "Alice.md"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text(wiki.render_entity_md(entity), encoding="utf-8")
    unknown = own.parent / "Personal.md"
    unknown.write_text("private synthetic note", encoding="utf-8")
    original = own.read_bytes()
    path = wiki.export_entity(entity)
    assert path.parent == own.parent and path.name.startswith("Alice (")
    result = wiki.rebuild_vault()
    assert not result["conflicts"]
    assert result["orphans_removed"] == 0
    wiki.clear_wiki_folder()
    assert not path.exists()
    assert own.read_bytes() == original
    assert unknown.read_text(encoding="utf-8") == "private synthetic note"


@pytest.mark.parametrize("operation", ["export", "delete", "clear", "rebuild"])
def test_external_edits_survive_all_generated_mutations(wiki_stack, operation):
    wiki = wiki_stack["wiki_vault"]
    entity = article(wiki_stack)
    path = wiki.export_entity(entity)
    edited = path.read_text(encoding="utf-8") + "\nexternal edits\n"
    path.write_text(edited, encoding="utf-8")
    if operation == "export":
        assert wiki.export_entity(dict(entity, description="new database content long enough")) is None
    elif operation == "delete":
        wiki.delete_entity_md(entity)
    elif operation == "clear":
        assert wiki.clear_wiki_folder() == 0
    else:
        assert wiki.rebuild_vault()["conflicts"]
    assert path.read_text(encoding="utf-8") == edited


@pytest.mark.parametrize("failure", ["enumeration", "render", "cancel"])
def test_incomplete_rebuild_never_retires_owned_orphans(wiki_stack, monkeypatch, failure):
    wiki = wiki_stack["wiki_vault"]
    kg = wiki_stack["kg"]
    entity = article(wiki_stack)
    orphan = wiki.export_entity(dict(entity, id="orphan-id", subject="Orphan"))
    original = orphan.read_bytes()
    if failure == "enumeration":
        def broken():
            yield entity
            raise OSError("synthetic snapshot failure")
        monkeypatch.setattr(kg, "iter_entities_snapshot", broken)
    elif failure == "render":
        monkeypatch.setattr(wiki, "render_entity_md", lambda *_: (_ for _ in ()).throw(ValueError("synthetic render")))
    if failure == "cancel":
        assert wiki.rebuild_vault(cancelled=lambda: True)["cancelled"]
    else:
        with pytest.raises((OSError, ValueError)):
            wiki.rebuild_vault()
    assert orphan.read_bytes() == original


def test_owned_orphan_retired_with_recoverable_copy(wiki_stack):
    wiki = wiki_stack["wiki_vault"]
    entity = article(wiki_stack)
    orphan = wiki.export_entity(dict(entity, id="orphan-id"))
    original = orphan.read_bytes()
    assert wiki.rebuild_vault()["orphans_removed"] == 1
    assert not orphan.exists()
    recovery = wiki_stack["vault"] / "wiki" / ".row-bot-recovery"
    assert any(path.read_bytes() == original for path in recovery.glob("*.retained"))


def test_manifest_failure_after_publication_reconciles_without_overwriting(wiki_stack, monkeypatch):
    wiki = wiki_stack["wiki_vault"]
    entity = article(wiki_stack)
    path = wiki.export_entity(entity)
    original_write = wiki._write_manifest
    calls = 0
    def fail_final(manifest):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic final manifest failure")
        original_write(manifest)
    monkeypatch.setattr(wiki, "_write_manifest", fail_final)
    changed = dict(entity, description="Replacement has sufficient synthetic content.")
    with pytest.raises(OSError):
        wiki.export_entity(changed)
    monkeypatch.setattr(wiki, "_write_manifest", original_write)
    manifest = wiki._read_manifest()
    relative = path.relative_to(wiki_stack["vault"] / "wiki").as_posix()
    assert manifest["files"][relative]["hash"] == wiki._digest(path.read_bytes())
    assert wiki.export_entity(changed) == path


def test_interrupted_intent_does_not_adopt_identical_unowned_article(wiki_stack, monkeypatch):
    wiki = wiki_stack["wiki_vault"]
    entity = article(wiki_stack)
    wiki.tidy_vault()
    path = wiki._entity_md_path(entity)
    original_write = wiki._write_manifest
    def interrupt_after_intent(manifest):
        original_write(manifest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(wiki.render_entity_md(entity), encoding="utf-8")  # an identical copy, not the app's
        raise SystemExit("synthetic interruption before file effects")
    monkeypatch.setattr(wiki, "_write_manifest", interrupt_after_intent)
    with pytest.raises(SystemExit):
        wiki.export_entity(entity)
    monkeypatch.setattr(wiki, "_write_manifest", original_write)
    assert wiki._read_manifest()["files"] == {}
    assert wiki.clear_wiki_folder() == 0
    assert path.exists()


def test_new_external_creator_during_publication_wins_name(wiki_stack, monkeypatch):
    wiki = wiki_stack["wiki_vault"]
    entity = article(wiki_stack)
    path = wiki.export_entity(entity)
    original_link = os.link
    def racing_link(source, destination, *args, **kwargs):
        if destination == path and not path.exists():
            path.write_text("external creator", encoding="utf-8")
        return original_link(source, destination, *args, **kwargs)
    monkeypatch.setattr(wiki.os, "link", racing_link)
    assert wiki.export_entity(dict(entity, description="Replacement with enough content.")) is None
    assert path.read_text(encoding="utf-8") == "external creator"


def test_legacy_sync_retains_unowned_status_and_rejects_stale_revision(wiki_stack):
    wiki = wiki_stack["wiki_vault"]
    kg = wiki_stack["kg"]
    entity = article(wiki_stack)
    legacy = wiki_stack["vault"] / "wiki" / "person" / "Alice.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(wiki.render_entity_md(entity) + "\nlegacy edit\n", encoding="utf-8")
    edited_later = time.time() + 60  # a hand edit after the export: the tidy leaves it alone
    os.utime(legacy, (edited_later, edited_later))
    assert wiki.read_article("Alice")
    review = wiki.check_vault_sync()[0]
    assert review["status"] == "legacy_review"
    assert not wiki.import_from_vault(entity["id"], legacy)
    assert wiki.import_from_vault(entity["id"], legacy,
                                  expected_db_revision=review["db_revision"],
                                  expected_vault_hash=review["vault_hash"])
    assert "legacy edit" in kg.get_entity(entity["id"])["description"]
    assert wiki.clear_wiki_folder() == 0
    assert legacy.exists()
    legacy.write_text(legacy.read_text(encoding="utf-8") + "\nsecond legacy edit\n", encoding="utf-8")
    kg.update_entity(entity["id"], "Database edit after accepted legacy import.")
    assert not wiki.import_from_vault(entity["id"], legacy)


def test_initial_legacy_sync_cannot_infer_unchanged_properties_from_timestamp(wiki_stack):
    wiki = wiki_stack["wiki_vault"]
    kg = wiki_stack["kg"]
    entity = article(wiki_stack)
    legacy = wiki_stack["vault"] / "wiki" / "person" / "Alice.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(wiki.render_entity_md(entity) + "\nlegacy edit\n", encoding="utf-8")
    edited_later = time.time() + 60
    os.utime(legacy, (edited_later, edited_later))
    with closing(sqlite3.connect(kg.DB_PATH)) as conn:  # a property change that keeps updated_at
        conn.execute("UPDATE entities SET properties=? WHERE id=?", ('{"status": "archived"}', entity["id"]))
        conn.commit()
    current = kg.get_entity(entity["id"])
    assert current["updated_at"] == entity["updated_at"]
    assert wiki.import_from_vault(entity["id"], legacy) is False
    assert kg.get_entity(entity["id"])["properties"] == current["properties"]


def test_legacy_editor_open_checks_id_and_uses_existing_file(wiki_stack, monkeypatch):
    import platform
    wiki = wiki_stack["wiki_vault"]
    entity = article(wiki_stack)
    legacy = wiki_stack["vault"] / "wiki" / "person" / "Alice.md"
    legacy.write_text(wiki.render_entity_md(dict(entity, id="another-entity")), encoding="utf-8")
    opened = []
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(wiki.os, "startfile", opened.append, raising=False)
    assert not wiki.open_in_editor(entity)
    assert opened == []
    legacy.write_text(wiki.render_entity_md(entity), encoding="utf-8")
    assert wiki.open_in_editor(entity)
    assert opened == [str(legacy)]


def test_atomic_external_replace_during_retirement_preserves_racing_bytes(wiki_stack, monkeypatch):
    wiki = wiki_stack["wiki_vault"]
    entity = article(wiki_stack)
    path = wiki.export_entity(entity)
    original_rename = os.rename
    def racing_rename(source, destination, *args, **kwargs):
        if source == path:
            replacement = path.with_suffix(".external")
            replacement.write_text("external replacement", encoding="utf-8")
            os.replace(replacement, path)
        return original_rename(source, destination, *args, **kwargs)
    monkeypatch.setattr(wiki.os, "rename", racing_rename)
    wiki.delete_entity_md(entity)
    assert path.read_text(encoding="utf-8") == "external replacement"


@pytest.mark.parametrize("timestamp", [1, 2_000_000_000])
def test_hash_sync_detects_backdated_and_future_edits(wiki_stack, timestamp):
    wiki = wiki_stack["wiki_vault"]
    entity = article(wiki_stack)
    path = wiki.export_entity(entity)
    path.write_text(path.read_text(encoding="utf-8") + "\nexternal edit\n", encoding="utf-8")
    os.utime(path, (timestamp, timestamp))
    rows = wiki.check_vault_sync()
    assert len(rows) == 1 and rows[0]["status"] == "edited"
    assert wiki.import_from_vault(entity["id"], path)
    assert wiki.check_vault_sync() == []


def test_divergent_db_and_vault_preserve_both_and_reject_import(wiki_stack):
    wiki = wiki_stack["wiki_vault"]
    kg = wiki_stack["kg"]
    entity = article(wiki_stack)
    path = wiki.export_entity(entity)
    path.write_text(path.read_text(encoding="utf-8") + "\nvault version\n", encoding="utf-8")
    edited = path.read_bytes()
    updated = kg.update_entity(entity["id"], "Database replacement with enough content.")
    assert wiki.export_entity(updated) is None
    assert wiki.check_vault_sync()[0]["status"] == "conflict"
    assert not wiki.import_from_vault(entity["id"], path)
    assert path.read_bytes() == edited
    assert kg.get_entity(entity["id"])["description"] == updated["description"]
    manifest = wiki._read_manifest()
    candidate = next(iter(manifest["conflicts"].values()))["candidate"]
    assert updated["description"] in wiki._wiki_path(candidate).read_text(encoding="utf-8")


@pytest.mark.parametrize("changed", [None, "database", "vault", "missing_hash"])
def test_explicit_conflict_resolution_binds_both_reviewed_versions(wiki_stack, changed):
    wiki = wiki_stack["wiki_vault"]
    kg = wiki_stack["kg"]
    entity = article(wiki_stack)
    path = wiki.export_entity(entity)
    path.write_text(path.read_text(encoding="utf-8") + "\nreviewed vault version\n", encoding="utf-8")
    database = kg.update_entity(entity["id"], "Reviewed database version with enough content.")
    review = wiki.check_vault_sync()[0]
    assert review["status"] == "conflict"
    if changed == "database":
        kg.update_entity(entity["id"], "Database version changed after review.")
    elif changed == "vault":
        path.write_text(path.read_text(encoding="utf-8") + "\nchanged after review\n", encoding="utf-8")
    result = wiki.import_from_vault(
        entity["id"], path, expected_db_revision=review["db_revision"],
        expected_vault_hash=None if changed == "missing_hash" else review["vault_hash"],
    )
    assert result is (changed is None)
    if changed is None:
        assert "reviewed vault version" in kg.get_entity(entity["id"])["description"]
        recovery = wiki_stack["vault"] / "wiki" / ".row-bot-recovery"
        assert any(database["description"] in candidate.read_text(encoding="utf-8")
                   for candidate in recovery.glob("*.candidate"))
    else:
        assert "reviewed vault version" not in kg.get_entity(entity["id"])["description"]


def test_import_cas_rejects_db_edit_after_revision_read(wiki_stack, monkeypatch):
    wiki = wiki_stack["wiki_vault"]
    kg = wiki_stack["kg"]
    entity = article(wiki_stack)
    path = wiki.export_entity(entity)
    path.write_text(path.read_text(encoding="utf-8") + "\nvault edit\n", encoding="utf-8")
    entered, proceed = threading.Event(), threading.Event()
    original_update = kg.update_entity
    def delayed_update(*args, **kwargs):
        if "expected_updated_at" in kwargs:
            entered.set()
            assert proceed.wait(10)
        return original_update(*args, **kwargs)
    monkeypatch.setattr(kg, "update_entity", delayed_update)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(wiki.import_from_vault, entity["id"], path)
        assert entered.wait(10)
        original_update(entity["id"], "Concurrent database edit must survive.")
        proceed.set()
        assert future.result(timeout=10) is False
    assert kg.get_entity(entity["id"])["description"] == "Concurrent database edit must survive."
    assert "vault edit" in path.read_text(encoding="utf-8")


def test_import_parses_the_hashed_snapshot_without_file_reopen(wiki_stack, monkeypatch):
    wiki = wiki_stack["wiki_vault"]
    entity = article(wiki_stack)
    path = wiki.export_entity(entity)
    reviewed = path.read_text(encoding="utf-8") + "\nreviewed bytes\n"
    path.write_text(reviewed, encoding="utf-8")
    original_parse = wiki._parse_entity_text
    def editor_aba_during_parse(text):
        path.write_bytes(text.replace("reviewed bytes", "unreviewed bytes").encode("utf-8"))
        parsed = original_parse(text)
        path.write_bytes(text.encode("utf-8"))
        return parsed
    monkeypatch.setattr(wiki, "_parse_entity_text", editor_aba_during_parse)
    monkeypatch.setattr(wiki, "parse_entity_md", lambda *_: pytest.fail("Import must parse captured bytes"))
    assert wiki.import_from_vault(entity["id"], path)
    description = wiki_stack["kg"].get_entity(entity["id"])["description"]
    assert "reviewed bytes" in description and "unreviewed bytes" not in description


@pytest.mark.parametrize("mutation", ["recall", "same_timestamp_edit"])
def test_full_row_cas_preserves_changes_without_revision_timestamp(wiki_stack, monkeypatch, mutation):
    wiki = wiki_stack["wiki_vault"]
    kg = wiki_stack["kg"]
    entity = article(wiki_stack)
    path = wiki.export_entity(entity)
    path.write_text(path.read_text(encoding="utf-8") + "\nvault edit\n", encoding="utf-8")
    original_update = kg.update_entity
    def mutate_before_cas(*args, **kwargs):
        if mutation == "recall":
            kg.touch_recalled([entity["id"]])
        else:
            conn = kg._get_conn()
            conn.execute("UPDATE entities SET description = ? WHERE id = ?",
                         ("Concurrent same-timestamp database edit.", entity["id"]))
            conn.commit()
            conn.close()
        assert kg.get_entity(entity["id"])["updated_at"] == entity["updated_at"]
        return original_update(*args, **kwargs)
    monkeypatch.setattr(kg, "update_entity", mutate_before_cas)
    imported = wiki.import_from_vault(entity["id"], path)
    current = kg.get_entity(entity["id"])
    if mutation == "recall":
        # A recall is not a knowledge change: the reviewed import still applies.
        assert imported
        assert entity["id"] in kg.recall_stamps()
        assert "vault edit" in current["description"]
    else:
        assert not imported
        assert current["description"] == "Concurrent same-timestamp database edit."
        assert "vault edit" not in current["description"]


def test_full_row_cas_releases_writer_on_update_failure(wiki_stack, monkeypatch):
    kg = wiki_stack["kg"]
    entity = article(wiki_stack)
    original_get_conn = kg._get_conn
    class FailingConnection:
        def __init__(self):
            self.connection = original_get_conn()
            self.closed = False
        def execute(self, sql, *args):
            if sql.startswith("UPDATE entities SET"):
                raise sqlite3.OperationalError("synthetic failure after BEGIN IMMEDIATE")
            return self.connection.execute(sql, *args)
        def close(self):
            self.closed = True
            self.connection.close()
    failing = FailingConnection()
    monkeypatch.setattr(kg, "_get_conn", lambda: failing)
    with pytest.raises(sqlite3.OperationalError):
        kg.update_entity(entity["id"], "Attempted edit", expected_entity=entity)
    assert failing.closed
    # A new writer must acquire the database immediately, without garbage collection.
    connection = original_get_conn()
    try:
        connection.execute("PRAGMA busy_timeout = 0")
        connection.execute("UPDATE entities SET description = ? WHERE id = ?",
                           ("Subsequent writer succeeds.", entity["id"]))
        connection.commit()
    finally:
        connection.close()


@pytest.mark.slow
def test_snapshot_has_no_old_limit_and_is_consistent_across_batches(wiki_stack):
    kg = wiki_stack["kg"]
    entity = article(wiki_stack)
    conn = kg._get_conn()
    try:
        columns = list(entity)
        values = [entity[key] for key in columns]
        index = columns.index("id")
        rows = []
        for number in range(100_001):
            row = values.copy()
            row[index] = f"synthetic-{number:06}"
            rows.append(row)
        conn.executemany(f"INSERT INTO entities ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", rows)
        conn.commit()
    finally:
        conn.close()
    snapshot = kg.iter_entities_snapshot(batch_size=17)
    first = next(snapshot)
    kg.update_entity(entity["id"], "Concurrent update after snapshot started.")
    all_rows = [first, *snapshot]
    assert len(all_rows) == 100_002
    assert next(row for row in all_rows if row["id"] == entity["id"])["description"] == entity["description"]


@pytest.mark.parametrize("relative", ["../outside.md", "/outside.md", "person/../../outside.md", "person\\outside.md"])
def test_invalid_manifest_paths_fail_closed(wiki_stack, relative):
    wiki = wiki_stack["wiki_vault"]
    article(wiki_stack)
    path = wiki._manifest_path()
    payload = json.dumps({"version": 1, "files": {relative: {"hash": "a" * 64}}})
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        wiki.clear_wiki_folder()
    assert path.read_text(encoding="utf-8") == payload
