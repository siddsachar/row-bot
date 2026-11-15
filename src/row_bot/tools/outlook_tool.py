"""Outlook mail and calendar through Microsoft Graph v1.0, signed in with the person's own Microsoft app.

Two tools, as Gmail and Calendar are: ``outlook_mail`` and ``outlook_calendar``, both through the one
Microsoft sign-in (``microsoft_account``). Reading never asks. Sending, replying, forwarding and deleting
mail, cancelling an event and answering an invitation always ask first (``destructive_tool_names``); creating
or changing an event asks when it has attendees, since its invitations reach other people. Each card says in
words who it goes to and what it says (``approval_card``). Results are plain summaries with Outlook on the web
links; ids are there for the next step, never a token.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import logging
import mimetypes
from pathlib import Path
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, field_validator

from row_bot import microsoft_account
from row_bot.microsoft_account import GraphError, SignInError, segment
from row_bot.tools import registry
from row_bot.tools.base import BaseTool

logger = logging.getLogger(__name__)

MAIL_READS = ("outlook_search_mail", "outlook_read_mail")
MAIL_OPERATIONS = (*MAIL_READS, "outlook_draft_mail", "outlook_send_mail", "outlook_reply_mail",
                   "outlook_forward_mail", "outlook_update_mail", "outlook_delete_mail")
CALENDAR_OPERATIONS = ("outlook_list_events", "outlook_find_free_times", "outlook_create_event",
                       "outlook_update_event", "outlook_cancel_event", "outlook_respond_event")
MAIL_ASKS = {"outlook_send_mail", "outlook_reply_mail", "outlook_forward_mail", "outlook_delete_mail"}
CALENDAR_ASKS = {"outlook_cancel_event", "outlook_respond_event"}
# Each operation's title, for the app's approval cards and step rows ("Allow Outlook to send an email?").
TITLES = {
    "outlook_search_mail": "Search email", "outlook_read_mail": "Read an email", "outlook_draft_mail": "Draft an email",
    "outlook_send_mail": "Send an email", "outlook_reply_mail": "Reply to an email",
    "outlook_forward_mail": "Forward an email", "outlook_update_mail": "Move or flag an email",
    "outlook_delete_mail": "Delete an email", "outlook_list_events": "List events",
    "outlook_find_free_times": "Find free times", "outlook_create_event": "Create an event",
    "outlook_update_event": "Change an event", "outlook_cancel_event": "Cancel an event",
    "outlook_respond_event": "Answer an invitation",
}
FOLDERS = {"inbox": "Inbox", "drafts": "Drafts", "sentitems": "Sent Items", "deleteditems": "Deleted Items",
           "archive": "Archive", "junkemail": "Junk Email"}
_ATTACHMENT_LIMIT = 3 * 1024 * 1024  # Graph takes a file in the message itself up to about 3 MB...
_UPLOAD_LIMIT = 150 * 1024 * 1024  # ...and up to 150 MB through an upload session (5.3.1 A4).
_BODY_CHARS = 30_000
_PREVIEW_CHARS = 300
_MAIL_FIELDS = ("id,subject,from,toRecipients,ccRecipients,receivedDateTime,isRead,isDraft,flag,hasAttachments,"
                "bodyPreview,webLink")
_EVENT_FIELDS = ("id,subject,start,end,location,attendees,organizer,isOrganizer,isAllDay,isCancelled,"
                 "isOnlineMeeting,onlineMeeting,responseStatus,webLink")


# --- Words -------------------------------------------------------------------------------------------------

def _zone() -> str:
    from row_bot.schedule_zones import local_zone_name
    return local_zone_name()


def _person(value: Any) -> str:
    """``Amy Lee <amy@example.com>`` from Graph's recipient shape."""
    email = (value or {}).get("emailAddress") or {} if isinstance(value, dict) else {}
    name, address = str(email.get("name") or "").strip(), str(email.get("address") or "").strip()
    return f"{name} <{address}>" if name and address and name.casefold() != address.casefold() else address or name


def _people(values: Any) -> str:
    return ", ".join(found for found in (_person(value) for value in values or []) if found)


def _moment(value: str, zone: str = "") -> datetime | None:
    """A Graph time (``2026-10-23T10:00:00.0000000`` in ``zone``, or with an offset) as an aware time."""
    try:
        moment = datetime.fromisoformat(str(value)[:19].replace("Z", ""))
    except ValueError:
        return None
    try:
        return moment.replace(tzinfo=ZoneInfo(zone)) if zone else moment.replace(tzinfo=timezone.utc)
    except (ValueError, KeyError):
        return moment.replace(tzinfo=timezone.utc)


def _when(moment: datetime | None, *, date_only: bool = False) -> str:
    if moment is None:
        return "an unknown time"
    return moment.strftime("%a %d %b %Y" if date_only else "%a %d %b %Y %H:%M")


def _range(begins: datetime | None, ends: datetime | None, all_day: bool = False) -> str:
    """"Fri 23 Oct 2026 10:00–10:30", or both ends in full when it spans days."""
    if all_day:
        return _when(begins, date_only=True) + " (all day)"
    if begins and ends and begins.date() == ends.date():
        return f"{_when(begins)}–{ends.strftime('%H:%M')}"
    return f"{_when(begins)} – {_when(ends)}"


