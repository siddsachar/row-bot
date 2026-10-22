"""The four built-in packs (D30): every task's verifier passes its reference and fails its
decoy, and the fake apps behave like apps."""

from __future__ import annotations

import json

import pytest

from row_bot.learning.packs import PACKS, visible_tasks
from row_bot.learning.packs.fake_apps import FakeApps
from row_bot.learning.packs.verify import Outcome, solve, verify

pytestmark = pytest.mark.subsystem

NON_CODE = [task for task in visible_tasks() if task.verifier.kind != "code_tests"]
CODE = [task for task in visible_tasks() if task.verifier.kind == "code_tests"]


def test_each_pack_has_sixteen_visible_tasks_of_its_kind() -> None:
    by_pack: dict[str, int] = {}
    for task in visible_tasks():
        by_pack[task.pack] = by_pack.get(task.pack, 0) + 1
    assert by_pack == dict.fromkeys(PACKS, 16)
    kinds = {task.pack: task.verifier.kind for task in visible_tasks()}
    assert kinds["small_coding"] == "code_tests"


@pytest.mark.parametrize("task", NON_CODE, ids=lambda task: task.id)
def test_a_tasks_verifier_passes_its_reference_and_fails_its_decoy(task) -> None:
    assert verify(task, solve(task, task.reference)).passed
    assert not verify(task, solve(task, task.decoy)).passed
    assert not verify(task, Outcome()).passed  # doing nothing never passes


@pytest.mark.platform
def test_a_coding_tasks_tests_pass_its_reference_and_fail_its_decoy_in_the_sandbox() -> None:
    task = CODE[0]
    assert verify(task, solve(task, task.reference), backend="local").passed
    failed = verify(task, solve(task, task.decoy), backend="local")
    assert not failed.passed and failed.backend == "local" and "of 4 tests failed" in failed.detail
    missing = verify(task, Outcome(files={"other.py": "x = 1\n"}), backend="local")
    assert not missing.passed and missing.detail == "solution.py was not written"


@pytest.mark.slow
@pytest.mark.platform
@pytest.mark.parametrize("task", CODE, ids=lambda task: task.id)
def test_every_coding_tasks_tests_pass_its_reference_and_fail_its_decoy(task) -> None:
    assert verify(task, solve(task, task.reference), backend="local").passed
    assert not verify(task, solve(task, task.decoy), backend="local").passed


def test_answers_are_matched_the_way_the_verifiers_say() -> None:
    lookup = next(task for task in visible_tasks() if task.id == "doc_10")  # number: 52,500; decoy 50,000
    assert verify(lookup, Outcome(answer="**52,500 EUR**")).passed
    # METRIC_VERSION 2: a sentence that states the value counts, unless it gives the known wrong value first.
    assert verify(lookup, Outcome(answer="The approved Q1 budget for Operations is 52,500 EUR (the draft said "
                                         "50,000).")).passed
    assert not verify(lookup, Outcome(answer="50,000 in the draft, or 52,500")).passed
    assert not verify(lookup, Outcome(answer="About 50,000 EUR.")).passed
    exact = next(task for task in visible_tasks() if task.id == "doc_04")  # decoy: Mara Lindqvist
    assert verify(exact, Outcome(answer="ines duarte.")).passed
    assert verify(exact, Outcome(answer="The supplier review is owned by **Ines Duarte**.")).passed
    assert not verify(exact, Outcome(answer="Mara Lindqvist owns it; Ines Duarte took over later.")).passed
    assert not verify(exact, Outcome(answer="Ines Duartes")).passed  # a whole phrase, not a prefix
    dated = next(task for task in visible_tasks() if task.id == "doc_07")  # 2026-07-14; decoy 2026-06-30
    assert verify(dated, Outcome(answer="Project Heron's current go-live date is 2026-07-14.")).passed
    assert not verify(dated, Outcome(answer="It moved from 2026-06-30 to 2026-07-14.")).passed


