from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from row_bot.tools import calendar_tool
from tests.helpers.fake_google_calendar import FakeCalendarBackend


pytestmark = pytest.mark.subsystem


def _event_args(index: int = 1, *, summary: str | None = None) -> dict[str, Any]:
    return {
        "summary": summary or f"World Cup Match {index}",
        "start_datetime": f"2026-07-{10 + index:02d} 20:00:00",
        "end_datetime": f"2026-07-{10 + index:02d} 22:30:00",
        "timezone": "Europe/London",
        "calendar_id": "primary",
        "location": "Test Stadium",
        "description": "Deterministic Calendar test event",
        "reminders": True,
        "transparency": "opaque",
    }


def _fake_tool(monkeypatch: pytest.MonkeyPatch, backend: FakeCalendarBackend) -> calendar_tool.CalendarTool:
    tool = calendar_tool.CalendarTool()
    monkeypatch.setattr(tool, "_build_api_resource", backend.service_factory)
    return tool


def test_calendar_tool_exposes_native_bulk_operation_and_gates_every_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = calendar_tool.CalendarTool()
    monkeypatch.setattr(tool, "has_credentials_file", lambda: True)
    monkeypatch.setattr(tool, "is_authenticated", lambda: True)
    monkeypatch.setattr(tool, "_get_selected_operations", lambda: list(calendar_tool.ALL_OPERATIONS))

    subtools = {subtool.name: subtool for subtool in tool.as_langchain_tools()}

    assert set(subtools) == set(calendar_tool.ALL_OPERATIONS)
    assert "multiple" in subtools["create_calendar_events"].description.lower()
    # Every change asks first (W21); only the reads don't.
    assert tool.destructive_tool_names == set(calendar_tool.ALL_OPERATIONS) - {
        "get_current_datetime",
        "search_events",
    }


def test_request_scoped_services_are_unique_for_concurrent_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeCalendarBackend()
    tool = _fake_tool(monkeypatch, backend)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _index: tool._get_current_datetime(), range(4)))

    assert all(json.loads(result)["ok"] is True for result in results)
    assert len(backend.services) == 4
    assert len({id(service) for service in backend.services}) == 4


def test_same_turn_toolnode_fanout_serializes_eight_calendar_creates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeCalendarBackend()
    backend.insert_gate = threading.Barrier(2)
    tool = _fake_tool(monkeypatch, backend)
    monkeypatch.setattr(tool, "has_credentials_file", lambda: True)
    monkeypatch.setattr(tool, "is_authenticated", lambda: True)
    monkeypatch.setattr(tool, "_get_selected_operations", lambda: ["create_calendar_event"])
    create_tool = next(
        item for item in tool.as_langchain_tools() if item.name == "create_calendar_event"
    )
    calls = [
        {
            "name": "create_calendar_event",
            "args": _event_args(index),
            "id": f"calendar-call-{index}",
            "type": "tool_call",
        }
        for index in range(1, 9)
    ]

    result = ToolNode([create_tool]).invoke(
        {"messages": [AIMessage(content="", tool_calls=calls)]},
        {"configurable": {"__pregel_runtime": Runtime()}},
    )

    messages = result["messages"]
    assert len(messages) == 8
    assert all(json.loads(message.content)["ok"] is True for message in messages)
    assert backend.max_active_inserts == 1
    assert len(backend.events) == 8
    assert len({id(service) for service in backend.services}) == len(backend.services)


def test_timeout_after_backend_commit_is_reconciled_without_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeCalendarBackend()
    backend.insert_behaviors["Ambiguous Match"] = ["timeout_after_commit"]
    tool = _fake_tool(monkeypatch, backend)
    monkeypatch.setattr(calendar_tool.time, "sleep", lambda _seconds: pytest.fail("unexpected retry"))

    first = json.loads(
        tool._create_calendar_event(**_event_args(summary="Ambiguous Match"))
    )
    second = json.loads(
        tool._create_calendar_event(**_event_args(summary="Ambiguous Match"))
    )

    assert first["status"] == "confirmed_after_transport_error"
    assert second["status"] == "already_present"
    assert first["event_id"] == second["event_id"]
    assert backend.insert_calls == 1
    assert len(backend.events) == 1


