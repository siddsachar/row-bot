"""Truthful wiki projection completion with real isolated SQLite and files."""
import json
import sqlite3

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.subsystem]


def article(stack, *, subject="Projection source"):
    wiki, kg = stack["wiki_vault"], stack["kg"]
    wiki.set_enabled(True)
    entity = kg.save_entity("person", subject, "A synthetic description with enough article content.")
    result = wiki.export_entity_projection(entity)
    assert result.complete and result.disposition == "published" and result.path.is_file()
    return entity, result.path


def test_article_result_is_bound_to_exact_source_and_legacy_path_api(wiki_stack):
    entity, path = article(wiki_stack)
    wiki = wiki_stack["wiki_vault"]
    result = wiki.export_entity_projection(entity)
    assert result.source_revision == wiki._source_revision(entity)
    assert result.path == path and wiki.export_entity(entity) == path


def test_sparse_exclusion_retires_owned_article_and_publishes_rollup(wiki_stack):
    entity, path = article(wiki_stack)
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    original = path.read_bytes()
    sparse = kg.update_entity(entity["id"], "Short fact")
    result = wiki.export_entity_projection(sparse)
    assert result.complete and result.disposition == "excluded" and result.path is None
    assert not path.exists()
    assert sparse["subject"] in (path.parent / "_index.md").read_text()
    assert original in [file.read_bytes() for file in (wiki.get_vault_path() / "wiki/.row-bot-recovery").glob("*.retained")]


def test_type_change_and_sparse_exclusion_retire_old_type_only_for_same_id(wiki_stack):
    entity, old = article(wiki_stack)
    _other, other_path = article(wiki_stack, subject="Other source")
    untouched = other_path.read_bytes()
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    sparse = kg.update_entity(entity["id"], "Short", entity_type="fact")
    result = wiki.export_entity_projection(sparse)
    assert result.complete and result.disposition == "excluded"
    assert not old.exists() and not wiki._entity_md_path(sparse).exists()
    assert other_path.read_bytes() == untouched
    assert not any(item.get("entity_id") == entity["id"] for item in wiki._read_manifest()["files"].values())


def test_rollup_conflict_does_not_acknowledge_or_retire_old_article(wiki_stack):
    entity, path = article(wiki_stack)
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    original = path.read_bytes()
    index = path.parent / "_index.md"
    index.write_bytes(b"External unowned rollup bytes")
    sparse = kg.update_entity(entity["id"], "Short")
    result = wiki.export_entity_projection(sparse)
    assert not result.complete and result.disposition == "conflict"
    assert path.read_bytes() == original
    assert index.read_bytes() == b"External unowned rollup bytes"


def test_edited_owned_article_is_preserved_and_conflict_stays_incomplete(wiki_stack):
    entity, path = article(wiki_stack)
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    edited = path.read_bytes() + b"\nExternal edited source\n"
    path.write_bytes(edited)
    sparse = kg.update_entity(entity["id"], "Short", entity_type="fact")
    result = wiki.export_entity_projection(sparse)
    assert not result.complete and result.disposition == "conflict"
    assert path.read_bytes() == edited
    assert any(item.get("entity_id") == entity["id"] for item in wiki._read_manifest()["files"].values())


def test_explicit_unmanaged_article_remains_preserved_during_exclusion(wiki_stack):
    entity, path = article(wiki_stack)
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    original = path.read_bytes()
    manifest = wiki._read_manifest()
    relative = path.relative_to(wiki.get_vault_path() / "wiki").as_posix()
    manifest["files"][relative]["managed"] = False
    wiki._write_manifest(manifest)
    sparse = kg.update_entity(entity["id"], "Short")
    result = wiki.export_entity_projection(sparse)
    assert result.complete and result.disposition == "excluded"
    assert path.read_bytes() == original


def test_full_row_stale_guard_preserves_current_article_without_timestamp_change(wiki_stack):
    entity, path = article(wiki_stack)
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    original = path.read_bytes()
    with sqlite3.connect(kg.DB_PATH) as conn:
        conn.execute("UPDATE entities SET properties=? WHERE id=?", (json.dumps({"new": "value"}), entity["id"]))
    result = wiki.export_entity_projection(entity)
    assert not result.complete and result.disposition == "stale"
    assert path.read_bytes() == original


def test_source_writer_cannot_replace_row_during_publication(wiki_stack, monkeypatch):
    entity, path = article(wiki_stack)
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    original = wiki._stage
    denied = []

    def stage(*args, **kwargs):
        with sqlite3.connect(kg.DB_PATH, timeout=0) as conn:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                conn.execute("UPDATE entities SET description=? WHERE id=?", ("Concurrent changed source", entity["id"]))
            denied.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(wiki, "_stage", stage)
    assert wiki.export_entity_projection(entity).complete
    assert denied and kg.get_entity(entity["id"]) == entity and path.is_file()


def test_retirement_publication_failure_raises_and_retry_recovers(wiki_stack, monkeypatch):
    entity, path = article(wiki_stack)
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    sparse = kg.update_entity(entity["id"], "Short")
    original = wiki._retire

    def fail(*args):
        raise OSError("synthetic retirement failure")

    monkeypatch.setattr(wiki, "_retire", fail)
    with pytest.raises(OSError, match="retirement failure"):
        wiki.export_entity_projection(sparse)
    assert path.is_file()
    monkeypatch.setattr(wiki, "_retire", original)
    assert wiki.export_entity_projection(sparse).complete
    assert not path.exists()


def test_disabled_and_missing_source_are_not_acknowledged(wiki_stack):
    entity, path = article(wiki_stack)
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    wiki.set_enabled(False)
    assert wiki.export_entity_projection(entity).disposition == "disabled"
    wiki.set_enabled(True)
    with sqlite3.connect(kg.DB_PATH) as conn:
        conn.execute("DELETE FROM entities WHERE id=?", (entity["id"],))
    outcome = wiki.export_entity_projection(entity)
    assert not outcome.complete and outcome.disposition == "stale"
    assert path.is_file()