def _span(event: dict) -> str:
    start, end = event.get("start") or {}, event.get("end") or {}
    return _range(_moment(start.get("dateTime"), start.get("timeZone") or ""),
                  _moment(end.get("dateTime"), end.get("timeZone") or ""), bool(event.get("isAllDay")))


def _link(value: Any) -> str:
    text = str(value or "")
    return text if text.startswith("https://") and len(text) <= 2048 else ""


def _size(count: int) -> str:
    return f"{count / (1024 * 1024):.1f} MB" if count >= 1024 * 1024 else f"{max(1, round(count / 1024))} KB"


def _plain_failure(error: Exception) -> str:
    if isinstance(error, GraphError):
        return f"Outlook couldn't do that: {error.plain}"
    if isinstance(error, SignInError):
        return ("Outlook isn't signed in, or its sign-in stopped working. Ask the person to sign in again in "
                "Settings › Apps › Outlook.")
    if isinstance(error, ValueError):
        return f"Outlook couldn't do that: {error}"
    logger.warning("An Outlook request failed (%s)", type(error).__name__)
    return "Outlook couldn't do that: Row-Bot couldn't reach Microsoft. Try again in a moment."


def _run(work) -> Any:
    """A tool's work, with any failure said in words (never a token or a raw response). An approval pausing
    the run and Stop are not failures: they go on up."""
    from langgraph.errors import GraphBubbleUp

    def call(**kwargs: Any) -> str:
        try:
            return work(**kwargs)
        except (GraphBubbleUp, InterruptedError):
            raise
        except Exception as error:  # noqa: BLE001 -- every failure becomes the tool's plain answer
            return _plain_failure(error)
    call.__name__ = work.__name__
    return call


# --- Mail --------------------------------------------------------------------------------------------------

def _recipients(values: list[str] | None) -> list[dict]:
    found = []
    for value in values or []:
        address = str(value).strip()
        if not address or "@" not in address or any(ch in address for ch in "\r\n<>,;"):
            raise ValueError(f"“{address}” isn't an email address.")
        found.append({"emailAddress": {"address": address}})
    return found


def _attachments(paths: list[str] | None) -> tuple[list[dict], list[Path]]:
    """The files in the message itself (up to 3 MB each), and the larger ones to upload after."""
    from row_bot.tools.gmail_tool import _resolve_file_path

    inline, large = [], []
    for given in paths or []:
        path = Path(_resolve_file_path(given))
        if not path.is_file():
            raise ValueError(f"there's no file at {given} to attach.")
        size = path.stat().st_size
        if size > _UPLOAD_LIMIT:
            raise ValueError(f"{path.name} is larger than 150 MB, more than Outlook takes as an attachment.")
        if size > _ATTACHMENT_LIMIT:
            large.append(path)
            continue
        inline.append({"@odata.type": "#microsoft.graph.fileAttachment", "name": path.name,
                       "contentType": _content_type(path),
                       "contentBytes": base64.b64encode(path.read_bytes()).decode("ascii")})
    return inline, large


def _content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _upload_large(message_id: str, large: list[Path]) -> None:
    """Attach each file over 3 MB to a draft through its own upload session."""
    for path in large:
        session = microsoft_account.graph(
            "POST", f"/me/messages/{segment(message_id)}/attachments/createUploadSession",
            body={"AttachmentItem": {"attachmentType": "file", "name": path.name, "size": path.stat().st_size,
                                     "contentType": _content_type(path)}})
        microsoft_account.upload(str(session.get("uploadUrl") or ""), path)


def _message(to: list[str], subject: str, body: str, cc: list[str] | None, bcc: list[str] | None,
             attachments: list[str] | None) -> tuple[dict, list[Path]]:
    """The message as Graph takes it, and the attachments too large to go in it."""
    message = {"subject": subject, "body": {"contentType": "Text", "content": body},
               "toRecipients": _recipients(to)}
    if cc:
        message["ccRecipients"] = _recipients(cc)
    if bcc:
        message["bccRecipients"] = _recipients(bcc)
    inline, large = _attachments(attachments)
    if inline:
        message["attachments"] = inline
    return message, large


def _listed(message: dict, number: int) -> str:
    marks = [mark for mark, on in (("unread", not message.get("isRead", True)),
                                   ("flagged", (message.get("flag") or {}).get("flagStatus") == "flagged"),
                                   ("attachments", message.get("hasAttachments"))) if on]
    received = _when(_moment(message.get("receivedDateTime") or ""))
    preview = " ".join(str(message.get("bodyPreview") or "").split())[:_PREVIEW_CHARS]
    sender = _person(message.get("from"))
    # A draft has no sender until it is sent: it goes from this mailbox (U22).
    who = (f"Draft · from {sender or microsoft_account.address() or 'your mailbox'}" if message.get("isDraft")
           else f"from {sender or 'unknown'}")
    lines = [f"{number}. {message.get('subject') or '(no subject)'} — {who}, "
             f"{received} UTC" + (" · " + " · ".join(marks) if marks else "")]
    if preview:
        lines.append(f"   {preview}")
    link = _link(message.get("webLink"))
    lines.append(f"   id: {message.get('id', '')}" + (f" · Open in Outlook: {link}" if link else ""))
    return "\n".join(lines)


def _folder(name: str) -> str:
    key = (name or "inbox").strip().lower().replace(" ", "")
    if key not in FOLDERS:
        raise ValueError("use one of these folders: " + ", ".join(FOLDERS) + ".")
    return key


