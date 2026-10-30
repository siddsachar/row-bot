"""The agent's browser tools: Row-Bot's browser, shown in the Browser pane.

Every action returns the page after it: a short accessibility outline with
stable refs (``[ref=e12]``), so one call does the job and a ref from the last
result works in the next call. A model that sees images also gets a picture
of the page (``browser.model_view``).

Row-Bot never types a password, one-time code or card number: the person does,
in the pane. Paying, buying, booking with a charge, sending, posting, deleting
and changing account settings ask first (D19, ``browser.policy``). The page is
shared: while the person clicks or types in it, the agent's next step waits
until they're still.
"""

from __future__ import annotations

from datetime import datetime
import time
from typing import Any, Literal, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.browser import BrowserSession, BrowserSessionManager
from row_bot.browser import local_access as _local
from row_bot.browser import model_view
from row_bot.browser.addresses import has_scheme
from row_bot.browser.history import (
    HISTORY_PATH as _DEFAULT_HISTORY_PATH,
    append_browser_history as _append_history,
    clear_browser_history as _clear_history,
    get_browser_history as _get_history,
)
from row_bot.browser.policy import (
    Consequence,
    PageFacts,
    approval_request,
    classify,
    history_url as _history_url,
    navigation_policy as _navigation_policy,
    upload_consequence,
)
from row_bot.browser.service import ACTION_TIMEOUT_MS, NAVIGATION_TIMEOUT_MS, STOPPED, BrowserStopped
from row_bot.cancellation import current_cancellation_scope
from row_bot.tools import registry
from row_bot.tools.base import BaseTool

DATA_DIR = _DEFAULT_HISTORY_PATH.parent
_HISTORY_PATH = _DEFAULT_HISTORY_PATH
# How long a step waits while the person keeps using the page before it asks them instead.
PERSON_WAIT_SECONDS = 120.0
# The person does these on the shared page: ask_person with Done as the recommended option, which they can also
# press above the page in the Browser pane; a message from them answers it too.
_SECRET_REFUSAL = {
    "password": ("That's a password field. Row-Bot never types passwords: ask the person (ask_person) to sign in "
                 "in the browser pane, with Done as the recommended option, then carry on."),
    "code": ("That's a one-time code field. The person types it in the browser pane: ask them (ask_person, with "
             "Done as the recommended option), then carry on."),
    "card": ("That's a card field. Row-Bot never types card numbers: ask the person (ask_person) to enter their "
             "card details in the browser pane, with Done as the recommended option."),
}


def get_browser_history(thread_id: str) -> list[dict]:
    return _get_history(thread_id, path=_HISTORY_PATH)


def append_browser_history(thread_id: str, entry: dict) -> None:
    _append_history(thread_id, entry, path=_HISTORY_PATH)


def clear_browser_history(thread_id: str) -> None:
    _clear_history(thread_id, path=_HISTORY_PATH)


def _get_thread_id() -> str:
    try:
        from row_bot.agent import _current_thread_id_var

        return _current_thread_id_var.get() or "default"
    except ImportError:
        return "default"


_session_manager = BrowserSessionManager()


def get_session_manager() -> BrowserSessionManager:
    return _session_manager


# ── Arguments ─────────────────────────────────────────────────────────────────


class _NavigateInput(BaseModel):
    url: str = Field(description="Web address to open, or back, forward or reload")


class _ClickInput(BaseModel):
    ref: str = Field(description="Ref from the page outline, e.g. e12")
    double: bool = Field(default=False, description="Double-click")
    right: bool = Field(default=False, description="Right-click")


class _TypeInput(BaseModel):
    ref: str = Field(description="Ref of the field")
    text: str = Field(description="Text to type (replaces what's there)")
    submit: bool = Field(default=False, description="Press Enter afterwards")


class _SelectInput(BaseModel):
    ref: str = Field(description="Ref of the dropdown")
    options: list[str] = Field(description="Option label(s) to choose")


class _PressInput(BaseModel):
    key: str = Field(description="Key or combination, e.g. Enter, Escape, ArrowDown, Control+A")
    ref: Optional[str] = Field(default=None, description="Ref to focus first; else the focused element")


class _ScrollInput(BaseModel):
    direction: Literal["up", "down"] = Field(default="down")
    amount: int = Field(default=3, ge=1, le=20, description="Screens of about 400 px")
    ref: Optional[str] = Field(default=None, description="Scroll this element into view instead")


class _WaitInput(BaseModel):
    text: Optional[str] = Field(default=None, description="Wait until this text shows")
    gone: Optional[str] = Field(default=None, description="Wait until this text goes")
    seconds: Optional[float] = Field(default=None, ge=0, le=30, description="Or just wait this long")


class _TabsInput(BaseModel):
    action: Literal["list", "switch", "new", "close"] = Field(default="list")
    tab: Optional[str] = Field(default=None, description="Tab id for switch or close, e.g. t2")
    url: Optional[str] = Field(default=None, description="Address for a new tab")


class _ScreenshotInput(BaseModel):
    full_page: bool = Field(default=False, description="The whole page instead of what's on screen")


class _ExtractInput(BaseModel):
    ref: Optional[str] = Field(default=None, description="Only this element's text")


class _UploadInput(BaseModel):
    ref: str = Field(description="Ref of the upload button or file field")
    files: list[str] = Field(description="Files from this chat or the workspace, by name or path")


class _DownloadInput(BaseModel):
    ref: str = Field(description="Ref of the link or button that downloads")


class _ViewportInput(BaseModel):
    preset: Literal["phone", "tablet", "desktop"] = Field(description="Page size: phone, tablet or desktop")
    device_scale: Optional[float] = Field(default=None, ge=1, le=3, description="Optional device pixel ratio, 1 to 3")


