"""Learning's own jobs and the personal model (L4c; D21, O153): a job moves to the personal model only when it matches
the main model on the same items of the job's own past work at equal budget (never the vault, hidden or held-out
work), goes back when a newer test fails, the version changes or it isn't served, and the main model stays the
fallback; the personal model never judges (a trial's tests and judging stay with the main model); with the build's
switch off nothing changes. Pull-request lane (D21 is a safety rule of the improver)."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from row_bot import learning
from row_bot.learning import corrections, diagnosis
from row_bot.learning.improver import jobs, strategy
from row_bot.learning.spending import TokenMeter
from row_bot.learning.store import MARK_HELD_OUT, LearningStore
from tests.fixtures.learning import CapturedWork, signal
from tests.fixtures.learning_eval import ScriptedClient
from tests.subsystem.learning.test_learning_meta import challenger_says, model, quiet_first, templates, trial, world

# ruff: noqa: F401, F811 -- the imported pytest fixtures (templates, world) are requested by name.

pytestmark = [pytest.mark.subsystem]

HOUR = 3600.0
NIGHT = "2027-01-16"
PERSONAL = "model:ollama:row-bot-personal-v3:abcd"
LESSON = {"finding": "It read the old rota.", "change": "lesson", "title": "Current rota",
          "text": "Answer from the current rota and say which version you used."}
WAIT = {"finding": "Not sure yet.", "change": "wait"}


@pytest.fixture
def work(writer, chats, clock):
    return CapturedWork(writer, chats, clock)


@pytest.fixture
def store(learning_on, clock):
    return LearningStore(learning_on, clock=clock)


@pytest.fixture
def jobs_on(monkeypatch):
    monkeypatch.setenv(learning.JOBS_ENV, "1")


@pytest.fixture
def served(monkeypatch):
    """Personal model v3, ready where it runs (``serving`` has its own tests); set ``served.now`` to change it."""

    class Served:
        now: tuple[int, str] | None = (3, PERSONAL)

    found = Served()
    monkeypatch.setattr(jobs, "newest_ready", lambda root=None: found.now)
    return found


def feedback(work, store, clock, count: int) -> list[str]:
    """``count`` replies the person corrected with Teach, each in its own chat (the drafting test's items)."""

    replies = []
    for index in range(count):
        thread = f"chat-{index}"
        reply = work.turn(thread, f"According to the rota policy, how many work shift {index}?", "Three.",
                          tool=False, person_message=True)[-1]
        replies.append(reply)
        store.put(signal(thread, f"sig_{index}", decision_id=reply, created_at=clock.now, note="Old rota",
                         category="wrong_answer"))
        clock.advance(60)
    clock.advance(HOUR)
    corrections.confirm(store)
    return replies


def drafts(personal_usable: int) -> ScriptedClient:
    """The main model drafts a usable lesson for every item; the personal model for the first ``personal_usable``
    shifts and only waits on the rest."""

    def respond(messages, tools, model_ref):
        if model_ref != PERSONAL:
            return AIMessage(content=json.dumps(LESSON))
        shift = int(str(messages[-1].content).split("work shift ")[1].split("?")[0])
        return AIMessage(content=json.dumps(LESSON if shift < personal_usable else WAIT))

    return ScriptedClient(respond)


def qualify(store, clock, client) -> jobs.Qualification:
    return jobs.qualify(store, jobs.DRAFTING, client=client, version=3, model_ref=PERSONAL, now=clock.now,
                        method=strategy.FIRST, meter=TokenMeter(10**9))


def test_it_qualifies_only_within_five_points_of_the_main_model_on_the_same_items_at_equal_budget(work, store,
                                                                                                    clock, jobs_on):
    feedback(work, store, clock, 22)

    close = drafts(personal_usable=21)
    passed = qualify(store, clock, close)
    behind = qualify(store, clock, drafts(personal_usable=19))

    assert (passed.verdict, passed.asked, passed.personal_passed, passed.main_passed) == ("qualified", 22, 21, 22)
    assert (behind.verdict, behind.personal_passed, behind.main_passed) == ("not_matched", 19, 22)
    # The same items, asked the same way, of each model; both tagged as drafting.
    asked = {}
    for call in close.calls:
        asked.setdefault(call["model_ref"], []).append([str(m.content) for m in call["messages"]])
    assert asked[PERSONAL] == asked[None] and len(asked[PERSONAL]) == 22
    assert {call["purpose"] for call in close.calls} == {diagnosis.PURPOSE}
    assert passed.personal_tokens == passed.main_tokens  # equal budget, equal spend for equal work


def test_fewer_than_twenty_items_is_no_test(work, store, clock, jobs_on):
    feedback(work, store, clock, 12)
    client = drafts(personal_usable=12)

    result = qualify(store, clock, client)

    assert result.verdict == "too_few" and result.items == 12 and client.calls == []


def test_the_test_never_uses_held_out_work(work, store, clock, jobs_on):
    replies = feedback(work, store, clock, 22)
    held = store.get("decision", replies[5])
    store.add_marks([("decision", held.id, held.conversation_id)], MARK_HELD_OUT)
    client = drafts(personal_usable=22)

    result = qualify(store, clock, client)

    shown = json.dumps([[str(m.content) for m in call["messages"]] for call in client.calls])
    assert result.items == 21 and "work shift 5?" not in shown and "work shift 4?" in shown


def test_a_job_moves_when_it_qualifies_and_goes_back_when_a_newer_test_fails(work, store, clock, jobs_on, served):
    feedback(work, store, clock, 22)
    assert jobs.model_for(store, jobs.DRAFTING) is None  # the main model, until it qualifies

    assert jobs.apply(store, qualify(store, clock, drafts(personal_usable=22))) == "personal"
    assert jobs.model_for(store, jobs.DRAFTING) == PERSONAL
    routed = ScriptedClient(lambda messages, tools, model_ref: AIMessage(content="ok"))
    jobs.client_for(store, jobs.DRAFTING, routed).invoke([HumanMessage(content="x")], tools=None, model_ref=None,
                                                          purpose=diagnosis.PURPOSE)
    assert [call["model_ref"] for call in routed.calls] == [PERSONAL]

    clock.advance(8 * 24 * HOUR)
    assert jobs.due(store, jobs.DRAFTING, 3, now=clock.now)
    assert jobs.apply(store, qualify(store, clock, drafts(personal_usable=10))) == "main"
    held = jobs.state(store)[jobs.DRAFTING]
    assert held["by"] == "main" and held["reason"] == "not_matched" and jobs.model_for(store, jobs.DRAFTING) is None
    assert [step["event"] for step in held["history"]] == ["took_over", "handed_back"]


def test_a_new_version_or_one_that_isnt_served_hands_the_job_back(work, store, clock, jobs_on, served):
    feedback(work, store, clock, 22)
    jobs.apply(store, qualify(store, clock, drafts(personal_usable=22)))

    served.now = (4, "model:ollama:row-bot-personal-v4:ef01")
    assert jobs.model_for(store, jobs.DRAFTING) is None
    assert jobs.state(store)[jobs.DRAFTING]["reason"] == "new_version"

    served.now = (3, PERSONAL)
    jobs.apply(store, qualify(store, clock, drafts(personal_usable=22)))
    served.now = None
    assert jobs.model_for(store, jobs.DRAFTING) is None
    assert jobs.state(store)[jobs.DRAFTING]["reason"] == "not_served"


def test_a_personal_call_that_fails_is_made_again_on_the_main_model_and_the_job_goes_back(work, store, clock,
                                                                                         jobs_on, served):
    feedback(work, store, clock, 22)
    jobs.apply(store, qualify(store, clock, drafts(personal_usable=22)))

    def respond(messages, tools, model_ref):
        if model_ref == PERSONAL:
            raise ConnectionError("the personal model isn't running")
        return AIMessage(content=json.dumps(LESSON))

    client = ScriptedClient(respond)
    reply = jobs.client_for(store, jobs.DRAFTING, client).invoke([HumanMessage(content="x")], tools=None,
                                                                 model_ref=None, purpose=diagnosis.PURPOSE)

    assert json.loads(reply.message.content)["change"] == "lesson"
    assert [call["model_ref"] for call in client.calls] == [PERSONAL, None]
    assert jobs.state(store)[jobs.DRAFTING]["reason"] == "not_served"


def test_the_nights_tests_take_a_job_over_and_count_the_personal_models_use(work, store, clock, jobs_on, served):
    feedback(work, store, clock, 22)

    report = jobs.run_due(store, client=drafts(personal_usable=22), night=NIGHT, now=clock.now)

    assert report["tested"] == {jobs.DRAFTING: {"verdict": "qualified", "by": "personal"}}
    assert jobs.offered() == (jobs.DRAFTING,)  # making practice tasks is a job only in a build that practises
    client = ScriptedClient(lambda messages, tools, model_ref: AIMessage(content="ok"), local=True)
    jobs.client_for(store, jobs.DRAFTING, client).invoke([HumanMessage(content="x")], tools=None, model_ref=None,
                                                         purpose=diagnosis.PURPOSE)
    usage = jobs.state(store)[jobs.DRAFTING]["usage"]
    assert usage["tokens"] == 1_100 and usage["seconds"] >= 0
    # Not tested again tonight or before a week has passed.
    assert jobs.run_due(store, client=drafts(personal_usable=0), night=NIGHT, now=clock.now)["tested"] == {}


def test_with_the_switch_off_nothing_changes(work, store, clock, served):
    feedback(work, store, clock, 22)
    client = drafts(personal_usable=22)

    assert jobs.run_due(store, client=client, night=NIGHT, now=clock.now) is None
    assert jobs.client_for(store, jobs.DRAFTING, client) is client and jobs.offered() == ()
    assert client.calls == [] and jobs.state(store) == {}


@pytest.mark.parametrize("world", ["forty"], indirect=True)
def test_the_personal_model_drafts_in_a_trial_but_never_tests_or_judges(world, store, clock, monkeypatch, jobs_on,
                                                                       served):
    # D21: it never judges its own next version. In a trial each arm drafts with whoever does the drafting job
    # (here the personal model); the arms' tests of their drafts and the trial's judging stay with the main model.
    challenger_says(monkeypatch)
    quiet_first(store)
    held = {jobs.DRAFTING: {"by": "personal", "version": 3, "model_ref": PERSONAL, "since": clock.now}}
    store.set_meta_values({jobs.STATE_KEY: json.dumps(held)})
    client = ScriptedClient(model)

    trial(store, client, clock)

    personal = [call for call in client.calls if call["model_ref"] == PERSONAL]
    others = [call for call in client.calls if call["model_ref"] != PERSONAL]
    learning_prompt = "You are Row-Bot's Learning"

    def system(call) -> str:
        return "\n".join(str(m.content) for m in call["messages"] if isinstance(m, SystemMessage))

    assert personal and all(system(call).startswith(learning_prompt) for call in personal)
    replays = [call for call in others if not system(call).startswith(learning_prompt)]
    assert replays and all(call["model_ref"] != PERSONAL for call in replays)


def test_making_practice_tasks_qualifies_on_tasks_kept_by_the_same_checks(work, store, clock, jobs_on, monkeypatch):
    from tests.subsystem.learning.test_learning_practice import lookup_task

    monkeypatch.setenv(learning.PRACTICE_ENV, "1")
    feedback(work, store, clock, 20)

    def respond(messages, tools, model_ref):
        shift = int(str(messages[-1].content).split("work shift ")[1].split("?")[0])
        made = []
        for number in range(2):
            right, wrong = f"Teal {shift}{number}", f"Amber {shift}{number}"
            # The personal model writes a check that can't tell right from wrong on its last four items.
            if model_ref == PERSONAL and shift >= 16:
                wrong = right
            made.append(lookup_task(f"In the Quillon depot memo {shift}-{number}, which crate holds the gaskets?",
                                    right, wrong))
        return AIMessage(content=json.dumps(made))

    client = ScriptedClient(respond)
    result = jobs.qualify(store, jobs.PRACTICE, client=client, version=3, model_ref=PERSONAL, now=clock.now,
                          method=strategy.FIRST, meter=TokenMeter(10**9))

    assert jobs.offered() == (jobs.DRAFTING, jobs.PRACTICE)
    assert (result.asked, result.main_passed, result.personal_passed) == (40, 40, 32)
    assert result.verdict == "not_matched"  # 80% against 100%
    assert {call["purpose"] for call in client.calls} == {"learning_teacher"}
