"""A fake Google Calendar v3 service, in memory: what ``CalendarTool._build_api_resource`` returns in tests.

Each request is answered from ``FakeCalendarBackend.events``; inserts can be told to fail or time out
(``insert_behaviors``), and every update, move and delete keeps its own arguments (``calls``), ``sendUpdates``
included. Nothing reaches Google.
"""

from __future__ import annotations

import ssl
import threading
from types import SimpleNamespace
from typing import Any, Callable


class FakeHttpError(Exception):
    def __init__(self, status: int, message: str = "fake Calendar error") -> None:
        super().__init__(message)
        self.resp = SimpleNamespace(status=status)


class FakeRequest:
    def __init__(self, callback: Callable[[], Any]) -> None:
        self._callback = callback

    def execute(self) -> Any:
        return self._callback()


class FakeCalendarBackend:
    def __init__(self) -> None:
        self.events: dict[tuple[str, str], dict[str, Any]] = {}
        self.services: list[FakeCalendarService] = []
        self.insert_behaviors: dict[str, list[str | int]] = {}
        self.insert_calls = 0
        self.active_inserts = 0
        self.max_active_inserts = 0
        self.insert_gate: threading.Barrier | None = None
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.guard = threading.Lock()

    def service_factory(self) -> "FakeCalendarService":
        service = FakeCalendarService(self)
        with self.guard:
            self.services.append(service)
        return service

    def next_insert_behavior(self, summary: str) -> str | int:
        with self.guard:
            self.insert_calls += 1
            self.active_inserts += 1
            self.max_active_inserts = max(self.max_active_inserts, self.active_inserts)
            behaviors = self.insert_behaviors.get(summary, [])
            behavior = behaviors.pop(0) if behaviors else "success"
        return behavior

    def finish_insert(self) -> None:
        with self.guard:
            self.active_inserts -= 1


class FakeEventsResource:
    def __init__(self, backend: FakeCalendarBackend) -> None:
        self.backend = backend

    def get(self, *, calendarId: str, eventId: str) -> FakeRequest:
        def callback() -> dict[str, Any]:
            event = self.backend.events.get((calendarId, eventId))
            if event is None:
                raise FakeHttpError(404)
            return dict(event)

        return FakeRequest(callback)

    def insert(self, *, calendarId: str, body: dict[str, Any], **_kwargs: Any) -> FakeRequest:
        def callback() -> dict[str, Any]:
            summary = str(body.get("summary") or "")
            behavior = self.backend.next_insert_behavior(summary)
            try:
                if self.backend.insert_gate is not None:
                    try:
                        self.backend.insert_gate.wait(timeout=0.2)
                    except threading.BrokenBarrierError:
                        pass
                event = dict(body)
                event["htmlLink"] = f"https://calendar.test/{body['id']}"
                if behavior == "timeout_after_commit":
                    self.backend.events[(calendarId, str(body["id"]))] = event
                    raise TimeoutError("read operation timed out")
                if behavior == "ssl_before_commit":
                    raise ssl.SSLError("record layer failure")
                if behavior == "timeout_before_commit":
                    raise TimeoutError("read operation timed out")
                if isinstance(behavior, int):
                    raise FakeHttpError(behavior, f"HTTP {behavior}")
                self.backend.events[(calendarId, str(body["id"]))] = event
                return event
            finally:
                self.backend.finish_insert()

        return FakeRequest(callback)

    def list(self, *, calendarId: str, **_kwargs: Any) -> FakeRequest:
        return FakeRequest(
            lambda: {
                "items": [
                    dict(event)
                    for (stored_calendar_id, _event_id), event in self.backend.events.items()
                    if stored_calendar_id == calendarId
                ]
            }
        )

    def update(self, *, calendarId: str, eventId: str, body: dict[str, Any], **kwargs: Any) -> FakeRequest:
        self.backend.calls.append(("update", {"calendarId": calendarId, "eventId": eventId, **kwargs}))

        def callback() -> dict[str, Any]:
            if (calendarId, eventId) not in self.backend.events:
                raise FakeHttpError(404)
            updated = dict(body)
            updated["id"] = eventId
            updated["htmlLink"] = f"https://calendar.test/{eventId}"
            self.backend.events[(calendarId, eventId)] = updated
            return updated

        return FakeRequest(callback)

    def move(self, *, eventId: str, calendarId: str, destination: str, **kwargs: Any) -> FakeRequest:
        self.backend.calls.append(("move", {"calendarId": calendarId, "eventId": eventId,
                                            "destination": destination, **kwargs}))

        def callback() -> dict[str, Any]:
            event = self.backend.events.pop((calendarId, eventId), None)
            if event is None:
                raise FakeHttpError(404)
            self.backend.events[(destination, eventId)] = event
            return dict(event)

        return FakeRequest(callback)

    def delete(self, *, eventId: str, calendarId: str, **kwargs: Any) -> FakeRequest:
        self.backend.calls.append(("delete", {"calendarId": calendarId, "eventId": eventId, **kwargs}))

        def callback() -> None:
            if self.backend.events.pop((calendarId, eventId), None) is None:
                raise FakeHttpError(404)
            return None

        return FakeRequest(callback)


class FakeCalendarListResource:
    def list(self) -> FakeRequest:
        return FakeRequest(
            lambda: {
                "items": [
                    {
                        "id": "primary",
                        "summary": "Test Calendar",
                        "timeZone": "Europe/London",
                    }
                ]
            }
        )


class FakeCalendarsResource:
    def get(self, *, calendarId: str) -> FakeRequest:
        return FakeRequest(lambda: {"id": calendarId, "timeZone": "Europe/London"})


class FakeCalendarService:
    def __init__(self, backend: FakeCalendarBackend) -> None:
        self.backend = backend

    def events(self) -> FakeEventsResource:
        return FakeEventsResource(self.backend)

    def calendarList(self) -> FakeCalendarListResource:
        return FakeCalendarListResource()

    def calendars(self) -> FakeCalendarsResource:
        return FakeCalendarsResource()
