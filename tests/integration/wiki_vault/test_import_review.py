"""Captured-version review and explicit local UI acceptance with isolated data."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

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


class _Element:
    def __init__(self, kind, *args, **kwargs):
        self.kind = kind
        self.text = args[0] if args else ""
        self.value = kwargs.get("value", "")
        self.callback = kwargs.get("on_click")
        self.enabled = True
        self.opened = False
        self.properties = []
        self.events = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def classes(self, *args):
        return self

    def style(self, *args):
        return self

    def props(self, value):
        self.properties.append(value)
        return self

    def on(self, event, callback):
        self.events[event] = callback
        return self

    def enable(self):
        self.enabled = True

    def disable(self):
        self.enabled = False

    def set_text(self, text):
        self.text = text

    def open(self):
        self.opened = True

    def close(self):
        self.opened = False
        if "hide" in self.events:
            self.events["hide"]()


@pytest.fixture
def review_ui(monkeypatch):
    from row_bot.ui import wiki_review

    elements, notices, pending = [], [], []

    def make(kind):
        def create(*args, **kwargs):
            element = _Element(kind, *args, **kwargs)
            elements.append(element)
            return element

        return create

    fake = SimpleNamespace(
        **{
            kind: make(kind)
            for kind in ("dialog", "card", "row", "label", "textarea", "button")
        }
    )
    fake.notify = lambda text, **kwargs: notices.append((text, kwargs))

    async def io_bound(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(wiki_review, "ui", fake)
    monkeypatch.setattr(wiki_review, "run", SimpleNamespace(io_bound=io_bound))
    monkeypatch.setattr(
        wiki_review, "safe_ui_task", lambda context, callback: pending.append(callback)
    )

    async def open_async(*args, **kwargs):
        wiki_review.open_wiki_import_review(*args, **kwargs)
        while pending:
            await pending.pop(0)()

    return SimpleNamespace(
        module=wiki_review,
        open=lambda *args, **kwargs: asyncio.run(open_async(*args, **kwargs)),
        open_async=open_async,
        pending=pending,
        elements=elements,
        notices=notices,
        button=lambda text: next(
            e for e in elements if e.kind == "button" and e.text == text
        ),
        dialog=lambda: next(e for e in elements if e.kind == "dialog"),
    )


def test_dialog_shows_plain_versions_and_cancel_has_no_import(
    wiki_stack, review_ui, monkeypatch
):
    wiki = wiki_stack["wiki_vault"]
    entity, path = _edited_article(wiki_stack)
    attack = "</textarea><script>globalThis.syntheticAttack = true</script>"
    path.write_bytes(path.read_bytes() + attack.encode())
    monkeypatch.setattr(
        wiki, "import_from_vault", lambda *a, **k: pytest.fail("Cancel must not import")
    )
    review_ui.open({"entity_id": entity["id"], "vault_path": str(path)})
    areas = [e for e in review_ui.elements if e.kind == "textarea"]
    assert len(areas) == 2
    assert json.loads(areas[0].value) == entity
    assert attack in areas[1].value
    assert all("readonly" in " ".join(e.properties) for e in areas)
    assert review_ui.dialog().opened
    review_ui.button("Cancel").callback()
    asyncio.run(review_ui.button("Accept vault version").callback())
    assert not review_ui.dialog().opened


def test_dialog_accepts_captured_guards_once_despite_duplicate_click(
    wiki_stack, review_ui, monkeypatch
):
    wiki = wiki_stack["wiki_vault"]
    entity, path = _edited_article(wiki_stack, legacy=True)
    expected = wiki.read_import_review(entity["id"], path)
    imported, saved = [], []
    original_import = wiki.import_from_vault

    async def scenario():
        entered, proceed = asyncio.Event(), asyncio.Event()

        async def io_bound(function, *args, **kwargs):
            if function is not wiki.import_from_vault:
                return function(*args, **kwargs)
            imported.append((args, kwargs))
            entered.set()
            await proceed.wait()
            return function(*args, **kwargs)

        monkeypatch.setattr(review_ui.module.run, "io_bound", io_bound)
        await review_ui.open_async(
            {"entity_id": entity["id"], "vault_path": str(path)},
            lambda: saved.append(True),
        )
        # Browser form values do not replace the server-owned review token/content.
        for element in review_ui.elements:
            if element.kind == "textarea":
                element.value = "Untrusted edited control value"
        accept = review_ui.button("Accept vault version")
        first = asyncio.create_task(accept.callback())
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert not accept.enabled
        assert not review_ui.button("Cancel").enabled
        await accept.callback()
        review_ui.button("Cancel").callback()
        assert review_ui.dialog().opened
        proceed.set()
        await asyncio.wait_for(first, timeout=2)
        await accept.callback()

    asyncio.run(scenario())
    assert wiki.import_from_vault is original_import
    assert imported == [
        (
            (entity["id"], str(path)),
            {
                "expected_db_revision": expected["expected_db_revision"],
                "expected_vault_hash": expected["expected_vault_hash"],
            },
        )
    ]
    assert saved == [True]
    assert not review_ui.dialog().opened


@pytest.mark.parametrize("failure", ["vault", "database", "exception"])
def test_dialog_failure_requires_reload_and_new_explicit_accept(
    wiki_stack, review_ui, monkeypatch, failure
):
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    entity, path = _edited_article(wiki_stack)
    saved = []
    review_ui.open(
        {"entity_id": entity["id"], "vault_path": str(path)}, lambda: saved.append(True)
    )
    original_import = wiki.import_from_vault
    if failure == "vault":
        path.write_bytes(path.read_bytes() + b"\nLater vault edit\n")
    elif failure == "database":
        kg.update_entity(entity["id"], "Later database edit")
    else:

        def fail(*args, **kwargs):
            raise OSError("Synthetic private path detail")

        monkeypatch.setattr(wiki, "import_from_vault", fail)
    accept = review_ui.button("Accept vault version")
    asyncio.run(accept.callback())
    assert review_ui.dialog().opened
    assert not accept.enabled
    assert saved == []
    assert any(
        "Reload both versions" in e.text
        for e in review_ui.elements
        if e.kind == "label"
    )
    assert all(
        "Synthetic private path detail" not in e.text
        for e in review_ui.elements
        if e.kind == "label"
    )
    monkeypatch.setattr(wiki, "import_from_vault", original_import)
    asyncio.run(accept.callback())
    assert saved == []
    asyncio.run(review_ui.button("Reload review").callback())
    assert accept.enabled
    assert saved == []
    asyncio.run(accept.callback())
    assert saved == [True]


def test_dialog_load_failure_is_recoverable_without_effect(wiki_stack, review_ui):
    entity, path = _edited_article(wiki_stack)
    original = path.read_bytes()
    path.write_bytes(b"invalid")
    review_ui.open({"entity_id": entity["id"], "vault_path": str(path)})
    accept = review_ui.button("Accept vault version")
    assert not accept.enabled
    assert review_ui.dialog().opened
    assert all(not e.value for e in review_ui.elements if e.kind == "textarea")
    path.write_bytes(original)
    asyncio.run(review_ui.button("Reload review").callback())
    assert accept.enabled


@pytest.mark.parametrize("close", ["before-load", "during-load"])
def test_dialog_loading_is_fenced_after_close(
    wiki_stack, review_ui, monkeypatch, close
):
    wiki = wiki_stack["wiki_vault"]
    entity, path = _edited_article(wiki_stack)
    reads, saved = [], []

    async def scenario():
        entered, proceed = asyncio.Event(), asyncio.Event()

        async def delayed_read(function, *args, **kwargs):
            assert function is wiki.read_import_review
            reads.append(True)
            captured = function(*args, **kwargs)
            entered.set()
            await proceed.wait()
            return captured

        monkeypatch.setattr(review_ui.module.run, "io_bound", delayed_read)
        review_ui.module.open_wiki_import_review(
            {"entity_id": entity["id"], "vault_path": str(path)},
            lambda: saved.append(True),
        )
        accept = review_ui.button("Accept vault version")
        assert not accept.enabled
        if close == "before-load":
            review_ui.button("Cancel").callback()
            await review_ui.pending.pop(0)()
            assert reads == []
        else:
            task = asyncio.create_task(review_ui.pending.pop(0)())
            await asyncio.wait_for(entered.wait(), timeout=2)
            assert not accept.enabled
            assert not review_ui.button("Reload review").enabled
            assert review_ui.button("Cancel").enabled
            await review_ui.button("Reload review").callback()
            await accept.callback()
            assert reads == [True]
            review_ui.button("Cancel").callback()
            proceed.set()
            await asyncio.wait_for(task, timeout=2)
        assert not review_ui.dialog().opened
        assert not accept.enabled
        assert all(
            not element.value
            for element in review_ui.elements
            if element.kind == "textarea"
        )
        assert saved == []
        assert review_ui.notices == []

    asyncio.run(scenario())
