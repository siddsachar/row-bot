"""The Browser pane's API (N12-A): state, the live picture, the person's input and controls, signed-in sites.

Reads never start the browser. The live stream sends the pane's state and the latest picture. The
person's controls refuse what never opens (the local network, Row-Bot itself) and allow an address
on this computer they typed themselves, in that chat. Nothing launches Chromium: the engine is the
real one with its page work faked.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from row_bot.browser.service import BrowserEngine, BrowserSessionManager, Frame, _Live, _Tab
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


class _Engine(BrowserEngine):
    def __init__(self) -> None:
        super().__init__()
        self.actions: list[tuple[str, str, str]] = []
        self.played: list[list[dict]] = []
        self.cleared: list[str | None] = []
        self.quit_after_picture = False
        self.sizes: list[tuple[int, int, float]] = []

    def watch(self, chat_id, **options):
        watch = super().watch(chat_id, **options)
        self.sizes.append(self.page_metrics(chat_id))  # The page's size while this pane watches.
        return watch

    def person_action(self, chat_id, action, *, url="", tab=""):
        self.actions.append((chat_id, action, url))

    def ensure_started(self):
        return None

    def person_input(self, chat_id, events):
        self.played.append(events)
        return {"status": "ok", "copied": "ABCD-1234" if events[0]["type"] == "copy" else ""}

    def latest_frame(self, chat_id):
        frame = super().latest_frame(chat_id)
        if frame is not None and self.quit_after_picture:
            self._closed = True  # Row-Bot quits right after the first picture: the stream ends with it.
        return frame

    def sites(self):
        return [{"site": "homeserve.co.uk", "cookies": 3}] if None not in self.cleared else []

    def clear_sites(self, site):
        self.cleared.append(site)
        return 1


@pytest.fixture
def browser(monkeypatch):
    from row_bot.tools import browser_tool, registry

    monkeypatch.setenv("ROW_BOT_PORT", "8080")
    manager = BrowserSessionManager()
    monkeypatch.setattr(browser_tool, "_session_manager", manager)
    monkeypatch.setattr(registry, "is_enabled", lambda name: name == "browser")
    yield manager
    engine = manager.engine()
    if engine is not None:
        engine._closed = True


def _chat(client_service):
    from row_bot import threads

    return threads.create_thread("Book a plumber", seed_default_skills=False)


def test_reading_the_pane_never_starts_the_browser(service, browser):  # noqa: F811
    chat = _chat(service)
    with _client(service) as client:
        _, headers = bootstrap(client)
        state = client.get(f"/api/v1/conversations/{chat}/browser", headers=headers)
    assert state.status_code == 200, state.text
    assert state.json() | {"revision": 0} == {
        "schema_version": 1, "conversation_id": chat, "status": "stopped", "acting": "",
        "waiting": "", "url": "", "title": "", "site": "", "loading": False, "sign_in": "", "recording": False,
        "tabs": [], "revision": 0}
    assert not browser.has_active_session()


def test_the_live_stream_sends_the_pane_state_and_the_latest_picture_at_the_panes_size(service, browser):  # noqa: F811
    chat = _chat(service)
    engine = _Engine()
    browser._engine = engine
    engine._status = "running"
    page = SimpleNamespace(url="https://www.homeserve.co.uk/claim/1", is_closed=lambda: False)
    tab = _Tab("t1", page)
    record = engine._chat(chat)
    record.tabs, record.active, record.acting = [tab], tab, 'Clicking button "Next"'
    engine._live[chat] = _Live(frame=Frame(4, "/9j/4AAQ", 1280, 800))
    events: dict[str, dict] = {}
    # Row-Bot quits once the stream has its first picture: the stream ends with the browser (and the test
    # client, which reads whole responses, gets it).
    engine.quit_after_picture = True
    with _client(service) as client:
        _, headers = bootstrap(client)
        response = client.get(f"/api/v1/conversations/{chat}/browser/live",
                              params={"width": 420, "height": 700, "scale": 2}, headers=headers)
        refused = client.get(f"/api/v1/conversations/{chat}/browser/live",
                             params={"width": 420, "height": 99999}, headers=headers)
    assert refused.status_code == 422
    assert engine.sizes == [(420, 700, 2.0)]  # The page lays out at the pane's size.
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    for block in response.text.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in fields:
            events[fields["event"]] = json.loads(fields["data"])
    assert events["state"]["site"] == "homeserve.co.uk" and events["state"]["acting"] == 'Clicking button "Next"'
    assert events["frame"] == {"seq": 4, "css_width": 1280.0, "css_height": 800.0, "image_base64": "/9j/4AAQ"}


def test_the_persons_input_is_checked_and_played_on_the_page(service, browser):  # noqa: F811
    chat = _chat(service)
    engine = _Engine()
    browser._engine = engine
    with _client(service) as client:
        _, headers = bootstrap(client)
        url = f"/api/v1/conversations/{chat}/browser/input"
        good = [{"type": "mouse", "action": "down", "x": 640, "y": 400, "button": "left", "clicks": 1},
                {"type": "key", "action": "down", "key": "a"}, {"type": "text", "text": "Leak under the sink"}]
        played = client.post(url, json={"events": good}, headers=headers)
        assert played.status_code == 200, played.text
        assert played.json() == {"status": "ok", "copied": ""}
        # Copy reads the page's selection for the person's clipboard.
        copied = client.post(url, json={"events": [{"type": "copy"}]}, headers=headers)
        assert copied.json() == {"status": "ok", "copied": "ABCD-1234"}
        # A paste is the page's own paste, long text included (a pasted message, not only a code).
        pasted = client.post(url, json={"events": [{"type": "paste", "text": "x" * 20_000}]}, headers=headers)
        assert pasted.status_code == 200, pasted.text
        for bad in ([{"type": "mouse", "action": "type", "x": 1, "y": 1}],
                    [{"type": "key", "action": "down", "key": ""}],
                    [{"type": "text", "text": "\x1b[2J"}],
                    [{"type": "paste", "text": ""}],
                    [{"type": "mouse", "action": "move"}] * 65):
            assert client.post(url, json={"events": bad}, headers=headers).status_code == 422
    assert len(engine.played) == 3 and engine.played[0][2]["text"] == "Leak under the sink"
    assert engine.played[2][0]["type"] == "paste" and len(engine.played[2][0]["text"]) == 20_000


def test_the_address_bar_refuses_what_never_opens_and_allows_what_the_person_typed(service, browser):  # noqa: F811
    chat = _chat(service)
    engine = _Engine()
    browser._engine = engine
    with _client(service) as client:
        _, headers = bootstrap(client)
        url = f"/api/v1/conversations/{chat}/browser/actions"
        for denied in ("http://169.254.169.254/latest/meta-data/", "http://localhost:8080/app-v2/",
                       "https://user:pw@example.com/"):
            response = client.post(url, json={"action": "navigate", "url": denied}, headers=headers)
            assert response.status_code == 403 and response.json()["code"] == "browser_navigation_denied"
        assert client.post(url, json={"action": "navigate", "url": "homeserve.co.uk"}, headers=headers).status_code == 200
        assert client.post(url, json={"action": "navigate", "url": "localhost:5173"}, headers=headers).status_code == 200
        # The person's own network (5.3.1 B3): what they typed opens, and the chat may use it.
        assert client.post(url, json={"action": "navigate", "url": "http://192.168.1.1/"},
                           headers=headers).status_code == 200
        # The page is shared: there's nothing to take over or give back.
        assert client.post(url, json={"action": "take_control"}, headers=headers).status_code == 422
        assert client.post(url, json={"action": "close_tab", "tab": "../x"}, headers=headers).status_code == 422
    assert engine.actions == [(chat, "navigate", "https://homeserve.co.uk"), (chat, "navigate", "http://localhost:5173"),
                              (chat, "navigate", "http://192.168.1.1/")]
    assert [(grant.address, grant.source) for grant in engine.local_address_grants(chat)] == [
        ("localhost:5173", "you"), ("192.168.1.1", "you")]


def test_with_the_browser_turned_off_the_pane_says_so(service, browser, monkeypatch):  # noqa: F811
    from row_bot.tools import registry

    monkeypatch.setattr(registry, "is_enabled", lambda name: False)
    chat = _chat(service)
    with _client(service) as client:
        _, headers = bootstrap(client)
        response = client.post(f"/api/v1/conversations/{chat}/browser/actions", json={"action": "reload"},
                               headers=headers)
    assert response.status_code == 403 and response.json()["code"] == "browser_disabled"


def test_signed_in_sites_are_listed_and_cleared(service, browser):  # noqa: F811
    engine = _Engine()
    browser._engine = engine
    with _client(service) as client:
        _, headers = bootstrap(client)
        assert client.get("/api/v1/browser/sites", headers=headers).json() == {
            "items": [{"site": "homeserve.co.uk", "cookies": 3}]}
        one = client.post("/api/v1/browser/sites/clear", json={"site": "www.homeserve.co.uk"}, headers=headers)
        assert one.status_code == 200 and one.json()["cleared"] == 1
        everything = client.post("/api/v1/browser/sites/clear", json={}, headers=headers).json()
        assert everything == {"cleared": 1, "items": []}
    assert engine.cleared == ["homeserve.co.uk", None]
