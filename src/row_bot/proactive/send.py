"""Sending a reply Row-Bot drafted in the background: only ever after the person approves it on its card.

Row-Bot wrote the draft on its own, so the words aren't the person's until they say so. Sending one is a
person-only approval (``approval_gate.person_approval``) under every approval level, Hands-off included:
no reviewer decides it, no level skips it, and the approval is bound to exactly the recipient, subject and
text the card showed (``call_binding``). An edit after the card was shown is a new question. Only an
approval sends, through the person's own mail app (Gmail or Outlook), as a reply in the thread when the
app allows; a refusal sends nothing.

1. ``ask``: the card for the draft as it is now, kept until it is answered or the draft changes.
2. ``answer``: the person's answer for that card; "sent", "denied", "changed" or "failed".
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from row_bot.proactive import store

logger = logging.getLogger(__name__)

APPS = {"gmail": "Gmail", "outlook": "Outlook"}
# The mail tool each app sends with: what the card and its binding name.
SEND_TOOLS = {"gmail": "send_gmail_message", "outlook": "outlook_send_mail"}
_TOOL_NAMES = {"gmail": "gmail", "outlook": "outlook_mail"}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PENDING = "send:{}"


class SendError(ValueError):
    """A draft that can't be sent as it is, with a code for the API and the reason in words."""

    def __init__(self, code: str, words: str) -> None:
        super().__init__(words)
        self.code = code


def _thread(moment: dict[str, Any] | None) -> dict[str, Any] | None:
    return next((item for item in (moment or {}).get("evidence") or []
                 if item.get("type") == "thread" and item.get("source") in APPS), None)


def reply_to(draft: dict[str, Any], moment: dict[str, Any] | None) -> dict[str, str]:
    """Who the draft replies to and in which thread, as the person last saved it or as the moment says:
    ``to``, ``subject``, ``source`` (gmail, outlook or "") and ``thread`` (the thread's subject)."""
    thread = _thread(moment)
    subject = str(thread.get("label") or "") if thread else ""
    found = _EMAIL.search(str((moment or {}).get("topic") or ""))
    words = {"to": found.group(0) if found else "", "subject": f"Re: {subject}" if subject else "",
             "source": str(thread["source"]) if thread else "", "thread": subject}
    edit = store.draft_edit(draft["id"])
    if edit and edit["updated_at"]:
        words.update(to=edit["to_addr"], subject=edit["subject"])
    return words


def body_of(draft: dict[str, Any]) -> str:
    edit = store.draft_edit(draft["id"])
    return edit["body"] if edit and edit["updated_at"] else draft["body"]


def connected(source: str) -> bool:
    """Whether the person's mail app for *source* is on and signed in."""
    from row_bot.proactive import sources

    return source in _TOOL_NAMES and sources._tool_ready(_TOOL_NAMES[source])


def why_not(draft: dict[str, Any], moment: dict[str, Any] | None) -> str:
    """Why this draft can't be sent now, in words; "" when it can."""
    if draft["kind"] != "reply":
        return "A brief is for reading; there's nothing to send."
    edit = store.draft_edit(draft["id"])
    if edit and edit["sent_at"]:
        return "It was sent."
    words = reply_to(draft, moment)
    if not words["source"]:
        return "Row-Bot doesn't know which mail app this reply belongs to."
    if not connected(words["source"]):
        return f"Sign in to {APPS[words['source']]} to send."
    if not _EMAIL.fullmatch(words["to"].strip()):
        return "Say who it goes to."
    if not body_of(draft).strip():
        return "Write something to send."
    return ""


