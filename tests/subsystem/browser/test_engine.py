"""Row-Bot's browser engine: how it launches, what it leaves out, crashes, Stop and pop-ups (N12-A).

A fake Playwright stands in for Chromium: nothing launches. What's checked is what Row-Bot asks
Chromium for: the person's own Chrome or Edge first, no window of its own, no proxy, no request
interception beyond the local-address guard, service workers on, no automation switches, and the
user agent of the same browser with a window.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from row_bot.browser import service as service_module
from row_bot.browser.runtime import BrowserRuntimeReadiness
from row_bot.cancellation import CancellationScope, use_cancellation_scope

pytestmark = pytest.mark.subsystem

HEADLESS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/155.0.0.0 Safari/537.36"
HEADED_UA = HEADLESS_UA.replace("HeadlessChrome/", "Chrome/")


class FakeSession:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.handlers: dict[str, object] = {}

    def on(self, event, handler) -> None:
        self.handlers[event] = handler

    async def send(self, method, params=None):
        self.sent.append((method, params or {}))
        if method == "Emulation.setTouchEmulationEnabled" and not 1 <= (params or {}).get("maxTouchPoints", 1) <= 16:
            raise RuntimeError("Touch points must be between 1 and 16")  # As Chromium answers.
        if method == "Browser.getVersion":
            return {"userAgent": HEADLESS_UA, "product": "HeadlessChrome/155.0"}
        return {}

    async def detach(self) -> None:
        return None


class FakePage:
    def __init__(self, opener=None, url="about:blank") -> None:
        self._opener = opener
        self.url = url
        self.frames: list = []
        self.handlers: dict[str, list] = {}
        self.closed = False

    async def opener(self):
        return self._opener

    def on(self, event, handler) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def is_closed(self) -> bool:
        return self.closed

    async def title(self) -> str:
        return ""

    async def close(self) -> None:
        self.closed = True
        for handler in self.handlers.get("close", []):
            handler(self)


class FakeContext:
    def __init__(self, options: dict) -> None:
        self.options = options
        self.pages: list[FakePage] = [FakePage()]
        self.handlers: dict[str, object] = {}
        self.sessions: list[FakeSession] = []
        self.closed = False

    def on(self, event, handler) -> None:
        self.handlers[event] = handler

    async def new_cdp_session(self, _page) -> FakeSession:
        self.sessions.append(FakeSession())
        return self.sessions[-1]

    async def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        handler = self.handlers.get("page")
        if handler is not None:
            await handler(page)
        return page

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, *, fail_channel: bool = False) -> None:
        self.fail_channel = fail_channel
        self.launches: list[dict] = []
        self.contexts: list[FakeContext] = []

    async def launch_persistent_context(self, user_data_dir, **options):
        self.launches.append({"user_data_dir": user_data_dir, **options})
        if options.get("channel") and self.fail_channel:
            raise RuntimeError("synthetic channel failure")
        self.contexts.append(FakeContext(options))
        return self.contexts[-1]


class FakePlaywright:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def chromium(monkeypatch, tmp_path):
    fake = FakeChromium()
    playwright = FakePlaywright(fake)

    class Starter:
        async def start(self):
            return playwright

    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: Starter())
    monkeypatch.setattr(service_module, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(service_module, "_installed_channel", lambda: "msedge")
    monkeypatch.setattr(service_module, "check_packaged_browser_runtime",
                        lambda: BrowserRuntimeReadiness(False, "missing", "missing", "", "", "", "", ""))
    monkeypatch.setattr(service_module, "check_managed_browser_runtime",
                        lambda: BrowserRuntimeReadiness(True, "ready", "ready", "C:/rowbot/chrome.exe", "C:/rowbot",
                                                        "1.62.0", "1234", "151"))
    engines: list[service_module.BrowserEngine] = []

    def engine() -> service_module.BrowserEngine:
        engines.append(service_module.BrowserEngine())
        return engines[-1]

    fake.engine = engine
    yield fake
    for item in engines:
        item.close()


def test_it_runs_the_persons_browser_with_its_own_profile_and_no_window_proxy_or_automation_switch(chromium):
    engine = chromium.engine()
    engine.ensure_started()

    first = chromium.launches[0]
    assert first["channel"] == "msedge"
    assert first["user_data_dir"] == str(service_module.PROFILE_DIR)
    assert first["headless"] is True  # No window of its own: the pane shows it.
    assert "--enable-automation" in first["ignore_default_args"]
    assert "--disable-blink-features=AutomationControlled" in first["args"]
    assert not any(arg.startswith(("--start-maximized", "--window-position", "--proxy")) for arg in first["args"])
    assert "proxy" not in first and "service_workers" not in first  # Service workers stay on.
    assert engine.status == "running"


def test_it_presents_the_user_agent_of_the_same_browser_with_a_window_and_remembers_it(chromium):
    engine = chromium.engine()
    engine.ensure_started()

    # The headless browser called itself HeadlessChrome: started again once as Chrome.
    assert [launch.get("user_agent") for launch in chromium.launches] == [None, HEADED_UA]
    assert chromium.contexts[0].closed
    engine.close()

    again = chromium.engine()
    again.ensure_started()
    assert [launch.get("user_agent") for launch in chromium.launches[2:]] == [HEADED_UA]


def test_only_the_local_address_guard_intercepts_requests(chromium):
    from row_bot.browser.guard import PATTERNS

    engine = chromium.engine()
    engine.ensure_started()
    context = chromium.contexts[-1]
    enabled = [params for session in context.sessions for method, params in session.sent if method == "Fetch.enable"]

    assert enabled and all(params["patterns"] == [{"urlPattern": pattern} for pattern in PATTERNS]
                           for params in enabled)
    assert all("*" != pattern and not pattern.startswith("*://*") for pattern in PATTERNS)


def test_it_falls_back_once_to_row_bots_own_browser(chromium):
    chromium.fail_channel = True
    engine = chromium.engine()
    engine.ensure_started()

    assert [launch.get("channel") or launch.get("executable_path") for launch in chromium.launches] == [
        "msedge", "C:/rowbot/chrome.exe", "C:/rowbot/chrome.exe"]


def test_without_any_browser_it_says_so_and_installs_nothing(chromium, monkeypatch):
    monkeypatch.setattr(service_module, "_installed_channel", lambda: None)
    monkeypatch.setattr(service_module, "check_managed_browser_runtime",
                        lambda: BrowserRuntimeReadiness(False, "missing", "missing", "", "", "", "", ""))
    engine = chromium.engine()
    with pytest.raises(RuntimeError, match="couldn't start"):
        engine.ensure_started()
    assert chromium.launches == []


def test_a_crash_is_logged_shown_and_restarts_cleanly(chromium, caplog):
    engine = chromium.engine()
    engine.ensure_started()

    async def open_tab(tab):
        return tab.id

    engine.act("chat-a", "Open", open_tab, settle=False)
    states: list[str] = []
    engine.add_listener(lambda state: states.append(state["status"]))

    with caplog.at_level(logging.WARNING, logger="row_bot.browser.service"):
        chromium.contexts[-1].handlers["close"]()
    assert engine.status == "crashed"
    assert "crashed" in states
    assert "stopped unexpectedly" in caplog.text

    engine.restart()
    assert engine.status == "running"
    assert len(chromium.contexts) == 3  # One more launch (the user agent was already known).


def test_closing_row_bot_is_not_a_crash(chromium, caplog):
    engine = chromium.engine()
    engine.ensure_started()
    with caplog.at_level(logging.WARNING, logger="row_bot.browser.service"):
        engine.close()
    assert "stopped unexpectedly" not in caplog.text


def test_stop_ends_a_browser_wait_at_once(chromium):
    engine = chromium.engine()
    engine.ensure_started()
    started = threading.Event()

    async def slow(_tab):
        import asyncio

        started.set()
        await asyncio.sleep(30)
        return "never"

    scope = CancellationScope()
    threading.Timer(0.3, scope.cancel).start()
    began = time.monotonic()
    with use_cancellation_scope(scope):
        with pytest.raises(service_module.BrowserStopped):
            engine.act("chat-a", "Waiting", slow)
    assert started.is_set() and time.monotonic() - began < 5


def test_a_pop_up_belongs_to_the_conversation_whose_page_opened_it(chromium):
    engine = chromium.engine()
    engine.ensure_started()
    context = chromium.contexts[-1]

    async def open_tab(tab):
        return tab.id

    engine.act("chat-a", "Open", open_tab, settle=False)
    opener = engine._chats["chat-a"].active.page
    popup = FakePage(opener=opener)

    async def appear():
        await context.handlers["page"](popup)

    engine._run(appear)
    chat_a = engine.pane_state("chat-a")
    assert [tab["id"] for tab in chat_a["tabs"]] == ["t1", "t2"] and chat_a["tabs"][1]["active"]
    assert engine.pane_state("chat-b")["tabs"] == []

    stranger = FakePage(opener=None)
    engine._run(lambda: context.handlers["page"](stranger))
    assert len(engine.pane_state("chat-a")["tabs"]) == 2


@pytest.mark.parametrize(
    ("folder", "relative", "channel"),
    [
        ("ProgramFiles", ("Google", "Chrome", "Application", "chrome.exe"), "chrome"),
        ("ProgramFiles(x86)", ("Google", "Chrome", "Application", "chrome.exe"), "chrome"),
        ("LOCALAPPDATA", ("Google", "Chrome", "Application", "chrome.exe"), "chrome"),
        ("ProgramFiles(x86)", ("Microsoft", "Edge", "Application", "msedge.exe"), "msedge"),
        ("ProgramFiles", ("Microsoft", "Edge", "Application", "msedge.exe"), "msedge"),
    ],
)
def test_installed_browsers_are_found_wherever_windows_keeps_programs(monkeypatch, tmp_path, folder, relative,
                                                                      channel):
    monkeypatch.delenv("ROW_BOT_BROWSER_CHANNEL", raising=False)
    monkeypatch.setattr(service_module.platform, "system", lambda: "Windows")
    for name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.setenv(name, str(tmp_path / "empty" / name))
    executable = tmp_path / "installed" / folder
    executable.joinpath(*relative[:-1]).mkdir(parents=True)
    executable.joinpath(*relative).write_bytes(b"")
    monkeypatch.setenv(folder, str(executable))

    assert service_module._installed_channel() == channel


def test_signed_in_sites_are_read_from_a_stopped_profile_without_starting_it(chromium, tmp_path):
    import sqlite3

    store = service_module.PROFILE_DIR / "Default" / "Network"
    store.mkdir(parents=True)
    with sqlite3.connect(store / "Cookies") as connection:
        connection.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT)")
        connection.executemany("INSERT INTO cookies VALUES (?, ?, '')", [
            (".homeserve.co.uk", "a"), ("www.homeserve.co.uk", "b"), (".github.com", "c")])
    engine = chromium.engine()

    assert engine.sites() == [{"site": "homeserve.co.uk", "cookies": 2}, {"site": "github.com", "cookies": 1}]
    assert chromium.launches == []


def test_the_page_takes_the_panes_size_so_the_picture_fills_it_1_to_1(chromium, monkeypatch):
    """Pane size → page viewport → picture size; no pane → a desktop window; too narrow → a phone's width."""

    monkeypatch.setattr(service_module, "PANE_GONE_GRACE_SECONDS", 0.0)
    engine = chromium.engine()
    engine.ensure_started()
    context = chromium.contexts[-1]

    async def open_tab(tab):
        return tab.id

    engine.act("chat-a", "Open", open_tab, settle=False)
    tab = engine._chats["chat-a"].active

    def page_size():
        sent = [params for method, params in tab.session.sent if method == "Emulation.setDeviceMetricsOverride"]
        return sent[-1] if sent else None

    def picture_size():
        sent = [params for session in context.sessions for method, params in session.sent
                if method == "Page.startScreencast"]
        return (sent[-1]["maxWidth"], sent[-1]["maxHeight"]) if sent else None

    def settle():
        engine._run(lambda: engine._follow_live("chat-a"))

    desktop = {"width": 1280, "height": 800, "deviceScaleFactor": 1.0, "mobile": False}
    assert page_size() == desktop  # Nobody watches: an ordinary desktop window.

    pane = engine.watch("chat-a", kind="pane", width=420, height=700, scale=2.0)
    settle()
    assert page_size() == {"width": 420, "height": 700, "deviceScaleFactor": 2.0, "mobile": False}
    assert picture_size() == (840, 1400)  # The pane's own pixels: shown 1:1.
    assert engine.pane_state("chat-a")["tabs"][0]["id"] == tab.id

    # Resized (a new connection at the new size): the page follows and the agent is told the width changed.
    wider = engine.watch("chat-a", kind="pane", width=900, height=640, scale=1.0)
    pane.close()
    settle()
    assert page_size() == {"width": 900, "height": 640, "deviceScaleFactor": 1.0, "mobile": False}
    assert picture_size() == (900, 640)
    assert any("900×640" in note for note in engine._chats["chat-a"].notes)

    # Narrower than a phone: the page keeps a phone's width and the picture scales down to the pane.
    narrow = engine.watch("chat-a", kind="pane", width=300, height=500, scale=1.0)
    wider.close()
    settle()
    assert page_size() == {"width": 360, "height": 600, "deviceScaleFactor": 1.0, "mobile": False}

    # A thumbnail doesn't size the page; with the pane gone the page is a desktop window again.
    thumb = engine.watch("chat-a", kind="thumb", width=480)
    narrow.close()
    settle()
    assert page_size() == desktop
    thumb.close()


