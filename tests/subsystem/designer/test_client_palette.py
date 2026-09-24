"""Shared Designer palette uses the bound project without mutating it."""

from types import SimpleNamespace

import pytest

from row_bot.designer import client_palette, storage, tool
from row_bot.designer.client_service import ArtifactError
from row_bot.designer.state import DesignerAsset
from tests.subsystem.designer.test_client_exports import project as _project, isolated as _isolated

project, isolated = _project, _isolated
pytestmark = pytest.mark.subsystem


def test_palette_searches_tools_pages_and_assets_without_saving(project, monkeypatch):
    project.assets.append(DesignerAsset(id="asset-synthetic", label="Synthetic chart"))
    storage.save_project(project)
    monkeypatch.setattr(
        tool.DesignerTool,
        "as_langchain_tools",
        lambda self: [SimpleNamespace(name="designer_generate_notes")],
    )
    monkeypatch.setattr(storage, "save_project", lambda *_: pytest.fail("palette saved project"))

    all_items = client_palette.read_palette(project.id, expected_revision=project.updated_at)
    assert [item["category"] for item in all_items["items"]] == [
        "tool", "page", "page", "asset"
    ]
    assert all_items["items"][0]["prefill"] == "Use designer_generate_notes for the current page"
    assert all_items["items"][1]["identity"] == "first"
    assert all_items["items"][3]["identity"] == "asset-synthetic"
    assert client_palette.read_palette(
        project.id, expected_revision=project.updated_at, query="synthetic"
    )["items"][0]["category"] == "asset"
    with pytest.raises(ArtifactError, match="resource_revision_conflict"):
        client_palette.read_palette(project.id, expected_revision="stale")


def test_palette_uses_preview_route_ids_and_never_truncates_asset_identity(project, monkeypatch):
    project.pages[0].route_id = ""
    project.assets.append(DesignerAsset(id="long-" + "x" * 130, label="Long asset"))
    storage.save_project(project)
    monkeypatch.setattr(tool.DesignerTool, "as_langchain_tools", lambda self: [])

    result = client_palette.read_palette(project.id, expected_revision=project.updated_at)

    assert result["items"][0]["identity"] == "page-1"
    assert not any(item["category"] == "asset" for item in result["items"])
    assert result["tools_available"] is True
