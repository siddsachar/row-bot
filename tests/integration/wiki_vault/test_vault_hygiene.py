"""The vault keeps recovery copies for a while, not for ever, and an article edit brings back only what a
person edits (memory audit, Nov 2026)."""
from __future__ import annotations

import json
import os
import time

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.subsystem]


def _article(stack, subject="Alice", text=None):
    stack["wiki_vault"].set_enabled(True)
    return stack["kg"].save_entity(
        "person", subject, text or f"{subject} has enough synthetic content for an article.", source="test",
    )


def _recovery(stack):
    return stack["vault"] / "wiki" / ".row-bot-recovery"


def _age(path, days):
    then = time.time() - days * 86400
    os.utime(path, (then, then))


def test_old_unedited_copies_are_pruned_and_edited_ones_kept(wiki_stack):
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    alice = _article(wiki_stack)
    path = wiki.export_entity(alice)
    kg.update_entity(alice["id"], "Alice moved to Leeds and has enough content for an article.")
    wiki.export_entity(kg.get_entity(alice["id"]))  # the old version goes into recovery, unedited
    bob = _article(wiki_stack, "Bob")
    bob_path = wiki.export_entity(bob)
    bob_path.write_text(bob_path.read_text(encoding="utf-8") + "\nThe person's own note.\n", encoding="utf-8")
    kg.update_entity(bob["id"], "Bob changed jobs and has enough content for an article.")
    wiki.export_entity(kg.get_entity(bob["id"]))  # the person's edited file is kept as a conflict copy
    for copy in _recovery(wiki_stack).iterdir():
        _age(copy, 40)

    with wiki._export_lock:
        removed = wiki.prune_recovery_copies()

    assert removed >= 1
    kept = [copy.read_text(encoding="utf-8") for copy in _recovery(wiki_stack).glob("*.retained")]
    assert not any("Alice has enough synthetic content" in text for text in kept)
    assert path.read_text(encoding="utf-8").count("Leeds") == 1  # the published article is untouched
    edited = [text for copy in _recovery(wiki_stack).glob("*") if copy.suffix in {".retained", ".candidate"}
              for text in [copy.read_text(encoding="utf-8")] if "The person's own note." in text]
    assert edited or "The person's own note." in bob_path.read_text(encoding="utf-8")


def test_recent_copies_wait_their_thirty_days(wiki_stack):
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    alice = _article(wiki_stack)
    wiki.export_entity(alice)
    kg.update_entity(alice["id"], "Alice moved to Leeds and has enough content for an article.")
    wiki.export_entity(kg.get_entity(alice["id"]))
    before = sorted(copy.name for copy in _recovery(wiki_stack).iterdir())

    with wiki._export_lock:
        assert wiki.prune_recovery_copies() == 0
    assert sorted(copy.name for copy in _recovery(wiki_stack).iterdir()) == before


def test_delete_all_leaves_no_copy_of_the_deleted_memories(wiki_stack):
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    for subject in ("Alice", "Bob"):
        wiki.export_entity(_article(wiki_stack, subject))

    assert kg.delete_all_entities() == 2

    left = [copy.read_text(encoding="utf-8", errors="replace") for copy in _recovery(wiki_stack).glob("*")
            if copy.suffix in {".retained", ".candidate"}]
    assert not any("synthetic content" in text for text in left)


def test_an_article_without_its_heading_is_not_imported_as_an_empty_memory(wiki_stack):
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    alice = _article(wiki_stack)
    path = wiki.export_entity(alice)
    text = path.read_text(encoding="utf-8")
    frontmatter, _, _body = text.partition("\n---\n")
    path.write_text(frontmatter + "\n---\nAlice works at Acme now.\n", encoding="utf-8")

    assert wiki.import_from_vault(alice["id"], path) is False
    assert kg.get_entity(alice["id"])["description"] == alice["description"]


def test_an_article_edit_without_a_blank_line_keeps_the_text_and_not_the_links(wiki_stack):
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    alice = _article(wiki_stack)
    bob = _article(wiki_stack, "Bob")
    kg.add_relation(alice["id"], bob["id"], "friend_of")
    path = wiki.export_entity(kg.get_entity(alice["id"]))
    text = path.read_text(encoding="utf-8")
    head, _, rest = text.partition("# Alice")
    _old, _, connections = rest.partition("## Connections")
    path.write_text(head + "# Alice\nAlice works at Acme now.\n\n## Connections" + connections, encoding="utf-8")

    assert wiki.import_from_vault(alice["id"], path) is True
    assert kg.get_entity(alice["id"])["description"] == "Alice works at Acme now."


def test_an_imported_edit_keeps_machine_properties_and_saves_no_local_path(wiki_stack):
    wiki, kg, evo = wiki_stack["wiki_vault"], wiki_stack["kg"], wiki_stack["memory_evolution"]
    old = _article(wiki_stack, "Old city", "The user lived in York, with enough content for an article.")
    new = _article(wiki_stack, "City", "The user lives in Leeds, with enough content for an article.")
    evo.mark_superseded(old["id"], new["id"], reason="moved")
    path = wiki.export_entity(kg.get_entity(old["id"]))
    path.write_text(path.read_text(encoding="utf-8").replace("lived in York", "lived in York city"), encoding="utf-8")

    assert wiki.import_from_vault(old["id"], path) is True

    props = json.loads(kg.get_entity(old["id"])["properties"])
    assert props["status"] == "superseded" and props["superseded_by"] == new["id"]
    assert str(wiki_stack["vault"]) not in json.dumps(props)
    assert json.loads(kg.get_entity(new["id"])["properties"])["supersedes"] == [old["id"]]
