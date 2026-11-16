"""W21: every Google Calendar change goes through the approval gate, and one that reaches other people is a send.

Turns run through the Agent's real graph and tool-call middleware (``AgentTurns``) with a scripted model; the
calendar tools are the real ones, offered as the agent offers them (``agent._gate_changes``), on a fake Google
Calendar service in memory. The reviewer is faked. Creating, changing, moving and deleting ask by the level
(the person under Ask, the reviewer first under Auto, nobody under Hands-off, never under Block); inviting
guests or emailing them an update is the person's own to approve even under Auto, as the card says; reads
never ask; and the activity log records who decided. Approval behaviour: these stay in the pull request lane.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from row_bot import activity_log, approval_review
from row_bot.approval_review import ReviewVerdict
from row_bot.tools import calendar_tool, registry
from tests.helpers.fake_google_calendar import FakeCalendarBackend
from tests.helpers.learning_turns import AgentTurns, ScriptedModel, asks, call, says

pytestmark = pytest.mark.subsystem

GUEST = "guest@example.test"
SYNC = {"summary": "Autumn launch sync", "start_datetime": "2026-11-16 16:55:00",
        "end_datetime": "2026-11-16 17:25:00", "timezone": "Europe/London"}
SEND_CARD = f"Create “Autumn launch sync” today 16:55–17:25 and send an invitation to {GUEST}?"
YOURS = "Only you can approve what your calendar sends to other people."
TEAM_SYNC = {"id": "ev1", "summary": "Team sync", "location": "Room 4",
             "start": {"dateTime": "2026-11-20T10:00:00+00:00", "timeZone": "Europe/London"},
             "end": {"dateTime": "2026-11-20T10:30:00+00:00", "timeZone": "Europe/London"},
             "attendees": [{"email": "me@example.test", "self": True}, {"email": "amy@example.test"}]}


@pytest.fixture
def google(tmp_path, monkeypatch):
    """Signed in to Google Calendar, with the service faked in memory and today Mon 16 Nov 2026."""
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    backend = FakeCalendarBackend()
    tool = registry.get_tool("calendar")
    monkeypatch.setattr(tool, "_build_api_resource", backend.service_factory)
    monkeypatch.setattr(tool, "has_credentials_file", lambda: True)
    monkeypatch.setattr(tool, "is_authenticated", lambda: True)
    monkeypatch.setattr(tool, "_get_selected_operations", lambda: list(calendar_tool.ALL_OPERATIONS))
    monkeypatch.setattr(calendar_tool, "_today", lambda zone: date(2026, 11, 16))
    yield backend
    activity_log.flush()


@pytest.fixture
def reviewer(monkeypatch):
    """The Auto reviewer, faked to approve: each action it saw."""
    seen: list[dict] = []

    def review(payload, *, risks=(), **_kwargs):
        seen.append(dict(payload))
        return ReviewVerdict("approve", "You asked for this event.")

    monkeypatch.setattr(approval_review, "review", review)
    return seen


def _offered(level: str) -> list:
    """The calendar tools as the agent offers them at this level."""
    import row_bot.agent as agent

    tool = registry.get_tool("calendar")
    return agent._gate_changes(tool.as_langchain_tools(), tool.destructive_tool_names, level)


def _turns(monkeypatch, *replies, level: str = "approve", offered_at: str | None = None) -> AgentTurns:
    model = ScriptedModel(responses=list(replies), seen=[])
    return AgentTurns(monkeypatch, model, _offered(offered_at or level), approval_mode=level)


def _card(events) -> dict:
    [interrupts] = [payload for kind, payload in events if kind == "interrupt"]
    [card] = interrupts
    return card


def _row(call_id: str) -> dict:
    """The activity log's row for one tool call."""
    assert activity_log.flush()
    rows, _next = activity_log.query(limit=50)
    [row] = [row for row in rows if row["call_id"] == call_id]
    return row


def test_reads_never_ask_and_block_leaves_every_change_out(google, monkeypatch):
    assert {tool.name for tool in _offered("block")} == {"get_current_datetime", "search_events"}

    turns = _turns(monkeypatch, asks(call("search_events", "r1", min_datetime="2026-11-16 00:00:00",
                                          max_datetime="2026-11-16 23:59:59")), says("Nothing today."))
    events = turns.send("What's on today?")

    assert not [payload for kind, payload in events if kind == "interrupt"]
    row = _row("r1")
    assert (row["tool"], row["outcome"], row["decided_by"]) == ("search_events", "ran", "none")


