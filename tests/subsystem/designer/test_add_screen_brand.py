"""A screen the agent adds without HTML starts from the design's brand (B189).

The branded blank page lived in the NiceGUI page navigator; without NiceGUI
the tool silently fell back to an unbranded page.
"""
from __future__ import annotations

import sys

import pytest

from row_bot.designer.state import BrandConfig, DesignerProject
from tests.subsystem.designer.test_designer_project_concurrency import _isolate_storage

pytestmark = pytest.mark.subsystem


def test_added_screen_uses_the_brand_without_nicegui(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "nicegui", None)
    monkeypatch.delitem(sys.modules, "row_bot.designer.page_navigator", raising=False)
    storage = _isolate_storage(tmp_path, monkeypatch)
    from row_bot.designer import tool

    storage.save_project(DesignerProject(
        id="site", name="Orbit site", mode="landing",
        brand=BrandConfig(primary_color="#123456", bg_color="#0A0B0C"),
    ))
    project = storage.load_project("site")
    monkeypatch.setattr(tool, "_require_project", lambda: project)
    monkeypatch.setattr(tool, "_pre_mutate", lambda _project, label="": None)

    tool._add_screen("Pricing")

    added = storage.load_project("site").pages[-1].html
    assert "--primary: #123456" in added
    assert "Pricing" in added
