"""Google Calendar tools with request-scoped, thread-safe API clients."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import pathlib
import random
import re
import ssl
import tempfile
import threading
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.tools import registry
from row_bot.tools.base import BaseTool

logger = logging.getLogger(__name__)

# Credential / token files live in the Row-Bot data directory.
_DATA_DIR = get_row_bot_data_dir()
_CALENDAR_DIR = _DATA_DIR / "calendar"
_CALENDAR_DIR.mkdir(parents=True, exist_ok=True)

# Re-use the same Google OAuth credentials.json as Gmail (same Cloud project),
# but keep a separate token file because the scopes differ.
_GMAIL_DIR = _DATA_DIR / "gmail"
DEFAULT_CREDENTIALS_PATH = str(_GMAIL_DIR / "credentials.json")
DEFAULT_TOKEN_PATH = str(_CALENDAR_DIR / "token.json")

# Calendar operations — tiered by risk.
_READ_OPS = ["get_current_datetime", "search_events"]
_WRITE_OPS = [
    "create_calendar_event",
    "create_calendar_events",
    "update_calendar_event",
]
_DESTRUCTIVE_OPS = ["move_calendar_event", "delete_calendar_event"]
ALL_OPERATIONS = _READ_OPS + _WRITE_OPS + _DESTRUCTIVE_OPS
DEFAULT_OPERATIONS = _READ_OPS + _WRITE_OPS

CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]

_MAX_RETRY_ATTEMPTS = 3
_RETRY_BASE_SECONDS = 1.0
_RETRY_CAP_SECONDS = 4.0
_EVENT_ID_NAMESPACE = uuid.UUID("1caa59e8-dc50-4f73-941f-1b4566d2b2cf")
_PRIVATE_FINGERPRINT_KEY = "row_bot_fingerprint"

_LOCKS_GUARD = threading.Lock()
_CREDENTIAL_LOCKS: dict[str, threading.Lock] = {}
_MUTATION_LOCKS: dict[str, threading.RLock] = {}


def _lock_for(
    cache: dict[str, threading.Lock | threading.RLock],
    key: str,
    factory: Callable[[], threading.Lock | threading.RLock],
) -> threading.Lock | threading.RLock:
    with _LOCKS_GUARD:
        lock = cache.get(key)
        if lock is None:
            lock = factory()
            cache[key] = lock
        return lock


def _credential_lock(token_path: str) -> threading.Lock:
    key = str(pathlib.Path(token_path).expanduser().resolve())
    return _lock_for(_CREDENTIAL_LOCKS, key, threading.Lock)  # type: ignore[return-value]


def _mutation_lock(token_path: str) -> threading.RLock:
    key = str(pathlib.Path(token_path).expanduser().resolve())
    return _lock_for(_MUTATION_LOCKS, key, threading.RLock)  # type: ignore[return-value]


def _write_credentials_atomically(token_path: str, credentials: Any) -> None:
    """Persist refreshed OAuth credentials without exposing a partial token file."""
    path = pathlib.Path(token_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    os.close(fd)
    temporary_path = pathlib.Path(temporary_name)
    try:
        temporary_path.write_text(credentials.to_json(), encoding="utf-8")
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("Could not clean temporary Calendar token file", exc_info=True)


def _load_google_credentials(token_path: str) -> Any:
    """Load independent credentials and refresh them single-flight if required."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = pathlib.Path(token_path)
    credentials = Credentials.from_authorized_user_file(str(path), CALENDAR_SCOPES)
    if credentials.valid:
        return credentials

    with _credential_lock(str(path)):
        # Another request may have refreshed the token while this request waited.
        credentials = Credentials.from_authorized_user_file(str(path), CALENDAR_SCOPES)
        if credentials.valid:
            return credentials
        if not credentials.refresh_token:
            raise RuntimeError("Calendar token is invalid and has no refresh token")
        credentials.refresh(Request())
        if not credentials.valid:
            raise RuntimeError("Calendar token refresh did not produce valid credentials")
        _write_credentials_atomically(str(path), credentials)
        return credentials