def test_a_create_without_guests_asks_under_ask_and_the_row_says_you_decided(google, monkeypatch):
    focus = {**SYNC, "summary": "Focus time"}
    turns = _turns(monkeypatch, asks(call("create_calendar_event", "c1", **focus)), says("Added."))
    events = turns.send("Block 16:55 today for focus time")

    card = _card(events)
    assert card["question"] == "Create “Focus time” today 16:55–17:25?"
    assert card["description"] == "Event: Focus time\nWhen: today 16:55–17:25 (Europe/London)"
    assert "risk_class" not in card and google.events == {}
    waiting = _row("c1")
    assert (waiting["outcome"], waiting["status"]) == ("asked", "pending")

    turns.answer(True, events)
    row = _row("c1")
    assert (row["outcome"], row["decided_by"], row["level"]) == ("approved_by_you", "person", "approve")
    assert row["title"] == "Create an event", "the log names what it did, never 'Check the calendar' (R1)"
    assert [event["summary"] for event in google.events.values()] == ["Focus time"]


def test_a_create_with_a_guest_asks_with_the_send_card_and_your_no_sends_nothing(google, monkeypatch):
    turns = _turns(monkeypatch, asks(call("create_calendar_event", "c1", **SYNC, attendees=[GUEST])),
                   says("I didn't."))
    events = turns.send(f"Set up the autumn launch sync at 16:55 with {GUEST}")

    card = _card(events)
    assert card["question"] == SEND_CARD
    assert card["description"].splitlines() == ["Event: Autumn launch sync", "When: today 16:55–17:25 (Europe/London)",
                                                f"Guests: {GUEST}"]
    assert card["risk_class"] == "high"

    turns.answer(False, events)
    row = _row("c1")
    assert (row["outcome"], row["decided_by"], row["risk"]) == ("denied", "person", "high")
    assert google.events == {} and google.insert_calls == 0


def test_under_auto_an_invitation_goes_to_you_and_a_plain_event_to_the_reviewer(google, monkeypatch, reviewer):
    turns = _turns(monkeypatch, asks(call("create_calendar_event", "c1", **SYNC, attendees=[GUEST])),
                   says("Invited."), level="auto")
    events = turns.send(f"Set up the autumn launch sync at 16:55 with {GUEST}")

    card = _card(events)
    assert reviewer == []  # Never approved as routine: the reviewer isn't asked.
    assert card["question"] == SEND_CARD and card["review"] == {"decision": "ask", "reason": YOURS}
    assert _row("c1")["outcome"] == "asked" and google.events == {}

    turns.answer(True, events)
    row = _row("c1")
    assert (row["outcome"], row["decided_by"], row["level"], row["reason"]) == (
        "approved_by_you", "person", "auto", YOURS)
    [event] = google.events.values()
    assert event["attendees"] == [{"email": GUEST}]

    plain = _turns(monkeypatch, asks(call("create_calendar_event", "c2", **{**SYNC, "summary": "Focus time"})),
                   says("Added."), level="auto")
    assert not [payload for kind, payload in plain.send("Block 16:55 for focus") if kind == "interrupt"]
    assert [seen["tool"] for seen in reviewer] == ["create_calendar_event"]
    row = _row("c2")
    assert (row["outcome"], row["decided_by"]) == ("approved_by_reviewer", "reviewer")


def test_hands_off_runs_and_block_refuses(google, monkeypatch):
    turns = _turns(monkeypatch, asks(call("create_calendar_event", "c1", **SYNC, attendees=[GUEST])),
                   says("Invited."), level="full")
    events = turns.send("Set up the sync")

    assert not [payload for kind, payload in events if kind == "interrupt"]
    row = _row("c1")
    assert (row["outcome"], row["decided_by"]) == ("hands_off", "hands_off")
    assert len(google.events) == 1

    # A graph built before the level became Block still refuses: nothing is created.
    blocked = _turns(monkeypatch, asks(call("create_calendar_event", "c2", **{**SYNC, "summary": "Other"})),
                     says("Skipped."), level="block", offered_at="approve")
    blocked.send("Set up another")
    assert (_row("c2")["outcome"], _row("c2")["decided_by"]) == ("blocked", "block")
    assert len(google.events) == 1


