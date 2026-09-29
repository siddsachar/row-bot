"""Captured-version review and guarded vault import with isolated data."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.subsystem]


def _edited_article(stack, *, legacy=False):
    wiki, kg = stack["wiki_vault"], stack["kg"]
    wiki.set_enabled(True)
    entity = kg.save_entity(
        "person",
        "Synthetic review",
        "Database content long enough for an article.",
        source="test",
    )
    if legacy:
        path = stack["vault"] / "wiki" / "person" / "Synthetic review.md"
        path.write_text(wiki.render_entity_md(entity), encoding="utf-8")
    else:
        path = wiki.export_entity(entity)
    path.write_bytes(path.read_bytes() + b"\r\nVault edit for explicit review.\r\n")
    return entity, path


@pytest.mark.parametrize("legacy", [False, True])
def test_review_captures_complete_versions_without_effects(
    wiki_stack, monkeypatch, legacy
):
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    entity, path = _edited_article(wiki_stack, legacy=legacy)
    path.write_bytes(path.read_bytes() + ("Complete content " * 10_000).encode())
    before = {p: p.read_bytes() for p in wiki_stack["vault"].rglob("*") if p.is_file()}

    def unexpected(*args, **kwargs):
        pytest.fail("Review must not write or import")

    monkeypatch.setattr(wiki, "_stage", unexpected)
    monkeypatch.setattr(wiki, "_write_manifest", unexpected)
    monkeypatch.setattr(kg, "update_entity", unexpected)
    monkeypatch.setattr(wiki_stack["memory_evolution"], "append_journal", unexpected)
    review = wiki.read_import_review(entity["id"], path)
    assert review["vault_text"] == before[path].decode("utf-8")
    assert review["expected_vault_hash"] == hashlib.sha256(before[path]).hexdigest()
    assert json.loads(review["database_text"]) == entity
    assert review["expected_db_revision"] == wiki._source_revision(entity)
    assert {
        p: p.read_bytes() for p in wiki_stack["vault"].rglob("*") if p.is_file()
    } == before
    assert kg.get_entity(entity["id"]) == entity


def test_review_parses_and_hashes_one_capture_despite_editor_aba(
    wiki_stack, monkeypatch
):
    wiki = wiki_stack["wiki_vault"]
    entity, path = _edited_article(wiki_stack)
    captured = path.read_bytes()
    original_parse = wiki._parse_entity_text

    def parse_while_editor_changes(text):
        path.write_bytes(captured.replace(b"Vault edit", b"Unreviewed edit"))
        parsed = original_parse(text)
        path.write_bytes(captured)
        return parsed

    monkeypatch.setattr(wiki, "_parse_entity_text", parse_while_editor_changes)
    monkeypatch.setattr(
        wiki, "parse_entity_md", lambda *_: pytest.fail("Do not reopen to parse")
    )
    review = wiki.read_import_review(entity["id"], path)
    assert review["vault_text"] == captured.decode()
    assert "Unreviewed edit" not in review["vault_text"]
    assert review["expected_vault_hash"] == hashlib.sha256(captured).hexdigest()


@pytest.mark.parametrize(
    "invalid", ["outside", "traversal", "id", "missing", "utf8", "ownership"]
)
def test_review_rejects_invalid_identity_or_path(wiki_stack, invalid):
    wiki = wiki_stack["wiki_vault"]
    entity, path = _edited_article(wiki_stack)
    if invalid == "outside":
        outside = wiki_stack["vault"].parent / "outside.md"
        outside.write_bytes(path.read_bytes())
        path = outside
    elif invalid == "traversal":
        path = path.parent / ".." / "person" / path.name
    elif invalid == "id":
        path.write_text(
            wiki.render_entity_md(dict(entity, id="different")), encoding="utf-8"
        )
    elif invalid == "missing":
        wiki_stack["kg"].delete_entity(entity["id"])
    elif invalid == "utf8":
        path.write_bytes(b"\xff\xfe")
    else:
        manifest = wiki._read_manifest()
        relative = path.relative_to(wiki_stack["vault"] / "wiki").as_posix()
        manifest["files"][relative]["entity_id"] = "different"
        wiki._write_manifest(manifest)
    with pytest.raises((OSError, ValueError)):
        wiki.read_import_review(entity["id"], path)


def test_review_rejects_linked_article_before_read(wiki_stack, monkeypatch):
    wiki = wiki_stack["wiki_vault"]
    entity, path = _edited_article(wiki_stack)
    original_is_symlink = Path.is_symlink
    original_read = Path.read_bytes
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda current: current == path or original_is_symlink(current),
    )

    def read(current):
        assert current != path, "Linked article must not be opened"
        return original_read(current)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(ValueError, match="Linked"):
        wiki.read_import_review(entity["id"], path)


@pytest.mark.parametrize(
    "change", [None, "vault", "database", "same-timestamp-property"]
)
def test_captured_guards_accept_only_the_reviewed_versions(wiki_stack, change):
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    entity, path = _edited_article(wiki_stack, legacy=True)
    review = wiki.read_import_review(entity["id"], path)
    if change == "vault":
        path.write_bytes(path.read_bytes() + b"Later vault version")
    elif change == "database":
        kg.update_entity(entity["id"], "Later database content")
    elif change == "same-timestamp-property":
        kg.touch_recalled([entity["id"]])
        assert kg.get_entity(entity["id"])["updated_at"] == entity["updated_at"]
    before = kg.get_entity(entity["id"])
    ok = wiki.import_from_vault(
        entity["id"],
        path,
        expected_db_revision=review["expected_db_revision"],
        expected_vault_hash=review["expected_vault_hash"],
    )
    assert ok is (change is None)
    if change is None:
        assert (
            "Vault edit for explicit review"
            in kg.get_entity(entity["id"])["description"]
        )
        assert wiki.clear_wiki_folder() == 0
        assert path.exists()
    else:
        assert kg.get_entity(entity["id"]) == before
