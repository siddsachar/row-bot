"""Page screenshots and sizes in Row-Bot's browser (F4): reads of its own browser, so no approval.

A screenshot is saved to the chat's files and shown there, with password and card fields
masked; a full-page picture of a very long page stops at a bounded height, and one too large
for the chat is refused. Phone, tablet and desktop sizes (and a device scale) apply to the
conversation's page. The page and Chromium's CDP are small fakes here.
"""

from __future__ import annotations

import base64
import io

import pytest

from row_bot.browser.service import MAX_FULL_PAGE_CSS, MAX_SCREENSHOT_BYTES, BrowserSession, BrowserSessionManager
from row_bot.tools import approval_gate, browser_tool

pytestmark = pytest.mark.subsystem

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class _Page:
    def __init__(self, url: str, *, metrics=(1280, 800, 2400, 1.0), image: bytes = _PNG, boxes=()) -> None:
        self.url, self.metrics, self.image, self.boxes = url, metrics, image, list(boxes)
        self.shots: list[dict] = []
        self.covered: list[list] = []

    def is_closed(self):
        return False

    def title(self):
        return "Fixture"

    def bring_to_front(self):
        return None

    def evaluate(self, _script, arg=None):
        if arg is None:
            return list(self.metrics)
        self.covered.append(arg)  # [the protected-field selector, document coordinates?]
        return self.boxes

    def screenshot(self, **_options):
        pytest.fail("Playwright's screenshot would undo the page size")


class _Cdp:
    def __init__(self, page) -> None:
        self.page = page
        self.sent: list[tuple[str, dict]] = []
        self.detached = False

    def send(self, method, params=None):
        self.sent.append((method, dict(params or {})))
        if method == "Page.captureScreenshot":
            self.page.shots.append(dict(params or {}))
            return {"data": base64.b64encode(self.page.image).decode()}
        return {}

    def detach(self):
        self.detached = True


class _Context:
    def __init__(self) -> None:
        self.sessions: list[_Cdp] = []
        self.pages: list[_Page] = []

    def new_cdp_session(self, page):
        self.sessions.append(_Cdp(page))
        return self.sessions[-1]


def _session(page: _Page | None) -> BrowserSession:
    session = BrowserSession()
    session._context = _Context()
    session._launched = True
    if page is not None:
        session._thread_pages = {"chat": page}
        session._page_owners = {page: "chat"}
    session._run_on_pw_thread = lambda fn: fn()
    return session