def _check_google_token(token_path: str) -> tuple[str, str]:
    """Probe a Google OAuth token and attempt a concurrency-safe silent refresh."""
    if not os.path.isfile(token_path):
        return ("missing", "No token file found")
    try:
        from google.oauth2.credentials import Credentials

        existing = Credentials.from_authorized_user_file(token_path, CALENDAR_SCOPES)
        if existing.valid:
            return ("valid", "Token is valid")
        if not existing.refresh_token:
            return ("expired", "Token expired and no refresh token available")
        _load_google_credentials(token_path)
        return ("refreshed", "Token refreshed successfully")
    except Exception as exc:
        error = str(exc).lower()
        if "invalid_grant" in error or "revoked" in error:
            return (
                "expired",
                "Refresh token expired or revoked — re-authenticate in Settings",
            )
        return ("error", f"Token check failed: {exc}")


@dataclass(frozen=True)
class _ErrorInfo:
    category: str
    retryable: bool
    detail: str
    status: int | None = None


def _http_status(exc: BaseException) -> int | None:
    response = getattr(exc, "resp", None)
    status = getattr(response, "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _classify_error(exc: BaseException) -> _ErrorInfo:
    status = _http_status(exc)
    error_text = str(exc).lower()

    if isinstance(exc, ssl.SSLError):
        return _ErrorInfo("ssl", True, "The secure Calendar connection failed.")
    if isinstance(exc, TimeoutError) or "timed out" in error_text:
        return _ErrorInfo("timeout", True, "The Calendar request timed out.")
    if isinstance(exc, (ConnectionError, ConnectionResetError, BrokenPipeError)):
        return _ErrorInfo("connection", True, "The Calendar connection was interrupted.")
    if exc.__class__.__module__.startswith("httplib2"):
        return _ErrorInfo("connection", True, "The Calendar connection failed.")

    if status == 409:
        return _ErrorInfo("conflict", False, "The Calendar event already exists.", status)
    if status == 404:
        return _ErrorInfo("not_found", False, "The Calendar event was not found.", status)
    if status == 401:
        return _ErrorInfo(
            "authorization",
            False,
            "Google Calendar authorization is invalid or expired.",
            status,
        )
    if status == 403 and ("rate" in error_text or "quota" in error_text):
        return _ErrorInfo("rate_limit", True, "Google Calendar rate limited the request.", status)
    if status == 403:
        return _ErrorInfo(
            "permission",
            False,
            "Google Calendar denied permission for this operation.",
            status,
        )
    if status == 429:
        return _ErrorInfo("rate_limit", True, "Google Calendar rate limited the request.", status)
    if status is not None and status >= 500:
        return _ErrorInfo(
            "google_backend",
            True,
            "Google Calendar temporarily failed to process the request.",
            status,
        )
    if status is not None and 400 <= status < 500:
        return _ErrorInfo(
            "invalid_request",
            False,
            f"Google Calendar rejected the request (HTTP {status}).",
            status,
        )
    if isinstance(exc, ValueError):
        return _ErrorInfo("validation", False, str(exc))
    return _ErrorInfo(
        "unexpected",
        False,
        f"Unexpected Calendar failure ({type(exc).__name__}).",
        status,
    )


def _retry_delay(attempt: int) -> float:
    base = min(_RETRY_CAP_SECONDS, _RETRY_BASE_SECONDS * (2 ** max(0, attempt - 1)))
    return base + random.random()


def _json_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _failure_result(
    operation: str,
    info: _ErrorInfo,
    *,
    attempts: int,
    event_id: str = "",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "operation": operation,
        "status": "failed",
        "error": {
            "category": info.category,
            "detail": info.detail,
            "retryable": info.retryable,
        },
        "attempts": attempts,
    }
    if info.status is not None:
        result["error"]["http_status"] = info.status
    if event_id:
        result["event_id"] = event_id
    return result


class _CurrentDatetimeInput(BaseModel):
    calendar_id: str = Field(
        default="primary", description="Calendar ID. Defaults to 'primary'."
    )


class _SearchEventsInput(BaseModel):
    min_datetime: str = Field(
        description="Start datetime in 'YYYY-MM-DD HH:MM:SS' format."
    )
    max_datetime: str = Field(
        description="End datetime in 'YYYY-MM-DD HH:MM:SS' format."
    )
    max_results: int = Field(
        default=10, ge=1, le=2500, description="Maximum results per calendar."
    )
    query: Optional[str] = Field(
        default=None, description="Optional free-text event search term."
    )


class _CreateEventInput(BaseModel):
    summary: str = Field(description="The event title.")
    start_datetime: str = Field(
        description="Start in 'YYYY-MM-DD HH:MM:SS' or all-day 'YYYY-MM-DD' format."
    )
    end_datetime: str = Field(
        description="End in 'YYYY-MM-DD HH:MM:SS' or all-day 'YYYY-MM-DD' format."
    )
    timezone: str = Field(description="IANA timezone, for example 'Europe/London'.")
    calendar_id: str = Field(default="primary", description="Destination calendar ID.")
    recurrence: Optional[dict[str, Any]] = Field(default=None)
    location: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None)
    attendees: Optional[list[str]] = Field(default=None)
    reminders: None | bool | list[dict[str, Any]] = Field(default=None)
    conference_data: Optional[bool] = Field(default=None)
    color_id: Optional[str] = Field(default=None)
    transparency: Optional[str] = Field(default=None)
    idempotency_key: Optional[str] = Field(
        default=None,
        description=(
            "Optional stable key used to distinguish intentionally similar events. "
            "Omit it for normal duplicate-safe creation."
        ),
    )