def test_a_size_the_agent_chose_wins_over_the_pane_until_it_goes_back_to_desktop(chromium):
    engine = chromium.engine()
    engine.ensure_started()

    async def open_tab(tab):
        return tab.id

    engine.act("chat-a", "Open", open_tab, settle=False)
    tab = engine._chats["chat-a"].active
    pane = engine.watch("chat-a", kind="pane", width=500, height=700, scale=1.0)
    engine._run(lambda: engine._follow_live("chat-a"))

    engine.set_viewport("phone", None, "chat-a")
    engine._run(lambda: engine._follow_live("chat-a"))
    sizes = [params for method, params in tab.session.sent if method == "Emulation.setDeviceMetricsOverride"]
    assert sizes[-1]["width"] == 390  # The pane doesn't undo the agent's phone size.

    words = engine.set_viewport("desktop", None, "chat-a")
    sizes = [params for method, params in tab.session.sent if method == "Emulation.setDeviceMetricsOverride"]
    assert sizes[-1] == {"width": 500, "height": 700, "deviceScaleFactor": 1.0, "mobile": False}
    assert words.startswith("The page now shows at the Browser pane's size: 500×700 at 1× scale.")

    words = engine.set_viewport("desktop", 2, "chat-a")
    sizes = [params for method, params in tab.session.sent if method == "Emulation.setDeviceMetricsOverride"]
    assert sizes[-1] == {"width": 1280, "height": 800, "deviceScaleFactor": 2.0, "mobile": False}
    assert words.startswith("The page now shows at desktop size: 1280×800 at 2× scale.")
    pane.close()


