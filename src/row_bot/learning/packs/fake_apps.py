"""Fake apps for the tool-workflow pack (D30): a calendar, a mailbox and an issue tracker.

Each lives only in memory, starts from a task's synthetic state and is changed only
through its tools. A task's verifier checks the final state (``app_state`` checks in
``packs.verify``). Nothing here touches the network, the person's accounts or files.

State shape (plain JSON)::

    {"calendar": {"events": [{"id", "title", "date", "start", "end", "attendees", "location"}]},
     "mail": {"messages": [{"id", "folder", "from", "to", "subject", "body", "date"}]},
     "issues": {"issues": [{"id", "title", "state", "labels", "assignee", "comments"}]}}

Dates are ``YYYY-MM-DD`` and times ``HH:MM`` (24-hour). Tool results are short JSON
text, as a real app's API would answer.
"""

from __future__ import annotations

import copy
import inspect
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

APPS: tuple[str, ...] = ("calendar", "mail", "issues")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_TIME = re.compile(r"\d{2}:\d{2}\Z")


class AppError(ValueError):
    """A tool call the app refuses (bad id, bad date); the model sees the message."""


@dataclass(frozen=True)
class AppTool:
    """One fake-app tool: its name, what it does, and the function that does it."""

    name: str
    app: str
    description: str
    function: Callable[..., str]

    def parameters(self) -> dict[str, Any]:
        """The JSON schema of the arguments (from the function's signature)."""

        properties: dict[str, Any] = {}
        required: list[str] = []
        for parameter in inspect.signature(self.function).parameters.values():
            kind = parameter.annotation
            text = kind if isinstance(kind, str) else getattr(kind, "__name__", str(kind))
            if "list" in text:
                schema: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
            elif "int" in text:
                schema = {"type": "integer"}
            else:
                schema = {"type": "string"}
            properties[parameter.name] = schema
            if parameter.default is inspect.Parameter.empty:
                required.append(parameter.name)
        return {"type": "object", "properties": properties, "required": required}

    def openai_schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters()}}


def _check_date(value: str) -> str:
    text = str(value or "").strip()
    if not _DATE.match(text):
        raise AppError(f"dates are YYYY-MM-DD, not {value!r}")
    return text


