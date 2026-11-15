"""What the watchers read: the person's own connected mail, calendars, chats, memory and Monitor.

Each source is a small read-only adapter over an integration Row-Bot already has (Gmail and Google Calendar
through the Google sign-in, Outlook mail and calendar through Microsoft Graph, chats in threads.db, memory
in the knowledge graph, Monitor's kept checks). A source is *available* only when its integration is
signed in and its tool is on; otherwise its watcher is skipped. Nothing here sends, changes or deletes,
and nothing here calls a model. Tests hand the watchers fakes with the same shape.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

logger = logging.getLogger(__name__)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SNIPPET_CHARS = 300


@dataclass(frozen=True)
class MailThread:
    """A thread whose last message is the person's (``sent``) or someone else's (waiting on the person)."""

    source: str  # gmail or outlook
    thread_id: str
    last_message_id: str
    other: str  # Who it is with: their address or name.
    subject: str
    at: float  # When its last message was sent or received.
    snippet: str = ""  # The last message's opening words: untrusted, from outside.


@dataclass(frozen=True)
class Event:
    source: str  # google_calendar or outlook_calendar
    id: str
    title: str
    start: float
    end: float
    attendees: tuple[str, ...] = ()
    has_files: bool = False
    cancelled: bool = False


@dataclass(frozen=True)
class ChatLine:
    """One of the person's own messages in a chat."""

    conversation_id: str
    conversation_title: str
    text: str

    @property
    def id(self) -> str:
        return text_id(self.text)


def text_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class MailSource(Protocol):
    name: str

    def available(self) -> bool: ...
    def unanswered_sent(self, since: float, before: float) -> list[MailThread]: ...
    def waiting_on_me(self, since: float, before: float) -> list[MailThread]: ...
    def still_last(self, thread_id: str, message_id: str) -> bool | None: ...


class CalendarSource(Protocol):
    name: str

    def available(self) -> bool: ...
    def events(self, start: float, end: float) -> list[Event]: ...
    def event(self, event_id: str) -> Event | None: ...


def _tool_ready(name: str) -> bool:
    from row_bot.tools import registry

    tool = registry.get_tool(name)
    return bool(tool is not None and registry.is_enabled(name) and tool.is_authenticated())


def _address(text: str) -> str:
    found = _EMAIL.search(text or "")
    return found.group(0).lower() if found else ""


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: str) -> float:
    """An ISO time from Google or Graph (Graph sends seven fraction digits and no zone: UTC)."""
    text = re.sub(r"(\.\d{6})\d+", r"\1", str(value or "").replace("Z", "+00:00"))
    moment = datetime.fromisoformat(text)
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp()


# --- Gmail and Google Calendar -----------------------------------------------------------------------------

class GmailSource:
    name = "gmail"

    def available(self) -> bool:
        return _tool_ready("gmail")

    def _service(self) -> Any:
        from row_bot.tools import registry

        return registry.get_tool("gmail")._build_api_resource()

    @staticmethod
    def _headers(message: dict) -> dict[str, str]:
        return {item["name"].lower(): item["value"] for item in message.get("payload", {}).get("headers", [])}

    def _threads(self, service: Any, query: str) -> list[dict]:
        found = service.users().threads().list(userId="me", q=query, maxResults=25).execute().get("threads", [])
        return [service.users().threads().get(userId="me", id=item["id"], format="metadata",
                                              metadataHeaders=["From", "To", "Subject"]).execute()
                for item in found]

    def _scan(self, query: str, since: float, before: float, *, mine: bool) -> list[MailThread]:
        service = self._service()
        me = str(service.users().getProfile(userId="me").execute().get("emailAddress") or "").lower()
        result = []
        for thread in self._threads(service, query):
            last = (thread.get("messages") or [{}])[-1]
            headers = self._headers(last)
            at = int(last.get("internalDate") or 0) / 1000
            if (_address(headers.get("from", "")) == me) != mine or not since <= at <= before:
                continue
            other = headers.get("to", "") if mine else headers.get("from", "")
            result.append(MailThread("gmail", thread["id"], last.get("id", ""), _address(other) or other.strip(),
                                     headers.get("subject", ""), at, str(last.get("snippet") or "")[:_SNIPPET_CHARS]))
        return result

    def unanswered_sent(self, since: float, before: float) -> list[MailThread]:
        return self._scan(f"in:sent after:{int(since)} before:{int(before)}", since, before, mine=True)

    def waiting_on_me(self, since: float, before: float) -> list[MailThread]:
        return self._scan(f"in:inbox category:primary is:important -from:me after:{int(since)} before:{int(before)}",
                          since, before, mine=False)

    def still_last(self, thread_id: str, message_id: str) -> bool | None:
        thread = self._service().users().threads().get(userId="me", id=thread_id, format="minimal").execute()
        messages = thread.get("messages") or []
        return bool(messages) and messages[-1].get("id") == message_id