def test_while_recording_the_page_keeps_the_recordings_size_whatever_the_pane_does(chromium, tmp_path, monkeypatch):
    """A recording is one fixed 16:9 picture at full size: the pane opening, closing or resizing never changes it."""

    monkeypatch.setattr(service_module, "PANE_GONE_GRACE_SECONDS", 0.0)
    engine = chromium.engine()
    engine.ensure_started()
    context = chromium.contexts[-1]

    async def open_tab(tab):
        tab.page.url = "https://shop.example/orders"
        return tab.id

    engine.act("chat-a", "Open", open_tab, settle=False)
    tab = engine._chats["chat-a"].active

    def page_size():
        return [params for method, params in tab.session.sent if method == "Emulation.setDeviceMetricsOverride"][-1]

    def picture():
        sent = [params for session in context.sessions for method, params in session.sent
                if method == "Page.startScreencast"]
        return sent[-1]["maxWidth"], sent[-1]["maxHeight"], sent[-1]["quality"]

    def settle():
        engine._run(lambda: engine._follow_live("chat-a"))

    narrow = engine.watch("chat-a", kind="pane", width=420, height=1226, scale=1.5)
    settle()
    engine.set_viewport("desktop", 1, "chat-a")  # A desktop size chosen before gives way to the recording's.
    ended: list[str] = []
    recorder = engine.start_recording(tmp_path / "rec.mp4", max_seconds=60, size_limit=10_000_000,
                                      on_end=lambda reason, _video: ended.append(reason), thread_id="chat-a")
    recording = {"width": 1440, "height": 810, "deviceScaleFactor": 4 / 3, "mobile": False}
    assert recorder.size == (1920, 1080)
    assert page_size() == recording
    assert picture() == (1920, 1080, 90), "the recording's own pixels; the pane shows them scaled"

    wide = engine.watch("chat-a", kind="pane", width=900, height=640, scale=1.0)
    narrow.close()
    settle()
    wide.close()
    settle()
    assert page_size() == recording, "the pane resizing or closing never changes a recording's page"

    pane = engine.watch("chat-a", kind="pane", width=500, height=700, scale=1.0)
    engine.stop_recording("chat-a", "agent")
    assert recorder.join(10) and ended == ["agent"]
    settle()
    assert page_size() == {"width": 500, "height": 700, "deviceScaleFactor": 1.0, "mobile": False}, \
        "afterwards the page follows the pane again"
    pane.close()


def test_a_recording_of_a_phone_size_keeps_the_phone_shape(chromium, tmp_path):
    engine = chromium.engine()
    engine.ensure_started()

    async def open_tab(tab):
        tab.page.url = "https://shop.example/"
        return tab.id

    engine.act("chat-a", "Open", open_tab, settle=False)
    engine.set_viewport("phone", None, "chat-a")
    recorder = engine.start_recording(tmp_path / "rec.mp4", max_seconds=60, size_limit=10_000_000,
                                      on_end=lambda *_: None, thread_id="chat-a")
    assert recorder.size == (886, 1920), "390×844 at 3×, at most 1920 tall"
    engine.stop_recording("chat-a", "agent")
    assert recorder.join(10)
