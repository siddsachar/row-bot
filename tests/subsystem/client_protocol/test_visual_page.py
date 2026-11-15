"""A visual's "Open as page" (F3): the same HTML becomes a page in that chat, under the pages' sandbox."""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

from __future__ import annotations

import pytest

from tests.subsystem.client_protocol.test_protocol_application import _client, service
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

SVG = '<svg viewBox="0 0 10 10"><title>Orbit</title><circle r="4"/></svg>'


@pytest.fixture
def chat(service, tmp_path, monkeypatch):
    from row_bot import pages, threads

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(pages, "_RECORDS", {})
    monkeypatch.setattr(pages, "_LOADED_ROOT", None)
    return threads.create_thread("Space", seed_default_skills=False)


def test_open_as_page_makes_a_page_in_that_chat_with_the_visual_s_html(service, chat):
    from row_bot import pages

    with _client(service) as client:
        _, headers = bootstrap(client)
        made = client.post(f"/api/v1/conversations/{chat}/visual-page", headers=headers,
                           json={"title": "Orbit <b>map</b>", "html": SVG})
        assert made.status_code == 200, made.text
        view = made.json()
        assert view["title"] == "Orbit map"
        assert view["conversation_id"] == chat
    page = pages.require(view["page_id"])
    assert SVG in pages.read_html(page)


def test_open_as_page_refuses_html_over_the_visual_limit_and_an_unknown_chat(service, chat):
    with _client(service) as client:
        _, headers = bootstrap(client)
        big = client.post(f"/api/v1/conversations/{chat}/visual-page", headers=headers,
                          json={"title": "Big", "html": "x" * (200 * 1024 + 1)})
        assert big.status_code in {400, 413, 422}, big.text
        missing = client.post("/api/v1/conversations/no-such-chat/visual-page", headers=headers,
                              json={"title": "Lost", "html": SVG})
        assert missing.status_code == 404, missing.text


THEME = {"mode": "dark", "light": {"text-primary": "#141922"}, "dark": {"text-primary": "#E8EBF0", "x": "#FF0000"}}


def test_a_visual_frame_is_served_once_sandboxed_with_no_network(service, chat):
    from fastapi.testclient import TestClient
    from row_bot.app import _page_frame_handler
    from starlette.applications import Starlette

    with _client(service) as client:
        _, headers = bootstrap(client)
        made = client.post(f"/api/v1/conversations/{chat}/visual-frame", headers=headers,
                           json={"html": SVG, "theme": THEME})
        assert made.status_code == 200, made.text
        frame_url = made.json()["frame_url"]
    frames = TestClient(Starlette(routes=[]))
    frames.app.add_route("/page-frames/{token}", _page_frame_handler, methods=["GET"])
    first = frames.get(frame_url)
    assert first.status_code == 200
    policy = first.headers["content-security-policy"]
    assert "default-src 'none'" in policy and "connect-src 'none'" in policy and "sandbox allow-scripts" in policy
    assert SVG in first.text
    assert '<html data-theme="dark">' in first.text and "--rb-text-primary:#E8EBF0" in first.text
    assert "#FF0000" not in first.text  # only Row-Bot's own colour tokens reach the frame
    # Served once: the visual reloading itself, or anyone else, gets nothing.
    assert frames.get(frame_url).status_code == 404


def test_a_visual_frame_refuses_html_over_the_limit(service, chat):
    with _client(service) as client:
        _, headers = bootstrap(client)
        big = client.post(f"/api/v1/conversations/{chat}/visual-frame", headers=headers,
                          json={"html": "x" * (200 * 1024 + 1), "theme": THEME})
        assert big.status_code in {400, 413, 422}, big.text
