"""#139: read_url gives a web page's main content as Markdown, read locally with BeautifulSoup.

Menus, sidebars, footers and cookie dialogs are left out; headings, lists, links (absolute), code and tables
are kept; blocks never run together. A page built only by JavaScript points to the browser. Paging and
other content types read as before. The pages here are small synthetic ones served from loopback.
"""

from __future__ import annotations

import pytest

from tests.fixtures.public_web import PUBLIC_ADDRESS, public_web

pytestmark = pytest.mark.subsystem

ARTICLE = b"""<!doctype html><html><head><title>Tide tables explained</title></head><body>
<header><a href="/">Coastal Times</a><nav><a href="/news">News</a> | <a href="/login">Log in</a></nav></header>
<div role="dialog">We use cookies. <button>Accept all</button></div>
<main><article>
  <header><h1>Tide tables explained</h1><p>By the harbour desk</p></header>
  <p>Tides rise and fall twice a day.<br>See the <a href="../guides/moon">moon guide</a> or
     <a href="https://tides.example/ports?id=4">port list</a>.</p>
  <p><a href="javascript:void(0)">Share</a> <a href="#notes">Notes</a></p>
  <h2>What to check</h2>
  <ol><li>High water time</li><li>Height<ul><li>in metres</li></ul></li></ol>
</article></main>
<aside>Most read: <a href="/beaches">Ten best beaches</a></aside>
<footer><p>Cookie settings</p><a href="/privacy">Privacy</a></footer>
</body></html>"""


@pytest.fixture
def web(monkeypatch):
    with public_web(monkeypatch) as site:
        site.dns["news.example"] = [PUBLIC_ADDRESS]
        yield site


def _read(web, body: bytes, content_type: str = "text/html; charset=utf-8", **paging) -> str:
    from row_bot.tools.url_reader_tool import _read_url

    web.page("http://news.example/tides/today", body, content_type)
    return _read_url("http://news.example/tides/today", **paging)


def test_an_article_keeps_its_content_and_drops_the_page_around_it(web):
    text = _read(web, ARTICLE)

    assert text.startswith("SOURCE_URL: http://news.example/tides/today\n\nTitle: Tide tables explained\n\n")
    for furniture in ("Coastal Times", "Log in", "cookies", "Accept all", "Ten best beaches", "Cookie settings",
                      "Privacy"):
        assert furniture not in text
    assert "# Tide tables explained\n\nBy the harbour desk" in text  # the article's own header stays
    assert "## What to check" in text
    assert "1. High water time\n2. Height\n  - in metres" in text
    assert "Tides rise and fall twice a day.\nSee the [moon guide](http://news.example/guides/moon)" in text
    assert "[port list](https://tides.example/ports?id=4)" in text
    assert "[Notes](http://news.example/tides/today#notes)" in text
    assert "Share" in text and "javascript:" not in text


def test_blocks_of_minified_html_never_run_together(web):
    page = (b"<html><body><div>Alpha</div><div>Beta</div><p>Gamma</p><h3>Delta</h3><span>Ep</span><span>silon</span>"
            b"<ul><li>One</li><li>Two</li></ul><dl><dt>Term</dt><dd>Meaning</dd></dl>Zeta<br>Eta</body></html>")

    text = _read(web, page).split("\n\n", 1)[1]

    assert text == "Alpha\n\nBeta\n\nGamma\n\n### Delta\n\nEpsilon\n\n- One\n- Two\n\nTerm\n\nMeaning\n\nZeta\nEta"


def test_a_docs_page_keeps_its_code_and_a_simple_table(web):
    page = b"""<html><body><main><h1>Ports API</h1><p>Call <code>height(t)</code> per port.</p>
    <pre><code>for port in ports:
    print(port.name,   height(port))</code></pre>
    <table><caption>Today</caption><thead><tr><th>Port</th><th>High | low</th></tr></thead>
    <tbody><tr><td>Dover</td><td><p>06:12</p><p>12:30</p></td></tr></tbody></table></main></body></html>"""

    text = _read(web, page)

    assert "Call `height(t)` per port." in text
    assert "```\nfor port in ports:\n    print(port.name,   height(port))\n```" in text
    assert "Today\n\n| Port | High \\| low |\n| --- | --- |\n| Dover | 06:12 12:30 |" in text


def test_a_page_built_by_javascript_points_to_the_browser(web):
    shell = (b'<html><head><title>Tide app</title><script src="/bundle.js"></script></head>'
             b'<body><div id="root"></div><noscript>Please enable JavaScript.</noscript></body></html>')

    text = _read(web, shell)

    assert text.startswith("SOURCE_URL: http://news.example/tides/today\n\nTitle: Tide app\n\n")
    assert "builds itself with JavaScript" in text and "browser_navigate" in text
    # A full article with scripts on it reads as itself.
    assert "browser_navigate" not in _read(web, ARTICLE.replace(b"</head>", b"<script>track()</script></head>"))


def test_a_long_page_is_still_read_a_page_at_a_time(web):
    from row_bot.tools.url_reader_tool import PAGE_CHARS

    page = b"<html><body>" + b"".join(b"<p>Paragraph %05d of the long article.</p>" % n for n in range(3_000)) + b"</body></html>"

    parts, offset = [], 0
    while offset is not None:
        window, _, footer = _read(web, page, offset=offset).split("\n\n", 1)[1].rpartition("\n\n[Characters ")
        assert len(window) <= PAGE_CHARS
        parts.append(window)
        offset = int(footer.split("offset=")[1].rstrip(".]")) if "offset=" in footer else None

    assert len(parts) == 3 and footer.endswith("End of page.]")
    assert "".join(parts).replace("\n\n", "\n").split("\n") == [f"Paragraph {n:05d} of the long article." for n in range(3_000)]


def test_other_content_types_read_as_before(web):
    plain = b"# Not a heading\n\n\n\nkeep   <b>this</b>   as text"
    data = b'{"port": "Dover", "high": "06:12"}'

    assert _read(web, plain, "text/plain; charset=utf-8").split("\n\n", 1)[1] == "# Not a heading\n\nkeep this as text"
    assert _read(web, data, "application/json").split("\n\n", 1)[1] == data.decode()
