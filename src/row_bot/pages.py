"""Pages (N5): plans, checklists, dashboards, comparisons, forms and small tools the agent makes.

A page is one HTML document with inline CSS and JS (at most ``MAX_HTML_BYTES``) and optional JSON data
(at most ``MAX_DATA_BYTES``), made and changed by the agent's ``page_create`` / ``page_update`` and shown
beside the chat it belongs to. Its last ``KEEP_VERSIONS`` versions are kept.

A page never runs in Row-Bot's origin. Its document is served once to a frame the client creates with
``sandbox="allow-scripts"`` only (an opaque origin), and with a policy header that repeats that sandbox, so
even a direct load in a tab is opaque: no network, cookies, storage, tools or Row-Bot API. The only things
a page can do go through the narrow bridge (``static/pages/rowbot-page.js``), each checked by the client and
again here:

- read the data the agent attached;
- keep its own small state (at most ``MAX_STATE_BYTES``), saved with the chat on every device;
- record the person's decisions (approve, change with a note, skip…).

Decisions only gather under the page. The person's own click on "Send to Row-Bot", outside the frame,
posts them to the chat as one message (``claim`` from ``conversation.submit``), labelled as from the page:
the agent reads them, and the Auto reviewer never takes them as the person's request, so a decision never
approves a tool call. A page can't send anything by itself.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PAGE_ID = re.compile(r"page-[0-9a-f]{8}")
DECISION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
FRAME_TOKEN = re.compile(r"[0-9a-f]{32}")
KB = 1024
MAX_HTML_BYTES = 1024 * KB
MAX_DATA_BYTES = 1024 * KB
MAX_STATE_BYTES = 256 * KB
MAX_TITLE = 120
MAX_CHOICE = 40
MAX_NOTE = 2000
MAX_LABEL = 200
MAX_DECISIONS = 200
KEEP_VERSIONS = 10
KEEP_SENT = 20
MAX_PAGES_PER_CONVERSATION = 50
# Writes from one page (state and decisions) and frames made for it, each a minute.
WRITES_PER_MINUTE = 120
FRAMES_PER_MINUTE = 30
LIBRARIES = ("charts", "rowbot-style")
# The bundled chart library: Chart.js 4.5.1 (MIT), static/pages/chart.umd.min.js, exactly these bytes.
CHART_JS_VERSION = "4.5.1"
CHART_JS_SHA256 = "48444a82d4edcb5bec0f1965faacdde18d9c17db3063d042abada2f705c9f54a"
# Row-Bot's colour tokens a page receives (as --rb-<name>), for the theme in use.
THEME_TOKENS = (
    "canvas", "surface", "surface-raised", "surface-sunken", "surface-hover", "surface-pressed",
    "text-primary", "text-secondary", "text-muted", "text-inverse",
    "border-subtle", "border-control", "border-strong", "shadow-color",
    "accent-solid", "accent-on-solid", "focus-ring",
    "status-info-text", "status-info-background", "status-success-text", "status-success-background",
    "status-warning-text", "status-warning-background", "status-danger-text", "status-danger-background",
    "chart-series-1", "chart-series-2", "chart-series-3", "chart-series-4", "chart-series-5", "chart-series-6",
    "chart-grid", "chart-axis",
)
_COLOUR = re.compile(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?")
_POLICY = (
    "default-src 'none'",
    "script-src 'unsafe-inline'",
    "style-src 'unsafe-inline'",
    "img-src data: blob:",
    "font-src data:",
    "media-src data: blob:",
    "connect-src 'none'",
    "frame-src 'none'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'self'",
    "sandbox allow-scripts",
)
CONTENT_SECURITY_POLICY = "; ".join(_POLICY)
# A downloaded copy has no headers: the same policy as a meta tag, less what a meta can't carry.
STANDALONE_POLICY = "; ".join(item for item in _POLICY if not item.startswith(("frame-ancestors", "sandbox")))
FRAME_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), clipboard-read=(), clipboard-write=(), usb=(), "
                          "serial=(), payment=(), display-capture=()",
}
_FOLDER = "pages"
_LOCK = threading.RLock()
_RECORDS: dict[str, "Page"] = {}
_LOADED_ROOT: Path | None = None
_FRAMES: dict[str, tuple[float, str]] = {}  # One frame's document, served once within FRAME_SECONDS.
FRAME_SECONDS = 120
_RATES: dict[tuple[str, str], list[float]] = {}


class PageError(ValueError):
    """A page can't be read or changed; ``code`` is a stable, client-safe word, ``str()`` plain words."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


