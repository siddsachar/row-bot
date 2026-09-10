from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from row_bot.designer import client_service as service
from row_bot.designer import storage
from row_bot.designer.state import DesignerPage, DesignerProject

pytestmark = pytest.mark.subsystem


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    root = tmp_path / "designer"
    for key, path in (("DESIGNER_DIR", root), ("PROJECTS_DIR", root / "projects"),
                      ("ASSETS_DIR", root / "assets"), ("REFERENCES_DIR", root / "references")):
        monkeypatch.setattr(storage, key, path)
    return root


def test_blank_deck_is_provider_free_and_reuses_exact_id(isolated, monkeypatch):
    import row_bot.designer.session as session
    monkeypatch.setattr(session, "set_active_project", lambda *_: pytest.fail("visible session changed"))
    with ThreadPoolExecutor(max_workers=4) as executor:
        created = list(executor.map(lambda _: service.create_deck("deck-a", service.DeckSetup()), range(8)))
    assert {p.id for p in created} == {"deck-a"}
    assert len(list(storage.PROJECTS_DIR.glob("*.json"))) == 1
    assert len({p.updated_at for p in created}) == 1
    project = created[0]
    assert project.name == "Untitled Design"
    assert project.mode == "deck"
    assert project.aspect_ratio == "16:9"
    assert project.brief is None
    assert project.thread_id is None
    assert project.thread_ownership == "resume"
    assert project.brand is not None


@pytest.mark.parametrize("setup,code", [
    (service.DeckSetup(template_id="missing"), "invalid_template"),
    (service.DeckSetup(template_id="blank_document"), "invalid_template"),
    (service.DeckSetup(aspect_ratio="A4"), "invalid_canvas"),
    (service.DeckSetup(name="x" * 201), "invalid_setup"),
])
def test_bad_setup_has_no_write(isolated, setup, code):
    with pytest.raises(service.ArtifactError, match=code):
        service.create_deck("deck-a", setup)
    assert not isolated.exists()


def test_all_offered_templates_and_canvas_options_are_real(isolated):
    options = service.deck_setup_options()
    for i, choice in enumerate(options.templates):
        project = service.create_deck(f"template-{i}", service.DeckSetup(template_id=choice.id))
        assert project.template_id == choice.id
        assert project.mode == "deck"
        assert project.pages
    for i, choice in enumerate(options.canvases):
        project = service.create_deck(f"canvas-{i}", service.DeckSetup(aspect_ratio=choice.id))
        assert project.aspect_ratio == choice.id


def test_read_does_not_create_or_normalize(isolated, monkeypatch):
    with pytest.raises(service.ArtifactError, match="not_found"):
        service.read_artifact("absent")
    assert not isolated.exists()
    project = service.create_deck("deck-a", service.DeckSetup())
    import row_bot.designer.render_assets as assets
    monkeypatch.setattr(assets, "normalize_project_inline_assets", lambda *_: pytest.fail("read mutation"))
    before = (storage.PROJECTS_DIR / "deck-a.json").read_bytes()
    assert service.read_artifact(project.id).id == project.id
    assert (storage.PROJECTS_DIR / "deck-a.json").read_bytes() == before


@pytest.mark.parametrize("resource_id", ["../outside", "a/b", "a\\b", "C:thing", "..", ""])
def test_invalid_identity_rejected(isolated, resource_id):
    with pytest.raises(service.ArtifactError, match="invalid_resource"):
        service.read_artifact(resource_id)
    assert not isolated.exists()


def test_origin_cas_repair_and_missing_origin_preserve_resource(isolated):
    project = service.create_deck("deck-a", service.DeckSetup())
    associated = service.associate_origin(project.id, "conversation-a",
        expected_revision=project.updated_at, expected_origin=None)
    assert associated.thread_id == "conversation-a"
    replay = service.associate_origin(project.id, "conversation-a",
        expected_revision=project.updated_at, expected_origin=None)
    assert replay.updated_at == associated.updated_at
    with pytest.raises(service.ArtifactError, match="origin_repair_required"):
        service.associate_origin(project.id, "conversation-b",
            expected_revision=associated.updated_at, expected_origin="conversation-a")
    with pytest.raises(service.ArtifactError, match="resource_revision_conflict"):
        service.associate_origin(project.id, "conversation-b", expected_revision=project.updated_at,
                                 expected_origin="conversation-a", repair=True)
    assert storage.detach_thread(project.id, "conversation-a")
    detached = service.read_artifact(project.id)
    assert detached.thread_id is None
    assert detached.missing_origin_thread_id == "conversation-a"
    with pytest.raises(service.ArtifactError, match="origin_repair_required"):
        service.associate_origin(project.id, "conversation-b", expected_revision=detached.updated_at,
                                 expected_origin="conversation-a")
    repaired = service.associate_origin(project.id, "conversation-b", expected_revision=detached.updated_at,
                                       expected_origin="conversation-a", repair=True)
    assert repaired.thread_id == "conversation-b"
    assert repaired.missing_origin_thread_id is None


