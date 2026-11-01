"""Page screenshots and sizes in Row-Bot's browser (F4): reads of its own browser, so no approval.

A screenshot is saved to the chat's files and shown there, with password and card fields
masked; a full-page picture of a very long page stops at a bounded height, and one too large
for the chat is refused. Phone, tablet and desktop sizes (and a device scale) apply to the
conversation's page. The page and Chromium's CDP are small fakes here.
"""

from __future__ import annotations

import base64
import io
import re

import pytest

from row_bot.browser.service import (
    MAX_FULL_PAGE_CSS,
    MAX_SCREENSHOT_BYTES,
    BrowserEngine,
    BrowserSessionManager,
    _Tab,
)
from row_bot.tools import approval_gate, browser_tool

pytestmark = pytest.mark.subsystem

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class _Locator:
    async def aria_snapshot(self, **_options):
        return '- heading "Fixture" [level=1]'


class _Page:
    parent_frame = None
    viewport_size = {"width": 1280, "height": 800}

    def __init__(self, url: str, *, metrics=(1280, 800, 2400, 1.0), image: bytes = _PNG, boxes=()) -> None:
        self.url, self.metrics, self.image, self.boxes = url, metrics, image, list(boxes)
        self.shots: list[dict] = []
        self.covered: list[list] = []
        self.frames = [self]

    def is_closed(self):
        return False

    async def title(self):
        return "Fixture"

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def locator(self, _selector):
        return _Locator()

    async def evaluate(self, script, arg=None):
        if arg is None:
            return list(self.metrics) if "innerWidth" in script else ""
        if isinstance(arg, list) and arg and isinstance(arg[0], int):
            return True  # The page went quiet.
        self.covered.append(arg)  # [the protected-field selector, document coordinates?]
        return self.boxes


class _Cdp:
    def __init__(self, page) -> None:
        self.page = page
        self.sent: list[tuple[str, dict]] = []

    async def send(self, method, params=None):
        self.sent.append((method, dict(params or {})))
        if method == "Page.captureScreenshot":
            self.page.shots.append(dict(params or {}))
            return {"data": base64.b64encode(self.page.image).decode()}
        return {}

    async def detach(self):
        return None


class _Context:
    def __init__(self) -> None:
        self.sessions: list[_Cdp] = []
        self.pages: list[_Page] = []

    async def new_cdp_session(self, page):
        self.sessions.append(_Cdp(page))
        return self.sessions[-1]


@pytest.fixture
def engines():
    made: list[BrowserEngine] = []

    def engine(page: _Page | None, base: BrowserEngine | None = None) -> BrowserEngine:
        session = base or BrowserEngine()
        session._ensure_loop()
        session._context = _Context()
        session._status = "running"
        if page is not None:
            chat = session._chat("chat")
            tab = _Tab("t1", page)
            chat.tabs, chat.active = [tab], tab
            session._owners[page] = "chat"
        made.append(session)
        return session

    yield engine
    for session in made:
        session._context = None
        session.close()


