"""Each built-in watcher speaks only on change, and re-checks its own evidence before a moment is shown."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from row_bot.proactive.changes import SignInWatcher, WorkflowWatcher, workflow_finished
from row_bot.proactive.dates import DatesWatcher, next_date
from row_bot.proactive.follow_ups import ChatsWatcher, find_promise
from row_bot.proactive.meetings import CalendarWatcher
from row_bot.proactive.sources import DatedMemory, GmailSource, _parse_time
from tests.subsystem.proactive.conftest import AT_DESK, at, start_day
from tests.subsystem.proactive.fakes import FakeCalendar, FakeChats, meeting

pytestmark = pytest.mark.subsystem

MONDAY_NOON = at("2026-11-16 12:00")


@pytest.mark.parametrize("text,what,due", [
    ("I'll send the deck to Ana on Friday.", "send the deck to Ana", "2026-11-20 09:00"),
    ("Sure — I’ll call the landlord tomorrow", "call the landlord", "2026-11-17 09:00"),
    ("I will review Ben's PR today.", "review Ben's PR", "2026-11-16 16:00"),
    ("Remind me to renew the passport next week.", "renew the passport", "2026-11-23 09:00"),
    ("remind me about the dentist", "the dentist", "2026-11-17 09:00"),
])
def test_promises_say_what_and_when(text, what, due):
    assert find_promise(text, MONDAY_NOON) == (what, at(due))


@pytest.mark.parametrize("text", [
    "Should I send the deck to Ana on Friday?", "Can you remind me to call Ana tomorrow?",
    "Will I finish it by Friday", "I'll send it sometime.", "What if I'll call tomorrow?",
])
def test_questions_and_promises_without_a_when_are_not_promises(text):
    assert find_promise(text, MONDAY_NOON) is None


def test_the_same_ask_in_three_chats_is_offered_once_and_never_again(proactive):
    chats = FakeChats()
    proactive.watchers.register(ChatsWatcher(chats))
    start_day(proactive)
    asks = ["Summarise the top AI news headlines for me", "summarise today's top AI news headlines please",
            "Can you summarise the top AI news headlines?"]
    for minute, (chat, ask) in enumerate(zip(("a", "b", "a"), asks)):
        chats.say(f"chat-{chat}", ask)
        proactive.engine.tick(at(f"2026-11-16 09:{minute * 11:02d}"), desktop=AT_DESK)
    assert proactive.store.list_moments() == [], "the same chat twice is not three chats"

    chats.say("chat-c", asks[2])
    proactive.engine.tick(at("2026-11-16 10:00"), desktop=AT_DESK)
    [offer] = proactive.store.list_moments()
    assert offer["kind"] == "workflow_offer" and [action["id"] for action in offer["actions"]] == ["make_workflow"]
    proactive.feedback.dismiss(offer["id"], now=at("2026-11-16 10:05"))

    chats.say("chat-d", asks[0])
    proactive.engine.tick(at("2026-11-16 10:20"), desktop=AT_DESK)
    assert len(proactive.store.list_moments()) == 1, "declined: never offered again"


def test_a_promise_in_a_deleted_chat_is_stale(proactive):
    chats = FakeChats()
    watcher = ChatsWatcher(chats)
    proactive.watchers.register(watcher)
    start_day(proactive)
    chats.say("chat-1", "I'll send the invoice on Friday.")
    proactive.engine.tick(at("2026-11-16 09:00"), desktop=AT_DESK)
    [moment] = proactive.store.list_moments()

    assert watcher.recheck(moment, at("2026-11-20 09:00")) is True
    chats.deleted.add("chat-1")
    assert watcher.recheck(moment, at("2026-11-20 09:00")) is False


def test_the_calendars_first_pass_only_remembers_and_a_meeting_alone_needs_no_prep(proactive):
    calendar = FakeCalendar()
    calendar.add(meeting("ev1", at("2026-11-16 15:00")))
    calendar.add(meeting("solo", at("2026-11-16 09:10"), title="Focus time", people=()))
    watcher = CalendarWatcher([calendar])

    found, cursor = watcher.scan({}, at("2026-11-16 09:00"))
    assert found == [] and set(cursor["events"]) == {"ev1", "solo"}

    calendar.items.pop("ev1")
    found, _cursor = watcher.scan(cursor, at("2026-11-16 09:05"))
    assert [candidate.title for candidate in found] == ["“Design review” at 15:00 was cancelled"]


def _memory(*items: DatedMemory, value: str | None = None):
    return SimpleNamespace(dated=lambda: list(items), value=lambda entity_id, field: value)


@pytest.mark.parametrize("value,field,expected", [
    ("1965-11-20", "birthday", date(2026, 11, 20)), ("11-10", "birthday", date(2027, 11, 10)),
    ("2026-11-18", "renewal", date(2026, 11, 18)), ("2026-11-01", "renewal", None),
    ("next spring", "renewal", None), ("1980-02-29", "anniversary", date(2027, 2, 28)),
])
def test_dates_come_round_yearly_or_happen_once(value, field, expected):
    assert next_date(value, field, date(2026, 11, 16)) == expected


def test_a_birthday_a_week_ahead_becomes_one_moment_and_a_changed_date_is_stale(proactive):
    mum = DatedMemory("e1", "Mum", "birthday", "1955-11-20")
    far = DatedMemory("e2", "Ben", "birthday", "1990-12-25")
    watcher = DatesWatcher(_memory(mum, far, value="1955-11-20"))

    found, _cursor = watcher.scan({}, MONDAY_NOON)
    assert [candidate.title for candidate in found] == ["Mum's birthday is on Friday 20 November"]

    moment = {"evidence": [found[0].evidence[0].view()], "dedupe_key": found[0].key}
    assert watcher.recheck(moment, MONDAY_NOON) is True
    assert DatesWatcher(_memory(mum, value="1955-11-21")).recheck(moment, MONDAY_NOON) is False


def test_a_failed_workflow_waits_two_hours_and_is_stale_once_it_completes(proactive, monkeypatch):
    task = {"id": "task-1", "name": "Weekly report"}
    workflow_finished(task, "run-ok", "completed", "", now=at("2026-11-16 08:00"))
    workflow_finished(task, "run-1", "failed", "The site timed out.", now=at("2026-11-16 08:00"))
    completed = {"value": False}
    runs = SimpleNamespace(completed_since=lambda task_id, since: completed["value"])
    watcher = WorkflowWatcher(runs)

    found, _cursor = watcher.scan({}, at("2026-11-16 08:01"))

    [candidate] = found
    assert candidate.title == "“Weekly report” didn't finish" and candidate.due_at == at("2026-11-16 10:00")
    assert watcher.scan({}, at("2026-11-16 08:02"))[0] == [], "each run once"
    moment = {"evidence": [candidate.evidence[0].view()], "created_at": at("2026-11-16 08:01")}
    assert watcher.recheck(moment, at("2026-11-16 10:00")) is True
    completed["value"] = True
    assert watcher.recheck(moment, at("2026-11-16 10:00")) is False


def test_runs_arent_queued_while_proactive_is_off(proactive):
    proactive.settings.save({"level": "off"})

    workflow_finished({"id": "t", "name": "x"}, "run-1", "failed", "", now=at("2026-11-16 08:00"))

    assert WorkflowWatcher(SimpleNamespace()).scan({}, at("2026-11-16 08:01"))[0] == []


def test_a_sign_in_that_turns_to_an_error_is_one_change(proactive):
    status = {"value": "ok"}
    fix = {"kind": "reconnect_account"}

    def checks():
        return [{"id": "google", "name": "Google", "status": status["value"], "detail": "Token revoked", "fix": fix},
                {"id": "disk", "name": "Disk space", "status": "error", "detail": "Low", "fix": {"kind": "open"}}]

    watcher = SignInWatcher(SimpleNamespace(checks=checks))
    found, cursor = watcher.scan({}, MONDAY_NOON)
    assert found == [], "the first pass only remembers"
    status["value"] = "error"
    found, cursor = watcher.scan(cursor, MONDAY_NOON + 900)
    assert [candidate.title for candidate in found] == ["Google needs you to sign in again"]
    assert watcher.scan(cursor, MONDAY_NOON + 1800)[0] == [], "still broken is not news"


class _GmailService:
    """Gmail's API shape, with one thread the person wrote last and one someone else wrote last."""

    def __init__(self, threads: dict[str, list[dict]]) -> None:
        self.threads_by_id = threads

    def users(self):
        return self

    def getProfile(self, userId):  # noqa: N802, N803
        return SimpleNamespace(execute=lambda: {"emailAddress": "me@example.com"})

    def threads(self):
        return self

    def list(self, userId, q, maxResults):  # noqa: N803
        return SimpleNamespace(execute=lambda: {"threads": [{"id": key} for key in self.threads_by_id]})

    def get(self, userId, id, **_values):  # noqa: A002, N803
        return SimpleNamespace(execute=lambda: {"id": id, "messages": self.threads_by_id[id]})


