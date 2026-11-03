"""B297: read_url reads a PDF link as text; web pages still read as before."""

from __future__ import annotations

import pytest

from tests.fixtures.public_web import PUBLIC_ADDRESS, public_web

pytestmark = pytest.mark.subsystem


def _pdf(text: str) -> bytes:
    """A one-page PDF whose page shows ``text`` (enough for a text extractor)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


@pytest.fixture
def web(monkeypatch):
    with public_web(monkeypatch) as site:
        site.dns["papers.example"] = [PUBLIC_ADDRESS]
        yield site


def test_a_pdf_link_comes_back_as_its_text(web):
    from row_bot.tools.url_reader_tool import _read_url

    study = _pdf("Four-day week trial: productivity held steady")
    web.page("http://papers.example/study.pdf", study, "application/pdf")
    web.page("http://papers.example/download", study, "application/octet-stream")
    by_type = _read_url("http://papers.example/study.pdf")
    by_content = _read_url("http://papers.example/download")

    for text in (by_type, by_content):
        assert "Four-day week trial: productivity held steady" in text
        assert "[Page 1]" in text and "%PDF" not in text


def test_a_web_page_still_reads_as_text(web):
    from row_bot.tools.url_reader_tool import _read_url

    page = "<html><head><title>Rent</title></head><body><p>Rent rises to £1,250.</p></body></html>".encode()
    web.page("http://papers.example/rent", page)
    text = _read_url("http://papers.example/rent")

    assert text.startswith("SOURCE_URL: http://papers.example/rent")
    assert "Rent rises to £1,250." in text and "<p>" not in text


def test_a_rate_limited_page_is_read_again_once_after_the_wait_the_site_asks(web, monkeypatch):
    """U10: a 429 is not the article; the reader waits as asked (at most 10 s) and reads it again."""
    from row_bot.tools import url_reader_tool

    page = "<html><body><p>Everest is 8,848.86 m high.</p></body></html>".encode()
    web.routes["papers.example/everest"] = (429, {"Retry-After": "30"}, b"Too many requests")
    waited: list[float] = []

    def wait(seconds):
        waited.append(seconds)
        web.page("http://papers.example/everest", page)

    monkeypatch.setattr(url_reader_tool.time, "sleep", wait)
    text = url_reader_tool._read_url("http://papers.example/everest")

    assert waited == [10.0] and "Everest is 8,848.86 m high." in text


def test_a_blocked_or_still_limited_page_says_so_instead_of_its_error_page(web, monkeypatch):
    from row_bot.tools import url_reader_tool

    monkeypatch.setattr(url_reader_tool.time, "sleep", lambda seconds: None)
    web.routes["papers.example/blocked"] = (403, {}, b"Our robot policy forbids this")
    web.routes["papers.example/busy"] = (429, {}, b"Too many requests")

    for path, status in (("blocked", 403), ("busy", 429)):
        text = url_reader_tool._read_url(f"http://papers.example/{path}")
        assert text.startswith(f"The site refused to be read just now ({status}") and "robot" not in text
        assert "open the page in the browser" in text
