"""The quick question (L4b; UX §3): asked only when the night's look said an issue needs more examples, at most one a
day, never again once answered or waved away; the answer is the person's feedback the next diagnosis reads (never a
Signal, so capture, the vault and held-out corrections don't change); with the build's switch off there is none.
Pull-request lane."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage

from row_bot.learning import corrections, diagnosis, question
from row_bot.learning.spending import TokenMeter
from row_bot.learning.store import LearningStore
from tests.fixtures.learning import CapturedWork, signal
from tests.fixtures.learning_eval import ScriptedClient

pytestmark = [pytest.mark.subsystem]

HOUR = 3600.0
DAY = 24 * HOUR
ASKS = {"question": "When two rota policies share a name, which should I use?", "answers": ["The newest", "Ask me"]}
UNSURE = {"finding": "The person said the policy was old, but not which one is right.", "looks_like_bug": False,
          "change": "needs_examples"}


@pytest.fixture
def work(writer, chats, clock):
    return CapturedWork(writer, chats, clock)


@pytest.fixture
def store(learning_on, clock):
    return LearningStore(learning_on, clock=clock)


@pytest.fixture
def asking(monkeypatch):
    from row_bot import learning

    monkeypatch.setenv(learning.QUICK_QUESTION_ENV, "1")


def says(answer: dict) -> ScriptedClient:
    return ScriptedClient(lambda messages, tools, model_ref: AIMessage(content=json.dumps(answer)))


def corrected_lookups(work, store, clock) -> None:
    for index in range(2):
        thread = f"chat-{index}"
        reply = work.turn(thread, f"According to the rota policy, how many work shift {index}?", "Three.",
                          tool=False, person_message=True)[-1]
        store.put(signal(thread, f"sig_t{index}", decision_id=reply, created_at=clock.now, note="Old policy",
                         category="wrong_answer"))
    clock.advance(HOUR)
    corrections.confirm(store)


def look(store, clock, answer: dict, night: str) -> None:
    diagnosis.run(store, client=says(answer), night=night, now=clock.now, meter=TokenMeter(10**9))


def ask(store, clock, night: str, answer: dict = ASKS) -> tuple[dict | None, ScriptedClient]:
    client = says(answer)
    return question.maybe_ask(store, client=client, night=night, now=clock.now, meter=TokenMeter(10**9)), client


def test_a_question_is_asked_only_when_the_night_left_an_issue_needing_more_examples(work, store, clock, asking):
    corrected_lookups(work, store, clock)
    nothing, client = ask(store, clock, "2027-01-15")
    assert nothing is None and client.calls == []  # no look said it was unsure: no call

    look(store, clock, UNSURE, "2027-01-16")
    asked, client = ask(store, clock, "2027-01-16")

    assert asked["question"] == ASKS["question"] and asked["answers"] == ASKS["answers"]
    assert asked["kind"] == "document_lookups"
    assert [call["purpose"] for call in client.calls] == ["learning_diagnosis"]
    assert question.current(store)["id"] == asked["id"]


def test_at_most_one_a_day_and_none_when_the_model_cant_write_one(work, store, clock, asking):
    corrected_lookups(work, store, clock)
    look(store, clock, UNSURE, "2027-01-16")
    first, _client = ask(store, clock, "2027-01-16", {"none": True})
    later, client = ask(store, clock, "2027-01-17")  # the same day, a second night

    assert first is None and later is None and client.calls == [] and question.current(store) is None


def test_the_answer_is_the_persons_feedback_the_next_look_reads_never_a_signal(work, store, clock, asking):
    corrected_lookups(work, store, clock)
    look(store, clock, UNSURE, "2027-01-16")
    asked, _client = ask(store, clock, "2027-01-16")
    signals = len(store.find("signal"))
    clock.advance(HOUR)

    question.answer(store, asked["id"], "The newest")

    assert question.current(store) is None and len(store.find("signal")) == signals
    issues, _left = diagnosis.collect(store, now=clock.now)
    [issue] = [item for item in issues if item.kind == "document_lookups"]
    assert issue.feedback == 3 and issue.answers[0]["answer"] == "The newest"
    shown = diagnosis.describe(store, corrections.Book(store), issue, now=clock.now)
    assert f"The person answered a quick question: {ASKS['question']} → The newest" in shown
    # It is new evidence: the issue the look had put down as unsure is looked at again.
    lesson = {"finding": "Use the newest policy.", "looks_like_bug": False, "change": "lesson",
              "title": "Use the newest policy", "text": "When two policies share a name, use the newest."}
    client = says(lesson)
    report = diagnosis.run(store, client=client, night="2027-01-17", now=clock.now, meter=TokenMeter(10**9))
    assert report.drafted == 1
    with pytest.raises(question.QuestionError) as again:
        question.answer(store, asked["id"], "Ask me")
    assert again.value.code == "changed"


def test_waved_away_it_is_never_asked_again(work, store, clock, asking):
    corrected_lookups(work, store, clock)
    look(store, clock, UNSURE, "2027-01-16")
    asked, _client = ask(store, clock, "2027-01-16")

    question.dismiss(store, asked["id"])
    clock.advance(DAY)
    look(store, clock, UNSURE, "2027-01-17")
    later, client = ask(store, clock, "2027-01-17")

    assert question.current(store) is None and later is None and client.calls == []


def test_only_one_of_its_two_answers_is_taken(work, store, clock, asking):
    corrected_lookups(work, store, clock)
    look(store, clock, UNSURE, "2027-01-16")
    asked, _client = ask(store, clock, "2027-01-16")

    with pytest.raises(question.QuestionError) as refused:
        question.answer(store, asked["id"], "Something else")
    assert refused.value.code == "unknown_answer" and question.current(store)["id"] == asked["id"]


@pytest.mark.parametrize("bad", [
    {"question": "Should I email sam@example.test the rota?", "answers": ["Yes", "No"]},
    {"question": "Which policy?", "answers": ["The newest"]},
    {"question": "Which policy?", "answers": ["Newest", "newest"]},
])
def test_a_question_that_isnt_two_plain_answers_is_not_asked(work, store, clock, asking, bad):
    corrected_lookups(work, store, clock)
    look(store, clock, UNSURE, "2027-01-16")
    asked, _client = ask(store, clock, "2027-01-16", bad)

    assert asked is None and question.current(store) is None


def test_with_the_switch_off_nothing_is_asked(work, store, clock):
    corrected_lookups(work, store, clock)
    look(store, clock, UNSURE, "2027-01-16")
    asked, client = ask(store, clock, "2027-01-16")

    assert asked is None and client.calls == [] and question.current(store) is None