class GoogleCalendarSource:
    name = "google_calendar"

    def available(self) -> bool:
        return _tool_ready("calendar")

    def _service(self) -> Any:
        from row_bot.tools import registry

        return registry.get_tool("calendar")._build_api_resource()

    @staticmethod
    def _event(calendar_id: str, item: dict) -> Event | None:
        start, end = item.get("start", {}).get("dateTime"), item.get("end", {}).get("dateTime")
        if not start or not end:
            return None  # All-day: nothing to prepare for at a time of day.
        attendees = tuple(str(person.get("email") or "") for person in item.get("attendees") or []
                          if not person.get("self") and not person.get("resource"))
        return Event("google_calendar", f"{calendar_id}|{item.get('id', '')}", str(item.get("summary") or ""),
                     _parse_time(start), _parse_time(end), attendees, bool(item.get("attachments")),
                     item.get("status") == "cancelled")

    def events(self, start: float, end: float) -> list[Event]:
        service = self._service()
        found: list[Event] = []
        for calendar in service.calendarList().list().execute().get("items", []):
            calendar_id = str(calendar.get("id") or "")
            items = service.events().list(calendarId=calendar_id, timeMin=_iso(start), timeMax=_iso(end),
                                          singleEvents=True, orderBy="startTime", maxResults=50).execute()
            found.extend(event for item in items.get("items", []) if (event := self._event(calendar_id, item)))
        return found

    def event(self, event_id: str) -> Event | None:
        calendar_id, _sep, own_id = event_id.partition("|")
        try:
            item = self._service().events().get(calendarId=calendar_id, eventId=own_id).execute()
        except Exception as exc:
            if getattr(getattr(exc, "resp", None), "status", None) in (404, 410):
                return None
            raise
        return self._event(calendar_id, item)


# --- Outlook mail and calendar (Microsoft Graph) -----------------------------------------------------------

def _graph(path: str, params: dict) -> list[dict]:
    from row_bot import microsoft_account

    return microsoft_account.graph("GET", path, params=params,
                                   headers={"Prefer": 'outlook.timezone="UTC"'}).get("value") or []


def _graph_me() -> str:
    from row_bot import microsoft_account

    me = microsoft_account.graph("GET", "/me", params={"$select": "mail,userPrincipalName"})
    return str(me.get("mail") or me.get("userPrincipalName") or "").lower()


def _sender(message: dict) -> str:
    return str(((message.get("from") or {}).get("emailAddress") or {}).get("address") or "").lower()


class OutlookSource:
    name = "outlook"

    def available(self) -> bool:
        return _tool_ready("outlook_mail")

    @staticmethod
    def _conversation(conversation_id: str) -> list[dict]:
        quoted = conversation_id.replace("'", "''")
        messages = _graph("/me/messages", {"$filter": f"conversationId eq '{quoted}'", "$top": 25,
                                           "$select": "id,from,receivedDateTime,sentDateTime,bodyPreview"})
        return sorted(messages, key=lambda item: str(item.get("receivedDateTime") or item.get("sentDateTime") or ""))

    def _scan(self, folder: str, field: str, since: float, before: float, *, mine: bool) -> list[MailThread]:
        me = _graph_me()
        found = _graph(f"/me/mailFolders/{folder}/messages", {
            "$filter": f"{field} ge {_iso(since)} and {field} le {_iso(before)}", "$top": 25,
            "$select": "id,conversationId,subject,from,toRecipients,sentDateTime,receivedDateTime"})
        result, seen = [], set()
        for message in found:
            conversation = str(message.get("conversationId") or "")
            if not conversation or conversation in seen:
                continue
            seen.add(conversation)
            last = (self._conversation(conversation) or [message])[-1]
            if (_sender(last) == me) != mine:
                continue
            if mine:
                people = [(item.get("emailAddress") or {}).get("address", "") for item in message.get("toRecipients") or []]
                other = ", ".join(person for person in people if person)
            else:
                other = _sender(last)
            result.append(MailThread("outlook", conversation, str(last.get("id") or ""), other,
                                     str(message.get("subject") or ""),
                                     _parse_time(str(last.get("receivedDateTime") or last.get("sentDateTime"))),
                                     str(last.get("bodyPreview") or "")[:_SNIPPET_CHARS]))
        return result

    def unanswered_sent(self, since: float, before: float) -> list[MailThread]:
        return self._scan("sentitems", "sentDateTime", since, before, mine=True)

    def waiting_on_me(self, since: float, before: float) -> list[MailThread]:
        return self._scan("inbox", "receivedDateTime", since, before, mine=False)

    def still_last(self, thread_id: str, message_id: str) -> bool | None:
        messages = self._conversation(thread_id)
        return bool(messages) and str(messages[-1].get("id") or "") == message_id