def _get_message(message_id: str, fields: str = "id,subject,from,toRecipients,ccRecipients,receivedDateTime,webLink,hasAttachments") -> dict:
    return microsoft_account.graph("GET", f"/me/messages/{segment(message_id)}", params={"$select": fields})


@_run
def search_mail(query: str = "", folder: str = "inbox", unread_only: bool = False, max_results: int = 10) -> str:
    key = _folder(folder)
    params: dict[str, Any] = {"$top": max(1, min(int(max_results), 25)), "$select": _MAIL_FIELDS}
    if query.strip():
        params["$search"] = '"' + query.replace('"', " ").strip() + '"'  # Graph can't also $filter a $search.
    elif unread_only:
        params["$filter"] = "isRead eq false"
    found = microsoft_account.graph("GET", f"/me/mailFolders/{key}/messages", params=params).get("value") or []
    if unread_only:
        found = [message for message in found if not message.get("isRead", True)]
    place = FOLDERS[key]
    if not found:
        return f"No messages in {place}" + (f" match “{query.strip()}”." if query.strip() else ".")
    head = f"{len(found)} message{'s' if len(found) != 1 else ''} in {place}" + (
        f" matching “{query.strip()}”" if query.strip() else "") + ":"
    return "\n".join([head, *(_listed(message, number) for number, message in enumerate(found, 1))])


@_run
def read_mail(message_id: str) -> str:
    from row_bot.tool_outputs import bounded

    message = microsoft_account.graph(
        "GET", f"/me/messages/{segment(message_id)}",
        params={"$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,body,hasAttachments,webLink,isRead"},
        headers={"Prefer": 'outlook.body-content-type="text"'})
    lines = [f"Subject: {message.get('subject') or '(no subject)'}", f"From: {_person(message.get('from'))}",
             f"To: {_people(message.get('toRecipients'))}"]
    if message.get("ccRecipients"):
        lines.append(f"Cc: {_people(message.get('ccRecipients'))}")
    lines.append(f"Received: {_when(_moment(message.get('receivedDateTime') or ''))} UTC")
    if message.get("hasAttachments"):
        listed = microsoft_account.graph("GET", f"/me/messages/{segment(message_id)}/attachments",
                                         params={"$select": "name,size,contentType"}).get("value") or []
        lines.append("Attachments: " + (", ".join(f"{item.get('name')} ({_size(int(item.get('size') or 0))})"
                                                  for item in listed) or "none Outlook could list"))
    link = _link(message.get("webLink"))
    if link:
        lines.append(f"Open in Outlook: {link}")
    body = str((message.get("body") or {}).get("content") or "").strip()
    return "\n".join(lines) + "\n\n" + (bounded(body, tool_name="outlook_read_mail", max_chars=_BODY_CHARS) or "(no text)")


def _with_files(attachments: list[str] | None) -> str:
    return f" with {len(attachments)} attachment{'s' if len(attachments) != 1 else ''}" if attachments else ""


@_run
def draft_mail(to: list[str], subject: str, body: str, cc: Optional[list[str]] = None,
               bcc: Optional[list[str]] = None, attachments: Optional[list[str]] = None) -> str:
    message, large = _message(to, subject, body, cc, bcc, attachments)
    draft = microsoft_account.graph("POST", "/me/messages", body=message)
    _upload_large(str(draft.get("id") or ""), large)
    link = _link(draft.get("webLink"))
    return (f"Saved a draft in Drafts: “{subject}” to {', '.join(to)}{_with_files(attachments)}. Nothing was sent."
            + (f"\nOpen in Outlook: {link}" if link else "") + f"\nid: {draft.get('id', '')}")


@_run
def send_mail(to: list[str], subject: str, body: str, cc: Optional[list[str]] = None,
              bcc: Optional[list[str]] = None, attachments: Optional[list[str]] = None) -> str:
    message, large = _message(to, subject, body, cc, bcc, attachments)
    if large:
        # A file over 3 MB goes onto a draft through an upload session, then the draft is sent.
        draft = microsoft_account.graph("POST", "/me/messages", body=message)
        draft_id = str(draft.get("id") or "")
        _upload_large(draft_id, large)
        microsoft_account.graph("POST", f"/me/messages/{segment(draft_id)}/send")
    else:
        microsoft_account.graph("POST", "/me/sendMail", body={"message": message, "saveToSentItems": True})
    copies = f" (cc {', '.join(cc)})" if cc else ""
    return f"Sent “{subject}” to {', '.join(to)}{copies}{_with_files(attachments)}. A copy is in Sent Items."


@_run
def reply_mail(message_id: str, body: str, reply_all: bool = False) -> str:
    original = _get_message(message_id)
    microsoft_account.graph("POST", f"/me/messages/{segment(message_id)}/{'replyAll' if reply_all else 'reply'}",
                            body={"comment": body})
    to = "everyone on it" if reply_all else _person(original.get("from"))
    return f"Replied to {to}: “{original.get('subject') or '(no subject)'}”. A copy is in Sent Items."


@_run
def forward_mail(message_id: str, to: list[str], comment: str = "") -> str:
    original = _get_message(message_id)
    microsoft_account.graph("POST", f"/me/messages/{segment(message_id)}/forward",
                            body={"toRecipients": _recipients(to), "comment": comment})
    return f"Forwarded “{original.get('subject') or '(no subject)'}” to {', '.join(to)}. A copy is in Sent Items."


