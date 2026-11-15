"""What Row-Bot's browser asks before doing (D19), and safe history URLs.

Paying, buying, booking with a charge, sending, posting, deleting and changing
account settings ask first: under Ask the person decides; under Auto the
reviewer may approve what clearly matches the person's request; Hands-off
runs it. Everything else on a page just runs, as a person would click it.
Sending on one of Row-Bot's own sandboxes runs too: it stays in that sandbox's
made-up data on this computer, and the sandbox's own Row-Bot asks before
anything real. Paying, deleting, account changes and uploads there still ask.

An action is classified by the control's own words (its accessible name and
role), the form around it (card fields, a search form) and the page (its
address, title and whether it shows a price).
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Literal
from urllib.parse import urlparse, urlunparse

Kind = Literal["pay", "book", "send", "delete", "account", "upload"]


def history_url(url: str) -> str:
    """An address without its query or fragment, for history and status."""

    try:
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    except Exception:
        return "[invalid URL]"


def site_of(url: str) -> str:
    """``homeserve.co.uk`` for ``https://www.homeserve.co.uk/claim``."""

    host = str(urlparse(str(url or "")).hostname or "").casefold()
    return host[4:] if host.startswith("www.") else host


def navigation_policy(url: str, current_url: str = "") -> tuple[str, str]:
    """``("allow", "")`` for an ordinary http(s) address, ``("block", why)`` otherwise.

    Addresses on this computer and the local network are decided by
    ``local_access`` (F4); this only refuses what no browser should open.
    """

    del current_url  # Where the page is now doesn't change what may open.
    try:
        parsed = urlparse(str(url or ""))
    except Exception:
        return "block", "That isn't a web address."
    if parsed.scheme == "file":
        # A file page could pull in any other file on this computer, which the request guard can't see;
        # a server started in this chat opens without asking (5.3.1 A2).
        return "block", ("Files don't open in Row-Bot's browser. To preview a page, start a server in its "
                         "folder in this chat (python -m http.server 8000) and open http://localhost:8000; "
                         "it opens without asking. Stop the server once you're done with it.")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "block", "Only http and https addresses open in Row-Bot's browser."
    if parsed.username or parsed.password:
        return "block", "Addresses with a name or password in them don't open."
    return "allow", ""


@dataclass(frozen=True)
class PageFacts:
    """What the page around a control shows (never its values)."""

    url: str = ""
    title: str = ""
    card_fields: bool = False
    shows_price: bool = False
    search_form: bool = False
    form_submit_label: str = ""


@dataclass(frozen=True)
class Consequence:
    kind: Kind
    site: str
    control: str
    # A sandbox's page (5.3.1 B4): its name. What happens there stays in its made-up data, on this computer.
    sandbox: str = ""

    @property
    def risk(self) -> str:
        return _RISKS[self.kind]

    def question(self) -> str:
        if self.sandbox:
            verb = {"pay": "Pay", "book": "Book", "send": "Send", "delete": "Delete",
                    "account": "Change the account", "upload": "Upload"}[self.kind]
            return f"{verb} in the {self.sandbox} sandbox?"
        return {
            "pay": f"Pay on {self.site}?",
            "book": f"Book on {self.site}?",
            "send": f"Send on {self.site}?",
            "delete": f"Delete on {self.site}?",
            "account": f"Change your account on {self.site}?",
            "upload": f"Upload to {self.site}?",
        }[self.kind]

    def label(self) -> str:
        return {
            "pay": "Pay or buy on a website",
            "book": "Book on a website",
            "send": "Send or post on a website",
            "delete": "Delete on a website",
            "account": "Change account settings on a website",
            "upload": "Upload a file to a website",
        }[self.kind]

    def description(self, verb: str = "click") -> str:
        what = {
            "pay": "This can charge you.",
            "book": "This books it, and the page shows a charge.",
            "send": "This sends it from your account.",
            "delete": "This may not be undoable.",
            "account": "This changes your account.",
            "upload": "The site gets a copy of the file.",
        }[self.kind]
        if self.sandbox:
            what = "It stays in the sandbox's made-up data: nothing leaves this computer."
            where = f"in the {self.sandbox} sandbox"
        else:
            where = f"on {self.site}"
        if self.kind == "upload":
            return f"Row-Bot will upload {self.control} {where}. {what}"
        return f'Row-Bot will {verb} "{self.control}" {where}. {what}'


_RISKS: dict[str, str] = {
    "pay": "pays or buys something on a website, which can charge the person",
    "book": "books something on a website that shows a charge",
    "send": "sends or posts something from the person's account on a website",
    "delete": "deletes something on a website, which may not be undoable",
    "account": "changes the person's account settings on a website",
    "upload": "uploads a file from this computer to a website",
}


def _words(*parts: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", " ".join(str(p or "") for p in parts))).strip()


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w-])(?:" + pattern + r")(?![\w-])", re.IGNORECASE)


