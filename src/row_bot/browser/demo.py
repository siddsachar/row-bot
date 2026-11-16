"""Show Row-Bot how (C3): the person does a task in the Browser pane and Row-Bot keeps its steps.

A demo belongs to one conversation and lives in memory only. While it records, the person's own
input in the pane (``BrowserEngine.person_input``) and the pane's controls (``person_action``) are
described as steps: the page (site, path and title), the control (its role and visible name) and,
for typing, which field. Never video, never a picture of the page.

What is never kept:

- anything typed into a password, one-time code, card or other sensitive field, into any field on a
  page that shows one (a sign-in or payment page), or inside a frame Row-Bot can't see into: the
  step only says the person typed there, and a skill drafted from it hands that step to the person;
- an address's query string and fragment, and token-like parts of its path;
- page text beyond short control names, context and titles, which are cleaned (tags, fence markers,
  long numbers, secrets) and left out when they look like instructions.

Other typed values stay in memory as examples for the person's review: a drafted skill turns each
into an input unless the person marks it "always this". A demo stops by itself at ``MAX_STEPS``
steps or ``MAX_SECONDS``; its steps are forgotten ``KEEP_SECONDS`` after it stops, when the person
discards it, or with its chat. While a demo records, the agent's browser tools in that chat wait
(``HOLD``), so the two never drive the page at once.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

MAX_STEPS = 200
MAX_SECONDS = 15 * 60
KEEP_SECONDS = 60 * 60
LABEL_CHARS = 80
TITLE_CHARS = 120
PATH_CHARS = 160
VALUE_CHARS = 300
# A press and release this close together is a click; further apart is a drag, which isn't a step.
CLICK_SLOP = 6.0
# What the agent's browser tools answer while the person shows Row-Bot a task (model-facing).
HOLD = ("The person is showing Row-Bot a task in the browser (Show Row-Bot how), so the browser wasn't "
        "used. Wait until they finish, then carry on.")

# Why a typed value isn't kept: the field's kind, or the page's (a sign-in or payment page), or a frame.
SECRET_KINDS = ("password", "code", "card", "sensitive", "sign_in", "hidden")
_SECRET_TEXT = {
    "password": "a password",
    "code": "a one-time code",
    "card": "card details",
    "sensitive": "private details",
    "sign_in": "a sign-in or payment page",
    "hidden": "a part of the page Row-Bot can't see into",
}
_NAMED_KEYS = frozenset({"Enter", "Tab", "Escape"})
_MODIFIERS = frozenset({"Control", "Meta", "Alt", "AltGraph", "OS"})

# The element under the pointer (``[x, y]``) or the focused one (``[null, null]``), as plain facts:
# its tag, type, autocomplete and naming attributes, role, visible name and surroundings, and the
# page's visible inputs (type and autocomplete). Python decides from these what is secret
# (``field_secret``, ``page_secret``). Field values are never read.
DESCRIBE_JS = r"""
([x, y]) => {
  const atPoint = x !== null && x !== undefined;
  let el = atPoint ? document.elementFromPoint(x, y) : document.activeElement;
  while (el && el.shadowRoot) {
    const inner = atPoint ? el.shadowRoot.elementFromPoint(x, y) : el.shadowRoot.activeElement;
    if (!inner || inner === el) break;
    el = inner;
  }
  const seen = (node) => { const r = node.getBoundingClientRect(); const s = getComputedStyle(node);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const inputs = Array.from(document.querySelectorAll('input')).filter(seen).slice(0, 200)
    .map((input) => [String(input.getAttribute('type') || '').toLowerCase(),
                     String(input.getAttribute('autocomplete') || '').toLowerCase()]);
  const base = {inputs, title: String(document.title || '').slice(0, 300)};
  if (!el || el === document.body || el === document.documentElement) return {...base, role: '', name: ''};
  const interactive = 'a[href],button,input,select,textarea,summary,label,[role=button],[role=link],[role=tab],'
    + '[role=menuitem],[role=option],[role=checkbox],[role=radio],[role=switch],[role=combobox],[role=textbox],'
    + '[role=searchbox],[contenteditable=""],[contenteditable=true],iframe';
  const target = (el.closest && el.closest(interactive)) || el;
  const tag = target.tagName.toLowerCase();
  const attr = (name) => String(target.getAttribute(name) || '');
  const type = attr('type').toLowerCase();
  const auto = attr('autocomplete').toLowerCase();
  const text = (node) => String((node && (node.innerText || node.textContent)) || '').replace(/\s+/g, ' ').trim();
  let role = attr('role').toLowerCase();
  if (!role) {
    if (tag === 'a') role = 'link';
    else if (tag === 'button' || tag === 'summary') role = 'button';
    else if (tag === 'select') role = 'combobox';
    else if (tag === 'textarea') role = 'textbox';
    else if (tag === 'iframe') role = 'frame';
    else if (tag === 'input') role = ({checkbox: 'checkbox', radio: 'radio', submit: 'button', button: 'button',
      reset: 'button', image: 'button', search: 'searchbox', range: 'slider', file: 'button'})[type] || 'textbox';
    else if (target.isContentEditable) role = 'textbox';
    else role = '';
  }
  const field = tag === 'input' || tag === 'textarea' || tag === 'select' || target.isContentEditable;
  let name = attr('aria-label');
  if (!name && attr('aria-labelledby')) name = attr('aria-labelledby').split(/\s+/)
    .map((id) => text(document.getElementById(id))).join(' ');
  if (!name && target.labels && target.labels.length) name = text(target.labels[0]);
  if (!name && !field) name = text(target);
  if (!name && tag === 'input' && ['submit', 'button', 'reset'].includes(type)) name = String(target.value || '');
  if (!name) name = attr('placeholder') || attr('title') || attr('alt') || attr('name');
  if (!name) { const img = target.querySelector && target.querySelector('img[alt]'); if (img) name = img.getAttribute('alt') || ''; }
  const words = [attr('name'), target.id || '', attr('aria-label'), attr('placeholder'), name].join(' ').slice(0, 600);
  const frame = tag === 'iframe' ? [attr('src'), attr('title'), attr('name')].join(' ').slice(0, 600) : '';
  if (tag === 'iframe') name = attr('title') || attr('name');
  const region = target.closest && target.closest('nav,header,footer,main,aside,form,dialog,[role=dialog],[role=navigation],[role=search],[role=form],section[aria-label]');
  let context = '';
  if (region) {
    const label = String(region.getAttribute('aria-label') || '');
    const heading = region.querySelector && region.querySelector('h1,h2,h3,legend,[role=heading]');
    context = label || text(heading) || '';
  }
  return {...base, role, tag, type, autocomplete: auto, words, frame, name: String(name || '').slice(0, 300),
    context: context.slice(0, 300),
    editable: field && !['checkbox', 'radio', 'submit', 'button', 'reset', 'image', 'file', 'range'].includes(type)};
}
"""

_PASSWORD_AUTO = re.compile(r"(?:^|\s)(?:current|new)-password\b")
_CODE_WORDS = re.compile(r"\b(?:otp|one.?time.?(?:code|pass\w*)|verification.?code|2fa|mfa|totp)\b", re.IGNORECASE)
_CARD_AUTO = re.compile(r"(?:^|\s)cc-")
_CARD_WORDS = re.compile(r"\b(?:card.?(?:number|no|holder)|cvv\d?|cvc\d?|csc|security.?code|expir\w*)\b",
                         re.IGNORECASE)
_PASSWORD_WORDS = re.compile(r"\b(?:password|passwd|passcode|passphrase|pwd)\b", re.IGNORECASE)
_SENSITIVE_WORDS = re.compile(
    r"\b(?:ssn|social.?security|national.?(?:id|insurance)|tax.?(?:id|number)|passport|iban|swift|bic|routing"
    r"|sort.?code|account.?(?:number|no)|pin|security.?(?:question|answer)|date.?of.?birth|dob|secret|token"
    r"|api.?key)\b",
    re.IGNORECASE,
)
_PAYMENT_FRAME = re.compile(r"card|pay|stripe|checkout|braintree|adyen", re.IGNORECASE)


def field_secret(facts: dict[str, Any]) -> str:
    """Why what's typed into this field is never kept: ``password``, ``code``, ``card``, ``sensitive``,
    ``hidden`` (a frame Row-Bot can't see into), or "" for an ordinary field."""

    tag = str(facts.get("tag") or "").lower()
    kind = str(facts.get("type") or "").lower()
    auto = str(facts.get("autocomplete") or "").lower()
    # "user_password", "cardNumber" and "card-number" read as words.
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(facts.get("words") or ""))
    words = re.sub(r"[_\-.:\[\]]+", " ", words)
    if tag == "iframe" or str(facts.get("role") or "") == "frame":
        return "card" if _PAYMENT_FRAME.search(str(facts.get("frame") or "")) else "hidden"
    if kind == "password" or _PASSWORD_AUTO.search(auto):
        return "password"
    if "one-time-code" in auto or _CODE_WORDS.search(words):
        return "code"
    if _CARD_AUTO.search(auto) or _CARD_WORDS.search(words):
        return "card"
    if _PASSWORD_WORDS.search(words):
        return "password"
    if _SENSITIVE_WORDS.search(words):
        return "sensitive"
    return ""


def page_secret(inputs: Any) -> str:
    """A visible password, one-time code or card field on the page: it is a sign-in or payment page."""

    pairs = [item for item in inputs or () if isinstance(item, (list, tuple)) and len(item) == 2]
    if any(str(kind).lower() == "password" for kind, _auto in pairs):
        return "password"
    if any("one-time-code" in str(auto).lower() for _kind, auto in pairs):
        return "code"
    if any(_CARD_AUTO.search(str(auto).lower()) for _kind, auto in pairs):
        return "card"
    return ""


class DemoError(Exception):
    """A demo request that can't be done; ``code`` is the client's error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class DemoStep:
    id: int
    at: float  # Seconds from the demo's start.
    kind: str  # open, click, type, key, back, forward, reload, tab
    site: str = ""
    path: str = ""
    title: str = ""
    role: str = ""
    name: str = ""
    context: str = ""
    key: str = ""
    value: str = ""
    secret: str = ""
    always: bool = False
    unsafe: bool = False  # The control's page text looked like instructions and was left out.
    editable: bool = False
    narration: str = ""


@dataclass
class Demo:
    id: str
    conversation_id: str
    started: float
    status: str = "recording"  # recording, stopped
    ended: str = ""  # done, step_limit, time_limit
    stopped_at: float | None = None
    steps: list[DemoStep] = field(default_factory=list)
    narrated: bool = False
    next_id: int = 1
    # What the person is in the middle of: a press not yet released, typing into one field, keys held.
    pressed: dict[str, Any] | None = None
    typing: bool = False
    held: set[str] = field(default_factory=set)

    @property
    def elapsed(self) -> float:
        end = self.stopped_at if self.stopped_at is not None else _clock()
        return max(0.0, end - self.started)


_LOCK = threading.RLock()
_DEMOS: dict[str, Demo] = {}
_clock: Callable[[], float] = time.monotonic


# ── Page text, made safe to keep ─────────────────────────────────────────────

_TAG = re.compile(r"<[^<>]{0,300}>")
_FENCE = re.compile(r"</?\s*EXTERNAL_CONTENT[^>]*>?|EXTERNAL_CONTENT|`{3,}|<\|[^|]{0,40}\|>", re.IGNORECASE)
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁩﻿]")
_LONG_NUMBER = re.compile(r"\d[\d \-]{6,}\d")
_TOKEN_SEGMENT = re.compile(r"^[A-Za-z0-9_\-.~%]{24,}$|\d{6,}")


def clean_label(text: Any, limit: int = LABEL_CHARS) -> tuple[str, bool]:
    """Short page text (a control's name, a heading, a title) made safe to keep: tags, fence markers and
    invisible characters out, long numbers and secrets replaced, at most ``limit`` characters. The
    second value is True when the text looked like instructions; the text is then left out."""

    from row_bot.evolution import injection_problems, redact_text
    from row_bot.external_content import scan_injection_categories

    raw = str(text or "")[:2000]
    unsafe = bool(scan_injection_categories(raw)) or "permission" in injection_problems(raw)
    value = _INVISIBLE.sub("", raw)
    value = _FENCE.sub(" ", _TAG.sub(" ", value))
    value = value.replace("`", "'").replace("“", '"').replace("”", '"')
    value = " ".join(value.split())
    if not unsafe:
        unsafe = bool(scan_injection_categories(value)) or "permission" in injection_problems(value)
    if unsafe:
        return "", True
    value = redact_text(_LONG_NUMBER.sub("[number]", value))
    if len(value) > limit:
        value = value[: limit - 1].rstrip() + "…"
    return value, False


def clean_address(url: Any) -> tuple[str, str]:
    """(site, path) of an address, without its query, fragment or token-like path parts."""

    from row_bot.browser.policy import site_of

    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return "", ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "", ""
    parts = [":id" if _TOKEN_SEGMENT.search(part) else part for part in parsed.path.split("/")]
    path = "/".join(parts)
    if path in {"", "/"}:
        path = ""
    if len(path) > PATH_CHARS:
        path = path[: PATH_CHARS - 1] + "…"
    return site_of(str(url)), path


# ── The demo of a conversation ───────────────────────────────────────────────


def _expire(conversation_id: str) -> Demo | None:
    """The conversation's demo, after its time limit and keep time are applied (under the lock)."""

    demo = _DEMOS.get(conversation_id)
    if demo is None:
        return None
    now = _clock()
    if demo.status == "recording" and now - demo.started >= MAX_SECONDS:
        _stop(demo, "time_limit", at=demo.started + MAX_SECONDS)
    if demo.status == "stopped" and demo.stopped_at is not None and now - demo.stopped_at >= KEEP_SECONDS:
        del _DEMOS[conversation_id]
        return None
    return demo


def _stop(demo: Demo, reason: str, *, at: float | None = None) -> None:
    demo.status = "stopped"
    demo.ended = reason
    demo.stopped_at = at if at is not None else _clock()
    demo.pressed = None
    demo.typing = False


def current(conversation_id: str) -> Demo | None:
    with _LOCK:
        return _expire(str(conversation_id))


def recording(conversation_id: str) -> bool:
    """A demo records in this chat now: the agent's browser tools wait."""

    demo = current(conversation_id)
    return demo is not None and demo.status == "recording"


def start(conversation_id: str, *, url: str = "", title: str = "") -> Demo:
    """Start recording the person's steps in this chat's page (a previous demo is replaced)."""

    clean = str(conversation_id)
    with _LOCK:
        existing = _expire(clean)
        if existing is not None and existing.status == "recording":
            raise DemoError("demo_recording")
        demo = Demo(id=uuid.uuid4().hex, conversation_id=clean, started=_clock())
        _DEMOS[clean] = demo
        site, path = clean_address(url)
        if site:
            name, _unsafe = clean_label(title, TITLE_CHARS)
            _add(demo, DemoStep(0, 0.0, "open", site=site, path=path, title=name))
        return demo


def _require(conversation_id: str, demo_id: str) -> Demo:
    demo = _expire(str(conversation_id))
    if demo is None:
        raise DemoError("demo_missing")
    if demo.id != demo_id:
        raise DemoError("demo_changed")
    return demo


def stop(conversation_id: str, demo_id: str) -> Demo:
    with _LOCK:
        demo = _require(conversation_id, demo_id)
        if demo.status == "recording":
            _stop(demo, "done")
        return demo


def undo(conversation_id: str, demo_id: str) -> Demo:
    """Take back the last step."""

    with _LOCK:
        demo = _require(conversation_id, demo_id)
        if not demo.steps:
            raise DemoError("demo_no_steps")
        demo.steps.pop()
        demo.typing = False
        demo.pressed = None
        return demo


def set_always(conversation_id: str, demo_id: str, step_id: int, always: bool) -> Demo:
    """Keep what was typed in one step as it is ("always this"), or make it an input again."""

    with _LOCK:
        demo = _require(conversation_id, demo_id)
        step = next((item for item in demo.steps if item.id == step_id), None)
        if step is None or step.kind != "type" or step.secret or not step.value:
            raise DemoError("demo_step_missing")
        step.always = bool(always)
        return demo


def discard(conversation_id: str, demo_id: str) -> None:
    with _LOCK:
        _require(conversation_id, demo_id)
        _DEMOS.pop(str(conversation_id), None)


def forget(conversation_id: str) -> bool:
    """A deleted chat: its demo goes with it."""

    with _LOCK:
        return _DEMOS.pop(str(conversation_id), None) is not None


def take(conversation_id: str, demo_id: str) -> Demo:
    """A stopped demo with steps, for drafting a skill."""

    with _LOCK:
        demo = _require(conversation_id, demo_id)
        if demo.status != "stopped":
            raise DemoError("demo_recording")
        if not demo.steps:
            raise DemoError("demo_no_steps")
        return demo


NARRATION_CHARS = 2000


def add_narration(conversation_id: str, demo_id: str, said: dict[int, str]) -> Demo:
    """What the person said around each step (transcribed on this computer), cleaned like page text but
    never dropped: their own words. Nothing is kept for a step whose typing was secret."""

    from row_bot.evolution import redact_text

    with _LOCK:
        demo = _require(conversation_id, demo_id)
        for step in demo.steps:
            words = " ".join(str(said.get(step.id) or "").split())
            if not words or step.secret:
                continue
            words = redact_text(_LONG_NUMBER.sub("[number]", _FENCE.sub(" ", _TAG.sub(" ", words))))
            step.narration = " ".join(f"{step.narration} {words}".split())[:NARRATION_CHARS]
        demo.narrated = True
        return demo


def _add(demo: Demo, step: DemoStep) -> None:
    step.id = demo.next_id
    demo.next_id += 1
    if not step.at:
        step.at = round(_clock() - demo.started, 2)
    demo.steps.append(step)
    if len(demo.steps) >= MAX_STEPS:
        _stop(demo, "step_limit")


# ── Observing the person's input ─────────────────────────────────────────────


def observer(conversation_id: str) -> Demo | None:
    """The demo recording in this chat (the engine's one check per input batch), else None."""

    if not _DEMOS:
        return None
    demo = current(conversation_id)
    return demo if demo is not None and demo.status == "recording" else None


async def _describe(page: Any, x: float | None, y: float | None) -> dict[str, Any]:
    try:
        facts = await page.evaluate(DESCRIBE_JS, [x, y])
    except Exception:
        logger.debug("A demo step couldn't be described", exc_info=True)
        facts = None
    return facts if isinstance(facts, dict) else {}


def _step_from(kind: str, facts: dict[str, Any], url: str) -> DemoStep:
    site, path = clean_address(url)
    name, unsafe = clean_label(facts.get("name"))
    context, context_unsafe = clean_label(facts.get("context"))
    title, _title_unsafe = clean_label(facts.get("title"), TITLE_CHARS)
    role = re.sub(r"[^a-z]", "", str(facts.get("role") or "").lower())[:24]
    secret = field_secret(facts) if facts else ""
    if not secret and page_secret(facts.get("inputs")):
        secret = "sign_in"
    return DemoStep(0, 0.0, kind, site=site, path=path, title=title, role=role, name=name,
                    context="" if context_unsafe else context, secret=secret, unsafe=unsafe,
                    editable=bool(facts.get("editable")))


def _same_control(a: DemoStep, b: DemoStep) -> bool:
    return (a.role, a.name, a.site, a.path) == (b.role, b.name, b.site, b.path)


async def before_event(demo: Demo, event: dict[str, Any], page: Any) -> bool:
    """Describe one of the person's input events before it plays; True when a step changed."""

    with _LOCK:
        if demo.status != "recording" or _DEMOS.get(demo.conversation_id) is not demo:
            return False
    kind = event.get("type")
    if kind == "mouse":
        return await _mouse(demo, event, page)
    if kind == "key":
        key = str(event.get("key") or "")
        if event.get("action") != "down":
            demo.held.discard(key)
            return False
        if key in _MODIFIERS:
            demo.held.add(key)
            return False
        if key in _NAMED_KEYS:
            facts = await _describe(page, None, None)
            step = _step_from("key", facts, _url(page))
            step.key = key
            with _LOCK:
                demo.typing = False
                _add(demo, step)
            return True
        if key == "Backspace":
            with _LOCK:
                last = demo.steps[-1] if demo.steps else None
                if demo.typing and last is not None and last.kind == "type" and last.value:
                    last.value = last.value[:-1]
                    return True
            return False
        if len(key) == 1 and not demo.held:
            return await _typed(demo, key, page)
        return False
    if kind in {"text", "paste"}:
        return await _typed(demo, str(event.get("text") or ""), page)
    return False


def _url(page: Any) -> str:
    try:
        return str(page.url or "")
    except Exception:
        return ""


async def _mouse(demo: Demo, event: dict[str, Any], page: Any) -> bool:
    action = event.get("action")
    if str(event.get("button") or "left") != "left" or action not in {"down", "up"}:
        return False
    x, y = float(event.get("x", 0)), float(event.get("y", 0))
    if action == "down":
        facts = await _describe(page, x, y)
        demo.pressed = {"x": x, "y": y, "facts": facts, "url": _url(page),
                        "clicks": int(event.get("clicks") or 1)}
        return False
    pressed, demo.pressed = demo.pressed, None
    if pressed is None or math.hypot(x - pressed["x"], y - pressed["y"]) > CLICK_SLOP:
        return False
    facts = pressed["facts"]
    if not facts.get("role") and not facts.get("name"):
        return False  # A click on nothing in particular (the page's background) isn't a step.
    step = _step_from("click", facts, pressed["url"])
    with _LOCK:
        last = demo.steps[-1] if demo.steps else None
        if pressed["clicks"] > 1 and last is not None and last.kind == "click" and _same_control(last, step):
            return False  # The second half of a double click.
        if last is not None and last.kind == "type" and demo.typing and _same_control(last, step):
            return False  # A click back into the field being typed into.
        demo.typing = False
        _add(demo, step)
    return True


async def _typed(demo: Demo, text: str, page: Any) -> bool:
    if not text:
        return False
    if not demo.typing:
        facts = await _describe(page, None, None)
        if facts and not facts.get("editable") and facts.get("role") != "frame":
            return False  # Keys on the page itself (its shortcuts), not typing into a field.
        step = _step_from("type", facts, _url(page))
        if not facts:
            step.secret = "hidden"  # The field couldn't be described: what's typed isn't kept.
        step.role = step.role or "textbox"
        with _LOCK:
            last = demo.steps[-1] if demo.steps else None
            if last is not None and last.kind == "click" and _same_control(last, step):
                demo.steps.pop()  # The click into the field is part of typing into it.
                step.at = last.at
            demo.typing = True
            _add(demo, step)
    with _LOCK:
        step = demo.steps[-1] if demo.steps else None
        if step is None or step.kind != "type":
            return False
        if step.secret:
            return False  # Never kept: not a character of it.
        step.value = (step.value + text)[:VALUE_CHARS]
        from row_bot.evolution import contains_unredacted_sensitive_text

        if contains_unredacted_sensitive_text(step.value):
            step.value, step.secret, step.always = "", "sensitive", False
        return True


def navigated(conversation_id: str, action: str, *, url: str = "", title: str = "") -> bool:
    """One of the pane's own controls was used (the address bar, Back, Forward, Reload or a tab)."""

    kinds = {"navigate": "open", "back": "back", "forward": "forward", "reload": "reload",
             "new_tab": "tab", "switch_tab": "tab", "close_tab": "tab"}
    if action not in kinds:
        return False
    with _LOCK:
        demo = _expire(str(conversation_id))
        if demo is None or demo.status != "recording":
            return False
        site, path = clean_address(url)
        name, _unsafe = clean_label(title, TITLE_CHARS)
        step = DemoStep(0, 0.0, kinds[action], site=site, path=path, title=name)
        if action != "navigate" and kinds[action] == "tab":
            step.key = action.removesuffix("_tab")
        demo.typing = False
        demo.pressed = None
        _add(demo, step)
        return True


# ── What the person sees ─────────────────────────────────────────────────────

_NOUNS = {
    "button": "button", "link": "link", "textbox": "field", "searchbox": "search box", "combobox": "list",
    "checkbox": "checkbox", "radio": "option", "tab": "tab", "menuitem": "menu item", "option": "option",
    "switch": "switch", "slider": "slider", "frame": "box",
}


def _control(step: DemoStep) -> str:
    noun = _NOUNS.get(step.role, "")
    if step.name:
        return f"the “{step.name}” {noun}".rstrip() if noun else f"“{step.name}”"
    return f"a {noun}" if noun else "the page"


def describe(step: DemoStep) -> str:
    """One line for the person: "Clicked the “Statements” link"."""

    where = f"{step.site}{step.path}" if step.site else "a page"
    if step.kind == "open":
        return f"Opened {where}"
    if step.kind == "back":
        return "Went back"
    if step.kind == "forward":
        return "Went forward"
    if step.kind == "reload":
        return "Reloaded the page"
    if step.kind == "tab":
        return {"new": "Opened a new tab", "close": "Closed a tab"}.get(step.key, "Switched tab")
    if step.kind == "key":
        return f"Pressed {step.key}"
    if step.kind == "type":
        return f"Typed into {_control(step)}"
    return f"Clicked {_control(step)}"


def step_view(step: DemoStep) -> dict[str, Any]:
    return {
        "id": step.id,
        "at": step.at,
        "kind": step.kind,
        "text": describe(step),
        "site": step.site,
        "path": step.path,
        "title": step.title,
        "value": "" if step.secret else step.value,
        "secret": step.secret,
        "secret_text": _SECRET_TEXT.get(step.secret, ""),
        "always": step.always,
        "input": step.kind == "type" and not step.secret and bool(step.value) and not step.always,
        "unsafe": step.unsafe,
        "narration": step.narration,
    }


def view(demo: Demo | None) -> dict[str, Any] | None:
    if demo is None:
        return None
    return {
        "id": demo.id,
        "status": demo.status,
        "ended": demo.ended,
        "elapsed_seconds": round(demo.elapsed, 1),
        "max_steps": MAX_STEPS,
        "max_seconds": MAX_SECONDS,
        "narrated": demo.narrated,
        "steps": [step_view(step) for step in demo.steps],
    }


def summary(conversation_id: str) -> dict[str, Any] | None:
    """The pane's line: whether a demo records, how many steps and for how long."""

    demo = current(conversation_id)
    if demo is None:
        return None
    return {"id": demo.id, "status": demo.status, "ended": demo.ended, "steps": len(demo.steps),
            "elapsed_seconds": round(demo.elapsed, 1)}


def page(conversation_id: str) -> dict[str, Any]:
    return {"conversation_id": str(conversation_id), "demo": view(current(conversation_id))}