@pytest.mark.parametrize("level", ["approve", "auto"])
def test_an_update_that_emails_guests_asks_with_what_it_will_send(google, monkeypatch, reviewer, level):
    google.events[("primary", "ev1")] = dict(TEAM_SYNC)
    change = {"event_id": "ev1", "start_datetime": "2026-11-20 11:00:00", "end_datetime": "2026-11-20 11:30:00",
              "send_updates": "all"}
    turns = _turns(monkeypatch, asks(call("update_calendar_event", "u1", **change)), says("Moved."), level=level)
    events = turns.send("Move the team sync to 11 and tell Amy")

    card = _card(events)
    assert reviewer == []
    assert card["question"] == "Change “Team sync” and email the change to amy@example.test?"
    assert card["description"].splitlines() == [
        "Event: Team sync", "New time: Fri 20 Nov 2026 11:00–11:30 (Europe/London)", "Where: Room 4",
        "Guests: amy@example.test", "Emails guests: Yes"]
    assert google.calls == []

    turns.answer(True, events)
    assert [(name, kwargs.get("sendUpdates")) for name, kwargs in google.calls] == [("update", "all")]
    row = _row("u1")
    assert (row["outcome"], row["decided_by"]) == ("approved_by_you", "person")


def test_cards_for_changes_name_the_event_and_who_hears_of_it(google):
    google.events[("primary", "ev1")] = dict(TEAM_SYNC)

    assert calendar_tool.approval_card("update_calendar_event", {"event_id": "ev1", "location": "Room 5"})[::2] == (
        "Change “Team sync”?", False)  # Google tells no one unless asked to.
    assert calendar_tool.approval_card(
        "update_calendar_event", {"event_id": "ev1", "attendees": ["amy@example.test", "bo@example.test"]})[::2] == (
        "Change “Team sync” and invite bo@example.test?", True)
    question, card, tells = calendar_tool.approval_card("delete_calendar_event",
                                                        {"event_id": "ev1", "send_updates": "all"})
    assert (question, tells) == (
        "Delete “Team sync” Fri 20 Nov 2026 10:00–10:30 and email amy@example.test that it's cancelled?", True)
    assert "Emails guests: Yes" in card
    assert calendar_tool.approval_card("delete_calendar_event", {"event_id": "ev1"})[::2] == (
        "Delete “Team sync” Fri 20 Nov 2026 10:00–10:30?", False)
    assert calendar_tool.approval_card("move_calendar_event", {
        "event_id": "ev1", "origin_calendar_id": "primary", "destination_calendar_id": "work",
        "send_updates": "externalOnly"})[::2] == ("Move “Team sync” to the calendar work and tell amy@example.test?",
                                                  True)
    # An event that can't be read may have guests: emailing them is still the person's to approve.
    assert calendar_tool.approval_card("delete_calendar_event", {"event_id": "gone", "send_updates": "all"})[::2] == (
        "Delete this event and email its guests that it's cancelled?", True)
    question, card, tells = calendar_tool.approval_card("create_calendar_events", {"events": [
        calendar_tool._CreateEventInput(**SYNC, attendees=[GUEST]),
        calendar_tool._CreateEventInput(**{**SYNC, "summary": "Retro", "start_datetime": "2026-11-17 09:00:00",
                                           "end_datetime": "2026-11-17 09:30:00"})]})
    assert (question, tells) == (f"Create 2 events and send invitations to {GUEST}?", True)
    assert card.splitlines() == [f"Event 1: “Autumn launch sync” today 16:55–17:25 · guests {GUEST}",
                                 "Event 2: “Retro” tomorrow 09:00–09:30"]
    assert json.loads(registry.get_tool("calendar")._get_current_datetime())["ok"] is True  # Reads still run.


def test_an_apps_calendar_tool_that_invites_guests_is_yours_to_approve_under_auto(monkeypatch, reviewer):
    """An MCP app's event tool: its own arguments say it invites people, so the reviewer never approves it."""
    import row_bot.agent as agent
    from langchain_core.tools import StructuredTool

    def create_event(title: str, attendees: list[str] | None = None) -> str:
        return f"Created {title}."

    tool = StructuredTool.from_function(create_event, name="mcp_cal_create_event", description="Create an event.")
    agent._wrap_with_interrupt_gate(tool, always_ask=True)
    asked: list[dict] = []
    monkeypatch.setattr(agent, "interrupt", lambda payload: asked.append(payload) or True)
    token = agent._approval_mode_var.set("auto")
    try:
        invited = tool.invoke({"title": "Sync", "attendees": [GUEST]})
        plain = tool.invoke({"title": "Focus"})
    finally:
        agent._approval_mode_var.reset(token)

    assert invited.endswith("Created Sync.") and plain.endswith("Created Focus.")
    [card] = asked
    assert card["review"]["reason"] == YOURS and card["risk_class"] == "high"
    assert [seen["description"] for seen in reviewer] == ["Title: Focus"]  # Only the plain one went to the reviewer.
