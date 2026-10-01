"""Page add/delete and size changes from the Design panel (Phase 12, parity rows 28/29)."""
from __future__ import annotations

from bs4 import BeautifulSoup
import pytest

from row_bot.designer import client_editing as editing, client_service as service, history, storage
from row_bot.designer.state import DesignerPage
from tests.subsystem.designer.test_client_artifact import isolated as _isolated

isolated = _isolated
pytestmark = pytest.mark.subsystem


@pytest.fixture
def deck(isolated, monkeypatch):
    monkeypatch.setattr(history, "HISTORY_DIR", isolated / "history")
    project = service.create_deck("Tides", service.DeckSetup())
    project.pages = [
        DesignerPage(title="Why tides happen", route_id="why",
                     html="<html><body><h1>Why tides happen</h1></body></html>"),
        DesignerPage(title="Spring and neap", route_id="spring",
                     html="<html><body><h1>Spring and neap</h1></body></html>"),
    ]
    project.active_page = 0
    storage.save_project(project)
    return service.read_artifact(project.id)


def _edit(project, **kwargs):
    return editing.apply_edit(project.id, expected_revision=project.updated_at, **kwargs)


def test_add_page_inserts_a_blank_page_after_the_given_one_and_selects_it(deck):
    result = _edit(deck, operation="page_add", page_id="why")
    assert [page.route_id for page in result.pages][0::2] == ["why", "spring"]
    added = result.pages[1]
    assert added.title == "New slide"
    assert result.active_page == 1
    assert added.route_id not in {"why", "spring"}
    text = BeautifulSoup(added.html, "html.parser").get_text(" ", strip=True)
    assert text == "New slide"
    assert f"width:{result.canvas_width}px" in added.html
    # The panel's Undo brings the previous version back.
    assert len(history.list_snapshots(deck.id)) == 1
    # The new page is readable and editable like any other.
    view = editing.read_editing(deck.id, page_id=added.route_id)
    assert view.page_title == "New slide" and view.page_count == 3


def test_added_pages_get_unique_ids_and_mode_words(deck):
    first = _edit(deck, operation="page_add", page_id="spring")
    second = _edit(first, operation="page_add", page_id="spring")
    ids = [page.route_id for page in second.pages]
    assert len(ids) == len(set(ids)) == 4
    mockup = service.create_artifact("Mockup", service.ArtifactSetup(mode="app_mockup"))
    screen = _edit(mockup, operation="page_add", page_id=mockup.pages[0].route_id).pages[1]
    assert screen.title == "New screen" and screen.kind == "screen"


def test_new_page_titles_are_text_not_markup(deck, monkeypatch):
    monkeypatch.setattr(editing, "_new_page_title", lambda _mode: "<script>x()</script>")
    added = _edit(deck, operation="page_add", page_id="why").pages[1]
    assert BeautifulSoup(added.html, "html.parser").find("script") is None


def test_delete_page_removes_exactly_that_page_and_keeps_history(deck):
    moved = service.read_artifact(deck.id)
    moved.active_page = 1
    storage.save_project(moved)
    current = service.read_artifact(deck.id)
    result = _edit(current, operation="page_delete", page_id="spring")
    assert [page.route_id for page in result.pages] == ["why"]
    assert result.active_page == 0
    snapshots = history.list_snapshots(deck.id)
    assert len(snapshots) == 1
    saved = history.read_snapshot(deck.id, snapshots[0]["id"])
    assert [page["route_id"] for page in saved["pages"]] == ["why", "spring"]


def test_the_last_page_cannot_be_deleted(deck):
    only = _edit(deck, operation="page_delete", page_id="spring")
    with pytest.raises(service.ArtifactError) as refused:
        _edit(only, operation="page_delete", page_id="why")
    assert refused.value.code == "invalid_edit"
    assert len(service.read_artifact(deck.id).pages) == 1


def test_page_ops_refuse_unknown_pages_and_stale_revisions(deck):
    with pytest.raises(service.ArtifactError) as missing:
        _edit(deck, operation="page_delete", page_id="nope")
    assert missing.value.code == "page_unavailable"
    with pytest.raises(service.ArtifactError) as stale:
        editing.apply_edit(deck.id, expected_revision="old", operation="page_add", page_id="why")
    assert stale.value.code == "resource_revision_conflict"
    assert len(service.read_artifact(deck.id).pages) == 2


def test_size_change_refits_every_page_to_the_new_canvas(deck):
    result = _edit(deck, operation="canvas_size", aspect_ratio="4:3")
    assert (result.aspect_ratio, result.canvas_width, result.canvas_height) == ("4:3", 1024, 768)
    for page in result.pages:
        assert 'data-row-bot-fit-scale' in page.html
    assert len(history.list_snapshots(deck.id)) == 1
    view = editing.read_editing(deck.id)
    assert (view.canvas_width, view.canvas_height) == (1024, 768)


@pytest.mark.parametrize("ratio", ["16:9", "4:3", "1:1", "A4", "9:16"])
def test_every_offered_size_is_accepted(deck, ratio):
    result = _edit(deck, operation="canvas_size", aspect_ratio=ratio)
    assert result.aspect_ratio == ratio


def test_same_size_changes_nothing_and_unknown_sizes_are_refused(deck):
    same = _edit(deck, operation="canvas_size", aspect_ratio=deck.aspect_ratio)
    assert same.updated_at == deck.updated_at
    assert not history.list_snapshots(deck.id)
    for ratio in ("landing", "7:5", ""):
        with pytest.raises(service.ArtifactError) as refused:
            _edit(deck, operation="canvas_size", aspect_ratio=ratio)
        assert refused.value.code == "invalid_edit"
