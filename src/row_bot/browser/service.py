"""Row-Bot's browser: Chromium with its own signed-in profile, shown inside the app.

One persistent Chromium context (the person's installed Chrome or Edge, else
Row-Bot's own Chromium) runs without a window of its own. Each conversation
has its own tabs. The Browser pane shows the live page by streaming each
conversation's screencast (CDP ``Page.startScreencast``) and sends the
person's mouse and keys back (CDP ``Input``); the agent reads an
accessibility outline with stable refs and acts through Playwright.

Normal sites work as in a normal browser: no proxy, no request interception
beyond the narrow local-address guard (``browser.guard``), service workers
and the HTTP cache on, no automation tells. Sign-ins persist in the profile.

Playwright's async API runs on one event loop in its own thread, so page
events (pop-ups, dialogs, downloads, guarded requests, frames) are handled as
they happen. Callers on other threads use the blocking methods below; each
waits with the turn's cancellation scope, so Stop ends a wait at once.
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import sys
import threading
import time
from typing import Any, Awaitable, Callable
import uuid

from row_bot.browser import guard as _guard
from row_bot.browser.local_access import (
    GRANT_SOURCES,
    GrantSource,
    LocalAddressGrant,
    address_label,
    is_row_bot_origin,
    local_development_origin,
)
from row_bot.browser.policy import PageFacts, history_url, site_of
from row_bot.browser.recording import PROTECTED_FIELDS, PageRecorder
from row_bot.browser.runtime import (
    check_managed_browser_runtime,
    check_packaged_browser_runtime,
    ensure_profile_engine,
)
from row_bot.browser.snapshot import RefMap, compact
from row_bot.cancellation import current_cancellation_scope
from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

PROFILE_DIR = get_row_bot_data_dir() / "browser_profile"
VIEWPORT = {"width": 1280, "height": 800}
# How long a local address stays allowed in a conversation.
LOCAL_ADDRESS_SECONDS = 60 * 60
# Page sizes people check a design at (CSS pixels, device scale, touch screen).
VIEWPORT_PRESETS: dict[str, tuple[int, int, float, bool]] = {
    "phone": (390, 844, 3.0, True),
    "tablet": (820, 1180, 2.0, True),
    "desktop": (1280, 800, 1.0, False),
}
MAX_FULL_PAGE_CSS = 10_000
MAX_FULL_PAGE_DEVICE = 16_384
MAX_SCREENSHOT_BYTES = 15 * 1024 * 1024
# The live picture: the pane's size is honoured up to this, a thumbnail is small.
MAX_PANE_WIDTH = 1600
THUMB_WIDTH = 480
# While a recording runs the page keeps this size, whatever the pane does: a 16:9 desktop window drawn at
# 1920×1080, so the video fills its frame and text stays sharp when a scene zooms in.
RECORD_METRICS = (1440, 810, 4 / 3)
MAX_RECORD_SIDE = 1920
# The drawn pointer's height in a recording, in the page's CSS pixels.
RECORD_POINTER_CSS = 24
RECORD_QUALITY = 90
# The page follows the Browser pane's size (CSS pixels and device scale), so sites lay out
# for it and the picture shows 1:1. Narrower than this, the page keeps this width and the
# picture scales down; with no pane showing, the page is an ordinary desktop window.
MIN_PANE_CSS_WIDTH = 360
DESKTOP_METRICS = (1280, 800, 1.0)
# A pane that reconnects at a new size (a resize) doesn't flash the desktop layout in between.
PANE_GONE_GRACE_SECONDS = 1.0
ACTION_TIMEOUT_MS = 8_000
# The person and Row-Bot share the page: Row-Bot's next step waits until the person has been still this long.
PERSON_STILL_SECONDS = 2.0
# What the pane's Copy may carry.
COPY_LIMIT = 65_536
NAVIGATION_TIMEOUT_MS = 30_000
MAX_EXTRACT_CHARS = 20_000
STOPPED = "Stopped: the person pressed Stop."
_BLANK_URLS = frozenset({"", "about:blank", "chrome://newtab/", "edge://newtab/"})
# The pane's Copy: the focused field's selected text (never a password field's), else the page's selection.
_SELECTION_SCRIPT = """() => {
  const field = document.activeElement;
  if (field && (field.tagName === 'TEXTAREA' || (field.tagName === 'INPUT' && field.type !== 'password'))) {
    try { return field.value.substring(field.selectionStart, field.selectionEnd); } catch (e) { return ''; }
  }
  if (field && field.tagName === 'INPUT') return '';
  return String(window.getSelection() || '');
}"""
_IDENTITY_FILE = "row-bot-identity.json"
# Fields Row-Bot never types into; the person does, in the pane.
_SECRET_JS = r"""
() => {
  const seen = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const any = (selector) => Array.from(document.querySelectorAll(selector)).some(seen);
  if (any('input[type="password"]')) return 'password';
  if (any('input[autocomplete~="one-time-code"]')) return 'code';
  if (any('input[autocomplete^="cc-"], input[autocomplete*=" cc-"]')) return 'card';
  return '';
}
"""
_TARGET_JS = r"""
(el) => {
  const attr = (name) => String(el.getAttribute(name) || '');
  const type = attr('type').toLowerCase();
  const auto = attr('autocomplete').toLowerCase();
  const words = [attr('name'), el.id || '', attr('aria-label'), attr('placeholder')].join(' ');
  let secret = '';
  if (type === 'password' || /(^|\s)(current|new)-password/.test(auto)) secret = 'password';
  else if (/one-time-code/.test(auto) || /\b(otp|one.?time.?(code|pass))\b/i.test(words)) secret = 'code';
  else if (/(^|\s)cc-(number|csc|exp)/.test(auto) || /\b(card.?number|cvv|cvc|security.?code)\b/i.test(words)) secret = 'card';
  else if (/\b(password|passcode)\b/i.test(words) && ['', 'text'].includes(type)) secret = 'password';
  const form = el.form || el.closest('form');
  const root = form || document;
  const card = !!root.querySelector('input[autocomplete^="cc-"], iframe[src*="stripe" i], iframe[title*="card" i], iframe[title*="payment" i], iframe[name*="card" i]');
  const search = !!(form && (form.getAttribute('role') === 'search' || form.querySelector('input[type=search]') || /search/i.test(form.getAttribute('action') || '')))
    || type === 'search' || el.getAttribute('role') === 'searchbox';
  let submit = '';
  if (form) { const b = form.querySelector('button[type=submit], button:not([type]), input[type=submit]');
    if (b) submit = String(b.innerText || b.value || b.getAttribute('aria-label') || '').trim().slice(0, 160); }
  const text = String((document.body && document.body.innerText) || '').slice(0, 30000);
  const price = /[£$€¥₹]\s?\d|\d\s?(GBP|USD|EUR)\b|\b(excess|deposit|call.?out (fee|charge)|order total|amount due|to pay)\b/i.test(text);
  return {secret, card, search, submit, price, file: type === 'file'};
}
"""
_QUIET_JS = r"""
([quiet, limit]) => new Promise((done) => {
  let timer = setTimeout(finish, quiet);
  const stop = setTimeout(finish, limit);
  const observer = new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(finish, quiet); });
  function finish() { observer.disconnect(); clearTimeout(timer); clearTimeout(stop); done(true); }
  observer.observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
})
"""


class BrowserStopped(Exception):
    """The person pressed Stop while a browser call waited."""


class TargetGone(Exception):
    """A ref the agent used isn't on the page any more."""


def can_start() -> bool:
    """A browser is there to start: the person's Chrome or Edge, or Row-Bot's own (read-only, no launch)."""
    return bool(_installed_channel()) or check_packaged_browser_runtime().ready or check_managed_browser_runtime().ready