def _png(size=(200, 100), colour=(255, 255, 255)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def test_a_screenshot_of_what_is_on_screen_covers_protected_fields(engines):
    page = _Page("http://localhost:5173/signup?step=2", image=_png(), boxes=[[10, 10, 50, 20, 2]])
    image, words = engines(page).capture_page("chat")

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


def test_the_models_picture_covers_protected_fields_too(engines):
    page = _Page("https://example.com/sign-in", image=_png(), boxes=[[10, 10, 50, 20, 2]])
    picture = engines(page).model_picture("chat")
    from PIL import Image

    assert page.shots == [{"format": "jpeg", "quality": 70}]
    pixel = Image.open(io.BytesIO(picture)).convert("RGB").getpixel((40, 40))
    assert all(abs(a - b) < 12 for a, b in zip(pixel, (38, 40, 46)))


def test_a_picture_is_never_saved_when_its_protected_fields_cant_be_checked(engines):
    page = _Page("https://example.com/")

    async def broken(script, arg=None):
        if arg is None:
            return [1280, 800, 800, 1.0]
        raise RuntimeError("page went away")

    page.evaluate = broken
    image, words = engines(page).capture_page("chat")
    assert image is None and words.startswith("The screenshot couldn't be taken.")


def test_a_full_page_screenshot_stops_at_a_bounded_height(engines):
    short = _Page("https://example.com/", metrics=(1280, 800, 2400, 1.0))
    image, words = engines(short).capture_page("chat", full_page=True)
    assert short.shots[0]["clip"] == {"x": 0, "y": 0, "width": 1280, "height": 2400, "scale": 1}
    assert short.shots[0]["captureBeyondViewport"] is True and "the whole page" in words
    assert short.covered[0][1] is True  # Fields are found in page coordinates.

    endless = _Page("https://example.com/feed", metrics=(390, 844, 90_000, 3.0))
    image, words = engines(endless).capture_page("chat", full_page=True)
    clip = endless.shots[0]["clip"]
    assert clip["height"] <= MAX_FULL_PAGE_CSS and clip["height"] * 3 <= 16_384
    assert f"the first {clip['height']:,} pixels of a longer page" in words


def test_a_picture_too_large_for_the_chat_is_refused_after_trying_jpeg(engines):
    page = _Page("https://example.com/", image=b"x" * (MAX_SCREENSHOT_BYTES + 1))
    image, words = engines(page).capture_page("chat", full_page=True)

    assert image is None
    assert [shot["format"] for shot in page.shots] == ["png", "jpeg"]
    assert words.startswith("The page is too large to save as one picture")


def test_without_a_page_there_is_nothing_to_capture(engines):
    image, words = engines(None).capture_page("chat")
    assert image is None and words.startswith("No page is open in this chat yet.")


@pytest.mark.parametrize(("preset", "scale", "expected"), [
    ("phone", None, {"width": 390, "height": 844, "deviceScaleFactor": 3.0, "mobile": True}),
    ("tablet", None, {"width": 820, "height": 1180, "deviceScaleFactor": 2.0, "mobile": True}),
    ("desktop", 2, {"width": 1280, "height": 800, "deviceScaleFactor": 2.0, "mobile": False}),
])
def test_page_sizes_apply_to_the_conversations_page(engines, preset, scale, expected):
    page = _Page("http://localhost:5173/")
    session = engines(page)
    words = session.set_viewport(preset, scale, "chat")

    [cdp] = session._context.sessions
    assert cdp.sent[0] == ("Emulation.setDeviceMetricsOverride", expected)
    # Chromium refuses touch points outside 1-16, so switching touch off sends none.
    assert cdp.sent[1] == ("Emulation.setTouchEmulationEnabled",
                           {"enabled": True, "maxTouchPoints": 5} if expected["mobile"] else {"enabled": False})
    assert words.startswith(f"The page now shows at {preset} size: {expected['width']}×{expected['height']}")

    # The screenshot says which size it shows.
    _image, shot = session.capture_page("chat")
    assert f"{preset} size" in shot


def test_back_to_desktop_is_a_desktop_window_again_and_bad_sizes_are_refused(engines):
    page = _Page("http://localhost:5173/")
    session = engines(page)
    session.set_viewport("phone", None, "chat")
    words = session.set_viewport("desktop", None, "chat")

    [cdp] = session._context.sessions
    assert cdp.sent[2:] == [
        ("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 800, "deviceScaleFactor": 1.0,
                                                "mobile": False}),
        ("Emulation.setTouchEmulationEnabled", {"enabled": False}),
    ]
    assert words.startswith("The page now shows at desktop size: 1280×800 at 1× scale.")
    assert session.set_viewport("watch", None, "chat") == "Choose phone, tablet, desktop or pane."
    assert session.set_viewport("phone", 5, "chat") == "The device scale must be a number from 1 to 3."


def test_the_screenshot_tool_shows_the_picture_in_the_chat_without_asking(engines, monkeypatch):
    shown: list[tuple[str, str]] = []
    asked: list[dict] = []
    manager = BrowserSessionManager()
    monkeypatch.setattr(browser_tool, "_session_manager", manager)
    monkeypatch.setattr(browser_tool, "_get_thread_id", lambda: "chat")
    monkeypatch.setattr("row_bot.tools.image_gen_tool._set_pending_image",
                        lambda value, name="": shown.append((value, name)))
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "block")  # Even on Block.
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: asked.append(payload) or True)
    page = _Page("http://localhost:5173/")
    engines(page, manager.get_session("chat"))

    result = browser_tool.browser_screenshot(full_page=False)
    [(value, name)] = shown
    assert base64.b64decode(value) == _PNG
    # Its own name, so a later step can attach this screenshot and not another.
    assert re.fullmatch(r"screenshot-\d{4}-\d{2}-\d{2}-\d{6}\.png", name)
    assert result.endswith(f"Saved to this chat's files as {name} and shown to the person.")
    assert browser_tool.browser_viewport("tablet").startswith("The page now shows at tablet size")
    assert asked == []


def test_a_page_with_too_many_protected_fields_to_cover_is_not_saved(engines):
    page = _Page("https://example.com/", image=_png(), boxes=[[1, 1, 2, 2, 1]] * 201)
    image, words = engines(page).capture_page("chat")

    assert image is None and words.startswith("The screenshot couldn't be taken.")