def test_legacy_delete_excludes_additive_conversations(isolated, tmp_path, monkeypatch):
    import row_bot.threads as threads
    import row_bot.thread_cleanup as cleanup
    import row_bot.designer.publish as publish
    import row_bot.designer.history as history
    from types import SimpleNamespace

    db = tmp_path / "threads.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE thread_meta (thread_id TEXT, resource_bindings_json TEXT)")
        conn.executemany("INSERT INTO thread_meta VALUES (?, ?)", [
            ("legacy", ""), ("ordinary", '[{"kind":"artifact"}]'), ("unbound", "[]"),
        ])
    monkeypatch.setattr(threads, "DB_PATH", db)
    monkeypatch.setattr(threads, "_list_project_thread_ids", lambda *_: ["legacy", "ordinary", "unbound"])
    monkeypatch.setattr(publish, "delete_published_project", lambda *_: False)
    monkeypatch.setattr(history, "delete_history", lambda *_: None)
    deleted = []
    monkeypatch.setattr(cleanup, "delete_threads", lambda ids: (deleted.extend(ids), SimpleNamespace(failures=[]))[1])
    project = DesignerProject(id="legacy-design", thread_id="ordinary")
    storage.save_project(project)
    assert storage.delete_project(project.id)
    assert deleted == ["legacy"]


def test_preview_is_revisioned_and_selects_exact_page(isolated, monkeypatch):
    import row_bot.designer.preview as preview
    project = service.create_deck("deck-a", service.DeckSetup())
    project.pages.append(DesignerPage(title="Older", route_id="page-older", html="<h1>Older</h1>"))
    storage.save_project(project)
    builds = []
    original = preview.render_page_html
    monkeypatch.setattr(preview, "render_page_html", lambda *a, **kw: (builds.append(1), original(*a, **kw))[1])
    first = service.read_preview(project.id, page_id="page-older")
    assert first.page_id == "page-older"
    assert first.page_index == 1
    assert "Older" in first.html
    for _ in range(120):
        result = service.read_preview(project.id, page_id="page-older", known_revision=first.preview_revision)
        assert result.unchanged and result.html is None
    assert len(builds) == 1
    project.pages[1].html = "<h1>Changed</h1>"
    storage.save_project(project)
    changed = service.read_preview(project.id, page_id="page-older", known_revision=first.preview_revision)
    assert not changed.unchanged and "Changed" in changed.html
    assert len(builds) == 2
    with pytest.raises(service.ArtifactError, match="page_unavailable"):
        service.read_preview(project.id, page_id="deleted-page")


def test_read_rejects_corrupt_identity(isolated):
    storage.PROJECTS_DIR.mkdir(parents=True)
    (storage.PROJECTS_DIR / "bad.json").write_text(json.dumps({"id": "other"}))
    with pytest.raises(service.ArtifactError, match="resource_state_invalid"):
        service.read_artifact("bad")


def test_complete_library_continues_and_keeps_other_modes_visible(isolated):
    assert service.list_artifacts().items == ()
    assert not isolated.exists()
    for index in range(107):
        storage.save_project(DesignerProject(id=f"project-{index:03d}", mode="document" if index == 100 else "deck"))
    seen = []
    cursor = None
    while True:
        result = service.list_artifacts(cursor, limit=17)
        seen.extend(result.items)
        assert len(result.items) <= 17
        if not result.has_more:
            break
        cursor = result.next_cursor
    assert len({item.id for item in seen}) == 107
    assert not next(item for item in seen if item.id == "project-100").available


def test_late_render_cannot_publish_after_newer_save(isolated, monkeypatch):
    import row_bot.designer.preview as preview
    project = service.create_deck("deck-a", service.DeckSetup())
    original = preview.render_page_html
    def edit_during_render(*args, **kwargs):
        rendered = original(*args, **kwargs)
        project.pages[0].html = "<p>Newer revision</p>"
        storage.save_project(project)
        return rendered
    monkeypatch.setattr(preview, "render_page_html", edit_during_render)
    with pytest.raises(service.ArtifactError, match="resource_revision_conflict"):
        service.read_preview(project.id)
    assert "Newer revision" in service.read_artifact(project.id).pages[0].html


def test_asset_reads_cannot_escape_project_scope(isolated, tmp_path):
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"synthetic private fixture")
    with pytest.raises(ValueError):
        storage.load_asset_bytes("deck", "../../../outside.bin")
    with pytest.raises(ValueError):
        storage.load_reference_bytes("deck", "../../../outside.bin")