class _RecordInput(BaseModel):
    action: Literal["start", "stop", "beat"] = Field(
        description="start or stop recording this chat's page; beat marks this moment while it records")
    max_minutes: int = Field(default=5, ge=1, le=15, description="For start: stops by itself after this many minutes")
    beat: Literal["live", "work", "cut"] = Field(
        default="live", description="For beat: live (watch as it is), work (a wait to speed up) or cut (leave out)")
    note: str = Field(default="", description="For beat: what happens next, in a few words")


class _SandboxInput(BaseModel):
    action: Literal["list", "create", "start", "stop", "reset", "open"] = Field(
        default="list", description="list, create (and start), start, stop, reset to its pack, or open in this "
                                    "chat's browser, signed in")
    sandbox: str = Field(default="", description="The sandbox (box-…), for every action but list and create")
    name: str = Field(default="", description="For create: its name, e.g. Demo shop")
    pack: Literal["empty", "shop", "charity", "paperwork", "code"] = Field(
        default="empty", description="For create: the made-up data it starts with")
    new: bool = Field(default=False, description="For create: a new one even if one with this pack is signed in "
                                                 "(only when the person asks)")


# ── Shared steps ──────────────────────────────────────────────────────────────


def _recording(thread_id: str) -> Any:
    """The browser recording running in this chat (its capture draws the pointer), or None."""
    from row_bot import recordings

    record = recordings.active_for(thread_id, "browser")
    return record.capture if record is not None and hasattr(record.capture, "pointer") else None


async def _point(tab: Any, locator: Any, capture: Any, *, click: bool = False) -> None:
    """While recording: move the pointer to the control at a viewer's pace, so the video shows where it goes."""
    import asyncio

    from row_bot.browser.recording import MOVE_SECONDS

    box = await locator.bounding_box(timeout=ACTION_TIMEOUT_MS)
    if not box:
        return
    width, height = await tab.page.evaluate("() => [innerWidth, innerHeight]")
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    capture.pointer(x / max(1, width), y / max(1, height))
    await tab.page.mouse.move(x, y, steps=12)
    await asyncio.sleep(MOVE_SECONDS)
    if click:
        capture.pointer(x / max(1, width), y / max(1, height), click=True)


def _normalise_url(url: str) -> tuple[str | None, str]:
    selected = str(url or "").strip()
    if selected.casefold().startswith("javascript:"):
        return None, "Error: javascript: addresses don't open."
    if not selected.casefold().startswith(("http://", "https://")) and not has_scheme(selected):
        # A local development server is plain http unless the person says otherwise. An address with its own
        # scheme (file:///…) stays as written, so the navigation policy says why it doesn't open.
        bare = "http://" + selected
        selected = bare if _local.local_development_origin(bare) else "https://" + selected
    return selected, ""


def _local_address_refusal(session: BrowserSession, thread_id: str, url: str, tool: str) -> tuple[str | None, str]:
    """Whether a local or private-network address may open: (refusal, note).

    An address on this computer opens once allowed for this conversation: by
    the person through the approval gate (or the level: the reviewer under
    Auto, Hands-off), or without asking when a server Row-Bot started here
    listens on it. Other private-network addresses never open.
    """

    origin = _local.local_development_origin(url)
    if origin is None:
        unspecified = _local.unspecified_refusal(url)
        if unspecified:
            return unspecified, ""
        return (_local.PRIVATE_NETWORK_REFUSAL, "") if _local.private_network_target(url) else (None, "")
    if _local.is_row_bot_origin(origin):
        return _local.row_bot_refusal(origin), ""
    if session.local_address_grant(thread_id, origin) is not None:
        return None, ""
    label = _local.address_label(origin)
    if _local.row_bot_server(origin, thread_id):
        session.allow_local_address(thread_id, origin, source="row_bot_server")
        return None, f"Opened without asking: Row-Bot started the server at {label} in this chat."
    from row_bot.tools.approval_gate import approval_check

    session.set_waiting(thread_id, "Waiting for your OK in the chat")
    refusal, line = approval_check(
        _local.approval_request(origin, tool=tool),
        risks=(_local.LOCAL_ADDRESS_RISK,),
        blocked_message=(
            f"BLOCKED: Opening {label} needs approval, and this chat is on Block. "
            "Ask the person to allow it or change the approval level."
        ),
        cancelled_message=f"The person didn't allow {label}; it wasn't opened.",
    )
    session.set_waiting(thread_id, "")
    if refusal:
        return refusal, ""
    session.allow_local_address(thread_id, origin, source=_local.source_of(line))
    return None, ""


def _wait_for_person(session: BrowserSession, thread_id: str) -> str | None:
    """Wait while the person is using the shared page, so Row-Bot never acts under their hand."""

    from row_bot.browser.service import PERSON_STILL_SECONDS

    if session.person_still_for(thread_id) >= PERSON_STILL_SECONDS:
        return None
    scope = current_cancellation_scope()
    session.set_waiting(thread_id, "Waiting while you use the page")
    deadline = time.monotonic() + PERSON_WAIT_SECONDS
    try:
        while session.person_still_for(thread_id) < PERSON_STILL_SECONDS:
            if scope is not None and scope.is_cancelled():
                return STOPPED
            if time.monotonic() >= deadline:
                return ("The person is still using the page, so nothing was done. Ask them (ask_person) what "
                        "they're doing there, with Done as an option, and carry on when they answer.")
            time.sleep(0.25)
    finally:
        session.set_waiting(thread_id, "")
    return None