def _message(sender: str, at_ms: int, subject: str = "The deck") -> dict:
    return {"id": f"m{at_ms}", "internalDate": str(at_ms), "snippet": "See attached",
            "payload": {"headers": [{"name": "From", "value": sender}, {"name": "To", "value": "Ana <ana@example.com>"},
                                    {"name": "Subject", "value": subject}]}}


def test_gmail_finds_threads_the_person_wrote_last(monkeypatch):
    sent_at = int(at("2026-11-12 09:00") * 1000)
    service = _GmailService({"t-mine": [_message("Ana <ana@example.com>", sent_at - 1000),
                                        _message("Me <me@example.com>", sent_at)],
                             "t-theirs": [_message("Me <me@example.com>", sent_at - 1000),
                                          _message("Ana <ana@example.com>", sent_at)]})
    source = GmailSource()
    monkeypatch.setattr(source, "_service", lambda: service)

    [found] = source.unanswered_sent(at("2026-11-06 09:00"), at("2026-11-13 09:00"))
    assert (found.thread_id, found.other, found.subject, found.last_message_id) == (
        "t-mine", "ana@example.com", "The deck", f"m{sent_at}")
    [waiting] = source.waiting_on_me(at("2026-11-06 09:00"), at("2026-11-13 09:00"))
    assert waiting.thread_id == "t-theirs"


@pytest.mark.parametrize("value", ["2026-11-16T10:00:00.0000000", "2026-11-16T10:00:00Z", "2026-11-16T11:00:00+01:00"])
def test_times_from_google_and_graph_read_the_same(value):
    assert _parse_time(value) == _parse_time("2026-11-16T10:00:00+00:00")