@_run
def update_mail(message_id: str, move_to: Optional[str] = None, flag: Optional[Literal["flagged", "complete", "none"]] = None,
                read: Optional[bool] = None) -> str:
    if move_to is None and flag is None and read is None:
        return "Nothing to change: give move_to, flag or read."
    if move_to is not None and _folder(move_to) == "deleteditems":
        # Deleting asks first: never a move that skips it.
        return "To delete it, use outlook_delete_mail, which asks the person first. Nothing was changed."
    original = _get_message(message_id, "id,subject")
    done, changes = [], {}
    if read is not None:
        changes["isRead"] = bool(read)
        done.append("marked read" if read else "marked unread")
    if flag is not None:
        changes["flag"] = {"flagStatus": {"none": "notFlagged"}.get(flag, flag)}
        done.append({"flagged": "flagged", "complete": "flag marked complete", "none": "flag removed"}[flag])
    if changes:
        microsoft_account.graph("PATCH", f"/me/messages/{segment(message_id)}", body=changes)
    moved_id = message_id
    if move_to is not None:
        key = _folder(move_to)
        moved_id = microsoft_account.graph("POST", f"/me/messages/{segment(message_id)}/move",
                                           body={"destinationId": key}).get("id") or message_id
        done.append(f"moved to {FOLDERS[key]}")
    return f"“{original.get('subject') or '(no subject)'}”: {', '.join(done)}." + (
        f"\nIts new id: {moved_id}" if moved_id != message_id else "")


@_run
def delete_mail(message_id: str) -> str:
    original = _get_message(message_id, "id,subject")
    microsoft_account.graph("POST", f"/me/messages/{segment(message_id)}/move", body={"destinationId": "deleteditems"})
    return (f"Moved “{original.get('subject') or '(no subject)'}” to Deleted Items. It can be restored from there "
            "until the folder is emptied.")


# --- Calendar ----------------------------------------------------------------------------------------------

def _local(value: str, zone: str, *, end: bool = False) -> datetime:
    """``YYYY-MM-DD`` or ``YYYY-MM-DD HH:MM`` in ``zone`` as an aware time (a date's end is the next day)."""
    text = str(value or "").strip().replace("T", " ")
    try:
        if len(text) == 10:
            moment = datetime.strptime(text, "%Y-%m-%d") + (timedelta(days=1) if end else timedelta())
        else:
            moment = datetime.fromisoformat(text[:19])
    except ValueError:
        raise ValueError(f"“{value}” isn't a date like 2026-10-23 or 2026-10-23 14:30.") from None
    return moment.replace(tzinfo=ZoneInfo(zone))


def _check_zone(name: str | None) -> str:
    from row_bot.schedule_zones import is_zone
    if name and not is_zone(name):
        raise ValueError(f"“{name}” isn't a time zone like Europe/London.")
    return name or _zone()


