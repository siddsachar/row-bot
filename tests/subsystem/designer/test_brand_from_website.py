"""Brand "From a website": a guarded fetch and the extractor on a fixture page (Phase 12, parity row 30)."""
from __future__ import annotations

import pytest

from row_bot.designer import brand, brand_fetch
from row_bot.designer.client_service import ArtifactError

pytestmark = pytest.mark.subsystem

PAGE = b"""<!doctype html><html><head><style>
:root { --brand: #1D4ED8; --accent: #F97316; }
body { font-family: 'Inter', sans-serif; color: #0F172A; }
h1 { font-family: "Playfair Display", serif; }
</style></head><body><h1>Harbour Cleanup</h1></body></html>"""


def fetcher(pages, calls):
    def request(scheme, host, address, port, target, timeout):
        calls.append((scheme, host, address, port, target))
        return pages[(host, target)]
    return request


def public(_host, _port):
    return ("93.184.216.34",)


def test_extracts_colours_and_fonts_from_a_fixture_page():
    suggestion = brand.brand_from_html(PAGE.decode())
    assert suggestion.primary_color == "#1D4ED8"
    assert suggestion.secondary_color == "#F97316"
    assert suggestion.accent_color == "#0F172A"
    assert (suggestion.heading_font, suggestion.body_font) == ("Inter", "Playfair Display")
    assert brand.brand_from_html("<p>No styles</p>") is None


def test_fetches_a_public_page_over_the_vetted_address():
    calls = []
    request = fetcher({("example.com", "/about?x=1"): (200, {"content-type": "text/html; charset=utf-8"}, PAGE)}, calls)
    html = brand_fetch.fetch_public_html("https://example.com/about?x=1", resolver=public, request=request)
    assert "Harbour Cleanup" in html
    assert calls == [("https", "example.com", "93.184.216.34", 443, "/about?x=1")]


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "192.168.1.20", "169.254.169.254", "::1", "fd00::1", "100.64.0.1"])
def test_refuses_addresses_on_this_computer_or_network(address):
    calls = []
    with pytest.raises(ArtifactError, match="brand_website_unavailable"):
        brand_fetch.fetch_public_html("https://intranet.example/", resolver=lambda *_: (address,),
                                      request=fetcher({}, calls))
    assert calls == []


def test_refuses_mixed_answers_and_odd_urls():
    mixed = lambda *_: ("93.184.216.34", "10.0.0.1")  # noqa: E731
    for url, resolver in [("https://example.com/", mixed), ("ftp://example.com/", public),
                          ("https://user:pass@example.com/", public), ("file:///etc/passwd", public),
                          ("https://", public), ("example.com", public)]:
        with pytest.raises(ArtifactError, match="brand_website_unavailable"):
            brand_fetch.fetch_public_html(url, resolver=resolver, request=fetcher({}, []))


def test_follows_a_few_checked_redirects_but_never_into_the_local_network():
    calls = []
    pages = {("example.com", "/"): (301, {"location": "https://www.example.com/home"}, b""),
             ("www.example.com", "/home"): (200, {"content-type": "text/html"}, PAGE)}
    assert "Harbour" in brand_fetch.fetch_public_html("http://example.com/", resolver=public,
                                                      request=fetcher(pages, calls))
    assert [call[1] for call in calls] == ["example.com", "www.example.com"]
    sneaky = {("example.com", "/"): (302, {"location": "http://internal.example/admin"}, b"")}
    resolver = lambda host, _port: ("10.1.2.3",) if host == "internal.example" else ("93.184.216.34",)  # noqa: E731
    with pytest.raises(ArtifactError, match="brand_website_unavailable"):
        brand_fetch.fetch_public_html("https://example.com/", resolver=resolver, request=fetcher(sneaky, []))
    loop = {("example.com", "/"): (302, {"location": "/"}, b"")}
    with pytest.raises(ArtifactError, match="brand_website_unavailable"):
        brand_fetch.fetch_public_html("https://example.com/", resolver=public, request=fetcher(loop, []))


def test_only_html_within_the_size_limit():
    too_big = {("example.com", "/"): (200, {"content-type": "text/html"}, b"x" * (brand_fetch.MAX_BYTES + 1))}
    image = {("example.com", "/"): (200, {"content-type": "image/png"}, b"png")}
    missing = {("example.com", "/"): (404, {"content-type": "text/html"}, b"gone")}
    for pages in (too_big, image, missing):
        with pytest.raises(ArtifactError, match="brand_website_unavailable"):
            brand_fetch.fetch_public_html("https://example.com/", resolver=public, request=fetcher(pages, []))


def test_suggestion_for_a_website_uses_the_guarded_fetch(monkeypatch):
    from row_bot.designer import client_design_controls
    from row_bot.designer.client_design_controls import DesignControlItem

    monkeypatch.setattr(client_design_controls, "_font_items", lambda: [
        DesignControlItem("Inter", "Inter", "font", "Bundled · offline", True)])
    monkeypatch.setattr(brand_fetch, "fetch_public_html", lambda url, **_: PAGE.decode())
    suggestion = brand_fetch.brand_suggestion("https://www.example.com/")
    # Only fonts Row-Bot has come back, so everything returned can be applied.
    assert suggestion == {"found": True, "site": "example.com", "primary_color": "#1D4ED8",
                          "secondary_color": "#F97316", "accent_color": "#0F172A",
                          "heading_font": "Inter", "body_font": None}
    monkeypatch.setattr(brand_fetch, "fetch_public_html",
                        lambda url, **_: "<style>a{color:#abc}</style>")
    assert brand_fetch.brand_suggestion("https://example.com/")["primary_color"] == "#AABBCC"
    monkeypatch.setattr(brand_fetch, "fetch_public_html", lambda url, **_: "<p>plain</p>")
    assert brand_fetch.brand_suggestion("https://example.com/")["found"] is False