def _failure(exc: BaseException) -> str:
    text = str(exc)
    if isinstance(exc, BrowserStopped):
        return STOPPED
    if "couldn't start" in text or "No browser" in text or "No browser could be launched" in text:
        return ("Row-Bot's browser couldn't start. Chrome or Edge, or Row-Bot's own browser (Settings › System › "
                "Browser and computer use), is needed.")
    if isinstance(exc, TimeoutError):
        return "Row-Bot's browser took too long to answer. Try again; if it keeps happening, restart it in the pane."
    return "Row-Bot's browser couldn't do that. Try again; if it keeps happening, restart it in the browser pane."


def _plain_error(verb: str, what: str, exc: BaseException) -> str:
    text = str(exc)
    if "ERR_BLOCKED_BY_CLIENT" in text:
        return "Blocked: that address is on this computer or the local network and this chat hasn't allowed it."
    if "ERR_NAME_NOT_RESOLVED" in text:
        return "That site couldn't be found: check the address."
    if "ERR_CONNECTION_REFUSED" in text:
        return "Nothing answered at that address: the server may not be running."
    if "ERR_INTERNET_DISCONNECTED" in text:
        return "This computer is offline."
    if "ERR_ABORTED" in text and verb == "open":
        return "The address didn't open a page (it may have started a download)."
    if "Timeout" in text or "timeout" in text:
        if verb == "open":
            return "The page is taking a long time to load; here is what's there so far."
        return f"Couldn't {verb} {what}: it didn't respond (it may be hidden, disabled or covered)."
    return f"Couldn't {verb} {what}."


def _what(info: dict[str, Any] | None, ref: str) -> str:
    if info and info.get("name"):
        return f'{info.get("role") or "control"} "{str(info["name"])[:80]}"'
    return f"ref {ref}"


def _keep_picture(session: BrowserSession, thread_id: str) -> None:
    if not model_view.model_sees_images():
        model_view.forget(thread_id)
        return
    model_view.keep(thread_id, session.model_picture(thread_id))


def _history(thread_id: str, action: str, **values: Any) -> None:
    try:
        append_browser_history(thread_id, {"action": action, **values, "timestamp": datetime.now().isoformat()})
    except Exception:
        pass


OWN_BROWSER_HOST = "cdn.playwright.dev"
OWN_BROWSER_SIZE = "about 300 MB"


def _own_browser_consent() -> tuple[str, str]:
    """(refusal, approval line): with no Chrome or Edge, Row-Bot's own browser downloads the first time it's needed,
    after the person agrees (by the approval level), as ffmpeg does."""
    from row_bot.browser.runtime import install_managed_browser_runtime
    from row_bot.tools.approval_gate import approval_check

    refusal, line = approval_check(
        {"tool": "browser", "label": "Download Row-Bot's browser",
         "question": f"Using websites needs a browser, and this computer has no Chrome or Edge. Download "
                     f"Row-Bot's own ({OWN_BROWSER_SIZE}) now?",
         "description": (f"Row-Bot will download Chromium, the open-source browser Chrome is built on, "
                         f"({OWN_BROWSER_SIZE}) from {OWN_BROWSER_HOST}, check that it starts, and keep it in "
                         "Row-Bot's data folder. This happens once."),
         "risk_class": "medium",
         "args": {"download": f"Chromium ({OWN_BROWSER_SIZE})", "from": OWN_BROWSER_HOST}},
        risks=(f"downloads a browser ({OWN_BROWSER_SIZE}) and keeps it in Row-Bot's data folder",),
        blocked_message=("BLOCKED: using websites needs a browser, and downloading Row-Bot's own needs approval, "
                         "but this chat is on Block. Install Chrome or Edge, or Row-Bot's browser in Settings › "
                         "System › Browser and computer use."),
        cancelled_message=("The person didn't allow downloading Row-Bot's browser; nothing was downloaded. "
                           "Websites need Chrome or Edge, or Row-Bot's browser from Settings › System › Browser "
                           "and computer use."),
    )
    if refusal:
        return refusal, ""
    result = install_managed_browser_runtime()
    if not result.ok:
        return f"Row-Bot's browser couldn't be downloaded: {result.message}", ""
    return "", line


def _run(session: BrowserSession, thread_id: str, label: str, step: Any, *, settle: bool = True) -> str:
    from row_bot.browser.service import can_start
    from row_bot.tools.approval_gate import with_approval

    refusal = _wait_for_person(session, thread_id)
    if refusal:
        return refusal
    line = ""
    for attempt in range(2):
        try:
            result = session.act(thread_id, label, step, settle=settle)
            break
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # It couldn't start because there's no browser at all: offer Row-Bot's own once, then go on.
            if attempt or can_start():
                return _failure(exc)
            refusal, line = _own_browser_consent()
            if refusal:
                return refusal
    _keep_picture(session, thread_id)
    return with_approval(line, result.text) if line else result.text


def _ask_first(session: BrowserSession, thread_id: str, tool: str, consequence: Consequence, url: str,
               verb: str) -> str | None:
    """The approval a consequential action needs (D19); a refusal, or None to go ahead."""

    from row_bot.tools.approval_gate import approval_check

    session.set_waiting(thread_id, "Waiting for your OK in the chat")
    refusal, _line = approval_check(
        approval_request(tool, consequence, url=url, verb=verb),
        risks=(consequence.risk,),
        blocked_message=(f"BLOCKED: {consequence.question()[:-1]} needs approval, and this chat is on Block. "
                         "Ask the person to do it in the browser pane, or to change the approval level."),
        cancelled_message=f"The person said no; Row-Bot didn't {verb} it.",
    )
    session.set_waiting(thread_id, "")
    return refusal