def _events(start: datetime, end: datetime, zone: str, top: int = 50) -> list[dict]:
    return microsoft_account.graph("GET", "/me/calendarView", params={
        "startDateTime": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDateTime": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "$top": top, "$select": _EVENT_FIELDS, "$orderby": "start/dateTime"},
        headers={"Prefer": f'outlook.timezone="{zone}"'}).get("value") or []


def _place(event: dict) -> str:
    place = str((event.get("location") or {}).get("displayName") or "").strip()
    online = "Teams" if event.get("isOnlineMeeting") else ""
    return " · ".join(part for part in (place, online) if part)


def _event_line(event: dict, number: int) -> str:
    who = event.get("attendees") or []
    parts = [_span(event), _place(event), f"{len(who)} attendee{'s' if len(who) != 1 else ''}" if who else "",
             "you organise" if event.get("isOrganizer") else
             f"organiser {_person(event.get('organizer'))}" if event.get("organizer") else "",
             {"accepted": "accepted", "tentativelyAccepted": "tentative", "declined": "declined",
              "notResponded": "not answered yet"}.get((event.get("responseStatus") or {}).get("response"), "")
             if not event.get("isOrganizer") else "",
             "cancelled" if event.get("isCancelled") else ""]
    lines = [f"{number}. {event.get('subject') or '(no title)'} — " + " · ".join(part for part in parts if part)]
    join = _link(((event.get("onlineMeeting") or {}).get("joinUrl")))
    if join:
        lines.append(f"   Join: {join}")
    link = _link(event.get("webLink"))
    lines.append(f"   id: {event.get('id', '')}" + (f" · Open in Outlook: {link}" if link else ""))
    return "\n".join(lines)


@_run
def list_events(start: str, end: str, max_results: int = 25) -> str:
    zone = _zone()
    begins, ends = _local(start, zone), _local(end, zone, end=True)
    found = _events(begins, ends, zone, max(1, min(int(max_results), 50)))
    span = f"{_when(begins, date_only=True)} to {_when(ends - timedelta(seconds=1), date_only=True)} ({zone})"
    if not found:
        return f"No events from {span}."
    return "\n".join([f"{len(found)} event{'s' if len(found) != 1 else ''} from {span}:",
                      *(_event_line(event, number) for number, event in enumerate(found, 1))])


def free_times(busy: list[tuple[datetime, datetime]], start: datetime, end: datetime, minutes: int,
               working_hours: bool) -> list[tuple[datetime, datetime]]:
    """The gaps of at least ``minutes`` between ``busy`` times, within ``start``–``end`` (and 09:00–17:00 on
    weekdays when ``working_hours``)."""
    windows, day = [], start
    while day < end:
        open_, close = day, min(end, (day + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0))
        if working_hours:
            if day.weekday() >= 5:
                day = close
                continue
            open_, close = max(open_, day.replace(hour=9, minute=0, second=0, microsecond=0)), min(
                close, day.replace(hour=17, minute=0, second=0, microsecond=0))
        if close > open_:
            windows.append((open_, close))
        day = (day + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    found = []
    for open_, close in windows:
        cursor = open_
        for begins, ends in sorted(busy):
            if ends <= cursor or begins >= close:
                continue
            if begins - cursor >= timedelta(minutes=minutes):
                found.append((cursor, begins))
            cursor = max(cursor, ends)
        if close - cursor >= timedelta(minutes=minutes):
            found.append((cursor, close))
    return found


@_run
def find_free_times(start: str, end: str, duration_minutes: int = 30, working_hours_only: bool = True) -> str:
    zone = _zone()
    begins, ends = _local(start, zone), _local(end, zone, end=True)
    busy = []
    for event in _events(begins, ends, zone):
        if event.get("isCancelled") or (event.get("responseStatus") or {}).get("response") == "declined":
            continue
        first, last = event.get("start") or {}, event.get("end") or {}
        busy.append((_moment(first.get("dateTime"), first.get("timeZone") or zone),
                     _moment(last.get("dateTime"), last.get("timeZone") or zone)))
    slots = free_times([pair for pair in busy if all(pair)], begins, ends, max(5, int(duration_minutes)), working_hours_only)
    if not slots:
        return f"No free {duration_minutes}-minute times between {_when(begins)} and {_when(ends)} ({zone})."
    shown = [f"- {_when(first)}–{last.strftime('%H:%M')}" for first, last in slots[:12]]
    return "\n".join([f"Free for at least {duration_minutes} minutes ({zone}"
                      + (", working hours" if working_hours_only else "") + "):", *shown])


def _event_body(subject: str | None, start: str | None, end: str | None, zone: str, location: str | None,
                body: str | None, attendees: list[str] | None, online_meeting: bool | None, all_day: bool) -> dict:
    found: dict[str, Any] = {}
    if subject is not None:
        found["subject"] = subject
    if start is not None:
        found["start"] = {"dateTime": _local(start, zone).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": zone}
    if end is not None:
        found["end"] = {"dateTime": _local(end, zone, end=all_day and len(end.strip()) == 10).strftime("%Y-%m-%dT%H:%M:%S"),
                        "timeZone": zone}
    if all_day:
        found["isAllDay"] = True
    if location is not None:
        found["location"] = {"displayName": location}
    if body is not None:
        found["body"] = {"contentType": "Text", "content": body}
    if attendees is not None:
        found["attendees"] = [{**recipient, "type": "required"} for recipient in _recipients(attendees)]
    if online_meeting is not None:
        found["isOnlineMeeting"] = bool(online_meeting)  # A Teams link, when the mailbox can have one.
    return found


def _asks(tool: str, kwargs: dict) -> str | None:
    """Ask first, for an event change whose invitations reach other people; None when it may go ahead."""
    from row_bot.tools.approval_gate import approval_check

    question, description = approval_card(tool, kwargs)
    refusal, _line = approval_check(
        {"tool": tool, "label": TITLES[tool], "question": question, "description": description,
         "args": {key: value for key, value in kwargs.items() if value is not None}},
        risks=("sends calendar invitations to other people",),
        cancelled_message="The person didn't allow it; nothing was changed in Outlook.")
    return refusal


@_run
def create_event(subject: str, start: str, end: str, timezone_name: Optional[str] = None, location: Optional[str] = None,
                 body: Optional[str] = None, attendees: Optional[list[str]] = None, online_meeting: bool = False,
                 all_day: bool = False) -> str:
    zone = _check_zone(timezone_name)
    event = _event_body(subject, start, end, zone, location, body, attendees, online_meeting or None, all_day)
    if attendees:
        refused = _asks("outlook_create_event", {"subject": subject, "start": start, "end": end, "timezone_name": zone,
                                                 "location": location, "attendees": attendees,
                                                 "online_meeting": online_meeting, "body": body})
        if refused:
            return refused
    made = microsoft_account.graph("POST", "/me/events", body=event)
    lines = [f"Added “{subject}” on {_span(made) if made.get('start') else start} ({zone})."]
    if attendees:
        lines.append(f"Invitations went to {', '.join(attendees)}.")
    join = _link((made.get("onlineMeeting") or {}).get("joinUrl"))
    if join:
        lines.append(f"Teams link: {join}")
    if _link(made.get("webLink")):
        lines.append(f"Open in Outlook: {made['webLink']}")
    lines.append(f"id: {made.get('id', '')}")
    return "\n".join(lines)


def _get_event(event_id: str) -> dict:
    return microsoft_account.graph("GET", f"/me/events/{segment(event_id)}", params={"$select": _EVENT_FIELDS},
                                   headers={"Prefer": f'outlook.timezone="{_zone()}"'})


@_run
def update_event(event_id: str, subject: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
                 timezone_name: Optional[str] = None, location: Optional[str] = None, body: Optional[str] = None,
                 attendees: Optional[list[str]] = None, online_meeting: Optional[bool] = None) -> str:
    zone = _check_zone(timezone_name)
    changes = _event_body(subject, start, end, zone, location, body, attendees, online_meeting, False)
    if not changes:
        return "Nothing to change: give a new subject, time, place, description, attendees or Teams setting."
    current = _get_event(event_id)
    if current.get("attendees") or attendees:
        refused = _asks("outlook_update_event", {"event_id": event_id, "subject": subject, "start": start, "end": end,
                                                 "timezone_name": zone if timezone_name else None, "location": location,
                                                 "attendees": attendees, "online_meeting": online_meeting, "body": body})
        if refused:
            return refused
    changed = microsoft_account.graph("PATCH", f"/me/events/{segment(event_id)}", body=changes)
    told = " Its attendees get the update." if (current.get("attendees") or attendees) else ""
    link = _link(changed.get("webLink") or current.get("webLink"))
    return (f"Changed “{changed.get('subject') or current.get('subject') or '(no title)'}”"
            f" ({_span(changed) if changed.get('start') else _span(current)}).{told}"
            + (f"\nOpen in Outlook: {link}" if link else ""))


@_run
def cancel_event(event_id: str, comment: str = "") -> str:
    current = _get_event(event_id)
    title = current.get("subject") or "(no title)"
    if current.get("isOrganizer") and current.get("attendees"):
        microsoft_account.graph("POST", f"/me/events/{segment(event_id)}/cancel", body={"comment": comment})
        count = len(current["attendees"])
        return f"Cancelled “{title}” ({_span(current)}) and told its {'attendee' if count == 1 else f'{count} attendees'}."
    microsoft_account.graph("DELETE", f"/me/events/{segment(event_id)}")
    return f"Deleted “{title}” ({_span(current)}) from your calendar."


@_run
def respond_event(event_id: str, response: Literal["accept", "tentative", "decline"], comment: str = "",
                  send_response: bool = True) -> str:
    current = _get_event(event_id)
    action = {"accept": "accept", "tentative": "tentativelyAccept", "decline": "decline"}[response]
    microsoft_account.graph("POST", f"/me/events/{segment(event_id)}/{action}",
                            body={"comment": comment, "sendResponse": bool(send_response)})
    said = {"accept": "Accepted", "tentative": "Tentatively accepted", "decline": "Declined"}[response]
    told = f" {_person(current.get('organizer'))} was told." if send_response and current.get("organizer") else ""
    return f"{said} “{current.get('subject') or '(no title)'}” ({_span(current)}).{told}"


# --- Approval cards ----------------------------------------------------------------------------------------

def _short(text: str, limit: int = 120) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _files(paths: list[str] | None) -> str:
    from row_bot.tools.gmail_tool import _resolve_file_path

    shown = []
    for given in paths or []:
        path = Path(_resolve_file_path(given))
        shown.append(f"{path.name} ({_size(path.stat().st_size)})" if path.is_file() else f"{given} (not found)")
    return ", ".join(shown)


def _quietly(read, fallback: dict) -> dict:
    """What a card shows about an item it names: read from Outlook, or only its id when that fails."""
    try:
        return read()
    except Exception:  # noqa: BLE001 -- the card still asks, with less detail
        return fallback


def approval_card(tool: str, kwargs: dict[str, Any]) -> tuple[str, str]:
    """An Outlook action's approval card: its question and what it will do, as labelled fields (who it goes to,
    what it says, its files, or the event's time, attendees and place). Reads Outlook for the item an id names."""
    from row_bot.approval_messages import approval_fields

    given = {key: value for key, value in kwargs.items() if value is not None}
    me = microsoft_account.address()
    if tool in {"outlook_send_mail", "outlook_draft_mail"}:
        to = ", ".join(given.get("to") or [])
        question = f"Send this email from {me}?" if me else "Send this email?"
        fields = {"From": me, "To": to, "Cc": ", ".join(given.get("cc") or []), "Bcc": ", ".join(given.get("bcc") or []),
                  "Subject": given.get("subject", ""), "Attachments": _files(given.get("attachments")),
                  "Body": given.get("body", "")}
    elif tool in {"outlook_reply_mail", "outlook_forward_mail", "outlook_delete_mail"}:
        message_id = str(given.get("message_id") or "")
        original = _quietly(lambda: _get_message(message_id), {"id": message_id})
        subject = original.get("subject") or "(no subject)"
        sender = _person(original.get("from"))
        about = f"“{_short(subject)}”" + (f" from {sender}" if sender else "")
        if tool == "outlook_reply_mail":
            everyone = bool(given.get("reply_all"))
            others = [item for item in (original.get("from"), *(original.get("toRecipients") or []),
                                        *(original.get("ccRecipients") or []))
                      if str(((item or {}).get("emailAddress") or {}).get("address") or "").casefold() != me.casefold()]
            to = _people(others) if everyone else sender
            question = f"Reply to everyone on {about}?" if everyone else f"Reply to {sender or 'this email'}?"
            fields = {"From": me, "To": to, "Replying to": about, "Body": given.get("body", "")}
        elif tool == "outlook_forward_mail":
            question = f"Forward {about} to {', '.join(given.get('to') or [])}?"
            fields = {"From": me, "To": ", ".join(given.get("to") or []), "Forwarding": about,
                      "Attachments": "the original's attachments go with it" if original.get("hasAttachments") else "",
                      "Message": given.get("comment", "")}
        else:
            question = f"Delete {about}?"
            fields = {"Email": about, "Received": f"{_when(_moment(original.get('receivedDateTime') or ''))} UTC"
                      if original.get("receivedDateTime") else "", "Where it goes": "Deleted Items, where it can be restored"}
    else:
        event_id = str(given.get("event_id") or "")
        current = _quietly(lambda: _get_event(event_id), {"id": event_id}) if event_id else {}
        title = given.get("subject") or current.get("subject") or "(no title)"
        attendees = given.get("attendees") or [_person(item) for item in current.get("attendees") or []]
        zone = given.get("timezone_name") or _zone()
        try:
            when = (f"{_range(_local(given['start'], zone), _local(given.get('end') or given['start'], zone))} ({zone})"
                    if given.get("start") else _span(current) if current.get("start") else "")
        except ValueError:
            when = f"{given['start']} – {given.get('end', '')} ({zone})"
        place = given.get("location") or _place(current)
        teams = "Yes" if given.get("online_meeting") or current.get("isOnlineMeeting") else ""
        if tool == "outlook_create_event":
            question = f"Send invitations for “{_short(title)}”?"
            fields = {"Event": title, "When": when, "Where": place, "Teams meeting": teams,
                      "Attendees": ", ".join(attendees), "Description": given.get("body", "")}
        elif tool == "outlook_update_event":
            question = f"Change “{_short(title)}” and update its attendees?"
            fields = {"Event": title, "When": when, "Where": place, "Teams meeting": teams,
                      "Attendees": ", ".join(attendees), "Description": given.get("body", "")}
        elif tool == "outlook_cancel_event":
            told = bool(current.get("isOrganizer") and current.get("attendees"))
            question = f"Cancel “{_short(title)}” and tell its attendees?" if told else f"Delete “{_short(title)}” from your calendar?"
            fields = {"Event": title, "When": when, "Where": place, "Attendees": ", ".join(attendees),
                      "Message": given.get("comment", "")}
        else:
            verb = {"accept": "Accept", "tentative": "Tentatively accept", "decline": "Decline"}.get(
                str(given.get("response")), "Answer")
            question = f"{verb} “{_short(title)}”?"
            fields = {"Event": title, "When": when, "Where": place, "Organiser": _person(current.get("organizer")),
                      "Answer": verb, "Tells the organiser": "Yes" if given.get("send_response", True) else "No",
                      "Message": given.get("comment", "")}
    return question[:240], approval_fields({key: value for key, value in fields.items() if value}) or TITLES[tool]


# --- Tools -------------------------------------------------------------------------------------------------

class _MailOut(BaseModel):
    to: list[str] = Field(description="Recipient email addresses.")
    subject: str = Field(description="The subject line.")
    body: str = Field(description="The message text.")
    cc: Optional[list[str]] = Field(default=None, description="Cc recipients.")
    bcc: Optional[list[str]] = Field(default=None, description="Bcc recipients.")
    attachments: Optional[list[str]] = Field(
        default=None, description="Files to attach (workspace-relative or absolute paths), up to 150 MB each.")

    @field_validator("to", "cc", "bcc", "attachments", mode="before")
    @classmethod
    def _listed(cls, value):
        return [value] if isinstance(value, str) else value


class _Search(BaseModel):
    query: str = Field(default="", description="Words to search for (sender, subject or text). Empty lists the newest.")
    folder: str = Field(default="inbox", description="inbox, drafts, sentitems, deleteditems, archive or junkemail.")
    unread_only: bool = Field(default=False, description="Only unread messages.")
    max_results: int = Field(default=10, ge=1, le=25)


class _MessageId(BaseModel):
    message_id: str = Field(description="The message's id, from a search.")


class _Reply(_MessageId):
    body: str = Field(description="The reply's text.")
    reply_all: bool = Field(default=False, description="Reply to everyone on the message.")


class _Forward(_MessageId):
    to: list[str] = Field(description="Who to forward it to.")
    comment: str = Field(default="", description="A note above the forwarded message.")

    @field_validator("to", mode="before")
    @classmethod
    def _listed(cls, value):
        return [value] if isinstance(value, str) else value


class _Update(_MessageId):
    move_to: Optional[str] = Field(default=None, description="Folder to move it to: archive, inbox, junkemail, ...")
    flag: Optional[Literal["flagged", "complete", "none"]] = Field(default=None, description="Flag it, complete or clear its flag.")
    read: Optional[bool] = Field(default=None, description="Mark it read (true) or unread (false).")


class _Range(BaseModel):
    start: str = Field(description="From: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM', in the computer's time zone.")
    end: str = Field(description="To: 'YYYY-MM-DD' (that whole day) or 'YYYY-MM-DD HH:MM'.")


class _List(_Range):
    max_results: int = Field(default=25, ge=1, le=50)


class _Free(_Range):
    duration_minutes: int = Field(default=30, ge=5, le=480, description="How long the free time must be.")
    working_hours_only: bool = Field(default=True, description="Only 09:00–17:00 on weekdays.")


class _NewEvent(BaseModel):
    subject: str = Field(description="The event title.")
    start: str = Field(description="Start: 'YYYY-MM-DD HH:MM' (or 'YYYY-MM-DD' for all day).")
    end: str = Field(description="End: 'YYYY-MM-DD HH:MM' (or 'YYYY-MM-DD' for all day).")
    timezone_name: Optional[str] = Field(default=None, description="IANA time zone; the computer's when left out.")
    location: Optional[str] = Field(default=None)
    body: Optional[str] = Field(default=None, description="The event's description.")
    attendees: Optional[list[str]] = Field(default=None, description="Email addresses to invite (asks first).")
    online_meeting: bool = Field(default=False, description="Add a Teams meeting link, when the mailbox has Teams.")
    all_day: bool = Field(default=False)


class _ChangeEvent(BaseModel):
    event_id: str = Field(description="The event's id, from list_events.")
    subject: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    timezone_name: Optional[str] = None
    location: Optional[str] = None
    body: Optional[str] = None
    attendees: Optional[list[str]] = Field(default=None, description="The full new attendee list.")
    online_meeting: Optional[bool] = None


class _CancelEvent(BaseModel):
    event_id: str = Field(description="The event's id.")
    comment: str = Field(default="", description="A note to the attendees, when it tells them.")


class _Respond(BaseModel):
    event_id: str = Field(description="The invitation's event id.")
    response: Literal["accept", "tentative", "decline"]
    comment: str = Field(default="")
    send_response: bool = Field(default=True, description="Tell the organiser.")


_MAIL = {
    "outlook_search_mail": (search_mail, _Search, "Search or list Outlook email in a folder (inbox by default). "
                            "Returns each message's sender, time, preview, id and Outlook link."),
    "outlook_read_mail": (read_mail, _MessageId, "Read one Outlook email in full, with its attachments listed."),
    "outlook_draft_mail": (draft_mail, _MailOut, "Save an Outlook email draft (never sent). Supports attachments."),
    "outlook_send_mail": (send_mail, _MailOut, "Send an email from the person's Outlook. Asks them first. "
                          "Supports attachments up to 150 MB each."),
    "outlook_reply_mail": (reply_mail, _Reply, "Reply to an Outlook email (or to everyone on it). Asks first."),
    "outlook_forward_mail": (forward_mail, _Forward, "Forward an Outlook email. Asks first."),
    "outlook_update_mail": (update_mail, _Update, "Move an Outlook email to a folder (archive it), flag it, or "
                            "mark it read or unread."),
    "outlook_delete_mail": (delete_mail, _MessageId, "Delete an Outlook email (to Deleted Items). Asks first."),
}
_CALENDAR = {
    "outlook_list_events": (list_events, _List, "List Outlook calendar events between two dates."),
    "outlook_find_free_times": (find_free_times, _Free, "Find free times in the Outlook calendar."),
    "outlook_create_event": (create_event, _NewEvent, "Create an Outlook calendar event; with attendees it sends "
                             "invitations and asks first. Can add a Teams link."),
    "outlook_update_event": (update_event, _ChangeEvent, "Change an Outlook event's title, time, place, "
                             "description or attendees; asks first when it has attendees."),
    "outlook_cancel_event": (cancel_event, _CancelEvent, "Cancel an Outlook event (telling attendees when the "
                             "person organised it) or delete it. Asks first."),
    "outlook_respond_event": (respond_event, _Respond, "Accept, tentatively accept or decline an Outlook "
                              "invitation. Asks first."),
}


def _tools(table: dict) -> list:
    me = microsoft_account.address()
    own = f" The person's own address, for “email me”, is {me}." if me else ""
    return [StructuredTool.from_function(func=work, name=name, args_schema=schema,
                                         description=description + (own if name == "outlook_send_mail" else ""))
            for name, (work, schema, description) in table.items()]


class _Outlook(BaseTool):
    @property
    def enabled_by_default(self) -> bool:
        return False  # Needs the person's own Microsoft app and a sign-in first.

    @property
    def required_api_keys(self) -> dict[str, str]:
        return {}

    def is_authenticated(self) -> bool:
        return microsoft_account.state() not in {"not_configured", "not_authenticated", "invalid"}

    def check_token_health(self) -> tuple[str, str]:
        return microsoft_account.check()

    def execute(self, query: str) -> str:
        return "Use the individual Outlook operations instead."


class OutlookMailTool(_Outlook):
    @property
    def name(self) -> str:
        return "outlook_mail"

    @property
    def display_name(self) -> str:
        return "📧 Outlook mail"

    @property
    def description(self) -> str:
        return ("Search, read, draft, send, reply to, forward, file and delete Outlook email through the person's "
                "own Microsoft app. Use it when they ask about their Outlook or Microsoft 365 email.")

    @property
    def destructive_tool_names(self) -> set[str]:
        return set(MAIL_ASKS)

    @property
    def untrusted_tool_names(self) -> set[str]:
        return set(MAIL_READS)  # Mail is written by whoever sent it.

    def as_langchain_tools(self) -> list:
        return _tools(_MAIL) if self.is_authenticated() else []


class OutlookCalendarTool(_Outlook):
    @property
    def name(self) -> str:
        return "outlook_calendar"

    @property
    def display_name(self) -> str:
        return "📅 Outlook calendar"

    @property
    def description(self) -> str:
        return ("See, create and change Outlook calendar events, find free times and answer invitations through "
                "the person's own Microsoft app.")

    @property
    def destructive_tool_names(self) -> set[str]:
        return set(CALENDAR_ASKS)

    @property
    def untrusted_tool_names(self) -> set[str]:
        return {"outlook_list_events"}  # Event titles come from whoever sent the invitation.

    def as_langchain_tools(self) -> list:
        return _tools(_CALENDAR) if self.is_authenticated() else []


registry.register(OutlookMailTool())
registry.register(OutlookCalendarTool())
