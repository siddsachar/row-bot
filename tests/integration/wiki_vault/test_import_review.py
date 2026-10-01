"""Guarded vault import and wiki path containment with isolated data."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import time
from contextlib import closing
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
    edited_later = time.time() + 60  # a hand edit happens after the export
    os.utime(path, (edited_later, edited_later))
    return entity, path


def _reviewed_guards(wiki, kg, entity, path):
    """The two versions a person reviewed, as the wiki import command passes them."""
    return {
        "expected_db_revision": wiki._source_revision(kg.get_entity(entity["id"])),
        "expected_vault_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


@pytest.mark.parametrize(
    "relative", ["../outside.md", "person/../../outside.md", "/abs.md", "a\\b.md", "c:x.md", "", "."]
)
def test_wiki_file_paths_never_leave_the_managed_folder(wiki_stack, relative):
    with pytest.raises(ValueError):
        wiki_stack["wiki_vault"]._wiki_path(relative)


def test_wiki_file_paths_resolve_inside_the_managed_folder(wiki_stack):
    wiki = wiki_stack["wiki_vault"]
    root = wiki.get_vault_path() / "wiki"

    assert wiki._wiki_path("person/Alice.md") == root / "person" / "Alice.md"
    assert wiki._wiki_path("person/./Alice.md") == root / "person" / "Alice.md"


def _link_directory(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            pytest.skip("symlinks are not available")
        import _winapi

        _winapi.CreateJunction(str(target), str(link))


def test_wiki_file_paths_never_follow_a_linked_folder(wiki_stack):
    wiki = wiki_stack["wiki_vault"]
    root = wiki.get_vault_path() / "wiki"
    root.mkdir(parents=True, exist_ok=True)
    outside = wiki_stack["vault"].parent / "outside"
    outside.mkdir()
    _link_directory(root / "person", outside)

    with pytest.raises(ValueError, match="Linked"):
        wiki._wiki_path("person/Alice.md")


def test_guarded_import_refuses_a_path_outside_the_managed_folder(wiki_stack):
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    entity, path = _edited_article(wiki_stack, legacy=True)
    guards = _reviewed_guards(wiki, kg, entity, path)
    escaped = path.parent / ".." / path.parent.name / path.name

    assert wiki.import_from_vault(entity["id"], escaped, **guards) is False
    assert kg.get_entity(entity["id"]) == entity
    assert wiki.import_from_vault(entity["id"], path, **guards) is True


@pytest.mark.parametrize(
    "change", [None, "recall", "vault", "database", "same-timestamp-property"]
)
def test_captured_guards_accept_only_the_reviewed_versions(wiki_stack, change):
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    entity, path = _edited_article(wiki_stack, legacy=True)
    review = _reviewed_guards(wiki, kg, entity, path)
    if change == "vault":
        path.write_bytes(path.read_bytes() + b"Later vault version")
    elif change == "database":
        kg.update_entity(entity["id"], "Later database content")
    elif change == "same-timestamp-property":
        with closing(sqlite3.connect(kg.DB_PATH)) as conn:
            conn.execute("UPDATE entities SET properties=? WHERE id=?", ('{"status": "archived"}', entity["id"]))
            conn.commit()
        assert kg.get_entity(entity["id"])["updated_at"] == entity["updated_at"]
    elif change == "recall":  # a recall is not a knowledge change
        kg.touch_recalled([entity["id"]])
    before = kg.get_entity(entity["id"])
    ok = wiki.import_from_vault(
        entity["id"],
        path,
        expected_db_revision=review["expected_db_revision"],
        expected_vault_hash=review["expected_vault_hash"],
    )
    accepted = change in {None, "recall"}
    assert ok is accepted
    if accepted:
        assert (
            "Vault edit for explicit review"
            in kg.get_entity(entity["id"])["description"]
        )
        assert wiki.clear_wiki_folder() == 0
        assert path.exists()
    else:
        assert kg.get_entity(entity["id"]) == before