def _facts(info: dict[str, Any]) -> PageFacts:
    return PageFacts(
        url=str(info.get("url") or ""),
        card_fields=bool(info.get("card")),
        shows_price=bool(info.get("price")),
        search_form=bool(info.get("search")),
        form_submit_label=str(info.get("submit") or ""),
    )


async def _focused_secret(page: Any) -> str:
    try:
        return str(await page.evaluate(
            "() => { const e = document.activeElement; if (!e || e.tagName !== 'INPUT') return '';"
            " const t = (e.type || '').toLowerCase(), a = (e.autocomplete || '').toLowerCase();"
            " if (t === 'password') return 'password'; if (a.includes('one-time-code')) return 'code';"
            " if (/cc-(number|csc|exp)/.test(a)) return 'card'; return ''; }"
        ) or "")
    except Exception:
        return ""


def _printable(key: str) -> bool:
    last = key.split("+")[-1]
    return len(last) == 1 and not any(part in {"Control", "Meta", "Alt"} for part in key.split("+")[:-1])


# ── The tools ─────────────────────────────────────────────────────────────────


def browser_navigate(url: str) -> str:
    """Open a web address (or go back, forward or reload) and return the page."""

    thread_id = _get_thread_id()
    session = _session_manager.get_session(thread_id)
    word = str(url or "").strip().casefold()
    if word in {"back", "forward", "reload"}:
        async def history_step(tab: Any) -> str:
            page = tab.page
            try:
                if word == "back":
                    moved = await page.go_back(wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
                elif word == "forward":
                    moved = await page.go_forward(wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
                else:
                    moved = await page.reload(wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
            except Exception as exc:
                return _plain_error("open", "the page", exc)
            tab.refs.reset()
            return {"back": "Went back.", "forward": "Went forward.", "reload": "Reloaded."}[word] if moved or \
                word == "reload" else f"There's no page to go {word} to."

        _history(thread_id, word)
        return _run(session, thread_id, {"back": "Going back", "forward": "Going forward",
                                          "reload": "Reloading"}[word], history_step)
    selected, error = _normalise_url(url)
    if selected is None:
        return error
    policy, reason = _navigation_policy(selected)
    if policy == "block":
        return f"BLOCKED: {reason}"
    refusal, note = _local_address_refusal(session, thread_id, selected, "browser_navigate")
    if refusal:
        return refusal

    async def step(tab: Any) -> str:
        try:
            await tab.page.goto(selected, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
        except Exception as exc:
            return _plain_error("open", selected, exc)
        tab.refs.reset()
        return f"Opened {_history_url(selected)}."

    _history(thread_id, "navigate", url=_history_url(selected))
    result = _run(session, thread_id, f"Opening {_history_url(selected)[:80]}", step)
    return f"{note}\n{result}" if note else result


def browser_click(ref: str, double: bool = False, right: bool = False) -> str:
    """Click a control on the page by its ref; returns the page after it."""

    thread_id = _get_thread_id()
    session = _session_manager.get_session(thread_id)
    try:
        info = session.inspect(thread_id, ref)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return _failure(exc)
    if info is not None:
        consequence = classify(action="click", control=str(info.get("name") or ""), role=str(info.get("role") or ""),
                               page=_facts(info))
        if consequence is not None:
            refusal = _ask_first(session, thread_id, "browser_click", consequence, str(info.get("url") or ""), "click")
            if refusal:
                return refusal

    capture = _recording(thread_id)

    async def step(tab: Any) -> str:
        locator = await session.locate(tab, ref)
        try:
            if capture is not None:
                await _point(tab, locator, capture, click=True)
            if double:
                await locator.dblclick(timeout=ACTION_TIMEOUT_MS)
            else:
                await locator.click(button="right" if right else "left", timeout=ACTION_TIMEOUT_MS)
        except Exception as exc:
            return _plain_error("click", _what(info, ref), exc)
        return f"Clicked {_what(info, ref)}."

    _history(thread_id, "click")
    return _run(session, thread_id, f"Clicking {_what(info, ref)}"[:160], step)


def browser_type(ref: str, text: str, submit: bool = False) -> str:
    """Type into a field (replacing its text); never into passwords or card fields."""

    thread_id = _get_thread_id()
    session = _session_manager.get_session(thread_id)
    try:
        info = session.inspect(thread_id, ref)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return _failure(exc)
    if info is not None and info.get("secret") in _SECRET_REFUSAL:
        session.note_secret(thread_id, str(info["secret"]))
        return _SECRET_REFUSAL[str(info["secret"])]
    if info is not None and submit:
        consequence = classify(action="submit", control=str(info.get("name") or ""),
                               role=str(info.get("role") or ""), page=_facts(info))
        if consequence is not None:
            refusal = _ask_first(session, thread_id, "browser_type", consequence, str(info.get("url") or ""), "submit")
            if refusal:
                return refusal

    capture = _recording(thread_id)

    async def step(tab: Any) -> str:
        locator = await session.locate(tab, ref)
        try:
            if capture is not None:
                # While recording, type as a person would, so the video shows the words appear.
                await _point(tab, locator, capture, click=True)
                await locator.fill("", timeout=ACTION_TIMEOUT_MS)
                await locator.press_sequentially(text, delay=45, timeout=ACTION_TIMEOUT_MS + 60 * len(text))
            else:
                await locator.fill(text, timeout=ACTION_TIMEOUT_MS)
            if submit:
                await locator.press("Enter", timeout=ACTION_TIMEOUT_MS)
        except Exception as exc:
            return _plain_error("type into", _what(info, ref), exc)
        return f"Typed into {_what(info, ref)}" + (" and pressed Enter." if submit else ".")

    _history(thread_id, "type", text_length=len(text), submit=submit)
    return _run(session, thread_id, f"Typing into {_what(info, ref)}"[:160], step)


def browser_select(ref: str, options: list[str]) -> str:
    """Choose option(s) in a dropdown by their labels."""

    thread_id = _get_thread_id()
    session = _session_manager.get_session(thread_id)
    wanted = [str(option) for option in (options or []) if str(option).strip()]
    if not wanted:
        return "Name the option(s) to choose."

    async def step(tab: Any) -> str:
        locator = await session.locate(tab, ref)
        try:
            try:
                await locator.select_option(label=wanted, timeout=ACTION_TIMEOUT_MS)
            except Exception:
                try:
                    await locator.select_option(value=wanted, timeout=ACTION_TIMEOUT_MS)
                except Exception:
                    # A custom dropdown: open it, then pick each option by name.
                    await locator.click(timeout=ACTION_TIMEOUT_MS)
                    for option in wanted:
                        await tab.page.get_by_role("option", name=option).first.click(timeout=ACTION_TIMEOUT_MS)
        except Exception as exc:
            return _plain_error("choose", ", ".join(wanted), exc)
        return f"Chose {', '.join(wanted)}."

    _history(thread_id, "select")
    return _run(session, thread_id, f"Choosing {', '.join(wanted)}"[:160], step)


def browser_press(key: str, ref: str | None = None) -> str:
    """Press a key (Enter, Escape, ArrowDown, Control+A) on a ref or the focused element."""

    thread_id = _get_thread_id()
    session = _session_manager.get_session(thread_id)
    chosen = str(key or "").strip()
    if not chosen:
        return "Name the key to press."
    info = None
    if ref:
        try:
            info = session.inspect(thread_id, ref)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return _failure(exc)
        if info is not None and info.get("secret") in _SECRET_REFUSAL and _printable(chosen):
            session.note_secret(thread_id, str(info["secret"]))
            return _SECRET_REFUSAL[str(info["secret"])]
    if chosen.split("+")[-1] == "Enter":
        facts = _facts(info) if info else session.page_facts(thread_id)
        consequence = classify(action="submit", control=str((info or {}).get("name") or ""),
                               role=str((info or {}).get("role") or ""), page=facts)
        if consequence is not None:
            refusal = _ask_first(session, thread_id, "browser_press", consequence, facts.url, "submit")
            if refusal:
                return refusal

    async def step(tab: Any) -> str:
        try:
            if ref:
                locator = await session.locate(tab, ref)
                await locator.press(chosen, timeout=ACTION_TIMEOUT_MS)
            else:
                secret = await _focused_secret(tab.page)
                if secret and _printable(chosen):
                    return _SECRET_REFUSAL[secret]
                await tab.page.keyboard.press(chosen)
        except Exception as exc:
            return _plain_error("press", chosen, exc)
        return f"Pressed {chosen}."

    _history(thread_id, "press")
    return _run(session, thread_id, f"Pressing {chosen}"[:160], step)


def browser_scroll(direction: str = "down", amount: int = 3, ref: str | None = None) -> str:
    """Scroll the page (or bring a ref into view); returns the page after it."""

    thread_id = _get_thread_id()
    session = _session_manager.get_session(thread_id)
    capture = _recording(thread_id)

    async def step(tab: Any) -> str:
        import asyncio

        try:
            if ref:
                locator = await session.locate(tab, ref)
                await locator.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT_MS)
                return f"Scrolled ref {ref} into view."
            # The page's own size: it follows the Browser pane, so ask the page.
            width, height = await tab.page.evaluate("() => [innerWidth, innerHeight]")
            await tab.page.mouse.move(width / 2, height / 2)
            steps = max(1, min(int(amount), 20))
            sign = -1 if direction == "up" else 1
            if capture is None:
                await tab.page.mouse.wheel(0, sign * steps * 400)
            else:  # While recording, scroll smoothly, a little at a time.
                capture.pointer(0.5, 0.5)
                for _ in range(steps * 8):
                    await tab.page.mouse.wheel(0, sign * 50)
                    await asyncio.sleep(0.02)
        except Exception as exc:
            return _plain_error("scroll", "the page", exc)
        return f"Scrolled {direction}."

    return _run(session, thread_id, f"Scrolling {direction}", step)


def browser_wait_for(text: str | None = None, gone: str | None = None, seconds: float | None = None) -> str:
    """Wait for text to show or go (or a few seconds); returns the page. With nothing, just reads the page."""

    thread_id = _get_thread_id()
    session = _session_manager.get_session(thread_id)

    async def step(tab: Any) -> str:
        import asyncio

        try:
            if text:
                await tab.page.get_by_text(text).first.wait_for(state="visible", timeout=30_000)
                return f'"{text[:80]}" is showing.'
            if gone:
                await tab.page.get_by_text(gone).first.wait_for(state="hidden", timeout=30_000)
                return f'"{gone[:80]}" has gone.'
            if seconds:
                await asyncio.sleep(min(float(seconds), 30.0))
                return f"Waited {float(seconds):g} seconds."
        except Exception:
            return f'Still waiting: "{(text or gone or "")[:80]}" after 30 seconds. Here is the page now.'
        return ""

    return _run(session, thread_id, "Waiting for the page" if (text or gone or seconds) else "Reading the page", step)


def browser_tabs(action: str = "list", tab: str | None = None, url: str | None = None) -> str:
    """List, switch, open or close this chat's tabs."""

    thread_id = _get_thread_id()
    session = _session_manager.get_session(thread_id)
    note = ""
    selected_url = None
    if action == "new" and url:
        selected_url, error = _normalise_url(url)
        if selected_url is None:
            return error
        policy, reason = _navigation_policy(selected_url)
        if policy == "block":
            return f"BLOCKED: {reason}"
        refusal, note = _local_address_refusal(session, thread_id, selected_url, "browser_tabs")
        if refusal:
            return refusal

    async def step(current: Any) -> str:
        return await session.tab_step(thread_id, action, tab, selected_url)

    _history(thread_id, f"tab_{action}", url=_history_url(selected_url) if selected_url else None)
    result = _run(session, thread_id, {"list": "Checking tabs", "switch": "Switching tab", "new": "Opening a tab",
                                        "close": "Closing a tab"}.get(action, "Tabs"), step)
    return f"{note}\n{result}" if note else result


def browser_extract(ref: str | None = None) -> str:
    """The page's text (or one element's), to read it."""

    thread_id = _get_thread_id()
    session = _session_manager.get_session(thread_id)
    refusal = _wait_for_person(session, thread_id)
    if refusal:
        return refusal
    try:
        return session.extract(thread_id, ref)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return _failure(exc)


def browser_upload(ref: str, files: list[str]) -> str:
    """Upload files from this chat or the workspace through a page's upload control (asks first)."""

    thread_id = _get_thread_id()
    session = _session_manager.get_session(thread_id)
    from row_bot.file_deliveries import resolve
    from row_bot.media_sources import SourceError

    try:
        sources = resolve(list(files or []), thread_id)
    except SourceError as exc:
        return str(exc)
    if not sources:
        return "Name the file(s) to upload."
    try:
        payloads = [_payload(source) for source in sources]
    except SourceError as exc:
        return str(exc)
    url = session.current_url(thread_id)
    refusal = _ask_first(session, thread_id, "browser_upload", upload_consequence(url, [s.name for s in sources]),
                         url, "upload")
    if refusal:
        return refusal

    async def step(tab: Any) -> str:
        locator = await session.locate(tab, ref)
        try:
            is_file = await locator.evaluate("e => e.tagName === 'INPUT' && e.type === 'file'")
            if is_file:
                await locator.set_input_files(payloads, timeout=ACTION_TIMEOUT_MS)
            else:
                async with tab.page.expect_file_chooser(timeout=ACTION_TIMEOUT_MS) as chooser:
                    await locator.click(timeout=ACTION_TIMEOUT_MS)
                await (await chooser.value).set_files(payloads)
        except Exception as exc:
            return _plain_error("upload through", f"ref {ref}", exc)
        return "Uploaded " + ", ".join(source.name for source in sources) + "."

    _history(thread_id, "upload", files=len(sources))
    return _run(session, thread_id, "Uploading " + ", ".join(s.name for s in sources)[:120], step)


def _payload(source: Any) -> dict[str, Any]:
    from row_bot.media_sources import SourceError

    limit = 500 * 1024 * 1024
    if source.size_bytes > limit:
        raise SourceError(f"{source.name} is over 50 MB, too large to upload from here.")
    if source.ref:
        from row_bot.application.attachments import AttachmentError, read_attachment

        try:
            _metadata, data = read_attachment(source.ref)
        except AttachmentError:
            raise SourceError(f"{source.name} can't be read.") from None
    else:
        try:
            data = source.path.read_bytes()
        except OSError:
            raise SourceError(f"{source.name} can't be read.") from None
    return {"name": source.name, "mimeType": source.mime_type or "application/octet-stream", "buffer": data}


def browser_download(ref: str) -> str:
    """Click a download link or button and keep the file in this chat's files."""

    thread_id = _get_thread_id()
    session = _session_manager.get_session(thread_id)

    async def step(tab: Any) -> str:
        locator = await session.locate(tab, ref)
        try:
            async with tab.page.expect_download(timeout=30_000) as started:
                await locator.click(timeout=ACTION_TIMEOUT_MS)
            await session.finish_download(await started.value)
        except Exception as exc:
            return _plain_error("download from", f"ref {ref}", exc)
        return ""

    _history(thread_id, "download")
    return _run(session, thread_id, "Downloading", step)


def browser_screenshot(full_page: bool = False) -> str:
    """Show the person a picture of this chat's page (saved to the chat's files)."""

    thread_id = _get_thread_id()
    session = _session_manager.get_session(thread_id)
    try:
        image, words = session.capture_page(thread_id, full_page=full_page)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return _failure(exc)
    if image is None:
        return words
    # Its own name, so a later step (x_post, media_find) can tell one screenshot from another.
    name = f"screenshot-{datetime.now():%Y-%m-%d-%H%M%S}" + (".png" if image.startswith(b"\x89PNG") else ".jpg")
    if not _show_image(image, name):
        return "The screenshot was taken but couldn't be shown in the chat."
    _keep_picture(session, thread_id)
    return f"{words} Saved to this chat's files as {name} and shown to the person."


def browser_viewport(preset: str, device_scale: float | None = None) -> str:
    """Show this chat's page at a phone, tablet or desktop size."""

    thread_id = _get_thread_id()
    session = _session_manager.get_session(thread_id)
    refusal = _wait_for_person(session, thread_id)
    if refusal:
        return refusal
    try:
        return session.set_viewport(preset, device_scale, thread_id)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return _failure(exc)


def _show_image(image: bytes, name: str = "") -> bool:
    """Show a picture in the chat and save it to the conversation's files, as a generated image is."""

    import base64

    try:
        from row_bot.tools.image_gen_tool import _set_pending_image

        _set_pending_image(base64.b64encode(image).decode("ascii"), name)
        return True
    except Exception:
        return False


def _record_approval(minutes: int) -> dict:
    return {
        "tool": "browser_record",
        "label": "Record Row-Bot's browser",
        "question": "Record Row-Bot's browser in this chat?",
        "description": (
            f"Row-Bot will record what its browser shows in this chat, for up to {minutes} minutes, and save "
            "the video to this chat's files. Password and card fields are hidden. You can stop it at any time."
        ),
        "args": {"max_minutes": minutes},
    }


def browser_record(action: str, max_minutes: int = 5, beat: str = "live", note: str = "") -> str:
    """Start or stop recording this chat's page in Row-Bot's browser to a video, or mark a beat while it records."""

    from row_bot import recordings
    from row_bot.browser.recording import encoder_problem

    thread_id = _get_thread_id()
    if action == "beat":
        capture = _recording(thread_id)
        if capture is None:
            return "Row-Bot's browser isn't being recorded in this chat: start a recording first."
        at = capture.beat(beat, note)
        return (f"Marked {beat} at {recordings.clock_words(at)}"
                + {"live": ": shown as it happens.", "work": ": sped up in the video.",
                   "cut": ": left out of the video."}[beat])
    if action == "stop":
        record = recordings.active_for(thread_id, "browser")
        if record is None:
            latest = recordings.latest_for(thread_id, "browser")
            return recordings.summary(latest) if latest else "Row-Bot's browser isn't being recorded in this chat."
        recordings.stop(record.recording_id, by="agent")
        recordings.wait(record, 30)
        return recordings.summary(record)
    if action != "start":
        return "Use action start, stop or beat."
    running = recordings.active_for(thread_id, "browser")
    if running is not None:
        return recordings.summary(running)
    problem = encoder_problem()
    if problem:
        return problem
    session = _session_manager.get_session(thread_id)
    current = session.current_url(thread_id)
    if not current or current in {"about:blank"}:
        return "Open a page with browser_navigate first; the recording follows this chat's page."
    minutes = recordings.minutes(max_minutes, "browser")
    from row_bot.tools.approval_gate import approval_check

    refusal, _line = approval_check(
        _record_approval(minutes),
        risks=("records Row-Bot's browser window to a video saved in the chat",),
        blocked_message="BLOCKED: Recording needs approval, and this chat is on Block.",
        cancelled_message="The person didn't allow recording; nothing was recorded.",
    )
    if refusal:
        return refusal

    def begin(record, path):
        try:
            return session.start_recording(
                path,
                max_seconds=record.max_seconds,
                size_limit=recordings.SIZE_LIMIT,
                on_end=lambda reason, video: recordings.finished(record, reason, video),
                thread_id=thread_id,
            )
        except LookupError:
            raise recordings.RecordingError(
                "Open a page with browser_navigate first; the recording follows this chat's page."
            ) from None

    try:
        record = recordings.start(thread_id, "browser", max_minutes=minutes, begin=begin)
    except recordings.RecordingError as exc:
        return str(exc)
    except Exception:
        return "Row-Bot's browser couldn't start recording. Try again in a moment."
    capture = record.capture
    size = getattr(capture, "size", None)
    shape = (f" While it records, the page keeps one size, {size[0]}×{size[1]}, whatever the Browser pane shows: "
             "the video fills its frame." if size else "")
    return (
        recordings.summary(record)
        + shape
        + " Do the steps to record (clicks, typing and scrolling go at a viewer's pace, with a drawn pointer); mark "
        "beats as you go (beat=work before a wait, live when it's worth watching again, cut for a stretch to leave "
        "out); then call browser_record with action stop. The video is saved to this chat's files, and the video "
        "tool makes its scenes from the beats."
    )


def _sandbox_approval(what: str, name: str) -> dict:
    return {
        "tool": "sandbox",
        "label": "Start a sandbox",
        "question": f"{what} a sandbox ({name})?",
        "description": ("Row-Bot will start a second Row-Bot on this computer, on made-up data with its own chats, "
                        "settings and sign-ins. It can't see your own Row-Bot or its files, and it stops when "
                        "Row-Bot quits."),
        "args": {"sandbox": name},
    }


def _sandbox_line(item: Any) -> str:
    from row_bot import sandboxes

    view = sandboxes.view(item)
    where = f" at {view['url']}" if view["url"] else ""
    why = f" ({view['problem']})" if view["problem"] else ""
    model = sandboxes.model(item.sandbox_id)
    signed = f", signed in ({model.split(':')[-1]})" if model else ", no model yet"
    return (f"Sandbox {item.sandbox_id}: \"{item.name}\", {view['pack_name'].lower()}, {item.status}{where}{why}"
            f"{signed}.")


def _chat_model() -> str:
    """This chat's model (its own choice, else the default)."""
    from row_bot.models import get_current_model
    from row_bot.threads import _get_thread_model_override

    try:
        return _get_thread_model_override(_get_thread_id()) or get_current_model()
    except Exception:
        return get_current_model()


def sandbox(action: str = "list", sandbox: str = "", name: str = "", pack: str = "empty", new: bool = False) -> str:
    """A sandbox: a second Row-Bot on made-up data, to try things safely or film a demo in this chat's browser."""
    from row_bot import sandboxes
    from row_bot.tools.approval_gate import approval_check, current_approval_mode, with_approval

    if sandboxes.is_sandbox():
        return "This is a sandbox already: it starts no sandboxes of its own."
    if action == "list":
        found = sandboxes.list_sandboxes()
        return "\n".join(_sandbox_line(item) for item in found) if found else "There are no sandboxes yet."
    line = ""
    reused = None
    if action == "create" and not new:
        # One with this pack that's signed in already: reuse it rather than ask the person to sign in again.
        reused = sandboxes.reusable(pack)
        if reused is not None:
            action, sandbox = "start", reused.sandbox_id
    try:
        if action == "create":
            refusal, line = approval_check(
                _sandbox_approval("Create and start", name or pack),
                risks=("starts a second Row-Bot on this computer",),
                blocked_message="BLOCKED: Starting a sandbox needs approval, and this chat is on Block.",
                cancelled_message="The person didn't want a sandbox; none was made.")
            if refusal:
                return refusal
            item = sandboxes.create(name, pack, approval=current_approval_mode(), model=_chat_model())
            sandboxes.start(item.sandbox_id)
            item = sandboxes.wait_running(item.sandbox_id, 90) or item
        else:
            item = sandboxes.get(sandbox.strip())
            if item is None:
                return f"{sandbox or 'That'} isn't a sandbox here: list them, or create one."
            if action == "start" and item.status == "running":
                pass
            elif action == "start":
                refusal, line = approval_check(
                    _sandbox_approval("Start", item.name), risks=("starts a second Row-Bot on this computer",),
                    blocked_message="BLOCKED: Starting a sandbox needs approval, and this chat is on Block.",
                    cancelled_message="The person didn't want the sandbox started.")
                if refusal:
                    return refusal
                sandboxes.start(item.sandbox_id)
                item = sandboxes.wait_running(item.sandbox_id, 90) or item
            elif action == "stop":
                item = sandboxes.stop(item.sandbox_id)
            elif action == "reset":
                item = sandboxes.reset(item.sandbox_id)
            elif action == "open":
                result = browser_navigate(sandboxes.sign_in_url(item.sandbox_id))
                return f"Opened {item.name} in this chat's browser, signed in.\n{result}"
    except sandboxes.SandboxError as error:
        return str(error)
    text = _sandbox_line(item)
    if reused is not None:
        text = (f"Reusing it: it has this pack and keeps its sign-in (reset brings its made-up data back; new=true "
                f"makes another only if the person asks).\n{text}")
    if item.status == "running" and action in {"create", "start"}:
        text += " Open it with action open: this chat's browser signs in to it."
        if not sandboxes.has_model(item.sandbox_id):
            # Its own sign-in (decision 4): only the person signs in, so ask them; a demo needs real answers.
            text += (" It has no model yet: ask the person to open it from Settings › Data › Sandboxes and sign in to a "
                     "provider once; its chats can't answer until then.")
    return with_approval(line, text) if line else text


_TOOLS: tuple[tuple[Any, str, str, type[BaseModel] | None], ...] = (
    (browser_navigate, "browser_navigate",
     "Open a web address in Row-Bot's browser (or back, forward, reload) and return the page with refs. "
     "The person watches in the Browser pane and stays signed in to sites there. Addresses on this computer "
     "(localhost:5173) ask the person once per chat; servers you started here open without asking.",
     _NavigateInput),
    (browser_click, "browser_click", "Click a control by its ref; returns the page after it.", _ClickInput),
    (browser_type, "browser_type",
     "Type into a field by ref (submit=true presses Enter). Never passwords, codes or card numbers: the person "
     "types those in the Browser pane.", _TypeInput),
    (browser_select, "browser_select", "Choose option(s) in a dropdown by ref.", _SelectInput),
    (browser_press, "browser_press", "Press a key or combination (Enter, Escape, Tab, ArrowDown).", _PressInput),
    (browser_scroll, "browser_scroll", "Scroll the page, or bring a ref into view.", _ScrollInput),
    (browser_wait_for, "browser_wait_for",
     "Wait for text to show or go, or a few seconds; with nothing, returns the page as it is now.", _WaitInput),
    (browser_tabs, "browser_tabs", "List, switch, open or close this chat's tabs.", _TabsInput),
    (browser_extract, "browser_extract", "Read the page's text (or one element's) to answer from it.",
     _ExtractInput),
    (browser_upload, "browser_upload", "Upload files from this chat or the workspace through an upload control.",
     _UploadInput),
    (browser_download, "browser_download", "Download a file by clicking its link; it's kept in this chat's files.",
     _DownloadInput),
    (browser_screenshot, "browser_screenshot",
     "Show the person a picture of the page in the chat (saved to its files).", _ScreenshotInput),
    (browser_viewport, "browser_viewport",
     "Show the page at a phone, tablet or desktop size to check a responsive layout.", _ViewportInput),
    (browser_record, "browser_record",
     "Record this chat's page in Row-Bot's browser to a video (asks first): start, do the steps (paced, with a "
     "drawn pointer), mark beats (live, work, cut), then stop. To record the whole screen use computer_record.",
     _RecordInput),
    (sandbox, "sandbox",
     "A sandbox: a second Row-Bot on made-up data (a shop with its app, a charity's documents, paperwork, sample "
     "code, or empty) to try a workflow safely or film a demo. create starts one (asks first), reusing a signed-in "
     "one with that pack; open signs this chat's browser in to it; stop, reset (sign-in kept), list.",
     _SandboxInput),
)


class BrowserTool(BaseTool):
    @property
    def name(self) -> str:
        return "browser"

    @property
    def display_name(self) -> str:
        return "🌐 Browser"

    @property
    def description(self) -> str:
        return (
            "Use websites in Row-Bot's own browser, shown live in the Browser pane, where the person stays signed "
            "in. The page is shared: the person can use it any time, and your next step waits while they do. "
            "Use the computer tool for desktop apps or the person's own browser."
        )

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def config_schema(self) -> dict[str, dict]:
        return {}

    @property
    def destructive_tool_names(self) -> set[str]:
        return set()

    @property
    def untrusted_tool_names(self) -> set[str]:
        return {name for _function, name, _description, _schema in _TOOLS if name != "browser_record"}

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(func=function, name=name, description=description, args_schema=schema)
            for function, name, description, schema in _TOOLS
        ]


registry.register(BrowserTool())
