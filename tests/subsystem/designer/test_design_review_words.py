"""Review findings people read: a template passes its own review, and no
finding names an internal tool or flags a page's own CSS variables."""
from __future__ import annotations

import re

import pytest

from row_bot.designer import client_design_controls as client, client_service, fonts
from row_bot.designer.brand import BrandConfig
from row_bot.designer.brand_lint import lint_page
from row_bot.designer.critique import critique_page_html
from row_bot.designer.state import DESIGNER_MODES
from tests.subsystem.designer.test_client_artifact import isolated as _isolated

isolated = _isolated
pytestmark = pytest.mark.subsystem

_ROOT = """<style>:root { --primary:#2563EB; --accent:#F59E0B; --bg:#0F172A; --text:#F8FAFC;
  --body-font:'Inter', system-ui, 'Helvetica Neue', Arial, sans-serif;
  --display-font:'Comic Neue', cursive; }
  body { font-family: var(--body-font); background: var(--bg); color: var(--text); }</style>"""


@pytest.fixture(autouse=True)
def offline_fonts(monkeypatch):
    monkeypatch.setattr(fonts, "get_font_css_embedded", lambda _family: "")
    monkeypatch.setattr(fonts, "ensure_font", lambda *_a, **_k: pytest.fail("no font network"))


def _findings(html: str, brand=None):
    return lint_page(html, brand=brand or BrandConfig(), page_index=0)


def test_every_template_offered_in_new_design_passes_its_own_review(isolated):
    """Each mode's templates, on each canvas New design offers for them."""
    failures = {}
    for mode in DESIGNER_MODES:
        options = client_service.artifact_setup_options(mode)
        assert options.templates, mode
        for choice in options.templates:
            for canvas in options.canvases:
                project = client_service.create_artifact(
                    f"{mode}-{choice.id}-{canvas.id}".replace(":", "x"),
                    client_service.ArtifactSetup(mode, choice.id, canvas.id))
                review = client.read_review(project.id, scope="project", limit=50)
                if review.findings:
                    failures[(choice.id, canvas.id)] = [(item.category, item.message) for item in review.findings]
    assert failures == {}


def test_no_template_review_names_an_internal_tool_or_its_own_font_variables(isolated):
    for mode in DESIGNER_MODES:
        for choice in client_service.artifact_setup_options(mode).templates:
            project = client_service.create_artifact(
                f"{mode}-{choice.id}", client_service.ArtifactSetup(mode, choice.id))
            review = client.read_review(project.id, scope="project", limit=50)
            for item in review.findings:
                text = item.message + " " + item.suggested_fix
                assert not re.search(r"designer_[a-z_]+", text), (choice.id, text)
                assert "var(--" not in text, (choice.id, text)


def test_font_variables_resolve_to_the_fonts_they_name():
    html = _ROOT + '<h1 style="font-family: var(--body-font)">Title</h1>'
    assert not [item for item in _findings(html) if item.category == "font"]
    off_brand = _ROOT + '<h1 style="font-family: var(--display-font)">Title</h1>'
    [finding] = [item for item in _findings(off_brand) if item.category == "font"]
    assert "Comic Neue" in finding.message and "designer_" not in finding.suggested_fix


def test_a_see_through_background_is_not_read_as_a_solid_one():
    html = _ROOT + ('<body><a style="background:rgba(255,255,255,0.06);color:var(--text)">'
                    "See how it works</a></body>")
    assert not [item for item in _findings(html) if item.category == "contrast"]
    assert not [item for item in critique_page_html(html, 1920, 1080)["findings"]
                if item["category"] == "contrast"]


def test_large_text_needs_3_to_1_and_body_text_4_5_to_1():
    # #5D82A8 on #0F172A is about 4.4:1: fine for a large heading, not for body copy.
    html = _ROOT + ('<body><h1 style="font-size:3rem;color:#5D82A8">Big title</h1>'
                    '<p style="color:#5D82A8">Small body copy here</p></body>')
    contrast = [item for item in _findings(html) if item.category == "contrast"]
    assert [item.excerpt for item in contrast] == ["Small body copy here"]
    assert "4.5:1" in contrast[0].message


def test_page_palette_and_neutral_grays_are_not_off_brand_but_other_colors_are():
    html = _ROOT + ('<body><p style="color:#F59E0B">Own accent</p>'
                    '<p style="color:#94A3B8">Slate gray</p>'
                    '<p style="color:#22C55E">Green</p></body>')
    flagged = [item.message for item in _findings(html) if item.category == "off_palette"]
    assert len(flagged) == 1 and "#22c55e" in flagged[0]


def test_a_blank_starter_page_is_not_told_it_lacks_headings():
    html = _ROOT + '<body><p style="opacity:0.3">Blank slide — describe what to build</p></body>'
    assert not [item for item in critique_page_html(html, 1920, 1080)["findings"]
                if item["category"] == "hierarchy"]


def _categories(html: str, width: int = 1920, height: int = 1080) -> list[str]:
    return [item["category"] for item in critique_page_html(html, width, height)["findings"]]


_LONG = "A long paragraph of body copy that easily runs past eighty characters on one line."


def test_spacing_and_line_height_set_in_the_pages_own_styles_count():
    styled = ("<style>* { margin:0; padding:0; } section { padding: 48px 0; } "
              "body { line-height: 1.5; } .copy p { max-width: 60ch; }</style>"
              f"<body><section><h1>Features</h1><div class='copy'><p>{_LONG}</p></div><p>Tail</p></section></body>")
    assert _categories(styled) == []
    # A reset to zero is no spacing; the same page without its rules is flagged.
    bare = ("<style>* { margin:0; padding:0; }</style>"
            f"<body><section><h1>Features</h1><div class='copy'><p>{_LONG}</p></div><p>Tail</p></section></body>")
    assert sorted(_categories(bare)) == ["readability", "spacing"]


def test_a_margin_between_each_pair_of_blocks_spaces_a_flex_column():
    column = ('<body><div style="display:flex;flex-direction:column;">'
              '<h1 style="margin-bottom:16px;">Title</h1><p style="margin-bottom:24px;">Lead</p>'
              '<p>Last</p></div></body>')
    assert "spacing" not in _categories(column)
    touching = column.replace('style="margin-bottom:24px;"', "")
    assert "spacing" in _categories(touching), "two blocks still touch"


def test_a_phone_width_page_keeps_lines_short_without_a_max_width():
    page = f'<body><h1>Detail</h1><p style="line-height:1.6;">{_LONG}</p></body>'
    assert "readability" not in _categories(page, 390, 844)
    assert "readability" in _categories(page, 1440, 900)


def test_empty_bars_are_not_sections_and_a_tall_page_has_room_for_more():
    bars = "".join('<div style="height:6px;background:#334155;border-radius:3px;"></div>' for _ in range(8))
    assert "overflow" not in _categories(f"<body><h1>Sources</h1>{bars}</body>")
    cards = "".join(f'<div class="card"><p>Card {index}</p></div>' for index in range(8))
    assert "overflow" in _categories(f"<body><h1>Cards</h1>{cards}</body>")
    sections = "".join(f'<section style="padding:24px;"><h2>Part {index}</h2></section>' for index in range(6))
    page = f"<body><h1>Landing</h1>{sections}</body>"
    assert "overflow" in _categories(page, 1920, 1080)
    assert "overflow" not in _categories(page, 1440, 3200)