def _installed_channel() -> str | None:
    """The person's installed Chrome or Edge, read-only (never launches a probe)."""

    requested = str(os.environ.get("ROW_BOT_BROWSER_CHANNEL") or "").strip().casefold()
    if requested in {"chrome", "msedge"}:
        return requested
    system = platform.system().casefold()
    candidates: tuple[tuple[str, str], ...]
    if system == "windows":
        program_files = os.environ.get("ProgramFiles") or r"C:\Program Files"
        program_files_x86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
        local_app_data = os.environ.get("LOCALAPPDATA") or ""
        candidates = (
            ("chrome", str(Path(program_files, "Google", "Chrome", "Application", "chrome.exe"))),
            ("chrome", str(Path(program_files_x86, "Google", "Chrome", "Application", "chrome.exe"))),
            ("chrome", str(Path(local_app_data, "Google", "Chrome", "Application", "chrome.exe")) if local_app_data else ""),
            ("msedge", str(Path(program_files_x86, "Microsoft", "Edge", "Application", "msedge.exe"))),
            ("msedge", str(Path(program_files, "Microsoft", "Edge", "Application", "msedge.exe"))),
        )
    elif system == "darwin":
        candidates = (
            ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ("msedge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        )
    else:
        candidates = (("chrome", shutil.which("google-chrome") or ""), ("msedge", shutil.which("microsoft-edge") or ""))
    return next((channel for channel, path in candidates if path and Path(path).is_file()), None)


def _system_locale() -> str | None:
    """The computer's language (``en-GB``), as a headed browser would use it."""

    value = ""
    try:
        if sys.platform == "win32":
            import ctypes

            buffer = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, 85):
                value = buffer.value
        else:
            import locale

            value = (locale.getlocale()[0] or os.environ.get("LANG", "")).split(".", 1)[0]
    except Exception:
        value = ""
    value = value.replace("_", "-")
    return value if re.fullmatch(r"[a-z]{2,3}(-[A-Z]{2})?", value) else None


def _active_generation_thread_ids() -> set[str]:
    from row_bot.runtime import executions

    return {str(handle.conversation_id) for handle in executions.generation_registry.active()}


def _cover(image: bytes, boxes: list[Any], *, jpeg: bool) -> bytes:
    """Paint over protected fields (CSS-pixel boxes with their device ratio) in a picture."""

    import io

    from PIL import Image, ImageDraw

    picture = Image.open(io.BytesIO(image)).convert("RGB")
    draw = ImageDraw.Draw(picture)
    for box in boxes:
        try:
            x, y, width, height, ratio = (float(value) for value in box)
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        draw.rectangle(
            [x * ratio - 2, y * ratio - 2, (x + width) * ratio + 2, (y + height) * ratio + 2],
            fill=(38, 40, 46),
        )
    output = io.BytesIO()
    picture.save(output, format="JPEG" if jpeg else "PNG", **({"quality": 75} if jpeg else {"optimize": False}))
    return output.getvalue()


# ── State ─────────────────────────────────────────────────────────────────────


@dataclass
class Frame:
    """One live picture of a conversation's page: a JPEG (base64) and the page's CSS size."""

    seq: int
    data: str
    css_width: float
    css_height: float


@dataclass
class _Tab:
    id: str
    page: Any
    refs: RefMap = field(default_factory=RefMap)
    session: Any = None  # Row-Bot's own CDP session: the guard, history and pictures.
    viewport: tuple[str, float] | None = None  # A size the agent chose (browser_viewport) wins over the pane.
    fitted: tuple[int, int, float] | None = None  # The size (CSS width, height, scale) the page has now.
    crashed: bool = False


@dataclass
class _Chat:
    id: str
    tabs: list[_Tab] = field(default_factory=list)
    active: _Tab | None = None
    person_at: float = 0.0  # When the person last clicked, typed or scrolled in the pane (monotonic).
    acting: str = ""
    waiting: str = ""
    secret: str = ""
    loading: bool = False
    notes: list[str] = field(default_factory=list)
    last_used: float = field(default_factory=time.monotonic)
    sequence: int = 0
    lock: asyncio.Lock | None = None


@dataclass
class _Viewer:
    chat: str
    kind: str  # "pane", "thumb" or "record"
    width: int
    loop: asyncio.AbstractEventLoop | None = None
    event: asyncio.Event | None = None
    on_frame: Callable[[Frame], None] | None = None
    # The pane's own size (CSS width, height, device pixel ratio): the page follows it.
    fit: tuple[int, int, float] | None = None


@dataclass
class _Live:
    viewers: set[int] = field(default_factory=set)
    page: Any = None
    session: Any = None
    params: tuple[int, int, int] = (0, 0, 0)
    frame: Frame | None = None
    seq: int = 0


@dataclass
class ActionResult:
    """What one agent action did, for its tool result."""

    text: str
    url: str = ""
    secret: str = ""


class Watch:
    """One viewer of a conversation's live page (the pane, a thumbnail).

    ``changed`` waits on the caller's own event loop; ``frame`` and ``state``
    read the latest picture and pane state.
    """

    def __init__(self, engine: "BrowserEngine", viewer: _Viewer, key: int) -> None:
        self._engine = engine
        self._viewer = viewer
        self.key = key

    async def changed(self, timeout: float) -> bool:
        event = self._viewer.event
        if event is None:
            return False
        try:
            await asyncio.wait_for(event.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        event.clear()
        return True

    def frame(self) -> Frame | None:
        return self._engine.latest_frame(self._viewer.chat)

    def state(self) -> dict[str, Any]:
        return self._engine.pane_state(self._viewer.chat)

    def close(self) -> None:
        self._engine.unwatch(self)


# ── The engine ────────────────────────────────────────────────────────────────


class BrowserEngine:
    """The one Chromium context Row-Bot owns, and every conversation's tabs in it."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._pw: Any = None
        self._context: Any = None
        self._status = "stopped"  # stopped, starting, running, crashed
        self._closing = False
        self._closed = False
        self._launch_error = ""
        self._start_lock: asyncio.Lock | None = None
        self._chats: dict[str, _Chat] = {}
        self._owners: dict[Any, str] = {}
        self._live: dict[str, _Live] = {}
        self._follow_locks: dict[str, asyncio.Lock] = {}  # One size-and-screencast change at a time per chat.
        self._viewers: dict[int, _Viewer] = {}
        self._viewer_seq = 0
        self._revision = 0
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._grants: dict[str, LocalAddressGrant] = {}
        self._recorders: dict[str, PageRecorder] = {}
        self._wiring: dict[Any, asyncio.Future[None]] = {}
        self._sessions: dict[Any, Any] = {}
        self._titles: dict[int, str] = {}
        self._downloads: dict[int, asyncio.Future[Any]] = {}
        self.browser_tool_calls = 0

    # ---- Threads and the loop -------------------------------------------------------

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._thread is not None and self._thread.is_alive():
                return self._loop
            ready = threading.Event()

            def run() -> None:
                loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                self._start_lock = asyncio.Lock()
                self._follow_locks = {}
                ready.set()
                try:
                    loop.run_forever()
                finally:
                    loop.close()

            self._thread = threading.Thread(target=run, daemon=True, name="row-bot-browser")
            self._thread.start()
            ready.wait(10)
            assert self._loop is not None
            return self._loop

    def _run(self, factory: Callable[[], Awaitable[Any]], *, timeout: float = 120.0, cancellable: bool = True) -> Any:
        """Run one coroutine on the browser's loop and wait for it (Stop ends the wait)."""

        if self._closed:
            raise RuntimeError("Row-Bot's browser is shut down")
        scope = current_cancellation_scope() if cancellable else None
        if scope is not None and scope.is_cancelled():
            raise BrowserStopped()
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(factory(), loop)
        unregister = scope.register(future.cancel, "browser.cancel") if scope is not None else None
        try:
            deadline = time.monotonic() + timeout
            while True:
                if scope is not None and scope.is_cancelled():
                    future.cancel()
                    raise BrowserStopped()
                try:
                    return future.result(timeout=0.1)
                except concurrent.futures.TimeoutError:
                    if time.monotonic() >= deadline:
                        future.cancel()
                        raise TimeoutError("Row-Bot's browser took too long") from None
                except concurrent.futures.CancelledError:
                    raise BrowserStopped() from None
        finally:
            if unregister is not None:
                unregister()

    def _soon(self, coroutine: Awaitable[Any]) -> None:
        """Schedule work on the loop from any thread, without waiting."""

        loop = self._loop
        if loop is None or loop.is_closed():
            close = getattr(coroutine, "close", None)
            if close:
                close()
            return
        asyncio.run_coroutine_threadsafe(coroutine, loop)

    # ---- Launch, crash and restart --------------------------------------------------

    @property
    def status(self) -> str:
        return self._status

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def running(self) -> bool:
        return self._status == "running" and self._context is not None

    def ensure_started(self) -> None:
        """Start Row-Bot's browser if it isn't running (after a crash too)."""

        if self.running:
            return
        self._run(self._start, timeout=90.0, cancellable=False)

    def restart(self) -> None:
        """Start again after a crash (or start, when stopped)."""

        self._run(self._restart, timeout=90.0, cancellable=False)

    async def _restart(self) -> None:
        await self._shutdown_context()
        await self._start()

    async def _start(self) -> None:
        assert self._start_lock is not None
        async with self._start_lock:
            if self._status == "running" and self._context is not None:
                return
            self._set_status("starting")
            try:
                await self._launch()
            except BaseException as exc:
                self._launch_error = str(exc)[:300]
                logger.warning("Row-Bot's browser could not start", exc_info=True)
                self._set_status("crashed" if self._chats else "stopped")
                raise RuntimeError("Row-Bot's browser couldn't start.") from exc
            self._set_status("running")
            logger.info("Row-Bot's browser started")

    def _attempts(self) -> list[dict[str, Any]]:
        attempts: list[dict[str, Any]] = []
        channel = _installed_channel()
        if channel:
            attempts.append({"channel": channel})
        readiness = check_packaged_browser_runtime()
        if not readiness.ready:
            readiness = check_managed_browser_runtime()
        if readiness.ready:
            attempts.append({"executable_path": readiness.executable_path})
        if not attempts:
            raise RuntimeError("No browser is installed: install Chrome or Edge, or Row-Bot's browser in Settings.")
        return attempts

    def _identity(self) -> dict[str, str]:
        try:
            value = json.loads((PROFILE_DIR / _IDENTITY_FILE).read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_identity(self, identity: dict[str, str]) -> None:
        try:
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            (PROFILE_DIR / _IDENTITY_FILE).write_text(json.dumps(identity), encoding="utf-8")
        except OSError:
            logger.debug("Row-Bot's browser identity could not be saved", exc_info=True)

    async def _launch_once(self, attempt: dict[str, Any], user_agent: str | None) -> Any:
        options: dict[str, Any] = {
            "headless": True,
            "viewport": dict(VIEWPORT),
            "accept_downloads": True,
            # Automation tells: Chromium's "controlled by automated software" switch and webdriver flag.
            "ignore_default_args": ["--enable-automation"],
            "args": [
                "--disable-blink-features=AutomationControlled",
                # Room for a sharp picture on a high-density screen: each page still renders at the
                # scale Row-Bot gives it (the pane's, or 1× with no pane), up to 2×.
                "--force-device-scale-factor=2",
                "--disable-domain-reliability",
                "--disable-sync",
                "--no-pings",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            **attempt,
        }
        locale = _system_locale()
        if locale:
            options["locale"] = locale
        if user_agent:
            options["user_agent"] = user_agent
        return await self._pw.chromium.launch_persistent_context(str(PROFILE_DIR), **options)

    async def _launch(self) -> None:
        from playwright.async_api import async_playwright

        ensure_profile_engine(PROFILE_DIR)
        if self._pw is None:
            self._pw = await async_playwright().start()
        last: BaseException | None = None
        identity = self._identity()
        for attempt in self._attempts()[:2]:
            key = "|".join(f"{k}={v}" for k, v in sorted(attempt.items()))
            try:
                context = await self._launch_once(attempt, identity.get(key))
                actual = await self._headed_user_agent(context)
                if actual and actual != identity.get(key):
                    # A headless browser names itself "HeadlessChrome": sites treat that as a robot.
                    # Start again with the user agent of the same browser with a window (once per version).
                    identity[key] = actual
                    self._save_identity(identity)
                    await context.close()
                    context = await self._launch_once(attempt, actual)
                self._context = context
                break
            except BaseException as exc:
                last = exc
                logger.info("A browser launch attempt failed", exc_info=True)
        if self._context is None:
            raise RuntimeError("No browser could be launched.") from last
        self._context.on("page", self._on_page)
        self._context.on("close", self._on_context_closed)
        for page in list(self._context.pages):
            await self._adopt(page, owner=None)

    async def _headed_user_agent(self, context: Any) -> str:
        page = context.pages[0] if context.pages else await context.new_page()
        session = await context.new_cdp_session(page)
        try:
            version = await session.send("Browser.getVersion")
        finally:
            try:
                await session.detach()
            except Exception:
                pass
        return str(version.get("userAgent") or "").replace("HeadlessChrome/", "Chrome/")

    def _on_context_closed(self, *_args: Any) -> None:
        if self._closing:
            return
        logger.warning("Row-Bot's browser stopped unexpectedly")
        self._context = None
        for chat in self._chats.values():
            if chat.tabs:
                chat.notes.append("Row-Bot's browser had stopped and started again: open the page again.")
        self._forget_pages()
        self._set_status("crashed")

    def _forget_pages(self) -> None:
        self._owners.clear()
        self._wiring.clear()
        self._sessions.clear()
        for chat in self._chats.values():
            chat.tabs.clear()
            chat.active = None
            chat.acting = ""
            chat.secret = ""
            chat.loading = False
        for live in self._live.values():
            live.page = None
            live.session = None
            live.params = (0, 0, 0)

    async def _shutdown_context(self) -> None:
        self._closing = True
        try:
            if self._context is not None:
                try:
                    await self._context.close()
                except Exception:
                    pass
            self._context = None
            self._forget_pages()
        finally:
            self._closing = False

    def _set_status(self, status: str) -> None:
        self._status = status
        for chat in list(self._chats):
            self._publish(chat)
        for chat in list(self._live):
            if chat not in self._chats:
                self._notify(chat)

    # ---- Pages and tabs -------------------------------------------------------------

    def _chat(self, chat_id: str) -> _Chat:
        chat_id = str(chat_id or "default")
        chat = self._chats.get(chat_id)
        if chat is None:
            chat = self._chats[chat_id] = _Chat(chat_id)
        if chat.lock is None and self._loop is not None:
            chat.lock = asyncio.Lock()
        return chat

    async def _on_page(self, page: Any) -> None:
        owner = None
        try:
            opener = await page.opener()
            owner = self._owners.get(opener) if opener is not None else None
        except Exception:
            owner = None
        await self._adopt(page, owner=owner)
        if owner:
            chat = self._chat(owner)
            tab = self._tab_of(page)
            if tab is not None:
                chat.active = tab
                chat.notes.append(f"A new tab opened ({tab.id}) and is now the active one.")
                await self._follow_live(owner)
                self._publish(owner)

    async def _adopt(self, page: Any, *, owner: str | None) -> _Tab | None:
        """Watch one page: guard it, answer its dialogs, keep its downloads, and give it to *owner*.

        A page is wired once, whichever comes first (the context's page event or
        Row-Bot opening it), and nobody gets the page before its guard is on.
        Its owner may become known second.
        """

        wiring = self._wiring.get(page)
        if wiring is None:
            wiring = self._wiring[page] = asyncio.ensure_future(self._wire(page))
        await asyncio.shield(wiring)
        owner = owner or self._owners.get(page)
        if owner is None:
            return None
        known = self._tab_of(page)
        if known is not None:
            return known
        tab = self._give(page, owner, self._sessions.get(page))
        url = str(page.url or "")
        if url not in _BLANK_URLS and not _guard.allowed(url, owner, self._granted):
            # A pop-up that started loading before its guard was on: it may not stay there.
            try:
                await page.goto("about:blank")
            except Exception:
                pass
            self._chat(owner).notes.append("A pop-up tried to open a local address; it was kept blank.")
        return tab

    async def _wire(self, page: Any) -> None:
        session = None
        try:
            session = await self._context.new_cdp_session(page)
            await _guard.PageGuard(
                session,
                owner=lambda: self._owners.get(page),
                granted=self._granted,
                on_blocked=lambda url, document: self._blocked(page, url, document),
            ).start()
        except Exception:
            logger.debug("A page could not be guarded", exc_info=True)
        self._sessions[page] = session
        page.on("dialog", self._on_dialog)
        page.on("download", self._on_download)
        page.on("crash", lambda *_: self._on_crash(page))
        page.on("close", lambda *_: self._on_close(page))
        page.on("framenavigated", lambda frame: self._on_navigated(page, frame))
        page.on("load", lambda *_: self._soon_check(page))

    def _give(self, page: Any, owner: str, session: Any) -> _Tab:
        """Make an adopted page one of *owner*'s tabs."""

        self._owners[page] = owner
        existing = self._tab_of(page)
        if existing is not None:
            return existing
        chat = self._chat(owner)
        chat.sequence += 1
        tab = _Tab(f"t{chat.sequence}", page, session=session)
        chat.tabs.append(tab)
        return tab

    def _tab_of(self, page: Any) -> _Tab | None:
        owner = self._owners.get(page)
        chat = self._chats.get(owner) if owner else None
        return next((tab for tab in chat.tabs if tab.page is page), None) if chat else None

    async def _blocked(self, page: Any, url: str, document: bool) -> None:
        owner = self._owners.get(page)
        if owner and document:
            origin = local_development_origin(url)
            where = address_label(origin) if origin else "an address on your local network"
            self._chat(owner).notes.append(
                f"Blocked: the page tried to open {where}, which this chat hasn't allowed."
            )

    async def _on_dialog(self, dialog: Any) -> None:
        page = getattr(dialog, "page", None)
        owner = self._owners.get(page) if page is not None else None
        kind = str(getattr(dialog, "type", "") or "")
        message = str(getattr(dialog, "message", "") or "")[:200]
        try:
            if kind == "prompt":
                await dialog.dismiss()
                answer = "Row-Bot closed it (it doesn't answer prompts)"
            else:
                await dialog.accept()
                answer = "Row-Bot answered OK"
        except Exception:
            return
        if owner:
            self._chat(owner).notes.append(f'The page asked: "{message}". {answer}.')

    def _on_download(self, download: Any) -> None:
        page = getattr(download, "page", None)
        owner = self._owners.get(page) if page is not None else None
        if owner:
            self._downloads[id(download)] = asyncio.ensure_future(self._keep_download(download, owner))

    async def finish_download(self, download: Any) -> None:
        """Wait until a download the agent started is kept in its chat's files."""

        task = self._downloads.pop(id(download), None)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 120)

    async def _keep_download(self, download: Any, owner: str) -> None:
        name = re.sub(r'[\\/:*?"<>|\r\n\x00]', "_", str(download.suggested_filename or "download"))[:200] or "download"
        try:
            failure = await download.failure()
            if failure:
                self._chat(owner).notes.append(f"The download of {name} failed.")
                return
            path = await download.path()
            data = Path(path).read_bytes() if path else b""
        except Exception:
            self._chat(owner).notes.append(f"The download of {name} failed.")
            return
        try:
            from row_bot.application.attachments import register_attachment
            from row_bot.file_deliveries import size_words

            register_attachment(owner, name, data)
            self._chat(owner).notes.append(f"Downloaded {name} ({size_words(len(data))}) to this chat's files.")
        except Exception:
            logger.info("A browser download could not be kept", exc_info=True)
            self._chat(owner).notes.append(f"Downloaded {name}, but it couldn't be kept in this chat's files.")

    def _on_crash(self, page: Any) -> None:
        tab = self._tab_of(page)
        if tab is not None:
            tab.crashed = True
            logger.warning("A tab in Row-Bot's browser crashed")
            owner = self._owners.get(page)
            if owner:
                self._chat(owner).notes.append("The page crashed; Row-Bot reloads it on the next step.")
                self._publish(owner)

    def _on_close(self, page: Any) -> None:
        self._wiring.pop(page, None)
        self._sessions.pop(page, None)
        self._titles.pop(id(page), None)
        owner = self._owners.pop(page, None)
        if not owner or owner not in self._chats:
            return
        chat = self._chats[owner]
        chat.tabs = [tab for tab in chat.tabs if tab.page is not page]
        if chat.active is not None and chat.active.page is page:
            chat.active = chat.tabs[-1] if chat.tabs else None
        self._soon(self._follow_live(owner))
        self._publish(owner)

    def _on_navigated(self, page: Any, frame: Any) -> None:
        if getattr(frame, "parent_frame", None) is not None:
            return
        owner = self._owners.get(page)
        if owner:
            self._publish(owner)

    def _soon_check(self, page: Any) -> None:
        owner = self._owners.get(page)
        if owner:
            self._soon(self._check_secret(owner))

    async def _check_secret(self, chat_id: str) -> str:
        chat = self._chat(chat_id)
        tab = chat.active
        found = ""
        if tab is not None and not tab.crashed:
            for frame in list(tab.page.frames):
                try:
                    found = str(await frame.evaluate(_SECRET_JS) or "")
                except Exception:
                    found = ""
                if found:
                    break
        if found != chat.secret:
            chat.secret = found
            self._publish(chat_id)
        return found

    async def _new_tab(self, chat: _Chat) -> _Tab:
        page = await self._context.new_page()
        tab = await self._adopt(page, owner=chat.id)
        assert tab is not None
        chat.active = tab
        return tab

    async def _agent_tab(self, chat: _Chat) -> _Tab:
        if self._context is None:
            await self._start()
        tab = chat.active
        if tab is None or tab.page.is_closed():
            tab = await self._new_tab(chat)
            await self._follow_live(chat.id)
        if tab.crashed:
            tab.crashed = False
            try:
                await tab.page.reload(wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
            except Exception:
                pass
        return tab

    # ---- Local addresses (F4) -------------------------------------------------------

    def _granted(self, chat_id: str, origin: str) -> bool:
        now = time.time()
        with self._lock:
            for key in [key for key, grant in self._grants.items() if grant.expires_at <= now]:
                self._grants.pop(key, None)
            return any(g.conversation_id == chat_id and g.origin == origin for g in self._grants.values())

    def allow_local_address(self, conversation_id: str, origin: str, *, source: GrantSource) -> LocalAddressGrant:
        """Let one conversation's pages open one address on this computer or the person's network, for an hour.

        It covers the server's pages and its live-reload socket (same host and
        port); a new grant for the same address replaces the old one. Row-Bot's
        own app and cloud metadata addresses are never granted.
        """

        conversation = str(conversation_id or "").strip()
        canonical = local_development_origin(origin)
        if not conversation or canonical is None or canonical != origin:
            raise ValueError("an exact local address and a conversation are required")
        if is_row_bot_origin(canonical):
            raise ValueError("Row-Bot's own app cannot be opened in its browser")
        if source not in GRANT_SOURCES:
            raise ValueError("unknown grant source")
        now = time.time()
        grant = LocalAddressGrant(
            id=uuid.uuid4().hex, conversation_id=conversation, origin=canonical, address=address_label(canonical),
            source=source, granted_at=now, expires_at=now + LOCAL_ADDRESS_SECONDS,
        )
        with self._lock:
            for key in [k for k, g in self._grants.items() if g.conversation_id == conversation and g.origin == canonical]:
                self._grants.pop(key, None)
            self._grants[grant.id] = grant
        logger.info("Local address allowed in one conversation (%s)", source)
        return grant

    def local_address_grants(self, conversation_id: str) -> list[LocalAddressGrant]:
        conversation = str(conversation_id or "")
        self._granted(conversation, "")  # Drops expired grants.
        with self._lock:
            return sorted((g for g in self._grants.values() if g.conversation_id == conversation),
                          key=lambda grant: grant.granted_at)

    def local_address_grant(self, conversation_id: str, origin: str) -> LocalAddressGrant | None:
        return next((g for g in self.local_address_grants(conversation_id) if g.origin == origin), None)

    def revoke_local_address(self, conversation_id: str, grant_id: str) -> bool:
        with self._lock:
            grant = self._grants.get(str(grant_id or ""))
            if grant is None or grant.conversation_id != str(conversation_id or ""):
                return False
            self._grants.pop(grant.id, None)
        return True

    def revoke_local_addresses(self, conversation_id: str) -> int:
        with self._lock:
            keys = [k for k, g in self._grants.items() if g.conversation_id == str(conversation_id)]
            for key in keys:
                self._grants.pop(key, None)
        return len(keys)

    # ---- State the pane shows -------------------------------------------------------

    def add_listener(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(callback)

        def remove() -> None:
            with self._lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)

        return remove

    add_activity_listener = add_listener

    def _publish(self, chat_id: str) -> None:
        with self._lock:
            self._revision += 1
            listeners = list(self._listeners)
        state = self.pane_state(chat_id)
        for callback in listeners:
            try:
                callback(dict(state))
            except Exception:
                logger.debug("A browser listener failed", exc_info=True)
        self._notify(chat_id)

    def _notify(self, chat_id: str) -> None:
        for viewer in [v for v in list(self._viewers.values()) if v.chat == chat_id]:
            if viewer.loop is not None and viewer.event is not None:
                try:
                    viewer.loop.call_soon_threadsafe(viewer.event.set)
                except RuntimeError:
                    pass

    def pane_state(self, chat_id: str) -> dict[str, Any]:
        """What the Browser pane shows for one conversation (never page content or field values)."""

        chat = self._chats.get(str(chat_id or "default"))
        tabs: list[dict[str, Any]] = []
        active = chat.active if chat else None
        for tab in (list(chat.tabs) if chat else []):
            try:
                url = str(tab.page.url or "")
            except Exception:
                url = ""
            tabs.append({"id": tab.id, "url": url[:2048], "title": self._title_cache(tab)[:160],
                         "active": tab is active})
        url = next((tab["url"] for tab in tabs if tab["active"]), "")
        title = next((tab["title"] for tab in tabs if tab["active"]), "")
        status = self._status
        return {
            "status": status,
            "acting": (chat.acting if chat else "")[:160],
            "waiting": (chat.waiting if chat else "")[:160],
            "url": "" if url in _BLANK_URLS else url,
            "title": title,
            "site": site_of(url) if url not in _BLANK_URLS else "",
            "loading": bool(chat and chat.loading),
            "sign_in": (chat.secret if chat else "") or "",
            "tabs": [tab for tab in tabs],
            "recording": bool(chat and chat.id in self._recorders),
            "revision": self._revision,
        }

    status_snapshot = pane_state

    def _title_cache(self, tab: _Tab) -> str:
        return self._titles.get(id(tab.page), "")

    async def _refresh_titles(self, chat: _Chat) -> None:
        for tab in list(chat.tabs):
            try:
                self._titles[id(tab.page)] = str(await tab.page.title() or "")
            except Exception:
                pass

    def has_activity(self, chat_id: str) -> bool:
        chat = self._chats.get(str(chat_id))
        return bool(chat and chat.tabs)

    def current_url(self, chat_id: str = "default") -> str:
        chat = self._chats.get(str(chat_id))
        try:
            return str(chat.active.page.url or "") if chat and chat.active else ""
        except Exception:
            return ""

    # ---- The live picture -----------------------------------------------------------

    def watch(self, chat_id: str, *, kind: str, width: int, loop: asyncio.AbstractEventLoop | None = None,
              on_frame: Callable[[Frame], None] | None = None, height: int = 0, scale: float = 1.0) -> Watch:
        """Follow a conversation's live page. Passive: it never starts the browser.

        A pane gives its own size (*width* × *height* CSS pixels at *scale*): while it
        shows, the page lays out at that size and the picture is 1:1.
        """

        event = asyncio.Event() if loop is not None else None
        fit = None
        if kind == "pane" and height:
            fit = (max(160, min(int(width), 4000)), max(160, min(int(height), 4000)),
                   max(1.0, min(float(scale or 1.0), 3.0)))
        pixels = round(fit[0] * fit[2]) if fit else int(width or THUMB_WIDTH)
        viewer = _Viewer(str(chat_id), kind, max(160, min(pixels, MAX_PANE_WIDTH)), loop, event, on_frame, fit)
        with self._lock:
            self._viewer_seq += 1
            key = self._viewer_seq
            self._viewers[key] = viewer
            self._live.setdefault(viewer.chat, _Live()).viewers.add(key)
        if self.running:
            self._soon(self._follow_live(viewer.chat))
        if event is not None:
            loop.call_soon_threadsafe(event.set)  # The current picture and state at once.
        return Watch(self, viewer, key)

    def unwatch(self, watch: Watch) -> None:
        with self._lock:
            viewer = self._viewers.pop(watch.key, None)
            if viewer is None:
                return
            live = self._live.get(viewer.chat)
            if live is not None:
                live.viewers.discard(watch.key)
        if self.running:
            self._soon(self._follow_live(viewer.chat, after=PANE_GONE_GRACE_SECONDS if viewer.fit else 0.0))

    def latest_frame(self, chat_id: str) -> Frame | None:
        live = self._live.get(str(chat_id))
        return live.frame if live else None

    def _pane_fit(self, chat_id: str) -> tuple[int, int, float] | None:
        """The newest pane's size for a conversation (None: no pane shows it)."""

        with self._lock:
            panes = [(key, viewer.fit) for key, viewer in self._viewers.items()
                     if viewer.chat == chat_id and viewer.fit is not None]
        return max(panes)[1] if panes else None

    def page_metrics(self, chat_id: str) -> tuple[int, int, float]:
        """The page size for a conversation: a recording's while one runs, its pane's (CSS width, height,
        scale), else a desktop window."""

        if chat_id in self._recorders:
            return RECORD_METRICS
        fit = self._pane_fit(chat_id)
        if fit is None:
            return DESKTOP_METRICS
        width, height, scale = fit
        if width < MIN_PANE_CSS_WIDTH:
            # Too narrow to lay out a site: keep a phone's width and let the picture scale down.
            height = round(height * MIN_PANE_CSS_WIDTH / width)
            width = MIN_PANE_CSS_WIDTH
        height = max(240, min(height, 2400))
        scale = max(1.0, min(scale, 2.0, MAX_PANE_WIDTH / width))
        return width, height, round(scale, 2)

    async def _fit(self, chat: _Chat) -> None:
        """Give the active tab the size the person sees it at (or a desktop window when nobody does)."""

        tab = chat.active
        if tab is None or tab.viewport is not None or self._context is None:
            return
        wanted = self.page_metrics(chat.id)
        if tab.fitted == wanted:
            return
        try:
            tab.session = tab.session or await self._context.new_cdp_session(tab.page)
            width, height, scale = wanted
            await tab.session.send("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": scale, "mobile": False})
        except Exception:
            logger.debug("The page could not take the pane's size", exc_info=True)
            return
        if tab.fitted is not None and tab.fitted[0] != wanted[0]:
            chat.notes.append(f"The page is now {wanted[0]}×{wanted[1]}, {self._where(chat.id, wanted)}; "
                              "its layout may have changed.")
        tab.fitted = wanted

    def _where(self, chat_id: str, size: tuple[int, int, float]) -> str:
        if chat_id in self._recorders and size == RECORD_METRICS:
            return "the recording's size"
        return "a desktop window's size" if size == DESKTOP_METRICS else "the Browser pane's size"

    async def _follow_live(self, chat_id: str, after: float = 0.0) -> None:
        """Size the active tab for whoever watches, and run its screencast while someone does."""

        if after:
            await asyncio.sleep(after)
        # Calls overlap (a pane reconnecting while a tab opens): one at a time, so one screencast runs.
        async with self._follow_locks.setdefault(chat_id, asyncio.Lock()):
            await self._follow(chat_id)

    async def _follow(self, chat_id: str) -> None:
        chat = self._chats.get(chat_id)
        if chat is not None and self._context is not None:
            await self._fit(chat)
        live = self._live.get(chat_id)
        if live is None:
            return
        viewers = [self._viewers[key] for key in list(live.viewers) if key in self._viewers]
        page = chat.active.page if chat and chat.active and self._context is not None else None
        if not viewers or page is None:
            await self._stop_screencast(live)
            return
        quality = 70 if any(v.kind != "thumb" for v in viewers) else 55
        recorder = self._recorders.get(chat_id)
        if recorder is not None and recorder.size is not None:
            # The recording's own pixels, whoever else watches: the pane shows them scaled.
            (width, height), quality = recorder.size, RECORD_QUALITY
        elif any(viewer.fit is not None for viewer in viewers) and chat.active.fitted is not None:
            # 1:1 with the pane: the page's own pixels, at most the cap.
            css_width, css_height, scale = chat.active.fitted
            width = min(MAX_PANE_WIDTH, round(css_width * scale))
            height = round(css_height * width / css_width)
        else:
            width = max(viewer.width for viewer in viewers)
            height = max(160, width * 3 // 4)
        params = (width, height, quality)
        if live.page is page and live.params == params and live.session is not None:
            return
        await self._stop_screencast(live)
        try:
            session = await self._context.new_cdp_session(page)
            session.on("Page.screencastFrame", lambda event: self._on_frame(chat_id, live, session, event))
            await session.send("Page.startScreencast", {
                "format": "jpeg", "quality": quality, "maxWidth": width, "maxHeight": height,
                "everyNthFrame": 1,
            })
        except Exception:
            logger.debug("The live picture could not start", exc_info=True)
            return
        live.page, live.session, live.params = page, session, params

    async def _stop_screencast(self, live: _Live) -> None:
        session, live.session, live.page, live.params = live.session, None, None, (0, 0, 0)
        if session is None:
            return
        try:
            await session.send("Page.stopScreencast")
        except Exception:
            pass
        try:
            await session.detach()
        except Exception:
            pass

    def _on_frame(self, chat_id: str, live: _Live, session: Any, event: dict[str, Any]) -> None:
        self._soon(self._ack(session, event.get("sessionId")))
        if live.session is not session:
            return
        metadata = event.get("metadata") or {}
        live.seq += 1
        frame = Frame(live.seq, str(event.get("data") or ""), float(metadata.get("deviceWidth") or 0),
                      float(metadata.get("deviceHeight") or 0))
        live.frame = frame
        recorder = self._recorders.get(chat_id)
        if recorder is not None:
            chat = self._chats.get(chat_id)
            recorder.shielded = bool(chat and (chat.secret or self._person_active(chat)))
            try:
                recorder.offer_frame(base64.b64decode(frame.data))
            except Exception:
                pass
        for viewer in [self._viewers.get(key) for key in list(live.viewers)]:
            if viewer is None:
                continue
            if viewer.on_frame is not None:
                try:
                    viewer.on_frame(frame)
                except Exception:
                    pass
        self._notify(chat_id)

    @staticmethod
    async def _ack(session: Any, screencast_session: Any) -> None:
        try:
            await session.send("Page.screencastFrameAck", {"sessionId": screencast_session})
        except Exception:
            pass

    # ---- The person in the pane -----------------------------------------------------

    @staticmethod
    def _person_active(chat: _Chat) -> bool:
        return time.monotonic() - chat.person_at < PERSON_STILL_SECONDS

    def person_still_for(self, chat_id: str) -> float:
        """Seconds since the person last clicked, typed or scrolled in this chat's page (a large number if never)."""

        chat = self._chats.get(str(chat_id))
        return time.monotonic() - chat.person_at if chat and chat.person_at else float("inf")

    def person_input(self, chat_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Play the person's mouse, wheel and keys on the active tab; the person is never refused.

        The page is shared: a click, key, scroll or paste here makes Row-Bot's next step wait until the person is
        still (``PERSON_STILL_SECONDS``); hovering never does. A ``copy`` event returns the page's selection.
        Returns ``{"status": "ok" | "no_page", "copied": text}``.
        """

        chat = self._chat(chat_id)
        if not self.running or chat.active is None:
            return {"status": "no_page", "copied": ""}
        if any(not (event.get("type") == "mouse" and event.get("action") == "move") for event in events):
            chat.person_at = time.monotonic()
        return self._run(lambda: self._play(chat, events), timeout=15.0, cancellable=False)

    async def _play(self, chat: _Chat, events: list[dict[str, Any]]) -> str:
        tab = chat.active
        if tab is None:
            return "no_page"
        page = tab.page
        copied = ""
        for event in events:
            kind = event.get("type")
            try:
                if kind == "copy":
                    copied = str(await page.evaluate(_SELECTION_SCRIPT) or "")[:COPY_LIMIT]
                    continue
                if kind == "mouse":
                    x, y = float(event.get("x", 0)), float(event.get("y", 0))
                    action = event.get("action")
                    button = event.get("button", "left")
                    if action == "move":
                        await page.mouse.move(x, y)
                    elif action == "down":
                        await page.mouse.move(x, y)
                        await page.mouse.down(button=button, click_count=int(event.get("clicks") or 1))
                    elif action == "up":
                        await page.mouse.move(x, y)
                        await page.mouse.up(button=button, click_count=int(event.get("clicks") or 1))
                    elif action == "wheel":
                        await page.mouse.move(x, y)
                        await page.mouse.wheel(float(event.get("dx", 0)), float(event.get("dy", 0)))
                elif kind == "key":
                    key = str(event.get("key") or "")
                    if event.get("action") == "down":
                        await page.keyboard.down(key)
                    else:
                        await page.keyboard.up(key)
                elif kind == "text":
                    await page.keyboard.insert_text(str(event.get("text") or ""))
            except Exception:
                # An unknown key name, or the page moved on: the next event still plays.
                logger.debug("A pane input event was skipped", exc_info=True)
        chat.last_used = time.monotonic()
        return {"status": "ok", "copied": copied}

    def person_action(self, chat_id: str, action: str, *, url: str = "", tab: str = "") -> None:
        """The pane's own controls: address bar, Back, Forward, Reload, tabs and Restart."""

        if action == "restart":
            self.restart()
            return
        if action == "stop_loading":
            self._run(lambda: self._person_page_action(chat_id, "stop_loading", url, tab), timeout=10.0,
                      cancellable=False)
            return
        if not self.running:
            self.ensure_started()
        self._run(lambda: self._person_page_action(chat_id, action, url, tab), timeout=45.0, cancellable=False)

    async def _person_page_action(self, chat_id: str, action: str, url: str, tab_id: str) -> None:
        chat = self._chat(chat_id)
        if action == "new_tab":
            await self._new_tab(chat)
            await self._follow_live(chat.id)
        elif action in {"switch_tab", "close_tab"}:
            selected = next((tab for tab in chat.tabs if tab.id == tab_id), None)
            if selected is None:
                return
            if action == "switch_tab":
                chat.active = selected
                await self._follow_live(chat.id)
            else:
                await selected.page.close()
        else:
            tab = await self._agent_tab(chat)
            page = tab.page
            chat.loading = True
            self._publish(chat.id)
            try:
                if action == "navigate":
                    await page.goto(url, wait_until="commit", timeout=NAVIGATION_TIMEOUT_MS)
                elif action == "back":
                    await page.go_back(wait_until="commit", timeout=NAVIGATION_TIMEOUT_MS)
                elif action == "forward":
                    await page.go_forward(wait_until="commit", timeout=NAVIGATION_TIMEOUT_MS)
                elif action == "reload":
                    await page.reload(wait_until="commit", timeout=NAVIGATION_TIMEOUT_MS)
                elif action == "stop_loading" and tab.session is not None:
                    await tab.session.send("Page.stopLoading")
            except Exception:
                logger.debug("A pane navigation didn't finish", exc_info=True)
            finally:
                self._soon(self._finish_loading(chat))
        await self._refresh_titles(chat)
        self._publish(chat.id)

    async def _finish_loading(self, chat: _Chat) -> None:
        tab = chat.active
        if tab is not None:
            try:
                await tab.page.wait_for_load_state("domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
            except Exception:
                pass
        chat.loading = False
        await self._refresh_titles(chat)
        await self._check_secret(chat.id)
        self._publish(chat.id)

    # ---- The agent's actions --------------------------------------------------------

    def set_waiting(self, chat_id: str, waiting: str) -> None:
        """Show what the agent waits on in the pane ("Waiting for your OK in the chat")."""

        chat = self._chat(chat_id)
        chat.waiting = str(waiting or "")
        self._publish(chat.id)

    def act(self, chat_id: str, label: str, step: Callable[[_Tab], Awaitable[str]], *, settle: bool = True,
            timeout: float = 120.0) -> ActionResult:
        """Run one agent action on the conversation's active tab and report the page after it."""

        self.browser_tool_calls += 1
        if not self.running:
            self.ensure_started()
        return self._run(lambda: self._act(chat_id, label, step, settle), timeout=timeout)

    async def _act(self, chat_id: str, label: str, step: Callable[[_Tab], Awaitable[str]], settle: bool) -> ActionResult:
        chat = self._chat(chat_id)
        assert chat.lock is not None
        async with chat.lock:
            chat.acting = label
            chat.waiting = ""
            chat.last_used = time.monotonic()
            self._publish(chat.id)
            try:
                tab = await self._agent_tab(chat)
                try:
                    said = await step(tab)
                except TargetGone as gone:
                    said = str(gone)
                if settle:
                    await self._settle(chat)
                return await self._report(chat, said)
            finally:
                chat.acting = ""
                await self._refresh_titles(chat)
                self._publish(chat.id)

    async def _settle(self, chat: _Chat) -> None:
        tab = chat.active
        if tab is None:
            return
        page = tab.page
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
        except Exception:
            pass
        try:
            await page.evaluate(_QUIET_JS, [300, 2500])
        except Exception:
            pass
        await self._follow_live(chat.id)

    async def _outline(self, tab: _Tab) -> tuple[str, int]:
        try:
            raw = await tab.page.locator(":root").aria_snapshot(mode="ai", timeout=ACTION_TIMEOUT_MS)
        except Exception:
            raw = ""
        return compact(raw, tab.refs)

    async def _report(self, chat: _Chat, said: str) -> ActionResult:
        tab = chat.active
        if tab is None:
            return ActionResult(f"{said}\nNo page is open.".strip())
        page = tab.page
        outline, _count = await self._outline(tab)
        try:
            title = str(await page.title() or "")[:160]
        except Exception:
            title = ""
        url = str(page.url or "")
        secret = await self._check_secret(chat.id)
        lines = [said] if said else []
        lines.extend(dict.fromkeys(chat.notes))
        chat.notes.clear()
        if len(chat.tabs) > 1:
            lines.append("Tabs: " + ", ".join(
                f"{t.id}{' (active)' if t is tab else ''} {self._title_cache(t)[:40] or site_of(str(t.page.url))}"
                for t in chat.tabs))
        if secret == "password":
            lines.append("This page asks for a password. Row-Bot never types passwords: ask the person "
                         "(ask_person) to sign in here in the browser pane, then carry on.")
        elif secret == "code":
            lines.append("This page asks for a one-time code. The person types it in the browser pane: ask them "
                         "(ask_person), then carry on.")
        elif secret == "card":
            lines.append("This page asks for card details. Row-Bot never types card numbers: the person enters "
                         "them in the browser pane.")
        where = history_url(url) if url not in _BLANK_URLS else "a blank tab"
        lines.append(f"Page: {title or site_of(url) or 'Untitled'} ({where})")
        lines.append(outline or "(The page shows nothing to act on yet.)")
        return ActionResult("\n".join(line for line in lines if line), url=url, secret=secret)

    async def locate(self, tab: _Tab, ref: str) -> Any:
        """The page element for one of the agent's refs, re-found by what it is if the page re-rendered."""

        await self._outline(tab)
        page_ref = tab.refs.page_ref(ref)
        if page_ref is None:
            known = tab.refs.describe(ref)
            what = f'{known[0]} "{known[1]}"' if known and known[1] else (known[0] if known else f"ref {ref}")
            raise TargetGone(f"{what} isn't on the page now. Here is the page as it is:")
        return tab.page.locator(f"aria-ref={page_ref}")

    def inspect(self, chat_id: str, ref: str) -> dict[str, Any] | None:
        """What a ref names and the page around it, for approvals and the password rule (None: gone)."""

        if not self.running:
            self.ensure_started()

        async def run() -> dict[str, Any] | None:
            chat = self._chat(chat_id)
            tab = await self._agent_tab(chat)
            try:
                locator = await self.locate(tab, ref)
                facts = await locator.evaluate(_TARGET_JS, timeout=ACTION_TIMEOUT_MS)
            except Exception:
                return None
            known = tab.refs.describe(ref) or ("", "")
            return {"role": known[0], "name": known[1], "url": str(tab.page.url or ""), **dict(facts or {})}

        return self._run(run, timeout=30.0)

    def note_secret(self, chat_id: str, kind: str) -> None:
        """The agent met a password, code or card field: the pane says "Sign in here"."""

        chat = self._chat(chat_id)
        chat.secret = str(kind or "")
        self._publish(chat.id)

    async def tab_step(self, chat_id: str, action: str, tab_id: str | None, url: str | None) -> str:
        """``browser_tabs``: runs inside an agent action (the chat's lock is held)."""

        chat = self._chat(chat_id)
        if action == "list":
            await self._refresh_titles(chat)
            return "Tabs: " + ", ".join(
                f"{tab.id}{' (active)' if tab is chat.active else ''} {self._title_cache(tab)[:60] or 'New tab'}"
                for tab in chat.tabs)
        if action == "new":
            tab = await self._new_tab(chat)
            await self._follow_live(chat.id)
            if url:
                try:
                    await tab.page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
                except Exception:
                    return f"Opened a new tab ({tab.id}); {history_url(url)} didn't load."
            return f"Opened a new tab ({tab.id})."
        selected = next((tab for tab in chat.tabs if tab.id == str(tab_id or "")), None)
        if selected is None:
            return "There's no tab " + str(tab_id or "") + ". Tabs: " + ", ".join(tab.id for tab in chat.tabs)
        if action == "switch":
            chat.active = selected
            await self._follow_live(chat.id)
            return f"Switched to {selected.id}."
        if action == "close":
            if len(chat.tabs) <= 1:
                return "That's the only tab; it stays open."
            await selected.page.close()
            return f"Closed {selected.id}."
        return "Use list, switch, new or close."

    def extract(self, chat_id: str, ref: str | None = None) -> str:
        """The text of the conversation's page (or one element), bounded."""

        if not self.running:
            self.ensure_started()

        async def run() -> str:
            chat = self._chat(chat_id)
            assert chat.lock is not None
            async with chat.lock:
                tab = await self._agent_tab(chat)
                if ref:
                    try:
                        locator = await self.locate(tab, ref)
                        text = str(await locator.inner_text(timeout=ACTION_TIMEOUT_MS))
                    except TargetGone as gone:
                        return str(gone).split(".")[0] + "."
                    except Exception:
                        return f"Couldn't read ref {ref}."
                else:
                    parts: list[str] = []
                    for frame in list(tab.page.frames):
                        try:
                            parts.append(str(await frame.evaluate(
                                "() => document.body ? document.body.innerText : ''") or ""))
                        except Exception:
                            continue
                    text = "\n\n".join(part.strip() for part in parts if part.strip())
                try:
                    title = str(await tab.page.title() or "")
                except Exception:
                    title = ""
                text = re.sub(r"\n{3,}", "\n\n", text).strip()
                where = history_url(str(tab.page.url or ""))
                if not text:
                    return f"{title or 'The page'} ({where}) has no text yet."
                cut = len(text) > MAX_EXTRACT_CHARS
                return (f"Text of {title or 'the page'} ({where}):\n" + text[:MAX_EXTRACT_CHARS]
                        + ("\n… the rest of the page isn't shown." if cut else ""))

        return self._run(run, timeout=60.0)

    def page_facts(self, chat_id: str) -> PageFacts:
        """The active page's address and title (for actions with no target, such as Enter)."""

        chat = self._chats.get(str(chat_id))
        url = self.current_url(chat_id)
        return PageFacts(url=url, title=self._title_cache(chat.active) if chat and chat.active else "")

    # ---- Housekeeping ---------------------------------------------------------------

    def release_thread(self, chat_id: str) -> None:
        """Close a conversation's tabs and forget it (it was deleted, or its work ended)."""

        chat_id = str(chat_id)
        recorder = self._recorders.get(chat_id)
        if recorder is not None:
            recorder.request_stop("closed")
        self.revoke_local_addresses(chat_id)
        chat = self._chats.get(chat_id)
        if chat is None:
            return
        if self.running:
            async def close() -> None:
                for tab in list(chat.tabs):
                    try:
                        if len([p for p in self._context.pages if not p.is_closed()]) <= 1:
                            await self._context.new_page()  # A persistent context ends with its last page.
                        await tab.page.close()
                    except Exception:
                        pass
                self._chats.pop(chat_id, None)

            try:
                self._run(close, timeout=20.0, cancellable=False)
            except Exception:
                pass
        else:
            self._chats.pop(chat_id, None)
        self._notify(chat_id)

    def evict_idle(self, ttl_seconds: float = 600.0) -> int:
        """Close tabs nobody has used for a while (not while a turn runs, someone watches or the person uses them)."""

        if not self.running:
            return 0
        busy = _active_generation_thread_ids() | set(self._recorders)
        watched = {viewer.chat for viewer in list(self._viewers.values())}
        cutoff = time.monotonic() - ttl_seconds
        idle = [chat.id for chat in list(self._chats.values())
                if chat.tabs and chat.id not in busy and chat.id not in watched and not self._person_active(chat)
                and chat.last_used < cutoff]
        for chat_id in idle:
            self.release_thread(chat_id)
        return len(idle)

    def close(self) -> None:
        if self._closed:
            return
        recorders = list(self._recorders.values())
        for recorder in recorders:
            recorder.request_stop("quit")
        deadline = time.monotonic() + 15
        for recorder in recorders:
            recorder.join(max(0.0, deadline - time.monotonic()))
        loop = self._loop
        if loop is not None and not loop.is_closed():
            async def shutdown() -> None:
                await self._shutdown_context()
                if self._pw is not None:
                    try:
                        await self._pw.stop()
                    except Exception:
                        pass
                    self._pw = None

            try:
                asyncio.run_coroutine_threadsafe(shutdown(), loop).result(timeout=25)
            except Exception:
                logger.warning("Row-Bot's browser did not stop within the shutdown deadline")
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        self._loop = None
        self._closed = True
        self._status = "stopped"
        for chat_id in {viewer.chat for viewer in list(self._viewers.values())}:
            self._notify(chat_id)  # Live streams end with the browser.
        with self._lock:
            self._grants.clear()
        self._chats.clear()

    # ---- Pictures -------------------------------------------------------------------

    async def _picture(self, tab: _Tab, *, clip: dict[str, Any] | None = None, document: bool = False,
                       jpeg: bool = False, quality: int = 80, cover: bool = True) -> bytes | None:
        """Chromium's own screenshot of a page (device pixels), with protected fields covered."""

        session = tab.session or await self._context.new_cdp_session(tab.page)
        try:
            params: dict[str, Any] = {"format": "jpeg" if jpeg else "png"}
            if jpeg:
                params["quality"] = quality
            if clip is not None:
                params["clip"] = clip
                params["captureBeyondViewport"] = True
            data = base64.b64decode(str((await session.send("Page.captureScreenshot", params)).get("data") or ""))
        except Exception:
            logger.debug("A page picture failed", exc_info=True)
            return None
        if not data or not cover:
            return data or None
        boxes: list[Any] = []
        try:
            for frame in tab.page.frames:
                if frame.parent_frame is not None:
                    continue  # Only the top page's coordinates match the picture.
                boxes.extend(await frame.evaluate(
                    "([selector, documentCoordinates]) => Array.from(document.querySelectorAll(selector)).map(e => {"
                    " const r = e.getBoundingClientRect();"
                    " const x = documentCoordinates ? r.left + window.scrollX : r.left;"
                    " const y = documentCoordinates ? r.top + window.scrollY : r.top;"
                    " return [x, y, r.width, r.height, window.devicePixelRatio || 1]; })",
                    [PROTECTED_FIELDS, bool(document)],
                ))
        except Exception:
            return None  # Unknown: never keep a picture that might show a protected field.
        if len(boxes) > 200:
            return None
        return _cover(data, boxes, jpeg=jpeg) if boxes else data

    def model_picture(self, chat_id: str) -> bytes | None:
        """A JPEG of what's on screen now, protected fields covered, for a model that sees images."""

        chat = self._chats.get(str(chat_id))
        if not self.running or chat is None or chat.active is None:
            return None
        tab = chat.active
        try:
            picture = self._run(lambda: self._picture(tab, jpeg=True, quality=70), timeout=15.0)
        except Exception:
            return None
        if picture and tab.fitted and tab.fitted[2] > 1:
            import io

            from PIL import Image

            image = Image.open(io.BytesIO(picture))
            css_width = tab.fitted[0]
            if image.width > css_width:
                image = image.convert("RGB").resize((css_width, round(image.height * css_width / image.width)))
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=75)
                picture = output.getvalue()
        return picture

    def capture_page(self, chat_id: str = "default", *, full_page: bool = False) -> tuple[bytes | None, str]:
        """A PNG (JPEG when very large) of this conversation's page, and what it shows in words."""

        chat = self._chats.get(str(chat_id))
        if not self.running or chat is None or chat.active is None or str(chat.active.page.url) in _BLANK_URLS:
            return None, "No page is open in this chat yet. Open one with browser_navigate first."
        tab = chat.active

        async def run() -> tuple[bytes | None, str]:
            page = tab.page
            try:
                width, height, scroll_height, ratio = await page.evaluate(
                    "() => [window.innerWidth, window.innerHeight, Math.max(document.documentElement.scrollHeight,"
                    " document.body ? document.body.scrollHeight : 0), window.devicePixelRatio || 1]"
                )
                width, height, scroll_height, ratio = int(width), int(height), int(scroll_height), float(ratio) or 1.0
            except Exception:
                width, height, scroll_height, ratio = 0, 0, 0, 1.0
            cut = 0
            clip: dict[str, Any] | None = None
            if full_page and width:
                limit = max(height, min(MAX_FULL_PAGE_CSS, int(MAX_FULL_PAGE_DEVICE / max(ratio, 1.0))))
                cut = limit if scroll_height > limit else 0
                clip = {"x": 0, "y": 0, "width": width, "height": min(scroll_height, limit) or height, "scale": 1}
            image = await self._picture(tab, clip=clip, document=full_page)
            if image is not None and len(image) > MAX_SCREENSHOT_BYTES:
                image = await self._picture(tab, clip=clip, document=full_page, jpeg=True)
            if image is None:
                return None, "The screenshot couldn't be taken. Try again in a moment."
            if len(image) > MAX_SCREENSHOT_BYTES:
                return None, "The page is too large to save as one picture; take a screenshot of what's on screen."
            size = f"{tab.viewport[0]} size" if tab.viewport else (f"{width}×{height}" if width else "its size")
            what = "the whole page" if full_page and not cut else (
                f"the first {cut:,} pixels of a longer page" if cut else "what's on screen")
            return image, (f"Screenshot of {history_url(str(page.url))} ({what}, {size} at {ratio:g}× scale). "
                           "Password and card fields are covered.")

        return self._run(run, timeout=60.0)

    def take_screenshot(self, chat_id: str | None = None) -> bytes | None:
        """A PNG of the conversation's page (vision's look at the browser page)."""

        image, _words = self.capture_page(str(chat_id or "default"))
        return image

    def set_viewport(self, preset: str, device_scale: float | None = None, thread_id: str = "default") -> str:
        """Show this conversation's page at a phone, tablet or desktop size (optionally another device scale)."""

        if preset not in VIEWPORT_PRESETS:
            return "Choose phone, tablet or desktop."
        width, height, scale, touch = VIEWPORT_PRESETS[preset]
        if device_scale is not None:
            try:
                scale = float(device_scale)
            except (TypeError, ValueError):
                return "The device scale must be a number from 1 to 3."
            if not 1.0 <= scale <= 3.0:
                return "The device scale must be a number from 1 to 3."

        async def step(tab: _Tab) -> str:
            session = tab.session or await self._context.new_cdp_session(tab.page)
            tab.session = session
            if preset == "desktop" and device_scale is None:
                # Back to the size the person sees it at (or a desktop window, or the recording's).
                await session.send("Emulation.setTouchEmulationEnabled", {"enabled": False})
                tab.viewport, tab.fitted = None, None
                await self._fit(self._chat(thread_id))
                tab.refs.reset()
                size = tab.fitted or DESKTOP_METRICS
                where = "desktop size" if size == DESKTOP_METRICS else self._where(thread_id, size)
                return f"The page now shows at {where}: {size[0]}×{size[1]} at {size[2]:g}× scale."
            else:
                await session.send("Emulation.setDeviceMetricsOverride", {
                    "width": width, "height": height, "deviceScaleFactor": scale, "mobile": touch})
                # Chromium refuses touch points outside 1-16, even when switching touch off.
                await session.send("Emulation.setTouchEmulationEnabled",
                                   {"enabled": True, "maxTouchPoints": 5} if touch else {"enabled": False})
                tab.viewport, tab.fitted = (preset, scale), None
            tab.refs.reset()
            return (f"The page now shows at {preset} size: {width}×{height} at {scale:g}× scale"
                    + (", as a touch screen." if touch else "."))

        return self.act(thread_id, f"Showing the page at {preset} size", step).text

    # ---- Recordings (F2) ------------------------------------------------------------

    def start_recording(self, path: Path, *, max_seconds: float, size_limit: int, on_end: Any,
                        thread_id: str = "default") -> PageRecorder:
        """Record this conversation's page; the recording follows its active tab."""

        chat = self._chats.get(str(thread_id))
        if thread_id in self._recorders:
            raise RuntimeError("already_recording")
        if not self.running or chat is None or chat.active is None or str(chat.active.page.url) in _BLANK_URLS:
            raise LookupError("no_page")
        css_width, css_height, scale = self._record_metrics(chat.active)
        width, height = css_width * scale, css_height * scale
        shrink = min(1.0, MAX_RECORD_SIDE / max(width, height))
        recorder = PageRecorder(path, max_seconds=max_seconds, size_limit=size_limit,
                                size=(round(width * shrink), round(height * shrink)),
                                pointer_size=RECORD_POINTER_CSS * scale * shrink,
                                on_end=lambda reason, video: self._recording_ended(thread_id, on_end, reason, video))
        self._recorders[thread_id] = recorder
        # The page takes the recording's size before the first picture is kept.
        self._run(lambda: self._follow_live(thread_id), timeout=15.0, cancellable=False)
        frame = self.latest_frame(thread_id)
        if frame is not None and (round(frame.css_width), round(frame.css_height)) == (css_width, css_height):
            recorder.offer_frame(base64.b64decode(frame.data))
        recorder.watch = self.watch(thread_id, kind="record", width=recorder.size[0])
        recorder.start()
        self._publish(thread_id)
        return recorder

    def _record_metrics(self, tab: _Tab) -> tuple[int, int, float]:
        """The page size a recording keeps: a phone or tablet size the agent chose, else the recording's own."""

        if tab.viewport is not None and tab.viewport[0] != "desktop":
            width, height, _scale, _touch = VIEWPORT_PRESETS[tab.viewport[0]]
            return width, height, tab.viewport[1]
        # A desktop size chosen earlier gives way to the recording's.
        tab.viewport, tab.fitted = None, None
        return RECORD_METRICS

    def _recording_ended(self, chat_id: str, on_end: Any, reason: str, video: Path | None) -> None:
        recorder = self._recorders.pop(chat_id, None)
        watch = getattr(recorder, "watch", None)
        if watch is not None:
            watch.close()
        if self.running:
            self._soon(self._follow_live(chat_id))  # Back to the pane's size.
        self._publish(chat_id)
        on_end(reason, video)

    def stop_recording(self, thread_id: str, reason: str) -> bool:
        recorder = self._recorders.get(thread_id)
        if recorder is None:
            return False
        recorder.request_stop(reason)
        return True

    # ---- Signed-in sites ------------------------------------------------------------

    def sites(self) -> list[dict[str, Any]]:
        """The sites with saved sign-ins (cookies) in Row-Bot's browser, most cookies first."""

        if self.running:
            cookies = self._run(lambda: self._context.cookies(), timeout=20.0, cancellable=False)
            domains = [str(cookie.get("domain") or "") for cookie in cookies]
        else:
            domains = _profile_cookie_domains(PROFILE_DIR)
        counts: dict[str, int] = {}
        for domain in domains:
            site = registrable_domain(domain)
            if site:
                counts[site] = counts.get(site, 0) + 1
        return [{"site": site, "cookies": count} for site, count in sorted(counts.items(), key=lambda i: (-i[1], i[0]))]

    def clear_sites(self, site: str | None) -> int:
        """Sign out of one site (its cookies and stored data), or every site. Returns how many sites."""

        self.ensure_started()

        async def run() -> int:
            cookies = await self._context.cookies()
            matching = [c for c in cookies if site is None or registrable_domain(str(c.get("domain") or "")) == site]
            sites = {registrable_domain(str(c.get("domain") or "")) for c in matching}
            if site is None:
                await self._context.clear_cookies()
            else:
                await self._context.clear_cookies(domain=re.compile(r"(^|\.)" + re.escape(site) + r"$"))
            page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            session = await self._context.new_cdp_session(page)
            try:
                origins = {f"{scheme}://{host.lstrip('.')}" for c in matching for host in [str(c.get("domain") or "")]
                           for scheme in ("https", "http")}
                for origin in origins:
                    try:
                        await session.send("Storage.clearDataForOrigin", {"origin": origin, "storageTypes": "all"})
                    except Exception:
                        pass
                if site is None:
                    await session.send("Network.clearBrowserCache")
            finally:
                try:
                    await session.detach()
                except Exception:
                    pass
            return len(sites - {""})

        cleared = int(self._run(run, timeout=60.0, cancellable=False))
        logger.info("Row-Bot's browser signed out of %s", "every site" if site is None else "one site")
        return cleared


_MULTI_PART_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "me.uk", "net.uk", "nhs.uk", "police.uk",
    "com.au", "net.au", "org.au", "edu.au", "gov.au", "co.nz", "org.nz", "co.jp", "ne.jp", "or.jp",
    "co.in", "net.in", "org.in", "com.br", "com.mx", "com.cn", "com.hk", "com.sg", "com.tr", "co.za",
    "co.kr", "com.ar", "com.tw", "co.il", "com.my", "com.ph", "com.pk", "com.ng", "com.eg",
})


def registrable_domain(domain: str) -> str:
    """``homeserve.co.uk`` for ``.www.homeserve.co.uk`` (a short list of two-part endings, no lookup)."""

    host = str(domain or "").strip().lstrip(".").casefold()
    if not host or re.fullmatch(r"[\d.:\[\]]+", host):
        return host
    labels = [label for label in host.split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels)
    take = 3 if ".".join(labels[-2:]) in _MULTI_PART_SUFFIXES else 2
    return ".".join(labels[-take:])


def _profile_cookie_domains(profile: Path) -> list[str]:
    """Cookie domains from a stopped profile's cookie store (names only, never values)."""

    for candidate in (profile / "Default" / "Network" / "Cookies", profile / "Default" / "Cookies"):
        if not candidate.is_file():
            continue
        try:
            uri = candidate.resolve().as_uri() + "?mode=ro&immutable=1"
            with sqlite3.connect(uri, uri=True, timeout=1) as connection:
                return [str(row[0]) for row in connection.execute("SELECT host_key FROM cookies")]
        except sqlite3.Error:
            logger.debug("The browser's cookie store could not be read", exc_info=True)
            return []
    return []


BrowserSession = BrowserEngine


class BrowserSessionManager:
    """The one engine every conversation shares (created on first use)."""

    def __init__(self) -> None:
        self._engine: BrowserEngine | None = None
        self._lock = threading.Lock()
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

    def has_active_session(self) -> bool:
        return self._engine is not None

    def engine(self) -> BrowserEngine | None:
        return self._engine

    def get_session(self, thread_id: str = "") -> BrowserEngine:
        with self._lock:
            if self._engine is None:
                self._engine = BrowserEngine()
                for listener in self._listeners:
                    self._engine.add_listener(listener)
            return self._engine

    def add_activity_listener(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(callback)
            engine = self._engine
        remove = engine.add_listener(callback) if engine else None

        def unregister() -> None:
            with self._lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)
            if remove:
                remove()

        return unregister

    def take_screenshot(self, thread_id: str) -> bytes | None:
        return self._engine.take_screenshot(thread_id) if self._engine else None

    def evict_idle(self, ttl_seconds: float = 600.0) -> int:
        return self._engine.evict_idle(ttl_seconds=ttl_seconds) if self._engine else 0

    def kill_session(self, thread_id: str) -> None:
        if self._engine:
            self._engine.release_thread(thread_id)

    def local_address_grants(self, conversation_id: str) -> list[LocalAddressGrant]:
        return self._engine.local_address_grants(conversation_id) if self._engine else []

    def revoke_local_address(self, conversation_id: str, grant_id: str) -> bool:
        return bool(self._engine and self._engine.revoke_local_address(conversation_id, grant_id))

    def revoke_local_addresses(self, conversation_id: str) -> int:
        return self._engine.revoke_local_addresses(conversation_id) if self._engine else 0

    def kill_all(self) -> None:
        with self._lock:
            engine, self._engine = self._engine, None
        if engine:
            engine.close()
