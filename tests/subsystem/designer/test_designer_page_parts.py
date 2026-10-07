"""B324: a long page is written in parts, so no single tool call runs past a provider's time limit."""

from __future__ import annotations

import pytest

from row_bot.designer import client_service as service
from tests.subsystem.designer.test_client_artifact import isolated as _isolated

isolated = _isolated
pytestmark = pytest.mark.subsystem


def test_append_html_adds_a_section_before_the_page_end(isolated, monkeypatch):
    from row_bot.designer import tool

    project = service.create_deck("landing", service.DeckSetup())
    monkeypatch.setattr(tool, "_require_project", lambda: project)
    monkeypatch.setattr(tool, "_pre_mutate", lambda *_args: None)
    monkeypatch.setattr(tool, "save_project", lambda _project: None)

    tool._update_page(0, html="<!doctype html><html><body><section id='hero'>Autumn</section></body></html>")
    reply = tool._update_page(0, append_html="<section id='products'>Mugs, tees, totes</section>")

    page = project.pages[0].html
    assert reply.startswith("Updated page 1")
    assert page.index("hero") < page.index("products")
    assert page.lower().rstrip().endswith("</html>") and page.lower().count("</body>") == 1
    assert tool._update_page(0) == "Error: html cannot be empty."