class _BulkCreateEventsInput(BaseModel):
    events: list[_CreateEventInput] = Field(
        min_length=1,
        max_length=100,
        description="Events to create. They are processed safely in the supplied order.",
    )


class _UpdateEventInput(BaseModel):
    event_id: str
    calendar_id: str = "primary"
    summary: Optional[str] = None
    start_datetime: Optional[str] = None
    end_datetime: Optional[str] = None
    timezone: Optional[str] = None
    recurrence: Optional[dict[str, Any]] = None
    location: Optional[str] = None
    description: Optional[str] = None
    attendees: Optional[list[str]] = None
    reminders: None | bool | list[dict[str, Any]] = None
    conference_data: Optional[bool] = None
    color_id: Optional[str] = None
    transparency: Optional[str] = None
    send_updates: Optional[str] = None


class _MoveEventInput(BaseModel):
    event_id: str
    origin_calendar_id: str
    destination_calendar_id: str
    send_updates: Optional[str] = None


class _DeleteEventInput(BaseModel):
    event_id: str
    calendar_id: str = "primary"
    send_updates: Optional[str] = None


def _parse_event_times(start: str, end: str, timezone: str) -> tuple[dict[str, str], dict[str, str]]:
    try:
        start_date = datetime.strptime(start, "%Y-%m-%d")
        end_date = datetime.strptime(end, "%Y-%m-%d")
    except ValueError:
        start_date = end_date = None  # type: ignore[assignment]
    if start_date is not None and end_date is not None:
        if end_date <= start_date:
            raise ValueError("The event end date must be after its start date.")
        return ({"date": start}, {"date": end})

    try:
        tz = ZoneInfo(timezone)
        start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
        end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
    except (ValueError, KeyError) as exc:
        raise ValueError(
            "Calendar datetimes must use 'YYYY-MM-DD HH:MM:SS' with a valid IANA timezone, "
            "or both values must be all-day 'YYYY-MM-DD' dates."
        ) from exc
    if end_dt <= start_dt:
        raise ValueError("The event end time must be after its start time.")
    return (
        {"dateTime": start_dt.isoformat(), "timeZone": timezone},
        {"dateTime": end_dt.isoformat(), "timeZone": timezone},
    )


def _validate_attendees(attendees: Optional[list[str]]) -> list[dict[str, str]] | None:
    if attendees is None:
        return None
    email_pattern = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    result = []
    for email in attendees:
        if not email_pattern.match(email):
            raise ValueError(f"Invalid attendee email address: {email}")
        result.append({"email": email})
    return result


def _normalize_reminders(
    reminders: None | bool | list[dict[str, Any]],
) -> dict[str, Any] | None:
    if reminders is None:
        return None
    if reminders is True:
        return {"useDefault": True}
    if reminders is False:
        return {"useDefault": False}
    overrides = []
    for reminder in reminders:
        method = reminder.get("method")
        minutes = reminder.get("minutes")
        if method not in {"email", "popup"}:
            raise ValueError("Reminder method must be 'email' or 'popup'.")
        if not isinstance(minutes, int) or minutes < 0:
            raise ValueError("Reminder minutes must be a non-negative integer.")
        overrides.append({"method": method, "minutes": minutes})
    return {"useDefault": False, "overrides": overrides}