def _arguments(draft: dict[str, Any], moment: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    words = reply_to(draft, moment)
    return words["source"], {"to": [words["to"].strip()], "subject": words["subject"], "message": body_of(draft)}


def card(draft: dict[str, Any], moment: dict[str, Any] | None) -> dict[str, Any]:
    """The approval card for sending the draft as it is now, and the binding its answer must carry."""
    from row_bot.tools.approval_gate import BACKGROUND_DRAFT_REASON, call_binding

    source, arguments = _arguments(draft, moment)
    return {"binding": call_binding(SEND_TOOLS[source], arguments), "action": "Send this reply",
            "destination": arguments["to"][0], "account": APPS[source], "subject": arguments["subject"],
            "text": arguments["message"], "reason": BACKGROUND_DRAFT_REASON}


def pending(draft_id: str) -> dict[str, Any] | None:
    return store.get_state(_PENDING.format(draft_id))


def forget(draft_id: str) -> None:
    store.set_state(_PENDING.format(draft_id), None)


def ask(draft: dict[str, Any], moment: dict[str, Any] | None, *, now: float) -> dict[str, Any]:
    """Show the card: nothing is sent, whatever the approval level."""
    reason = why_not(draft, moment)
    if reason:
        raise SendError("invalid_command", reason)
    shown = {**card(draft, moment), "asked_at": now}
    store.set_state(_PENDING.format(draft["id"]), shown)
    store.log("moment", f"Asked you before sending “{draft['title']}” (Row-Bot wrote it, so only you can send it).",
              now=now, moment_id=draft["moment_id"], detail={"draft_id": draft["id"]})
    return shown


def _gmail(arguments: dict[str, Any], thread_id: str) -> str:
    import base64

    from row_bot.tools import registry
    from row_bot.tools.gmail_tool import _build_mime_message

    mime = _build_mime_message(arguments["message"], arguments["to"], arguments["subject"])
    body: dict[str, Any] = {"raw": base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")}
    if thread_id:
        body["threadId"] = thread_id  # A reply in its thread (Gmail threads it when the subject matches).
    sent = registry.get_tool("gmail")._build_api_resource().users().messages().send(userId="me", body=body).execute()
    return f"Sent. Message Id: {sent.get('id', '')}"


def _outlook(arguments: dict[str, Any], thread_id: str) -> str:
    from row_bot.tools.outlook_tool import send_mail

    result = send_mail(to=arguments["to"], subject=arguments["subject"], body=arguments["message"])
    if not result.startswith("Sent"):  # Its failures come back as words.
        raise RuntimeError(result)
    return result


SENDERS: dict[str, Callable[[dict[str, Any], str], str]] = {"gmail": _gmail, "outlook": _outlook}


def answer(draft: dict[str, Any], moment: dict[str, Any] | None, *, binding: str, approved: bool,
           now: float) -> tuple[str, str]:
    """The person's answer for the card they saw: (outcome, words). Only "sent" sent anything."""
    from row_bot.tools.approval_gate import BACKGROUND_DRAFT_REASON, bound_answer, person_approval

    shown = pending(draft["id"])
    if not shown or shown.get("binding") != binding:
        return "changed", "That card is no longer the one to answer. Look at the reply again."
    forget(draft["id"])
    if why_not(draft, moment):
        return "failed", why_not(draft, moment)
    current = card(draft, moment)
    payload = {"tool": SEND_TOOLS[current["account"].lower()], "label": current["action"],
               "description": f"Send “{current['subject']}” to {current['destination']}", "card": current}
    allowed, _line = person_approval(payload, binding=current["binding"], reason=BACKGROUND_DRAFT_REASON,
                                     approval_callback=lambda _payload: bound_answer(approved, binding))
    title = draft["title"]
    if not approved:
        store.log("moment", f"You didn't send “{title}”. Nothing was sent.", now=now, moment_id=draft["moment_id"])
        return "denied", "Not sent."
    if not allowed:  # Approved, but for other words than the draft has now.
        return "changed", "The reply changed after the card was shown. Look at it again."
    source = current["account"].lower()
    thread = _thread(moment)
    try:
        SENDERS[source]({"to": [current["destination"]], "subject": current["subject"],
                         "message": current["text"]}, str(thread["id"]) if thread else "")
    except Exception:
        logger.warning("Sending a drafted reply failed", exc_info=True)
        store.log("moment", f"Couldn't send “{title}”.", now=now, moment_id=draft["moment_id"])
        return "failed", f"{APPS[source]} couldn't send it. Try again."
    store.mark_draft_sent(draft["id"], now=now)
    store.log("moment", f"Sent “{title}” to {current['destination']}, after you approved it.", now=now,
              moment_id=draft["moment_id"])
    if moment is not None and moment["state"] in ("pending", "shown"):
        from row_bot.proactive import feedback

        feedback.act(moment["id"], now=now, action_id="send_draft")
    return "sent", f"Sent to {current['destination']}"