@dataclass
class Page:
    page_id: str
    conversation_id: str
    title: str
    created_at: float
    updated_at: float
    version: int = 1
    libraries: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    state_revision: int = 0
    # Decisions not yet sent, by id: {choice, note, label, at}.
    decisions: dict[str, dict[str, Any]] = field(default_factory=dict)
    # What the person sent, newest last: {submission_id, at, decisions: [{id, choice, note, label}]}.
    sent: list[dict[str, Any]] = field(default_factory=list)
    # The versions kept: {version, at, title, libraries, html_bytes, data_bytes}.
    versions: list[dict[str, Any]] = field(default_factory=list)


def _now() -> float:
    return time.time()


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _plain(value: Any, maximum: int) -> str:
    """One line of plain text: control characters dropped, at most ``maximum`` characters."""

    text = "".join(character for character in str(value or "") if character == " " or character.isprintable())
    return " ".join(text.split())[:maximum]


def _note(value: Any) -> str:
    text = "".join(character for character in str(value or "") if character in "\n\t" or character.isprintable())
    return text.strip()[:MAX_NOTE]


def _json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


# ── Storage ────────────────────────────────────────────────────────────────


def pages_root() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir() / _FOLDER


def _folder(page: Page) -> Path:
    from row_bot.tool_outputs import thread_folder_name

    return pages_root() / thread_folder_name(page.conversation_id) / page.page_id


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.part")
    with open(partial, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(partial, path)


def _save(page: Page) -> None:
    _write(_folder(page) / "page.json", json.dumps(asdict(page), ensure_ascii=False))


def _load(path: Path) -> Page | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not PAGE_ID.fullmatch(str(data.get("page_id") or "")) or not data.get("conversation_id"):
            return None
        return Page(**data)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _ensure_loaded() -> None:
    global _LOADED_ROOT
    root = pages_root()
    with _LOCK:
        if _LOADED_ROOT == root:
            return
        _RECORDS.clear()
        _LOADED_ROOT = root
        if not root.is_dir():
            return
        for path in sorted(root.glob("*/page-*/page.json")):
            page = _load(path)
            if page is not None:
                _RECORDS[page.page_id] = page


def get(page_id: str) -> Page | None:
    if not PAGE_ID.fullmatch(str(page_id or "")):
        return None
    _ensure_loaded()
    with _LOCK:
        return _RECORDS.get(page_id)


def require(page_id: str) -> Page:
    page = get(page_id)
    if page is None:
        raise PageError("chat_page_unavailable", "That page isn't here any more.")
    return page


def list_pages(conversation_id: str | None = None) -> list[Page]:
    """Most recently changed first."""

    _ensure_loaded()
    with _LOCK:
        items = [page for page in _RECORDS.values()
                 if conversation_id is None or page.conversation_id == conversation_id]
    return sorted(items, key=lambda page: page.updated_at, reverse=True)


def find(conversation_id: str, reference: str) -> Page | None:
    """A page of this conversation by its id, or by its exact title (the newest with it)."""

    text = str(reference or "").strip()
    page = get(text)
    if page is not None:
        return page if page.conversation_id == conversation_id else None
    folded = text.casefold()
    return next((item for item in list_pages(conversation_id) if item.title.casefold() == folded), None)


def delete_conversation(conversation_id: str) -> int:
    """Forget a deleted conversation's pages and remove their files."""

    from row_bot.tool_outputs import thread_folder_name

    _ensure_loaded()
    with _LOCK:
        for page_id in [key for key, page in _RECORDS.items() if page.conversation_id == conversation_id]:
            _RECORDS.pop(page_id, None)
    folder = pages_root() / thread_folder_name(conversation_id)
    if not folder.exists():
        return 0
    shutil.rmtree(folder, ignore_errors=True)
    return 1


# ── Making and changing pages ──────────────────────────────────────────────


def _title(value: Any) -> str:
    title = _plain(value, MAX_TITLE)
    if not title:
        raise PageError("chat_page_invalid", "Give the page a short title.")
    return title


def _html(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PageError("chat_page_invalid", "The page needs its HTML: one document with inline CSS and JS.")
    if len(value.encode("utf-8")) > MAX_HTML_BYTES:
        raise PageError("chat_page_too_large", "The page's HTML is over 1 MB. Make it smaller, or move data into data.")
    if not re.search(r"<html[\s>]", value, re.IGNORECASE):
        value = f"<!doctype html>\n<html><head></head><body>\n{value}\n</body></html>"
    return value


def _data(value: Any) -> Any:
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else None
        except json.JSONDecodeError as exc:
            raise PageError("chat_page_invalid", "data must be JSON (an object or a list).") from exc
    if value is not None and not isinstance(value, (dict, list)):
        raise PageError("chat_page_invalid", "data must be JSON (an object or a list).")
    if value is not None and _json_bytes(value) > MAX_DATA_BYTES:
        raise PageError("chat_page_too_large", "The page's data is over 1 MB. Keep only what the page shows.")
    return value


def _libraries(value: Any) -> list[str]:
    names = [str(item).strip().casefold() for item in (value or [])]
    unknown = [name for name in names if name not in LIBRARIES]
    if unknown:
        raise PageError("chat_page_invalid", f"Unknown library {unknown[0]!r}: use charts, rowbot-style or both.")
    return [name for name in LIBRARIES if name in names]


def _write_version(page: Page, html: str, data: Any, what: str) -> None:
    folder = _folder(page)
    _write(folder / f"v{page.version}.html", html)
    _write(folder / f"v{page.version}.json", json.dumps(data, ensure_ascii=False))
    page.versions.append({"version": page.version, "at": page.updated_at, "title": page.title,
                          "libraries": list(page.libraries), "what": what,
                          "html_bytes": len(html.encode("utf-8")), "data_bytes": _json_bytes(data)})
    for old in page.versions[:-KEEP_VERSIONS]:
        for suffix in ("html", "json"):
            try:
                (folder / f"v{old['version']}.{suffix}").unlink(missing_ok=True)
            except OSError:
                logger.debug("Could not remove an old page version", exc_info=True)
    page.versions = page.versions[-KEEP_VERSIONS:]


def create(conversation_id: str, title: Any, html: Any, data: Any = None, libraries: Any = None) -> Page:
    if not conversation_id:
        raise PageError("chat_page_invalid", "A page belongs to a conversation.")
    page_title, page_html, page_data, names = _title(title), _html(html), _data(data), _libraries(libraries)
    if len(list_pages(conversation_id)) >= MAX_PAGES_PER_CONVERSATION:
        raise PageError("chat_page_invalid", f"This chat already has {MAX_PAGES_PER_CONVERSATION} pages. Update one "
                                        "of them instead.")
    now = _now()
    page = Page(page_id=f"page-{uuid.uuid4().hex[:8]}", conversation_id=conversation_id, title=page_title,
                created_at=now, updated_at=now, libraries=names)
    with _LOCK:
        _ensure_loaded()
        _write_version(page, page_html, page_data, "made")
        _save(page)
        _RECORDS[page.page_id] = page
    return page


def update(page: Page, *, title: Any = None, html: Any = None, data: Any = None, libraries: Any = None,
           data_given: bool = False) -> Page:
    """A new version from what changed; the rest comes from the current version. State and decisions stay."""

    if title is None and html is None and not data_given and libraries is None:
        raise PageError("chat_page_invalid", "Nothing to change: give html, data, title or libraries.")
    new_title = _title(title) if title is not None else page.title
    new_html = _html(html) if html is not None else read_html(page)
    new_data = _data(data) if data_given else read_data(page)
    names = _libraries(libraries) if libraries is not None else list(page.libraries)
    what = ", ".join(name for name, given in (("title", title is not None), ("html", html is not None),
                                              ("data", data_given), ("libraries", libraries is not None)) if given)
    with _LOCK:
        page.title, page.libraries = new_title, names
        page.version += 1
        page.updated_at = _now()
        _write_version(page, new_html, new_data, what)
        _save(page)
    return page


def _version_path(page: Page, version: int | None, suffix: str) -> Path:
    number = page.version if version is None else int(version)
    if not any(item["version"] == number for item in page.versions):
        raise PageError("chat_page_unavailable", f"Version {number} isn't kept (the last {KEEP_VERSIONS} are).")
    return _folder(page) / f"v{number}.{suffix}"


def read_html(page: Page, version: int | None = None) -> str:
    try:
        return _version_path(page, version, "html").read_text(encoding="utf-8")
    except OSError as exc:
        raise PageError("chat_page_unavailable", "That page's file can't be read.") from exc


def read_data(page: Page, version: int | None = None) -> Any:
    try:
        return json.loads(_version_path(page, version, "json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PageError("chat_page_unavailable", "That page's data can't be read.") from exc


# ── The bridge's writes: state and decisions ─────────────────────────────────


def _rate(page_id: str, kind: str, limit: int) -> None:
    now = time.monotonic()
    with _LOCK:
        recent = [at for at in _RATES.get((page_id, kind), []) if now - at < 60]
        if len(recent) >= limit:
            raise PageError("chat_page_rate_limited", "This page is busy. Wait a moment.")
        _RATES[(page_id, kind)] = [*recent, now]
        if len(_RATES) > 1024:
            for key in list(_RATES)[:256]:
                _RATES.pop(key, None)


def set_state(page_id: str, state: Any) -> Page:
    page = require(page_id)
    if not isinstance(state, dict):
        raise PageError("chat_page_invalid", "A page's state is an object.")
    if _json_bytes(state) > MAX_STATE_BYTES:
        raise PageError("chat_page_too_large", "This page's saved state is over 256 KB.")
    _rate(page_id, "write", WRITES_PER_MINUTE)
    with _LOCK:
        page.state = json.loads(json.dumps(state))
        page.state_revision += 1
        _save(page)
    return page


def decide(page_id: str, decision_id: Any, choice: Any, note: Any = "", label: Any = "") -> Page:
    """Record (or, with no choice, clear) the person's decision about one item. Nothing is sent."""

    page = require(page_id)
    key = str(decision_id or "")
    if not DECISION_ID.fullmatch(key):
        raise PageError("chat_page_invalid", "A decision needs a short id (letters, digits, - _ . :).")
    _rate(page_id, "write", WRITES_PER_MINUTE)
    with _LOCK:
        if choice is None:
            page.decisions.pop(key, None)
        else:
            word = _plain(choice, MAX_CHOICE)
            if not word:
                raise PageError("chat_page_invalid", "A decision needs a choice.")
            if key not in page.decisions and len(page.decisions) >= MAX_DECISIONS:
                raise PageError("chat_page_too_large", f"A page can hold {MAX_DECISIONS} decisions before sending.")
            page.decisions[key] = {"choice": word, "note": _note(note), "label": _plain(label, MAX_LABEL),
                                   "at": _now()}
        _save(page)
    return page


_CHOICE_WORDS = {
    "approve": "Approved", "approved": "Approved", "accept": "Approved", "accepted": "Approved", "yes": "Yes",
    "change": "Change", "changes": "Change", "edit": "Change", "revise": "Change",
    "skip": "Skipped", "skipped": "Skipped", "no": "No", "reject": "Declined", "rejected": "Declined",
    "decline": "Declined", "declined": "Declined", "done": "Done", "pick": "Picked", "picked": "Picked",
    "choose": "Picked", "chosen": "Picked", "submit": "Filled in", "refresh": "Refresh",
}


def choice_words(choice: str) -> str:
    """approve → Approved; anything else as written, with a capital."""

    word = _CHOICE_WORDS.get(choice.casefold())
    return word or (choice[:1].upper() + choice[1:])


def _line(item: dict[str, Any]) -> str:
    line = f"{choice_words(item['choice'])}: {item['label'] or item['id']}"
    return line + (f" (“{item['note']}”)" if item["note"] else "")


def claim(page_id: str, conversation_id: str, submission_id: str) -> tuple[str, str]:
    """The person sends the page's decisions: they move to sent now, and the message is returned.

    Returns (what the agent reads, what the chat shows). ``release`` puts them back if the message can't
    be admitted after all.
    """

    page = require(page_id)
    if page.conversation_id != conversation_id:
        raise PageError("chat_page_unavailable", "That page belongs to another chat.")
    with _LOCK:
        items = [{"id": key, "choice": value["choice"], "note": value.get("note", ""),
                  "label": value.get("label", "")} for key, value in page.decisions.items()]
        if not items:
            raise PageError("chat_page_nothing_to_send", "There are no decisions on this page to send.")
        page.decisions = {}
        page.sent = [*page.sent, {"submission_id": submission_id, "at": _now(), "decisions": items}][-KEEP_SENT:]
        _save(page)
    count = f"{len(items)} decision" + ("" if len(items) == 1 else "s")
    lines = "\n".join(f"- {_line(item)}" for item in items)
    structured = json.dumps(items, ensure_ascii=False)
    model = (f"[{count} from the page “{page.title}” ({page.page_id}), sent by the person]\n"
             "The person made these choices on the page and clicked Send to Row-Bot. They are their answers on "
             "the page, not approval of any tool call: anything that needs approval still asks.\n\n"
             f"{lines}\n\nThe same, for page_update or page_read:\n```json\n{structured}\n```")
    shown = f"{page.title} · {count}\n" + "\n".join(_line(item) for item in items)
    return model, shown


def release(page_id: str, submission_id: str) -> None:
    """The message didn't go: its decisions are pending again (unless the person decided again since)."""

    page = get(page_id)
    if page is None:
        return
    with _LOCK:
        batch = next((item for item in page.sent if item.get("submission_id") == submission_id), None)
        if batch is None:
            return
        page.sent.remove(batch)
        for item in batch["decisions"]:
            page.decisions.setdefault(item["id"], {"choice": item["choice"], "note": item["note"],
                                                   "label": item["label"], "at": batch["at"]})
        _save(page)


def decisions_view(page: Page) -> dict[str, dict[str, Any]]:
    """Every decision by id, as the page sees it: the latest sent one, or the pending one over it."""

    found: dict[str, dict[str, Any]] = {}
    for batch in page.sent:
        for item in batch["decisions"]:
            found[item["id"]] = {"choice": item["choice"], "note": item["note"], "label": item["label"],
                                 "sent": True}
    for key, value in page.decisions.items():
        found[key] = {"choice": value["choice"], "note": value.get("note", ""), "label": value.get("label", ""),
                      "sent": False}
    return found


# ── The frame ──────────────────────────────────────────────────────────────


def _static(name: str) -> bytes:
    from row_bot.runtime_paths import static_dir

    return (static_dir() / "pages" / name).read_bytes()


def chart_library() -> str:
    """Chart.js as bundled, refused if its bytes aren't exactly the pinned release."""

    content = _static("chart.umd.min.js")
    if hashlib.sha256(content).hexdigest() != CHART_JS_SHA256:
        raise PageError("chat_page_unavailable", "Row-Bot's chart library has changed on disk, so pages can't use it.")
    return content.decode("utf-8")


def theme_tokens(theme: Any) -> dict[str, Any]:
    """{mode, light, dark} with only Row-Bot's own token names and plain colours: anything else is dropped."""

    theme = theme if isinstance(theme, dict) else {}
    result: dict[str, Any] = {"mode": "dark" if theme.get("mode") == "dark" else "light"}
    for mode in ("light", "dark"):
        values = theme.get(mode) if isinstance(theme.get(mode), dict) else {}
        result[mode] = {name: str(values[name]) for name in THEME_TOKENS
                        if isinstance(values.get(name), str) and _COLOUR.fullmatch(values[name])}
    return result


def _script_json(value: Any) -> str:
    """JSON safe inside a <script>: no '<', '>', '&' or line separators as themselves."""

    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    for character, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), (" ", "\\u2028"),
                               (" ", "\\u2029")):
        text = text.replace(character, escaped)
    return text


def _token_css(tokens: dict[str, Any]) -> str:
    def block(values: dict[str, str]) -> str:
        return "".join(f"--rb-{name}:{value};" for name, value in values.items())

    light, dark = block(tokens["light"]), block(tokens["dark"])
    css = f":root{{{light}}}:root[data-theme=\"dark\"]{{{dark}}}"
    if dark:  # A downloaded copy follows the computer's theme until the page sets its own.
        css += f"@media (prefers-color-scheme: dark){{:root:not([data-theme]){{{dark}}}}}"
    return css


def document(page: Page, theme: Any, *, standalone: bool = False) -> str:
    """The page's current version with Row-Bot's tokens, its libraries and the bridge, ready to show."""

    tokens = theme_tokens(theme)
    boot = {
        "page": page.page_id, "title": page.title, "version": page.version, "mode": tokens["mode"],
        "standalone": standalone, "data": read_data(page), "state": page.state,
        "decisions": decisions_view(page),
        "tokens": {"light": tokens["light"], "dark": tokens["dark"]},
    }
    parts = [f'<style id="rowbot-tokens">{_token_css(tokens)}</style>']
    if "rowbot-style" in page.libraries:
        parts.append(f'<style id="rowbot-style">{_static("rowbot-style.css").decode("utf-8")}</style>')
    shim = _static("rowbot-page.js").decode("utf-8").strip()
    parts.append(f'<script id="rowbot-bridge">{shim}({_script_json(boot)});</script>')
    if "charts" in page.libraries:
        parts.append(f'<script id="rowbot-charts">{chart_library()}</script>')
    injected = "\n".join(parts)
    html = read_html(page)
    if standalone:
        # Opened from disk there are no headers: the policy comes first, before any of the page, so no
        # script of the page runs without it. The page's own doctype, html and head tags then merge in.
        html = re.sub(r"\A\s*<!doctype[^>]*>", "", html, flags=re.IGNORECASE)
        return ('<!doctype html>\n<html><head>\n<meta charset="utf-8">\n'
                f'<meta http-equiv="Content-Security-Policy" content="{STANDALONE_POLICY}">\n'
                f"{injected}\n{html}")
    for pattern in (r"<head(?:\s[^>]*)?>", r"<html(?:\s[^>]*)?>"):
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            prefix = "" if pattern.startswith("<head") else "<head>"
            suffix = "" if pattern.startswith("<head") else "</head>"
            return html[:match.end()] + "\n" + prefix + injected + suffix + "\n" + html[match.end():]
    return injected + "\n" + html


def render_frame(page_id: str, theme: Any) -> str:
    """Make one frame address for the page: its document, served once, within ``FRAME_SECONDS``."""

    page = require(page_id)
    _rate(page_id, "frame", FRAMES_PER_MINUTE)
    return _serve_once(document(page, theme))


# An inline visual in a reply (F3) reuses the page frame: served once, under ``FRAME_HEADERS`` (no network,
# sandboxed again). It has no bridge; it only tells the chat its height, and hears which theme is shown.
MAX_VISUAL_BYTES = 200 * KB
VISUAL_FRAMES_PER_MINUTE = 120
_VISUAL_BOOT = (
    "(function(){var last=0;function send(){var h=Math.ceil(document.documentElement.getBoundingClientRect().height);"
    "if(h!==last){last=h;parent.postMessage({type:'rowbot-visual-height',h:h},'*');}}"
    "new ResizeObserver(send).observe(document.documentElement);addEventListener('load',send);"
    "addEventListener('message',function(e){if(e.source===parent&&e.data&&e.data.type==='rowbot-visual-theme')"
    "{document.documentElement.dataset.theme=e.data.mode==='dark'?'dark':'light';}});})();"
)
# A quiet base look under the visual's own styles (which come later and win): the theme's colour scheme,
# Row-Bot's text colour and font, its accent on form controls, and modest margins.
_VISUAL_STYLE = (
    ":root{color-scheme:light}:root[data-theme=\"dark\"]{color-scheme:dark}"
    "html,body{margin:0;background:transparent}"
    "body{padding:12px;color:var(--rb-text-primary);font:14px/1.5 system-ui,-apple-system,\"Segoe UI\",Roboto,"
    "sans-serif;-webkit-font-smoothing:antialiased}"
    "body>:first-child{margin-top:0}body>:last-child{margin-bottom:0}"
    "h1,h2,h3,h4{margin:0 0 .5em;line-height:1.25;font-weight:600}h1{font-size:1.4em}h2{font-size:1.2em}"
    "h3,h4{font-size:1em}p,ul,ol,figure,table{margin:0 0 .75em}"
    "a{color:var(--rb-accent-solid)}small,figcaption{color:var(--rb-text-secondary)}"
    "input,select,textarea,button,progress,meter{accent-color:var(--rb-accent-solid);font:inherit;color:inherit}"
    "input[type=range]{width:100%;margin:.5em 0}"
    "button,select,input:not([type=range]):not([type=checkbox]):not([type=radio]),textarea{"
    "padding:.35em .7em;border:1px solid var(--rb-border-control);border-radius:8px;"
    "background:var(--rb-surface-raised)}button{cursor:pointer}"
    ":focus-visible{outline:2px solid var(--rb-focus-ring);outline-offset:2px}"
    "table{border-collapse:collapse}th,td{padding:.35em .6em;border-bottom:1px solid var(--rb-border-subtle);"
    "text-align:left}"
    "body>svg{display:block;margin:0 auto}svg,img,canvas,video{max-width:100%;height:auto}"
)


def render_visual_frame(conversation_id: str, html: str, theme: Any) -> str:
    """One frame address for a visual Row-Bot drew in this chat: its HTML/SVG with the theme's tokens."""

    if not isinstance(html, str) or not html.strip():
        raise PageError("chat_page_invalid", "The visual is empty.")
    if len(html.encode("utf-8")) > MAX_VISUAL_BYTES:
        raise PageError("chat_page_too_large", "The visual is over 200 KB.")
    _rate(f"visual:{conversation_id}", "frame", VISUAL_FRAMES_PER_MINUTE)
    tokens = theme_tokens(theme)
    return _serve_once(
        f'<!doctype html><html data-theme="{tokens["mode"]}"><head><meta charset="utf-8">'
        f'<style id="rowbot-tokens">{_token_css(tokens)}{_VISUAL_STYLE}</style>'
        f"<script>{_VISUAL_BOOT}</script></head><body>\n{html}\n</body></html>"
    )


def _serve_once(html: str) -> str:
    token = secrets.token_hex(16)
    now = time.monotonic()
    with _LOCK:
        for old in [key for key, (at, _html) in _FRAMES.items() if now - at > FRAME_SECONDS]:
            _FRAMES.pop(old, None)
        while len(_FRAMES) >= 64:
            _FRAMES.pop(next(iter(_FRAMES)))
        _FRAMES[token] = (now, html)
    return f"/page-frames/{token}"


def frame(token: str) -> tuple[str, dict[str, str]]:
    """The frame's document and its headers, once: a second load (the page navigating, or anyone) gets none."""

    if not isinstance(token, str) or not FRAME_TOKEN.fullmatch(token):
        raise PageError("chat_page_unavailable")
    with _LOCK:
        found = _FRAMES.pop(token, None)
    if found is None or time.monotonic() - found[0] > FRAME_SECONDS:
        raise PageError("chat_page_unavailable")
    return found[1], dict(FRAME_HEADERS)


def download_name(page: Page) -> str:
    stem = re.sub(r"[^A-Za-z0-9 _-]+", "", page.title).strip().replace(" ", "-")[:80] or "page"
    return f"{stem}.html"


# ── Views ──────────────────────────────────────────────────────────────────


def _conversation_titles(ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    try:
        from row_bot import threads

        return {str(row[0]): str(row[1] or "") for row in threads._list_threads() if str(row[0]) in ids}
    except Exception:
        logger.debug("Conversation titles for pages unavailable", exc_info=True)
        return {}


def view(page: Page, conversation_title: str | None = None) -> dict[str, Any]:
    if conversation_title is None:
        conversation_title = _conversation_titles({page.conversation_id}).get(page.conversation_id, "")
    return {
        "page_id": page.page_id,
        "conversation_id": page.conversation_id,
        "conversation_title": conversation_title[:256],
        "title": page.title,
        "version": page.version,
        "libraries": list(page.libraries),
        "created_at": _iso(page.created_at),
        "updated_at": _iso(page.updated_at),
        "state_revision": page.state_revision,
        "pending": len(page.decisions),
        # Those not sent yet first: the footer lists them, whatever was sent before.
        "decisions": sorted(({"id": key, **value} for key, value in decisions_view(page).items()),
                            key=lambda item: item["sent"])[:MAX_DECISIONS],
    }


def views(conversation_id: str | None = None) -> dict[str, Any]:
    items = list_pages(conversation_id)[:500]
    titles = _conversation_titles({page.conversation_id for page in items})
    return {"items": [view(page, titles.get(page.conversation_id, "")) for page in items]}


def summary(page: Page, *, made: bool) -> str:
    """The tool's result: its first line names the page, so the chat shows its card."""

    first = f"Page {page.page_id}: “{page.title}”, version {page.version}."
    if made:
        return (first + "\nIt's in the chat as a card the person opens beside the chat. Their decisions come back "
                "as a message only when they click Send to Row-Bot. Change it with page_update (state and "
                "decisions stay); see its state and unsent decisions with page_read.")
    return first + "\nThe person sees the new version when they open it (or at once, if it's open)."
