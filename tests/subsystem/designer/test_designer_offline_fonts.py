"""B317: a design whose font isn't on this computer still previews; the tools only offer offline fonts."""

from __future__ import annotations

import pytest

from row_bot.designer import client_service as service, storage
from row_bot.designer.fonts import offline_substitute
from tests.subsystem.designer.test_client_artifact import isolated as _isolated

isolated = _isolated
pytestmark = pytest.mark.subsystem


@pytest.mark.parametrize(("family", "used"), [
    ("Playfair Display", "Playfair Display"),
    ("Georgia", "Georgia"),
    ("Cormorant Garamond", "Lora"),
    ("Noto Serif", "Lora"),
    ("Source Code Pro", "IBM Plex Mono"),
    ("Helvetica Now", "Inter"),
    ("Not a font!", "Inter"),
])
def test_a_missing_family_is_replaced_by_the_nearest_bundled_face(family, used):
    assert offline_substitute(family) == used


def test_a_design_with_an_uncached_font_previews_with_a_notice(isolated):
    project = service.create_deck("launch-page", service.DeckSetup())
    project.brand.heading_font = "Cormorant Garamond"
    project.brand.body_font = "Inter"
    storage.save_project(project)

    preview = service.read_preview(project.id)

    assert preview.html and "font-family: 'Lora'" in preview.html
    assert preview.font_notice == "Cormorant Garamond isn't available offline, so this preview uses Lora."
    assert storage.load_project(project.id).brand.heading_font == "Cormorant Garamond"


def test_the_brand_tool_refuses_a_font_that_isnt_offline_and_lists_the_choices(isolated, monkeypatch):
    from row_bot.designer import tool

    project = service.create_deck("brand-deck", service.DeckSetup())
    before = project.brand.heading_font
    monkeypatch.setattr(tool, "_require_project", lambda: project)
    monkeypatch.setattr(tool, "_pre_mutate", lambda *_args: None)
    monkeypatch.setattr(tool, "save_project", lambda _project: None)

    refused = tool._set_brand(heading_font="Cormorant Garamond")
    accepted = tool._set_brand(heading_font="Playfair Display")

    assert refused.startswith("Error: nothing was changed. Cormorant Garamond isn't available offline. "
                              "Choose one of: Anton,")
    assert "Playfair Display" in refused
    assert project.brand.heading_font == "Playfair Display" != before
    assert not accepted.startswith("Error")



def test_a_blank_optional_value_doesnt_cost_the_rest_of_the_brand_change(isolated, monkeypatch):
    """B321: qwen passed an empty logo_mode and the colours it asked for were dropped too."""
    from row_bot.designer import tool

    project = service.create_deck("brand-deck", service.DeckSetup())
    monkeypatch.setattr(tool, "_require_project", lambda: project)
    monkeypatch.setattr(tool, "_pre_mutate", lambda *_args: None)
    monkeypatch.setattr(tool, "save_project", lambda _project: None)

    applied = tool._set_brand(primary_color="#8a3b12", heading_font="Playfair Display", logo_mode="")
    refused = tool._set_brand(primary_color="#000000", logo_position="middle")

    assert applied.startswith("Brand updated")
    assert (project.brand.primary_color, project.brand.heading_font) == ("#8a3b12", "Playfair Display")
    assert refused.startswith("Error: nothing was changed. logo_position must be one of")
    assert project.brand.primary_color == "#8a3b12"