def test_v2_passes_a_list_of_candidates_holding_the_right_one_when_the_known_wrong_value_is_absent() -> None:
    # Explicit on purpose (O67): v2 doesn't ask a reply to commit to one answer, so enumerating candidates can
    # pass. L4a adds a "commits to one answer" guard before strategy trials rely on these checks.
    owner = next(task for task in visible_tasks() if task.id == "doc_04")  # Ines Duarte; decoy Mara Lindqvist
    assert verify(owner, Outcome(answer="It's either Ines Duarte or Tomas Berg.")).passed
    assert not verify(owner, Outcome(answer="Mara Lindqvist or Ines Duarte.")).passed
    dinner = next(task for task in visible_tasks() if task.id == "doc_01")  # 85; decoy 60
    assert verify(dinner, Outcome(answer="Up to 85 or 90 EUR per person.")).passed
    assert not verify(dinner, Outcome(answer="60 or 85 EUR per person.")).passed


def test_v2_passes_a_reply_that_names_the_known_wrong_value_as_context_after_the_right_one() -> None:
    policy = next(task for task in visible_tasks() if task.id == "doc_14")  # POL-114; decoy POL-097
    assert verify(policy, Outcome(answer="POL-114 applies now; POL-097 was superseded by it.")).passed
    budget = next(task for task in visible_tasks() if task.id == "doc_10")  # 52,500; decoy 50,000
    assert verify(budget, Outcome(answer="52,500 EUR is approved. The draft (FIN-502) said 50,000.")).passed
    listing = next(task for task in visible_tasks() if task.id == "doc_16")
    assert verify(listing, Outcome(answer='```json\n["FIN-502", "POL-097"]\n```')).passed
    transform = next(task for task in visible_tasks() if task.id == "st_02")
    rows = "category,total\r\nmeals,41.50\r\noffice,42.75\r\ntravel,200.00\r\n"
    assert verify(transform, Outcome(files={"totals.csv": rows})).passed


def test_fake_apps_change_only_through_their_tools_and_refuse_like_apps() -> None:
    start = {"calendar": {"events": [{"id": "evt_1", "title": "Standup", "date": "2026-03-04", "start": "09:00",
                                      "end": "09:15", "attendees": [], "location": ""}]}}
    apps = FakeApps(start)
    created = json.loads(apps.call("calendar_create_event", {"title": "Review", "date": "2026-03-05",
                                                              "start": "10:00", "end": "10:30"}))
    assert created["created"]["id"] == "evt_2"
    assert json.loads(apps.call("calendar_create_event", {"title": "Bad", "date": "5 March", "start": "10:00",
                                                           "end": "10:30"})) == {"error": "dates are YYYY-MM-DD, not '5 March'"}
    assert "error" in json.loads(apps.call("calendar_update_event", {"event_id": "evt_9", "start": "11:00"}))
    assert "error" in json.loads(apps.call("calendar_update_event", {"event_id": "evt_1", "start": "10:00"}))
    assert "error" in json.loads(apps.call("mail_send", {"to": "nobody", "subject": "s", "body": "b"}))
    assert "error" in json.loads(apps.call("no_such_tool", {}))
    assert start["calendar"]["events"][0]["start"] == "09:00"  # the task's starting state is never changed
    state = apps.state()
    state["calendar"]["events"].clear()
    assert len(apps.state()["calendar"]["events"]) == 2
    schemas = [tool.openai_schema() for tool in apps.tools(["calendar", "issues"])]
    names = [schema["function"]["name"] for schema in schemas]
    assert names[0] == "calendar_list_events" and "issues_update" in names and "mail_send" not in names
    update = next(schema for schema in schemas if schema["function"]["name"] == "issues_update")["function"]
    assert update["parameters"]["required"] == ["issue_id"]
    assert update["parameters"]["properties"]["add_labels"] == {"type": "array", "items": {"type": "string"}}
