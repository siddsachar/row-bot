"""Share to company (Row-Bot for Teams, B4b): send something useful from a chat to a company space's review
queue (B4a), see what became of one's shares, and withdraw one while it waits.

- B4a's member routes, with the device's bearer through the C1b client: ``POST /api/brain/spaces/{id}/submissions``,
  ``GET /api/brain/submissions?mine=1&status=`` and ``POST /api/brain/submissions/{id}/withdraw`` (204: the share
  is deleted, text and all). The space's owners review a share before anyone else sees it.
- Not joined: None, and nothing is asked. The company's policy turning Share to company off
  (``data.share_to_company``): a share is refused here, without asking the server (which refuses it too). Listing
  and withdrawing still work. Disconnected or locked down (C3a): a state, and nothing is asked; a server that
  can't be used: a state (B8's, ``brain._read``).
- Every call answers the person's shares as they are afterwards and the spaces they may share to, with how the
  action went (``outcome``) and, for a refusal, the plain words (``words``).
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlencode

from row_bot.company import brain, enforce, store

logger = logging.getLogger(__name__)

SUBMISSIONS_PATH = "/api/brain/submissions"
# B4a's limits: a share's title, text and note, a person's shares waiting at once and shared in a UTC day, and
# the most a list shows.
MAX_TITLE = 200
MAX_TEXT = 8000
MAX_NOTE = 500
MAX_PENDING = 20
MAX_PER_DAY = 50
MAX_LISTED = 200
STATUSES = ("all", "pending", "approved", "rejected")

INVALID = (f"A share needs a title of up to {MAX_TITLE} characters and its text, up to {MAX_TEXT:,} characters. "
           f"The note is up to {MAX_NOTE} characters.")
TURNED_OFF = "Your company turned off Share to company, so this wasn't sent."
SPACE_GONE = "You can't share to that space any more. Choose another one."
SHARE_GONE = "That share isn't there any more."


def _waiting_words(company: str, limit: int) -> str:
    return (f"You have {limit} shares waiting for review, the most {company} allows. Withdraw one, or wait until "
            "its owners review some.")


def _today_words(company: str, limit: int) -> str:
    return f"You've shared {limit} times today, the most {company} allows in a day. Try again tomorrow."


def _decided_words(status: str) -> str:
    return f"It was already {'approved' if status == 'approved' else 'rejected'}, so it can't be withdrawn."


def _line(value: Any, limit: int) -> str | None:
    """One line of text (whitespace runs made one space), or None when empty or longer than ``limit``."""
    text = " ".join(value.split()) if isinstance(value, str) else ""
    return text if 0 < len(text) <= limit else None


def _body(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _share(item: Any) -> dict[str, Any] | None:
    """One of the person's shares as the app shows it (B4a's shape); None when malformed.

    ``space_id`` is where it is now and ``moved`` whether a reviewer moved it from the space it was sent to;
    ``space_name`` is that space's name (None for an unmoved share whose space the person no longer reads)."""
    if not isinstance(item, dict) or item.get("status") not in ("pending", "approved", "rejected"):
        return None
    share_id, space_id = brain._id(item.get("id")), brain._id(item.get("space_id"))
    title, text = brain._text(item.get("title"), MAX_TITLE), _body(item.get("text"), MAX_TEXT)
    if share_id is None or space_id is None or not title:
        return None
    sent = brain._id(item.get("sent_space_id")) or space_id
    decided = item["status"] != "pending"
    return {
        "id": share_id, "title": title, "text": text,
        "source_note": brain._text(item.get("source_note"), MAX_NOTE) or None,
        "space_id": space_id, "space_name": brain._text(item.get("space_name"), 160) or None,
        "moved": sent != space_id, "status": item["status"],
        "submitted_at": brain._text(item.get("submitted_at"), 64),
        "decided_at": (brain._text(item.get("decided_at"), 64) or None) if decided else None,
        "reason": (brain._text(item.get("reason"), 500) or None) if item["status"] == "rejected" else None,
        "fact_id": brain._id(item.get("fact_id")) if item["status"] == "approved" else None,
    }


def empty(state: str, company: str | None = None, reason: str | None = None, *,
          allowed: bool = False) -> dict[str, Any]:
    """A view with no shares: not joined, or why the company can't be asked now."""
    return {"schema_version": 1, "state": state, "company": company, "reason": reason, "allowed": allowed,
            "spaces": [], "shares": [], "truncated": False, "outcome": None, "words": None}


