"""Overnight preparation ("sleep-time compute"): Dream Cycle's last phase prepares the day ahead, read-only, and
what it prepares waits for its moment; old preparation threads are swept, never one a draft still links to."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

import pytest

from row_bot.proactive.follow_ups import MailWatcher
from row_bot.proactive.meetings import CalendarWatcher
from tests.contracts.client_platform.test_headless_lifecycle import platform  # noqa: F401
from tests.subsystem.proactive.conftest import AT_DESK, at, start_day
from tests.subsystem.proactive.fakes import FakeCalendar, FakeMail, meeting, thread

pytestmark = pytest.mark.subsystem

NIGHT = "2026-11-17 02:00"


class Preparer:
    """The agent a preparation runs: records how it was run and writes a short draft."""

    def __init__(self) -> None:
        self.configs: list[dict] = []
        self.prompts: list[str] = []

    def __call__(self, text: str, tools: list[str], config: dict) -> str:
        self.configs.append(config["configurable"])
        self.prompts.append(text)
        return f"Prepared #{len(self.prompts)}"


@pytest.fixture
def tomorrow(proactive, platform, monkeypatch):  # noqa: F811
    """A main model, a meeting with Ana on the day ahead and mail to Ben that comes due that day."""
    from row_bot import models

    monkeypatch.setattr(models, "get_current_model", lambda: "fixture/model")
    calendar = FakeCalendar(items={"e1": meeting("e1", at("2026-11-17 10:00"), title="Design review")})
    mail = FakeMail(sent=[thread("t1", at("2026-11-14 15:00"), other="ben@example.com", subject="The invoice")])
    proactive.calendars.append(calendar)
    proactive.mail.append(mail)
    start_day(proactive)  # Proactive Row-Bot has run on this computer.
    return calendar, mail


def _overnight(**options) -> int:
    from row_bot.proactive import overnight

    return overnight.prepare_tomorrow(at(NIGHT), **options)


def test_overnight_prepares_tomorrows_meeting_brief_and_follow_up_reply_read_only(proactive, tomorrow, platform):  # noqa: F811
    agent = Preparer()

    assert _overnight(agent=agent) == 2

    assert [config["runtime_surface"] for config in agent.configs] == ["proactive", "proactive"]
    assert {config["approval_mode"] for config in agent.configs} == {"block"}
    from row_bot import threads

    with closing(sqlite3.connect(threads.DB_PATH)) as connection:
        kinds = {row[0] for row in connection.execute(
            "SELECT COALESCE(thread_type, '') FROM thread_meta WHERE thread_id IN (?, ?)",
            tuple(config["thread_id"] for config in agent.configs))}
    assert kinds == {"proactive"}, "hidden preparation threads"
    listed = [item["id"] for item in platform.list_conversations(limit=50)["items"]]
    assert not {config["thread_id"] for config in agent.configs} & set(listed)
    waiting = proactive.store.drafts(waiting=True)
    assert sorted(draft["kind"] for draft in waiting) == ["brief", "reply"]
    assert proactive.store.drafts() == [], "nothing is shown until its moment comes"
    assert proactive.store.list_moments() == []
    assert _overnight(agent=agent) == 0, "prepared once"


def test_what_was_prepared_overnight_attaches_to_its_moment_when_it_comes(proactive, tomorrow, platform):  # noqa: F811
    calendar, mail = tomorrow
    _overnight(agent=Preparer())
    proactive.watchers.register(CalendarWatcher([calendar]))
    proactive.watchers.register(MailWatcher([mail]))

    proactive.engine.tick(at("2026-11-17 08:00"), desktop=AT_DESK)
    brief = next(moment for moment in proactive.store.list_moments() if moment["kind"] == "brief")
    assert "Prepared overnight:\n" in brief["body"]
    assert "- Design review at 10:00 (a brief, not sent)" in brief["body"]
    assert "- No reply from ben@example.com about “The invoice” (a drafted reply, not sent)" in brief["body"]
    proactive.engine.tick(at("2026-11-17 09:47"), desktop=AT_DESK)  # The meeting in 13 minutes.
    proactive.engine.tick(at("2026-11-17 15:30"), desktop=AT_DESK)  # Three days without Ben's reply.

    by_kind = {moment["kind"]: moment for moment in proactive.store.list_moments()}
    for kind, prepared in (("meeting_prep", "brief"), ("follow_up", "reply")):
        draft = proactive.store.get_draft(by_kind[kind]["prepared_ref"])
        assert draft["kind"] == prepared and draft["moment_id"] == by_kind[kind]["id"]
    assert proactive.prepared == [], "nothing prepared twice"
    assert len(proactive.store.drafts()) == 2


def test_overnight_does_nothing_at_off_or_once_the_person_is_back(proactive, tomorrow):
    agent = Preparer()

    assert _overnight(agent=agent, should_stop=lambda: True) == 0
    proactive.settings.save({"level": "off"})
    assert _overnight(agent=agent) == 0
    assert agent.prompts == [] and proactive.store.drafts(waiting=True) == []


def test_dream_cycle_prepares_tomorrow_last_and_stops_when_the_person_is_back(tmp_path, monkeypatch,
                                                                             reload_for_data_dir):
    from row_bot import models
    from row_bot.proactive import overnight
    from row_bot.runtime import executions

    monkeypatch.setattr(models, "_current_model", "model:ollama:fixture-model")
    reload_for_data_dir(tmp_path, "row_bot.knowledge_graph")
    [dream] = reload_for_data_dir(tmp_path, "row_bot.dream_cycle")
    registry = executions.GenerationRuntimeRegistry()
    monkeypatch.setattr(executions, "generation_registry", registry)
    calls: list = []
    monkeypatch.setattr(overnight, "prepare_tomorrow", lambda should_stop: calls.append(should_stop) or 2)
    monkeypatch.setattr(dream, "_is_idle", lambda: True)

    summary = dream.run_dream_cycle()

    assert summary["prepared"] == 2 and summary["summary"].endswith("; prepared 2 for tomorrow")
    [should_stop] = calls
    assert should_stop() is False
    monkeypatch.setattr(dream, "_is_idle", lambda: False)
    assert should_stop() is True, "the person came back"
    monkeypatch.setattr(dream, "_is_idle", lambda: True)
    turn = registry.register("conversation-a")
    assert should_stop() is True, "a turn started"
    registry.finish(turn)


def test_the_sweep_removes_old_preparation_threads_but_never_one_a_draft_links_to(tmp_path, reload_for_data_dir):
    from tests.subsystem.threads.test_thread_cleanup import _fresh_stack

    stack = _fresh_stack(tmp_path, reload_for_data_dir)
    threads, cleanup = stack["threads"], stack["cleanup"]
    [store] = reload_for_data_dir(stack["data_dir"], "row_bot.proactive.store")
    from row_bot.proactive.model import Candidate, Evidence

    now = datetime.now()
    old = (now - timedelta(days=10)).timestamp()
    ids = {name: threads.create_thread(f"Preparing: {name}", thread_type="proactive") for name in
           ("open", "settled", "empty", "recent")}
    with closing(sqlite3.connect(threads.DB_PATH)) as connection, connection:
        connection.execute("UPDATE thread_meta SET created_at = ?, updated_at = ? WHERE thread_id <> ?",
                           (datetime.fromtimestamp(old).isoformat(), datetime.fromtimestamp(old).isoformat(),
                            ids["recent"]))

    def moment(key: str) -> dict:
        found = Candidate(kind="meeting_prep", key=key, title=key, summary=key,
                          evidence=(Evidence("event", key, key, "google_calendar"),), expires_at=now.timestamp() + 86400)
        return store.add_moment(found, title=key, body="", now=old)

    still_open, settled = moment("open"), moment("settled")
    store.move(settled["id"], "dismissed", from_states=("pending",), now=old)
    store.add_draft(still_open["id"], "brief", "open", "Brief", thread_id=ids["open"], now=old)
    store.add_draft(settled["id"], "brief", "settled", "Brief", thread_id=ids["settled"], now=old)

    stats = cleanup.sweep_orphaned_thread_artifacts(now=now)

    assert stats["proactive_candidates"] == 4 and stats["proactive_deleted"] == 2
    assert threads._thread_exists(ids["open"]), "its draft's moment is still open"
    assert threads._thread_exists(ids["recent"]), "too new"
    assert not threads._thread_exists(ids["settled"]) and not threads._thread_exists(ids["empty"])
    assert [draft["moment_id"] for draft in store.drafts()] == [still_open["id"]]