class OutlookCalendarSource:
    name = "outlook_calendar"
    _FIELDS = "id,subject,start,end,attendees,isCancelled,hasAttachments,isAllDay"

    def available(self) -> bool:
        return _tool_ready("outlook_calendar")

    @staticmethod
    def _event(item: dict) -> Event | None:
        if item.get("isAllDay"):
            return None
        attendees = tuple(str((person.get("emailAddress") or {}).get("address") or "")
                          for person in item.get("attendees") or [])
        return Event("outlook_calendar", str(item.get("id") or ""), str(item.get("subject") or ""),
                     _parse_time(item["start"]["dateTime"]), _parse_time(item["end"]["dateTime"]), attendees,
                     bool(item.get("hasAttachments")), bool(item.get("isCancelled")))

    def events(self, start: float, end: float) -> list[Event]:
        items = _graph("/me/calendarView", {"startDateTime": _iso(start), "endDateTime": _iso(end), "$top": 50,
                                            "$select": self._FIELDS})
        return [event for item in items if (event := self._event(item))]

    def event(self, event_id: str) -> Event | None:
        from row_bot import microsoft_account
        from row_bot.microsoft_account import GraphError, segment

        try:
            item = microsoft_account.graph("GET", f"/me/events/{segment(event_id)}", params={"$select": self._FIELDS},
                                           headers={"Prefer": 'outlook.timezone="UTC"'})
        except GraphError as error:
            if error.status == 404:
                return None
            raise
        return self._event(item)


# --- Chats, memory and Monitor -----------------------------------------------------------------------------

# Conversations Proactive never reads: its own preparation, helpers' threads, and incognito chats.
_UNREAD_TYPES = ("proactive", "agent_child")


class ChatSource:
    """The person's own messages in their chats (never workflows', helpers', incognito or Proactive's own)."""

    name = "chats"

    def available(self) -> bool:
        return True

    def conversations(self, since: float) -> list[tuple[str, str]]:
        """(id, title) of chats changed since *since*, newest first."""
        from row_bot import threads

        threads._ensure_thread_db()
        with closing(sqlite3.connect(threads.DB_PATH)) as conn:
            rows = conn.execute(
                "SELECT thread_id, name FROM thread_meta WHERE COALESCE(deleted_at, '') = '' "
                f"AND COALESCE(thread_type, '') NOT IN ({', '.join('?' * len(_UNREAD_TYPES))}) "
                "AND COALESCE(incognito, 0) = 0 AND COALESCE(updated_at, '') > ? ORDER BY updated_at DESC LIMIT 50",
                (*_UNREAD_TYPES, datetime.fromtimestamp(since).isoformat())).fetchall()
        workflows = threads.get_workflow_thread_ids()
        return [(str(row[0]), str(row[1] or "")) for row in rows if row[0] not in workflows]

    def lines(self, conversation_id: str, title: str = "") -> list[ChatLine]:
        """The person's own messages in one chat, oldest first."""
        from row_bot import threads
        from row_bot.approval_review import person_messages

        return [ChatLine(conversation_id, title, text)
                for text in person_messages(threads.get_latest_checkpoint_messages(conversation_id), limit=200)]

    def still_there(self, conversation_id: str, line_id: str) -> bool | None:
        """Whether the chat is still there and still holds that message."""
        from row_bot import threads

        with closing(sqlite3.connect(threads.DB_PATH)) as conn:
            row = conn.execute("SELECT COALESCE(deleted_at, '') FROM thread_meta WHERE thread_id = ?",
                               (conversation_id,)).fetchone()
        if row is None or row[0]:
            return False
        return any(line.id == line_id for line in self.lines(conversation_id))


@dataclass(frozen=True)
class DatedMemory:
    entity_id: str
    subject: str
    field: str
    value: str


class MemorySource:
    """Dates kept in memory: an entity's properties like ``birthday`` or ``renewal``."""

    name = "memory"
    FIELDS = ("birthday", "anniversary", "renewal", "renews", "deadline", "due", "expires", "expiry", "date")

    def available(self) -> bool:
        return True

    def dated(self) -> list[DatedMemory]:
        from row_bot import knowledge_graph

        found = []
        for entity in knowledge_graph.iter_entities_snapshot():
            try:
                properties = json.loads(entity.get("properties") or "{}")
            except ValueError:
                continue
            for field, value in (properties.items() if isinstance(properties, dict) else ()):
                if str(field).lower() in self.FIELDS and isinstance(value, str) and value.strip():
                    found.append(DatedMemory(str(entity["id"]), str(entity.get("subject") or ""),
                                             str(field).lower(), value.strip()))
        return found

    def value(self, entity_id: str, field: str) -> str | None:
        from row_bot import knowledge_graph

        entity = knowledge_graph.get_entity(entity_id)
        if entity is None:
            return None
        properties = entity.get("properties") or {}
        if isinstance(properties, str):
            try:
                properties = json.loads(properties)
            except ValueError:
                return None
        return next((str(value).strip() for key, value in properties.items() if str(key).lower() == field), None)


class MonitorSource:
    """Monitor's own kept checks (they run on their own schedule; nothing is checked again here)."""

    name = "monitor"

    def available(self) -> bool:
        return True

    def checks(self) -> list[dict[str, Any]]:
        from row_bot.application.client_diagnosis import read_system_health

        return list(read_system_health()["checks"])


def mail_sources() -> list[MailSource]:
    return [GmailSource(), OutlookSource()]


def calendar_sources() -> list[CalendarSource]:
    return [GoogleCalendarSource(), OutlookCalendarSource()]