def _event_identity(payload: dict[str, Any]) -> tuple[str, str]:
    normalized = dict(payload)
    normalized["summary"] = str(normalized.get("summary") or "").strip()
    normalized["attendees"] = sorted(normalized.get("attendees") or [])
    reminders = normalized.get("reminders")
    if isinstance(reminders, list):
        normalized["reminders"] = sorted(
            reminders, key=lambda item: (str(item.get("method")), int(item.get("minutes", 0)))
        )
    identity_json = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    fingerprint = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()
    return fingerprint, uuid.uuid5(_EVENT_ID_NAMESPACE, fingerprint).hex


def _prepare_event_body(payload: dict[str, Any], fingerprint: str, event_id: str) -> dict[str, Any]:
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        raise ValueError("Calendar event summary cannot be empty.")
    start, end = _parse_event_times(
        str(payload["start_datetime"]),
        str(payload["end_datetime"]),
        str(payload["timezone"]),
    )
    body: dict[str, Any] = {
        "id": event_id,
        "summary": summary,
        "start": start,
        "end": end,
        "extendedProperties": {"private": {_PRIVATE_FINGERPRINT_KEY: fingerprint}},
    }
    for field in ("location", "description", "color_id", "transparency"):
        value = payload.get(field)
        if value is not None:
            api_name = "colorId" if field == "color_id" else field
            body[api_name] = value
    if body.get("transparency") not in {None, "transparent", "opaque"}:
        raise ValueError("Transparency must be 'transparent' or 'opaque'.")
    recurrence = payload.get("recurrence")
    if recurrence is not None:
        if not isinstance(recurrence, dict):
            raise ValueError("Recurrence must be an object of RRULE fields.")
        recurrence_items = [f"{key}={value}" for key, value in recurrence.items() if value is not None]
        body["recurrence"] = ["RRULE:" + ";".join(recurrence_items)]
    attendees = _validate_attendees(payload.get("attendees"))
    if attendees is not None:
        body["attendees"] = attendees
    reminders = _normalize_reminders(payload.get("reminders"))
    if reminders is not None:
        body["reminders"] = reminders
    if payload.get("conference_data"):
        body["conferenceData"] = {
            "createRequest": {
                "requestId": fingerprint[:32],
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
    return body


def _existing_fingerprint(event: dict[str, Any]) -> str:
    return str(
        event.get("extendedProperties", {})
        .get("private", {})
        .get(_PRIVATE_FINGERPRINT_KEY, "")
    )


class CalendarTool(BaseTool):
    @property
    def name(self) -> str:
        return "calendar"

    @property
    def display_name(self) -> str:
        return "📅 Google Calendar"

    @property
    def description(self) -> str:
        return (
            "View, search, create, and manage Google Calendar events. "
            "Use the bulk create operation whenever more than one event is requested."
        )

    @property
    def enabled_by_default(self) -> bool:
        return False

    @property
    def required_api_keys(self) -> dict[str, str]:
        return {}

    @property
    def config_schema(self) -> dict[str, dict]:
        return {
            "credentials_path": {
                "label": "credentials.json path",
                "type": "text",
                "default": DEFAULT_CREDENTIALS_PATH,
            },
            "selected_operations": {
                "label": "Allowed operations",
                "type": "multicheck",
                "default": DEFAULT_OPERATIONS,
                "options": ALL_OPERATIONS,
            },
        }

    @property
    def destructive_tool_names(self) -> set[str]:
        return {"move_calendar_event", "delete_calendar_event"}

    def _get_credentials_path(self) -> str:
        return self.get_config("credentials_path", DEFAULT_CREDENTIALS_PATH)

    def _get_token_path(self) -> str:
        return DEFAULT_TOKEN_PATH

    def has_credentials_file(self) -> bool:
        return os.path.isfile(self._get_credentials_path())

    def is_authenticated(self) -> bool:
        return os.path.isfile(self._get_token_path())

    def check_token_health(self) -> tuple[str, str]:
        return _check_google_token(self._get_token_path())

    def authenticate(self) -> None:
        """Run the interactive OAuth consent flow from Settings."""
        from langchain_google_community.calendar.utils import get_google_credentials

        get_google_credentials(
            scopes=CALENDAR_SCOPES,
            token_file=self._get_token_path(),
            client_secrets_file=self._get_credentials_path(),
        )

    def _build_api_resource(self) -> Any:
        """Build a fresh service and HTTP transport for one invocation only."""
        from langchain_google_community.calendar.utils import build_calendar_service

        credentials = _load_google_credentials(self._get_token_path())
        return build_calendar_service(credentials=credentials)

    def _get_selected_operations(self) -> list[str]:
        configured = self.get_config("selected_operations", DEFAULT_OPERATIONS)
        selected = [operation for operation in configured if operation in ALL_OPERATIONS]
        # Existing installations predate the bulk operation. Enabling single create
        # implicitly enables its safer multi-event companion.
        if "create_calendar_event" in selected and "create_calendar_events" not in selected:
            selected.append("create_calendar_events")
        return selected

    def _execute_request(
        self,
        operation: str,
        request: Callable[[Any], dict[str, Any]],
        *,
        mutate: bool = False,
        correlation: str = "",
    ) -> dict[str, Any]:
        lock_context = _mutation_lock(self._get_token_path()) if mutate else nullcontext()
        with lock_context:
            for attempt in range(1, _MAX_RETRY_ATTEMPTS + 1):
                started = time.perf_counter()
                try:
                    service = self._build_api_resource()
                    result = request(service)
                    logger.info(
                        "Calendar operation completed operation=%s correlation=%s attempt=%d duration_ms=%.1f",
                        operation,
                        correlation[:12],
                        attempt,
                        (time.perf_counter() - started) * 1000,
                    )
                    result.setdefault("ok", True)
                    result.setdefault("operation", operation)
                    result.setdefault("attempts", attempt)
                    return result
                except Exception as exc:
                    info = _classify_error(exc)
                    logger.warning(
                        "Calendar operation failed operation=%s correlation=%s attempt=%d category=%s "
                        "retryable=%s duration_ms=%.1f",
                        operation,
                        correlation[:12],
                        attempt,
                        info.category,
                        info.retryable,
                        (time.perf_counter() - started) * 1000,
                        exc_info=not info.retryable,
                    )
                    if not info.retryable or attempt >= _MAX_RETRY_ATTEMPTS:
                        return _failure_result(operation, info, attempts=attempt)
                    time.sleep(_retry_delay(attempt))
        return _failure_result(
            operation,
            _ErrorInfo("unexpected", False, "Calendar operation did not complete."),
            attempts=_MAX_RETRY_ATTEMPTS,
        )

    def _fetch_created_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        fingerprint: str,
    ) -> tuple[str, dict[str, Any] | None, _ErrorInfo | None]:
        try:
            service = self._build_api_resource()
            event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        except Exception as exc:
            info = _classify_error(exc)
            if info.category == "not_found":
                return ("missing", None, None)
            logger.warning(
                "Calendar create reconciliation failed correlation=%s category=%s retryable=%s",
                fingerprint[:12],
                info.category,
                info.retryable,
            )
            return ("unknown", None, info)
        if _existing_fingerprint(event) == fingerprint:
            return ("match", event, None)
        return (
            "collision",
            event,
            _ErrorInfo(
                "event_id_collision",
                False,
                "A different Calendar event already uses the generated event ID.",
            ),
        )

    def _create_event_unlocked(self, payload: dict[str, Any]) -> dict[str, Any]:
        operation = "create_calendar_event"
        fingerprint, event_id = _event_identity(payload)
        try:
            body = _prepare_event_body(payload, fingerprint, event_id)
        except Exception as exc:
            return _failure_result(operation, _classify_error(exc), attempts=0, event_id=event_id)
        calendar_id = str(payload.get("calendar_id") or "primary")

        state, existing, reconciliation_error = self._fetch_created_event(
            calendar_id=calendar_id,
            event_id=event_id,
            fingerprint=fingerprint,
        )
        if state == "match":
            return {
                "ok": True,
                "operation": operation,
                "status": "already_present",
                "event_id": event_id,
                "html_link": (existing or {}).get("htmlLink", ""),
                "attempts": 0,
            }
        if state == "collision":
            return _failure_result(
                operation,
                reconciliation_error
                or _ErrorInfo("event_id_collision", False, "Calendar event ID collision."),
                attempts=0,
                event_id=event_id,
            )

        last_info = _ErrorInfo("unexpected", False, "Calendar event was not created.")
        attempts_made = 0
        for attempt in range(1, _MAX_RETRY_ATTEMPTS + 1):
            attempts_made = attempt
            started = time.perf_counter()
            try:
                service = self._build_api_resource()
                request = service.events().insert(
                    calendarId=calendar_id,
                    body=body,
                    conferenceDataVersion=1 if payload.get("conference_data") else 0,
                )
                event = request.execute()
                logger.info(
                    "Calendar create completed correlation=%s attempt=%d duration_ms=%.1f",
                    fingerprint[:12],
                    attempt,
                    (time.perf_counter() - started) * 1000,
                )
                return {
                    "ok": True,
                    "operation": operation,
                    "status": "created",
                    "event_id": str(event.get("id") or event_id),
                    "html_link": event.get("htmlLink", ""),
                    "attempts": attempt,
                }
            except Exception as exc:
                last_info = _classify_error(exc)
                logger.warning(
                    "Calendar create failed correlation=%s attempt=%d category=%s retryable=%s "
                    "duration_ms=%.1f",
                    fingerprint[:12],
                    attempt,
                    last_info.category,
                    last_info.retryable,
                    (time.perf_counter() - started) * 1000,
                    exc_info=not last_info.retryable and last_info.category != "conflict",
                )
                if last_info.retryable or last_info.category == "conflict":
                    state, existing, reconciliation_error = self._fetch_created_event(
                        calendar_id=calendar_id,
                        event_id=event_id,
                        fingerprint=fingerprint,
                    )
                    if state == "match":
                        return {
                            "ok": True,
                            "operation": operation,
                            "status": (
                                "already_present"
                                if last_info.category == "conflict"
                                else "confirmed_after_transport_error"
                            ),
                            "event_id": event_id,
                            "html_link": (existing or {}).get("htmlLink", ""),
                            "attempts": attempt,
                        }
                    if state == "collision":
                        return _failure_result(
                            operation,
                            reconciliation_error or last_info,
                            attempts=attempt,
                            event_id=event_id,
                        )
                if not last_info.retryable or attempt >= _MAX_RETRY_ATTEMPTS:
                    break
                time.sleep(_retry_delay(attempt))

        # A final fresh-client read resolves writes that committed immediately
        # before the last response was lost.
        state, existing, reconciliation_error = self._fetch_created_event(
            calendar_id=calendar_id,
            event_id=event_id,
            fingerprint=fingerprint,
        )
        if state == "match":
            return {
                "ok": True,
                "operation": operation,
                "status": "confirmed_after_transport_error",
                "event_id": event_id,
                "html_link": (existing or {}).get("htmlLink", ""),
                "attempts": attempts_made,
            }
        return _failure_result(
            operation,
            reconciliation_error or last_info,
            attempts=attempts_made,
            event_id=event_id,
        )

    def _get_current_datetime(self, calendar_id: str = "primary") -> str:
        def request(service: Any) -> dict[str, Any]:
            calendar = service.calendars().get(calendarId=calendar_id).execute()
            timezone = str(calendar.get("timeZone") or "UTC")
            date_time = datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M:%S")
            return {
                "status": "ok",
                "calendar_id": calendar_id,
                "timezone": timezone,
                "datetime": date_time,
            }

        return _json_result(self._execute_request("get_current_datetime", request))

    def _search_events(
        self,
        min_datetime: str,
        max_datetime: str,
        max_results: int = 10,
        query: Optional[str] = None,
    ) -> str:
        def request(service: Any) -> dict[str, Any]:
            calendars = service.calendarList().list().execute().get("items", [])
            all_events: list[dict[str, Any]] = []
            for calendar in calendars:
                calendar_id = str(calendar.get("id") or "")
                if not calendar_id:
                    continue
                timezone = str(calendar.get("timeZone") or "UTC")
                tz = ZoneInfo(timezone)
                time_min = datetime.strptime(
                    min_datetime, "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=tz).isoformat()
                time_max = datetime.strptime(
                    max_datetime, "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=tz).isoformat()
                result = service.events().list(
                    calendarId=calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                    q=query,
                ).execute()
                for event in result.get("items", []):
                    all_events.append(
                        {
                            "event_id": event.get("id", ""),
                            "calendar_id": calendar_id,
                            "summary": event.get("summary", "(no title)"),
                            "start": event.get("start", {}).get("dateTime")
                            or event.get("start", {}).get("date", ""),
                            "end": event.get("end", {}).get("dateTime")
                            or event.get("end", {}).get("date", ""),
                            "location": event.get("location", ""),
                            "description": event.get("description", ""),
                            "calendar": calendar.get("summary", ""),
                        }
                    )
            return {"status": "ok", "events": all_events, "count": len(all_events)}

        return _json_result(self._execute_request("search_events", request))

    def _create_calendar_event(self, **kwargs: Any) -> str:
        payload = dict(kwargs)
        with _mutation_lock(self._get_token_path()):
            return _json_result(self._create_event_unlocked(payload))

    def _create_calendar_events(self, events: list[_CreateEventInput]) -> str:
        results = []
        with _mutation_lock(self._get_token_path()):
            for event in events:
                payload = event.model_dump() if isinstance(event, BaseModel) else dict(event)
                results.append(self._create_event_unlocked(payload))
        counts = {
            "created": sum(result.get("status") == "created" for result in results),
            "already_present": sum(
                result.get("status") == "already_present" for result in results
            ),
            "confirmed_after_transport_error": sum(
                result.get("status") == "confirmed_after_transport_error"
                for result in results
            ),
            "failed": sum(not result.get("ok") for result in results),
        }
        return _json_result(
            {
                "ok": counts["failed"] == 0,
                "operation": "create_calendar_events",
                "status": "completed" if counts["failed"] == 0 else "partial_failure",
                "counts": counts,
                "results": results,
            }
        )

    def _update_calendar_event(self, **kwargs: Any) -> str:
        payload = dict(kwargs)
        event_id = str(payload.pop("event_id"))
        calendar_id = str(payload.pop("calendar_id", "primary"))
        correlation = hashlib.sha256(
            json.dumps(
                {"event_id": event_id, "calendar_id": calendar_id, **payload},
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()

        def request(service: Any) -> dict[str, Any]:
            event = service.events().get(
                calendarId=calendar_id, eventId=event_id
            ).execute()
            body = copy.deepcopy(event)
            summary = payload.get("summary")
            if summary is not None:
                body["summary"] = summary
            start_value = payload.get("start_datetime")
            end_value = payload.get("end_datetime")
            if (start_value is None) != (end_value is None):
                raise ValueError("Start and end must be updated together.")
            if start_value is not None and end_value is not None:
                timezone = payload.get("timezone") or body.get("start", {}).get("timeZone") or "UTC"
                body["start"], body["end"] = _parse_event_times(
                    str(start_value), str(end_value), str(timezone)
                )
            if payload.get("recurrence") is not None:
                recurrence = payload["recurrence"]
                body["recurrence"] = [
                    "RRULE:"
                    + ";".join(
                        f"{key}={value}"
                        for key, value in recurrence.items()
                        if value is not None
                    )
                ]
            for source, target in (
                ("location", "location"),
                ("description", "description"),
                ("color_id", "colorId"),
                ("transparency", "transparency"),
            ):
                if payload.get(source) is not None:
                    body[target] = payload[source]
            if payload.get("attendees") is not None:
                body["attendees"] = _validate_attendees(payload["attendees"])
            if payload.get("reminders") is not None:
                body["reminders"] = _normalize_reminders(payload["reminders"])
            if payload.get("conference_data") is True:
                body["conferenceData"] = {
                    "createRequest": {
                        "requestId": correlation[:32],
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                }
            elif payload.get("conference_data") is False:
                body.pop("conferenceData", None)
            request_kwargs: dict[str, Any] = {
                "calendarId": calendar_id,
                "eventId": event_id,
                "body": body,
                "conferenceDataVersion": 1 if payload.get("conference_data") else 0,
            }
            if payload.get("send_updates") is not None:
                request_kwargs["sendUpdates"] = payload["send_updates"]
            updated = service.events().update(**request_kwargs).execute()
            return {
                "status": "updated",
                "event_id": str(updated.get("id") or event_id),
                "html_link": updated.get("htmlLink", ""),
            }

        return _json_result(
            self._execute_request(
                "update_calendar_event",
                request,
                mutate=True,
                correlation=correlation,
            )
        )

    def _move_calendar_event(
        self,
        event_id: str,
        origin_calendar_id: str,
        destination_calendar_id: str,
        send_updates: Optional[str] = None,
    ) -> str:
        correlation = hashlib.sha256(
            f"{origin_calendar_id}:{destination_calendar_id}:{event_id}".encode("utf-8")
        ).hexdigest()

        def request(service: Any) -> dict[str, Any]:
            request_kwargs: dict[str, Any] = {
                "eventId": event_id,
                "calendarId": origin_calendar_id,
                "destination": destination_calendar_id,
            }
            if send_updates is not None:
                request_kwargs["sendUpdates"] = send_updates
            moved = service.events().move(**request_kwargs).execute()
            return {
                "status": "moved",
                "event_id": str(moved.get("id") or event_id),
                "html_link": moved.get("htmlLink", ""),
            }

        return _json_result(
            self._execute_request(
                "move_calendar_event",
                request,
                mutate=True,
                correlation=correlation,
            )
        )

    def _delete_calendar_event(
        self,
        event_id: str,
        calendar_id: str = "primary",
        send_updates: Optional[str] = None,
    ) -> str:
        correlation = hashlib.sha256(f"{calendar_id}:{event_id}".encode("utf-8")).hexdigest()

        def request(service: Any) -> dict[str, Any]:
            request_kwargs: dict[str, Any] = {
                "eventId": event_id,
                "calendarId": calendar_id,
            }
            if send_updates is not None:
                request_kwargs["sendUpdates"] = send_updates
            service.events().delete(**request_kwargs).execute()
            return {"status": "deleted", "event_id": event_id}

        return _json_result(
            self._execute_request(
                "delete_calendar_event",
                request,
                mutate=True,
                correlation=correlation,
            )
        )

    def as_langchain_tools(self) -> list[StructuredTool]:
        """Return Calendar tools that create an independent service per invocation."""
        if not self.has_credentials_file() or not self.is_authenticated():
            return []

        tool_definitions = {
            "get_current_datetime": (
                self._get_current_datetime,
                _CurrentDatetimeInput,
                "Get the current date, time, and timezone for a Calendar.",
            ),
            "search_events": (
                self._search_events,
                _SearchEventsInput,
                "Search all accessible Google Calendars in a date/time range.",
            ),
            "create_calendar_event": (
                self._create_calendar_event,
                _CreateEventInput,
                "Create one duplicate-safe Google Calendar event. Use create_calendar_events for two or more events.",
            ),
            "create_calendar_events": (
                self._create_calendar_events,
                _BulkCreateEventsInput,
                "Create multiple Google Calendar events safely in one ordered, duplicate-safe operation.",
            ),
            "update_calendar_event": (
                self._update_calendar_event,
                _UpdateEventInput,
                "Update an existing Google Calendar event.",
            ),
            "move_calendar_event": (
                self._move_calendar_event,
                _MoveEventInput,
                "Move an event between Google Calendars.",
            ),
            "delete_calendar_event": (
                self._delete_calendar_event,
                _DeleteEventInput,
                "Delete a Google Calendar event.",
            ),
        }
        tools = []
        for operation in self._get_selected_operations():
            definition = tool_definitions.get(operation)
            if definition is None:
                continue
            function, schema, description = definition
            tools.append(
                StructuredTool.from_function(
                    func=function,
                    name=operation,
                    description=description,
                    args_schema=schema,
                )
            )
        return tools

    def execute(self, query: str) -> str:
        return "Use the individual Calendar operations instead."


registry.register(CalendarTool())
