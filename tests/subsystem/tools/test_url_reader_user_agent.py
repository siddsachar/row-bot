"""read_url tells a site who is reading: Row-Bot, its version and where to find its maker.

Wikipedia refused read_url with a 403 while it posed as a desktop browser; Wikimedia's policy asks automated
readers for a descriptive agent naming the program and a way to reach its maker. Nothing about the person or
their computer goes in it. The site here is a small synthetic one served from loopback.
"""

from __future__ import annotations

import pytest

from tests.fixtures.public_web import PUBLIC_ADDRESS, public_web

pytestmark = pytest.mark.subsystem

ARTICLE = b"""<!doctype html><html><head><title>Printing press</title></head><body>
<main><h1>Printing press</h1><p>A printing press applies pressure to an inked surface.</p>
<h2>History</h2><p>Movable type spread across Europe.</p></main></body></html>"""


def test_every_request_names_row_bot_its_version_and_its_website_and_the_page_reads(monkeypatch):
    from row_bot.tools.url_reader_tool import _read_url
    from row_bot.version import __version__

    with public_web(monkeypatch) as web:
        web.dns["wiki.example"] = [PUBLIC_ADDRESS]
        web.redirect("http://wiki.example/wiki/Printing", "/wiki/Printing_press", status=301)
        web.page("http://wiki.example/wiki/Printing_press", ARTICLE)

        text = _read_url("http://wiki.example/wiki/Printing")

    assert text.startswith("SOURCE_URL: http://wiki.example/wiki/Printing_press")
    assert "# Printing press" in text and "## History" in text
    # The link and its redirect both say who is asking: the program, its version and its website, and no more
    # (no browser it isn't, no machine or user name, no path or install id).
    assert web.user_agents == [f"Row-Bot/{__version__} (+https://row-bot.ai)"] * 2