def test_transient_ssl_and_backend_failures_retry_with_fresh_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeCalendarBackend()
    backend.insert_behaviors["Retry Match"] = ["ssl_before_commit", 500, "success"]
    tool = _fake_tool(monkeypatch, backend)
    delays: list[float] = []
    monkeypatch.setattr(calendar_tool.random, "random", lambda: 0.0)
    monkeypatch.setattr(calendar_tool.time, "sleep", delays.append)

    result = json.loads(tool._create_calendar_event(**_event_args(summary="Retry Match")))

    assert result["status"] == "created"
    assert result["attempts"] == 3
    assert backend.insert_calls == 3
    assert delays == [1.0, 2.0]
    assert len({id(service) for service in backend.services}) == len(backend.services)


@pytest.mark.parametrize(
    ("behavior", "category"),
    [
        ("timeout_before_commit", "timeout"),
        (429, "rate_limit"),
    ],
)
def test_other_transient_failures_retry_once_and_succeed(
    monkeypatch: pytest.MonkeyPatch,
    behavior: str | int,
    category: str,
) -> None:
    backend = FakeCalendarBackend()
    backend.insert_behaviors["Transient Match"] = [behavior, "success"]
    tool = _fake_tool(monkeypatch, backend)
    delays: list[float] = []
    monkeypatch.setattr(calendar_tool.random, "random", lambda: 0.0)
    monkeypatch.setattr(calendar_tool.time, "sleep", delays.append)

    result = json.loads(
        tool._create_calendar_event(**_event_args(summary="Transient Match"))
    )

    assert result["status"] == "created", category
    assert result["attempts"] == 2
    assert backend.insert_calls == 2
    assert delays == [1.0]


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (400, "invalid_request"),
        (401, "authorization"),
        (403, "permission"),
    ],
)
def test_permanent_calendar_error_is_structured_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    category: str,
) -> None:
    backend = FakeCalendarBackend()
    backend.insert_behaviors["Invalid Match"] = [status]
    tool = _fake_tool(monkeypatch, backend)
    monkeypatch.setattr(calendar_tool.time, "sleep", lambda _seconds: pytest.fail("unexpected retry"))

    raw_result = tool._create_calendar_event(**_event_args(summary="Invalid Match"))
    result = json.loads(raw_result)

    assert result["ok"] is False
    assert result["error"]["category"] == category
    assert result["attempts"] == 1
    assert backend.insert_calls == 1
    assert "Tool error" not in raw_result


def test_bulk_create_preserves_order_and_reports_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeCalendarBackend()
    backend.insert_behaviors["Bad Match"] = [400]
    tool = _fake_tool(monkeypatch, backend)
    events = [
        calendar_tool._CreateEventInput(**_event_args(1, summary="First Match")),
        calendar_tool._CreateEventInput(**_event_args(2, summary="Bad Match")),
        calendar_tool._CreateEventInput(**_event_args(3, summary="Third Match")),
    ]

    result = json.loads(tool._create_calendar_events(events))

    assert result["ok"] is False
    assert result["status"] == "partial_failure"
    assert result["counts"] == {
        "already_present": 0,
        "confirmed_after_transport_error": 0,
        "created": 2,
        "failed": 1,
    }
    assert [item["status"] for item in result["results"]] == [
        "created",
        "failed",
        "created",
    ]


def test_search_update_move_and_delete_use_request_scoped_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeCalendarBackend()
    tool = _fake_tool(monkeypatch, backend)
    created = json.loads(tool._create_calendar_event(**_event_args(summary="Managed Match")))
    event_id = created["event_id"]
    services_after_create = len(backend.services)

    searched = json.loads(
        tool._search_events("2026-07-01 00:00:00", "2026-07-31 23:59:59")
    )
    updated = json.loads(
        tool._update_calendar_event(
            event_id=event_id,
            calendar_id="primary",
            summary="Updated Match",
        )
    )
    moved = json.loads(
        tool._move_calendar_event(event_id, "primary", "secondary")
    )
    deleted = json.loads(tool._delete_calendar_event(event_id, "secondary"))

    assert searched["count"] == 1
    assert updated["status"] == "updated"
    assert moved["status"] == "moved"
    assert deleted["status"] == "deleted"
    assert len(backend.services) == services_after_create + 4
    assert len({id(service) for service in backend.services}) == len(backend.services)