def _listing(origin: str, company: str, *, status: str = "all", outcome: str | None = None,
             words: str | None = None) -> dict[str, Any]:
    """The spaces the person may share to and their shares (newest decided or sent first)."""
    spaces = brain._spaces(brain._get(origin, brain.SPACES_PATH))
    answer = brain._get(origin, f"{SUBMISSIONS_PATH}?{urlencode({'mine': '1', 'status': status})}")
    shares = [found for found in map(_share, brain._items(answer, "submissions")) if found]
    return {**empty("ready", company, allowed=enforce.allows_share_to_company()), "spaces": spaces,
            "shares": shares[:MAX_LISTED], "truncated": bool(answer and answer.get("truncated") is True),
            "outcome": outcome, "words": words}


def _read(record: store.Record, ask: Any) -> dict[str, Any]:
    return brain._read(record, lambda state, company=None, reason=None: empty(
        state, company, reason, allowed=enforce.allows_share_to_company()), ask)


def mine(status: str = "all") -> dict[str, Any] | None:
    """The person's shares (``status``: all, pending, approved or rejected) and the spaces they may share to;
    None when not joined."""
    record = store.read_record()
    if record is None:
        return None
    wanted = status if status in STATUSES else "all"
    return _read(record, lambda origin: _listing(origin, record.company, status=wanted))


def _limit(answer: dict[str, Any], key: str, default: int) -> int:
    value = answer.get(key)
    return value if type(value) is int and value > 0 else default


def _refusal(response: Any, company: str) -> tuple[str, str]:
    """A share's refusal as (outcome, words). Raises the client's Unreachable for an answer it doesn't expect."""
    from row_bot.company import client

    answer = client._answer(response)
    status, error = response.status_code, answer.get("error")
    if status == 404:
        return "space_not_found", SPACE_GONE
    if status == 403 and error == "share_to_company_off":
        return "share_to_company_off", TURNED_OFF
    if status == 429 and error == "too_many_pending":
        return "too_many_pending", _waiting_words(company, _limit(answer, "max_pending", MAX_PENDING))
    if status == 429 and error == "too_many_shares_today":
        return "too_many_shares_today", _today_words(company, _limit(answer, "max_per_day", MAX_PER_DAY))
    if status == 422:
        return "invalid", INVALID
    logger.info("The company's server answered a share with %d", status)
    raise client.Unreachable(str(status))


def submit(space_id: str, title: str, text: str, note: str = "") -> dict[str, Any] | None:
    """Send a share to a space's review queue: its owners review it before anyone else sees it. The person's
    shares afterwards; None when not joined. Refused without asking while the company turns sharing off, and
    when the title, text or note couldn't be sent."""
    record = store.read_record()
    if record is None:
        return None
    space = brain._id(space_id)
    body = {"title": _line(title, MAX_TITLE), "text": str(text or "").strip(),
            "source_note": " ".join(str(note or "").split())}

    def ask(origin: str) -> dict[str, Any]:
        from row_bot.company import client

        if not enforce.allows_share_to_company():
            return {**empty("ready", record.company), "outcome": "share_to_company_off", "words": TURNED_OFF}
        if body["title"] is None or not 0 < len(body["text"]) <= MAX_TEXT or len(body["source_note"]) > MAX_NOTE:
            return {**empty("ready", record.company, allowed=True), "outcome": "invalid", "words": INVALID}
        if space is None:
            return _listing(origin, record.company, outcome="space_not_found", words=SPACE_GONE)
        sent = {key: value for key, value in body.items() if value}
        response = client.call(origin, "POST", f"{brain.SPACES_PATH}/{space}/submissions",
                               content=json.dumps(sent).encode("utf-8"))
        if response.status_code in (200, 201):
            return _listing(origin, record.company, outcome="sent")
        outcome, words = _refusal(response, record.company)
        return _listing(origin, record.company, outcome=outcome, words=words)

    return _read(record, ask)


def withdraw(share_id: str) -> dict[str, Any] | None:
    """Take back a share that waits for review: the server deletes it, so nobody will see it. The person's shares
    afterwards; None when not joined."""
    record = store.read_record()
    if record is None:
        return None
    share = brain._id(share_id)

    def ask(origin: str) -> dict[str, Any]:
        from row_bot.company import client

        if share is None:
            return _listing(origin, record.company, outcome="not_found", words=SHARE_GONE)
        response = client.call(origin, "POST", f"{SUBMISSIONS_PATH}/{share}/withdraw")
        if response.status_code in (200, 204):
            return _listing(origin, record.company, outcome="withdrawn")
        answer = client._answer(response)
        if response.status_code == 404:
            return _listing(origin, record.company, outcome="not_found", words=SHARE_GONE)
        if response.status_code == 409 and answer.get("error") == "submission_decided":
            words = _decided_words(str(answer.get("status")))
            return _listing(origin, record.company, outcome="decided", words=words)
        logger.info("The company's server answered a withdraw with %d", response.status_code)
        raise client.Unreachable(str(response.status_code))

    return _read(record, ask)
