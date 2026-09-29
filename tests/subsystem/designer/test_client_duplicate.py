"""Duplicate a design from the Design panel (Phase 12, parity row 22)."""
from __future__ import annotations

import pytest

from row_bot.designer import client_service as service, storage
from row_bot.designer.state import DesignerPage
from tests.subsystem.designer.test_client_artifact import isolated as _isolated

isolated = _isolated
pytestmark = pytest.mark.subsystem


@pytest.fixture
def source(isolated):
    project = service.create_deck("source-deck", service.DeckSetup(name="Tides"))
    project.pages = [DesignerPage(title="Why", route_id="why", html="<p>Why</p>"),
                     DesignerPage(title="How", route_id="how", html="<p>How</p>")]
    project.thread_id = "conversation-a"
    project.publish_url = "http://127.0.0.1:8080/published/source-deck.html"
    project.published_at = "2026-09-29T00:00:00+00:00"
    storage.save_project(project)
    asset_dir = storage._project_asset_dir(project.id)
    asset_dir.mkdir(parents=True, exist_ok=True)
    (asset_dir / "logo.png").write_bytes(b"png")
    return service.read_artifact(project.id)


def test_duplicate_copies_the_design_under_a_new_identity(source):
    copy = service.duplicate_artifact("copy-deck", source.id, expected_revision=source.updated_at)
    assert copy.id == "copy-deck" and copy.name == "Tides (copy)"
    assert [page.route_id for page in copy.pages] == ["why", "how"]
    assert [page.html for page in copy.pages] == [page.html for page in source.pages]
    assert (copy.mode, copy.canvas_width, copy.canvas_height) == (source.mode, source.canvas_width, source.canvas_height)
    # Its own conversation later; never published; its own assets.
    assert copy.thread_id is None and copy.thread_ownership == "resume"
    assert copy.publish_url == "" and copy.published_at == ""
    assert (storage._project_asset_dir("copy-deck") / "logo.png").read_bytes() == b"png"
    # The original is untouched.
    assert service.read_artifact(source.id).to_dict() == source.to_dict()


def test_a_retried_duplicate_returns_the_same_copy(source):
    first = service.duplicate_artifact("copy-deck", source.id, expected_revision=source.updated_at)
    again = service.duplicate_artifact("copy-deck", source.id, expected_revision=source.updated_at)
    assert again.to_dict() == first.to_dict()
    assert len(list(storage.PROJECTS_DIR.glob("*.json"))) == 2


def test_duplicate_refuses_a_stale_or_missing_source(source):
    with pytest.raises(service.ArtifactError) as stale:
        service.duplicate_artifact("copy-deck", source.id, expected_revision="old")
    assert stale.value.code == "resource_revision_conflict"
    with pytest.raises(service.ArtifactError) as missing:
        service.duplicate_artifact("copy-deck", "no-such-deck", expected_revision="x")
    assert missing.value.code == "not_found"
    assert not (storage.PROJECTS_DIR / "copy-deck.json").exists()


def test_long_names_stay_within_the_name_limit(source):
    source.name = "N" * 200
    storage.save_project(source)
    current = service.read_artifact(source.id)
    copy = service.duplicate_artifact("copy-deck", source.id, expected_revision=current.updated_at)
    assert len(copy.name) <= 200 and copy.name.endswith("(copy)")