_PAY = _rx(
    r"pay(?: now| securely| £?[\d.,]+)?|buy(?: now| it now)?|purchase|place (?:your |my )?order|"
    r"complete (?:your |my )?(?:order|purchase|payment)|confirm (?:and|&) pay|"
    r"confirm (?:order|payment|purchase)|submit (?:order|payment)|authori[sz]e (?:payment|charge)|"
    r"subscribe(?: now)?|start (?:my |your )?subscription|upgrade(?: now| plan)?|donate(?: now)?|"
    r"add payment(?: method)?|save card"
)
_BOOK = _rx(
    r"book(?: now| it| online| (?:a|an|my|the) \w+)?|confirm (?:my |the )?(?:booking|appointment|reservation|visit)|"
    r"reserve(?: now)?|make (?:a |the )?(?:booking|reservation)|schedule (?:an? )?(?:appointment|visit|engineer)"
)
_SEND = _rx(
    r"send(?: message| email| now| it| reply)?|post(?: it| now| reply| comment)?|publish|tweet|reply|"
    r"comment|share|invite|submit(?: claim| request| application| form| review| enquiry| complaint)?|"
    r"send request"
)
_DELETE = _rx(
    r"delete(?: \w+)?|remove(?: \w+)?|erase|discard|empty (?:trash|bin)|"
    r"clear (?:all|history|data)|cancel (?:my |the |this )?(?:subscription|order|booking|appointment|plan|"
    r"membership|account|claim|policy|cover)|close (?:my |this )?account|unsubscribe|deactivate|terminate"
)
_ACCOUNT_CHANGE = _rx(
    r"change (?:my |your )?(?:password|email|e-mail|username|phone(?: number)?|address|details|plan)|"
    r"update (?:my |your )?(?:password|email|e-mail|account|details|profile|payment(?: details)?)|"
    r"sign out (?:of all|everywhere)|log out (?:of all|everywhere)|"
    r"(?:enable|disable|turn (?:on|off)) (?:two|2)[- ]?(?:factor|step)"
)
_SAVE = _rx(r"save(?: changes| settings| preferences| details)?|apply(?: changes)?|update")
_SETTINGS_PAGE = re.compile(r"account|settings|profile|preferences|security|privacy", re.IGNORECASE)
_GENERIC_SUBMIT = _rx(r"confirm|continue|next|submit|done|ok|proceed|finish|complete|place|go")
_SEARCH = _rx(r"search|find|filter|look ?up|go to")
_CLICKABLE_ROLES = frozenset({"button", "link", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "option",
                              "checkbox", "radio", "switch", "generic", ""})


def classify(
    *,
    action: str,
    control: str,
    role: str = "button",
    page: PageFacts | None = None,
) -> Consequence | None:
    """The consequence of one page action, or None when it just runs.

    ``action`` is ``click``, ``submit`` (Enter in a field, or typing with
    submit) or ``select``. ``control`` is the control's accessible name.
    """

    page = page or PageFacts()
    site = site_of(page.url) or "this site"
    found = _consequence(action, control, role, page, site)
    if found is None:
        return None
    from dataclasses import replace

    from row_bot import sandboxes

    named = sandboxes.name_at(page.url)
    if not named:
        return found
    # Sending there (talking to its Row-Bot, or a made-up customer's reply) reaches no one.
    return None if found.kind == "send" else replace(found, sandbox=named)


def _consequence(action: str, control: str, role: str, page: PageFacts, site: str) -> Consequence | None:
    role = str(role or "").casefold()
    name = _words(control)[:160]
    if action == "submit":
        # Enter submits the field's form: judge it by the form's own submit button.
        if page.search_form or _SEARCH.search(name):
            return None
        name = _words(page.form_submit_label)[:160] or name
        role = "button"
    elif action == "select" or role not in _CLICKABLE_ROLES:
        return None
    shown = name or "the button"
    if role == "link" and not (_PAY.search(name) or _DELETE.search(name)):
        # Links open pages; what they say to book or send happens on the next page's button.
        return None
    if _PAY.search(name) or (page.card_fields and _GENERIC_SUBMIT.search(name)):
        return Consequence("pay", site, shown)
    if _DELETE.search(name):
        return Consequence("delete", site, shown)
    if _ACCOUNT_CHANGE.search(name) or (
        _SAVE.search(name) and _SETTINGS_PAGE.search(_words(urlparse(page.url).path, page.title))
    ):
        return Consequence("account", site, shown)
    if _BOOK.search(name):
        return Consequence("book", site, shown) if page.shows_price else None
    if _SEND.search(name) and not page.search_form:
        return Consequence("send", site, shown)
    return None


def upload_consequence(url: str, files: list[str]) -> Consequence:
    names = ", ".join(files[:3]) + (f" and {len(files) - 3} more" if len(files) > 3 else "")
    return Consequence("upload", site_of(url) or "this site", names or "a file")


def approval_request(tool: str, consequence: Consequence, *, url: str, verb: str = "click") -> dict[str, Any]:
    """The approval card for one consequential page action (plain words, D19)."""

    return {
        "tool": tool,
        "label": consequence.label(),
        "action": consequence.kind,
        "question": consequence.question(),
        "description": consequence.description(verb),
        "risk_class": "high" if consequence.kind in {"pay", "delete"} else "medium",
        "origin_and_path": history_url(url),
        "args": {"site": consequence.site, "control": consequence.control},
    }