def _png(size=(200, 100), colour=(255, 255, 255)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def test_a_screenshot_of_what_is_on_screen_covers_protected_fields():
    page = _Page("http://localhost:5173/signup?step=2", image=_png(), boxes=[[10, 10, 50, 20, 2]])
    session = _session(page)
    image, words = session.capture_page("chat")

    [shot] = page.shots
    assert shot == {"format": "png"}  # What's on screen, through Chromium's own screenshot.
    [(selector, document)] = page.covered
    assert 'input[type="password"]' in selector and "cc-number" in selector and document is False
    from PIL import Image

    picture = Image.open(io.BytesIO(image)).convert("RGB")
    assert picture.getpixel((40, 40)) == (38, 40, 46)  # Inside the field, at its device ratio.
    assert picture.getpixel((150, 80)) == (255, 255, 255)
    assert words.startswith("Screenshot of http://localhost:5173/signup (what's on screen, 1280×800 at 1× scale).")
    assert "step=2" not in words  # Query values stay out of the words.
    assert session._context.sessions[0].detached is True  # A one-off session goes.


def test_a_picture_is_never_saved_when_its_protected_fields_cant_be_checked():
    page = _Page("https://example.com/")

    def broken(_script, arg=None):
        if arg is None:
            return [1280, 800, 800, 1.0]
        raise RuntimeError("page went away")

    page.evaluate = broken
    image, words = _session(page).capture_page("chat")
    assert image is None and words.startswith("The screenshot couldn't be taken.")


def test_a_full_page_screenshot_stops_at_a_bounded_height():
    short = _Page("https://example.com/", metrics=(1280, 800, 2400, 1.0))
    image, words = _session(short).capture_page("chat", full_page=True)
    assert short.shots[0]["clip"] == {"x": 0, "y": 0, "width": 1280, "height": 2400, "scale": 1}
    assert short.shots[0]["captureBeyondViewport"] is True and "the whole page" in words
    assert short.covered[0][1] is True  # Fields are found in page coordinates.

    endless = _Page("https://example.com/feed", metrics=(390, 844, 90_000, 3.0))
    image, words = _session(endless).capture_page("chat", full_page=True)
    clip = endless.shots[0]["clip"]
    assert clip["height"] <= MAX_FULL_PAGE_CSS and clip["height"] * 3 <= 16_384
    assert f"the first {clip['height']:,} pixels of a longer page" in words


def test_a_picture_too_large_for_the_chat_is_refused_after_trying_jpeg():
    page = _Page("https://example.com/", image=b"x" * (MAX_SCREENSHOT_BYTES + 1))
    image, words = _session(page).capture_page("chat", full_page=True)

    assert image is None
    assert [shot["format"] for shot in page.shots] == ["png", "jpeg"]
    assert words.startswith("The page is too large to save as one picture")


def test_without_a_page_there_is_nothing_to_capture():
    image, words = _session(None).capture_page("chat")
    assert image is None and words.startswith("No page is open in this chat yet.")


@pytest.mark.parametrize(("preset", "scale", "expected"), [
    ("phone", None, {"width": 390, "height": 844, "deviceScaleFactor": 3.0, "mobile": True}),
    ("tablet", None, {"width": 820, "height": 1180, "deviceScaleFactor": 2.0, "mobile": True}),
    ("desktop", 2, {"width": 1280, "height": 800, "deviceScaleFactor": 2.0, "mobile": False}),
])
def test_page_sizes_apply_to_the_conversations_page(preset, scale, expected):
    page = _Page("http://localhost:5173/")
    session = _session(page)
    words = session.set_viewport(preset, scale, "chat")

    [cdp] = session._context.sessions
    assert cdp.sent[0] == ("Emulation.setDeviceMetricsOverride", expected)
    assert cdp.sent[1] == ("Emulation.setTouchEmulationEnabled",
                           {"enabled": expected["mobile"], "maxTouchPoints": 5 if expected["mobile"] else 0})
    assert words.startswith(f"The page now shows at {preset} size: {expected['width']}×{expected['height']}")

    # The screenshot says which size it shows.
    _image, shot = session.capture_page("chat")
    assert f"{preset} size" in shot


def test_a_new_size_replaces_the_last_and_bad_sizes_are_refused():
    page = _Page("http://localhost:5173/")
    session = _session(page)
    session.set_viewport("phone", None, "chat")
    session.set_viewport("desktop", None, "chat")

    first, second = session._context.sessions
    assert first.detached is True and second.detached is False
    assert session.set_viewport("watch", None, "chat") == "Choose phone, tablet or desktop."
    assert session.set_viewport("phone", 5, "chat") == "The device scale must be a number from 1 to 3."


def test_the_screenshot_tool_shows_the_picture_in_the_chat_without_asking(monkeypatch):
    shown: list[str] = []
    asked: list[dict] = []
    manager = BrowserSessionManager()
    monkeypatch.setattr(browser_tool, "_session_manager", manager)
    monkeypatch.setattr(browser_tool, "_get_thread_id", lambda: "chat")
    monkeypatch.setattr("row_bot.tools.image_gen_tool._set_pending_image", shown.append)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "block")  # Even on Block.
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: asked.append(payload) or True)
    session = manager.get_session("chat")
    page = _Page("http://localhost:5173/")
    session._context, session._launched = _Context(), True
    session._thread_pages, session._page_owners = {"chat": page}, {page: "chat"}
    session._run_on_pw_thread = lambda fn: fn()
    tools = {tool.name: tool for tool in browser_tool.BrowserTool().as_langchain_tools()}

    result = tools["browser_screenshot"].func(full_page=False)
    assert result.endswith("Saved to this chat's files and shown to the person.")
    assert [base64.b64decode(value) for value in shown] == [_PNG]
    assert tools["browser_viewport"].func("tablet").startswith("The page now shows at tablet size")
    assert asked == []
    session._closed = True


def test_a_page_with_too_many_protected_fields_to_cover_is_not_saved():
    page = _Page("https://example.com/", image=_png(), boxes=[[1, 1, 2, 2, 1]] * 201)
    image, words = _session(page).capture_page("chat")

    assert image is None and words.startswith("The screenshot couldn't be taken.")