def _check_time(value: str) -> str:
    text = str(value or "").strip()
    if not _TIME.match(text):
        raise AppError(f"times are HH:MM (24-hour), not {value!r}")
    return text


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class FakeApps:
    """The apps one task uses, with their state. ``state()`` is a deep copy."""

    def __init__(self, state: Mapping[str, Any] | None = None) -> None:
        start = copy.deepcopy(dict(state or {}))
        self._state: dict[str, Any] = {
            "calendar": {"events": list((start.get("calendar") or {}).get("events") or [])},
            "mail": {"messages": list((start.get("mail") or {}).get("messages") or [])},
            "issues": {"issues": list((start.get("issues") or {}).get("issues") or [])},
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def state(self) -> dict[str, Any]:
        return copy.deepcopy(self._state)

    # ── Calendar ─────────────────────────────────────────────────────────

    def _events(self) -> list[dict[str, Any]]:
        return self._state["calendar"]["events"]

    def _event(self, event_id: str) -> dict[str, Any]:
        for event in self._events():
            if event["id"] == str(event_id).strip():
                return event
        raise AppError(f"no event {event_id!r}")

    def calendar_list_events(self, date: str = "") -> str:
        wanted = _check_date(date) if str(date or "").strip() else ""
        events = [event for event in self._events() if not wanted or event["date"] == wanted]
        events.sort(key=lambda event: (event["date"], event["start"], event["id"]))
        return _dump({"events": events})

    def calendar_create_event(self, title: str, date: str, start: str, end: str,
                              attendees: list[str] | None = None, location: str = "") -> str:
        if not str(title or "").strip():
            raise AppError("an event needs a title")
        start_time, end_time = _check_time(start), _check_time(end)
        if end_time <= start_time:
            raise AppError("an event ends after it starts")
        number = 1 + max((int(event["id"].split("_")[-1]) for event in self._events()
                          if event["id"].split("_")[-1].isdigit()), default=0)
        event = {"id": f"evt_{number}", "title": str(title).strip(), "date": _check_date(date),
                 "start": start_time, "end": end_time,
                 "attendees": [str(item).strip() for item in (attendees or []) if str(item).strip()],
                 "location": str(location or "").strip()}
        self._events().append(event)
        return _dump({"created": event})

    def calendar_update_event(self, event_id: str, title: str = "", date: str = "", start: str = "",
                              end: str = "", location: str = "") -> str:
        event = self._event(event_id)
        changed = dict(event)
        if str(title or "").strip():
            changed["title"] = str(title).strip()
        if str(date or "").strip():
            changed["date"] = _check_date(date)
        if str(start or "").strip():
            changed["start"] = _check_time(start)
        if str(end or "").strip():
            changed["end"] = _check_time(end)
        if str(location or "").strip():
            changed["location"] = str(location).strip()
        if changed["end"] <= changed["start"]:
            raise AppError("an event ends after it starts")
        event.update(changed)
        return _dump({"updated": event})

    def calendar_delete_event(self, event_id: str) -> str:
        event = self._event(event_id)
        self._events().remove(event)
        return _dump({"deleted": event["id"]})

    # ── Mail ─────────────────────────────────────────────────────────────

    def _messages(self) -> list[dict[str, Any]]:
        return self._state["mail"]["messages"]

    def _message(self, message_id: str) -> dict[str, Any]:
        for message in self._messages():
            if message["id"] == str(message_id).strip():
                return message
        raise AppError(f"no message {message_id!r}")

    def mail_list(self, folder: str = "inbox") -> str:
        wanted = str(folder or "inbox").strip().lower()
        rows = [{key: message[key] for key in ("id", "from", "to", "subject", "date")}
                for message in self._messages() if message["folder"] == wanted]
        rows.sort(key=lambda row: (row["date"], row["id"]))
        return _dump({"folder": wanted, "messages": rows})

    def mail_read(self, message_id: str) -> str:
        return _dump(self._message(message_id))

    def mail_send(self, to: str, subject: str, body: str) -> str:
        recipient = str(to or "").strip().lower()
        if "@" not in recipient:
            raise AppError(f"{to!r} is not an email address")
        number = 1 + sum(1 for message in self._messages() if message["folder"] == "sent")
        message = {"id": f"sent_{number}", "folder": "sent", "from": "me@example.test", "to": recipient,
                   "subject": str(subject or "").strip(), "body": str(body or "").strip(), "date": ""}
        self._messages().append(message)
        return _dump({"sent": message["id"]})

    def mail_archive(self, message_id: str) -> str:
        message = self._message(message_id)
        if message["folder"] != "inbox":
            raise AppError(f"{message_id} is not in the inbox")
        message["folder"] = "archive"
        return _dump({"archived": message["id"]})

    # ── Issues ───────────────────────────────────────────────────────────

    def _issues(self) -> list[dict[str, Any]]:
        return self._state["issues"]["issues"]

    def _issue(self, issue_id: str) -> dict[str, Any]:
        wanted = str(issue_id).strip().lstrip("#")
        for issue in self._issues():
            if str(issue["id"]) == wanted:
                return issue
        raise AppError(f"no issue {issue_id!r}")

    def issues_list(self, state: str = "open", label: str = "") -> str:
        wanted_state = str(state or "open").strip().lower()
        wanted_label = str(label or "").strip().lower()
        rows = [{key: issue[key] for key in ("id", "title", "state", "labels", "assignee")}
                for issue in self._issues()
                if (wanted_state == "all" or issue["state"] == wanted_state)
                and (not wanted_label or wanted_label in issue["labels"])]
        rows.sort(key=lambda row: int(row["id"]))
        return _dump({"issues": rows})

    def issues_get(self, issue_id: str) -> str:
        return _dump(self._issue(issue_id))

    def issues_update(self, issue_id: str, state: str = "", add_labels: list[str] | None = None,
                      remove_labels: list[str] | None = None, assignee: str = "") -> str:
        issue = self._issue(issue_id)
        if str(state or "").strip():
            value = str(state).strip().lower()
            if value not in {"open", "closed"}:
                raise AppError("an issue's state is open or closed")
            issue["state"] = value
        labels = [label for label in issue["labels"]
                  if label not in {str(item).strip().lower() for item in (remove_labels or [])}]
        for label in add_labels or []:
            clean = str(label).strip().lower()
            if clean and clean not in labels:
                labels.append(clean)
        issue["labels"] = labels
        if str(assignee or "").strip():
            value = str(assignee).strip().lower()
            issue["assignee"] = "" if value in {"none", "nobody", "unassigned"} else value
        return _dump({"updated": issue})

    def issues_comment(self, issue_id: str, body: str) -> str:
        issue = self._issue(issue_id)
        text = str(body or "").strip()
        if not text:
            raise AppError("a comment needs text")
        issue["comments"].append(text)
        return _dump({"commented": issue["id"]})

    # ── Tools ────────────────────────────────────────────────────────────

    _DESCRIPTIONS: dict[str, str] = {
        "calendar_list_events": "List calendar events, all or on one date (YYYY-MM-DD).",
        "calendar_create_event": "Create a calendar event. Dates YYYY-MM-DD, times HH:MM (24-hour).",
        "calendar_update_event": "Change an event by its id: only the fields given change.",
        "calendar_delete_event": "Delete an event by its id.",
        "mail_list": "List the messages in a mail folder: inbox (default), archive or sent.",
        "mail_read": "Read one message by its id.",
        "mail_send": "Send an email.",
        "mail_archive": "Move a message from the inbox to the archive.",
        "issues_list": "List issues: state open (default), closed or all; optionally only one label.",
        "issues_get": "Read one issue by its number, with its comments.",
        "issues_update": "Change an issue: its state (open/closed), labels to add or remove, or its assignee.",
        "issues_comment": "Add a comment to an issue.",
    }

    def tools(self, apps: Iterable[str]) -> list[AppTool]:
        wanted = [app for app in apps if app in APPS]
        return [AppTool(name, name.split("_", 1)[0], description, getattr(self, name))
                for name, description in self._DESCRIPTIONS.items() if name.split("_", 1)[0] in wanted]

    def call(self, name: str, arguments: Mapping[str, Any] | None = None) -> str:
        """Run one tool by name. A refused call answers ``{"error": ...}``, as an app would."""

        if name not in self._DESCRIPTIONS:
            return _dump({"error": f"no tool {name!r}"})
        arguments = dict(arguments or {})
        self.calls.append((name, copy.deepcopy(arguments)))
        try:
            return getattr(self, name)(**arguments)
        except (AppError, TypeError) as error:
            return _dump({"error": str(error)})


def tool_names(apps: Iterable[str]) -> list[str]:
    """The tool names a task's apps bring (for schemas and prompts)."""

    return [tool.name for tool in FakeApps().tools(apps)]


__all__ = ["APPS", "AppError", "AppTool", "FakeApps", "tool_names"]
