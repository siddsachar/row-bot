"""Held-out corrections (O87, amendment A3): every 2nd confirmed correction from exact capture that has a
reference (the person's accepted redo, or their answer to Teach's "What should it have done?"), counted in
the time order its reference came to exist, is held out with the mark datasets, drafting and practice
already leave out, so nothing trains on it; a correction without a reference is not held out and doesn't
count; imported (partial) corrections are never held out and don't count; once held, always held (those
held under the earlier rule too); the turn after a held-out correction never trains either."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from row_bot.learning import evaluation, holdout
from row_bot.learning.store import MARK_HELD_OUT, MARK_HELD_OUT_CORRECTION, LearningStore
from row_bot.learning.training import dataset, recipes
from row_bot.version import __version__
from tests.fixtures.learning import CapturedWork, signal

pytestmark = [pytest.mark.subsystem]

HOUR = 3600.0
ANSWER = "It should have used the other file and said 48."


@pytest.fixture
def work(writer, chats, clock):
    return CapturedWork(writer, chats, clock)


@pytest.fixture
def store(learning_on, clock):
    return LearningStore(learning_on, clock=clock)


def _teach(store: LearningStore, thread_id: str, decision_id: str, at: float, number: int, **fields) -> None:
    """A Teach ``at`` seconds after the store's now, with its answer (a reference, A3) unless told otherwise."""

    fields.setdefault("answer", ANSWER)
    store.put(signal(thread_id, f"sig_{number}", decision_id=decision_id, created_at=store.now() + at,
                     note="Use the other file", **fields))


def _partial(store: LearningStore, decision_id: str) -> None:
    store.put(replace(store.get("decision", decision_id), fidelity="partial"))


def test_every_second_correction_from_exact_capture_is_held_out_in_time_order(work, store):
    answers = [work.turn(f"chat-{index}", f"Question {index}", f"Answer {index}.")[-1] for index in range(7)]
    _partial(store, answers[2])  # an imported past chat's reply: never hidden, not counted
    # Taught out of order: the order that counts is when each correction got its reference.
    for number, (index, at) in enumerate([(4, 50.0), (0, 10.0), (1, 20.0), (2, 30.0), (3, 40.0), (5, 60.0)]):
        _teach(store, f"chat-{index}", answers[index], at, number)
    _teach(store, "chat-6", answers[6], 70.0, 9, withdrawn_at=store.now() + 71.0)  # undone at once

    report = holdout.mark_corrections(store)

    # Exact corrections in time order: answers 0, 1, 3, 4, 5; every 2nd is held.
    assert report.held == (answers[1], answers[4]) and report.counted == 5
    assert {answers[1], answers[4]} <= store.held_out_ids()
    assert holdout.waiting_for_vault(store) == {answers[1], answers[4]}
    assert answers[2] not in store.held_out_ids()


def test_once_held_always_held_and_the_count_carries_on(work, store):
    answers = [work.turn(f"chat-{index}", f"Question {index}", f"Answer {index}.")[-1] for index in range(5)]
    for number in range(3):
        _teach(store, f"chat-{number}", answers[number], 10.0 * (number + 1), number)
    first = holdout.mark_corrections(store)
    assert first.held == (answers[1],)

    store.remove("signal", "sig_1")  # the Teach is undone: what was held stays held
    for number in (3, 4):
        _teach(store, f"chat-{number}", answers[number], 10.0 * (number + 1), number)
    second = holdout.mark_corrections(store)

    assert second.held == (answers[3],) and second.counted == 2  # the 4th correction, counted on from 3
    assert holdout.mark_corrections(store).held == ()  # nothing new: nothing changes
    assert holdout.waiting_for_vault(store) == {answers[1], answers[3]}


def test_a_decision_corrected_again_later_is_counted_once(work, store):
    answers = [work.turn(f"chat-{index}", f"Question {index}", f"Answer {index}.")[-1] for index in range(3)]
    _teach(store, "chat-0", answers[0], 10.0, 0)
    assert holdout.mark_corrections(store).held == ()  # the 1st correction: not held (its redo may train)

    _teach(store, "chat-0", answers[0], 20.0, 1)  # taught again a day later: still the same problem
    again = holdout.mark_corrections(store)
    _teach(store, "chat-1", answers[1], 30.0, 2)
    then = holdout.mark_corrections(store)

    assert (again.counted, again.held) == (0, ())
    assert then.held == (answers[1],) and answers[0] not in store.held_out_ids()


def test_training_never_sees_a_held_out_correction_or_the_turn_that_redid_it(work, store, chats):
    first = work.turn("chat-1", "Question one", "Answer one is 41.")
    second = work.turn("chat-1", "Question two", "Answer two is 47.")
    redo = work.turn("chat-1", "No, use the other file", "Then it is 48.")
    work.turn("chat-1", "Question four", "Answer four is 60.")
    _teach(store, "chat-1", first[-1], 1.0, 1)
    _teach(store, "chat-1", second[-1], 2.0, 2)  # the 2nd correction: held out
    redone = store.get("decision", redo[-1]).episode_id
    store.put(replace(store.get("episode", redone), follows_decision_id=second[-1]))

    compiled = dataset.compile_dataset(store, recipes.load("16gb"), lookup=chats, refresh_vault=False,
                                       shape="captured")
    text = json.dumps([item.row for item in compiled.train])

    assert second[-1] in store.held_out_ids()  # marked before the rows were chosen
    assert compiled.left_out == {"corrected_or_stopped": 2, "held_out": 1}
    assert "Answer two is 47." not in text and "Then it is 48." not in text and "use the other file" not in text
    [row] = [item.row for item in compiled.train]
    assert row["messages"][1]["content"] == "Question four"


def test_replay_for_testing_changes_leaves_a_held_out_correction_to_the_vault(work, store):
    first = work.turn("chat-1", "Question one", "Answer one.")
    second = work.turn("chat-2", "Question two", "Answer two.")
    _teach(store, "chat-1", first[-1], 1.0, 1)
    _teach(store, "chat-2", second[-1], 2.0, 2)
    corrections = {first[-1]: evaluation.CorrectedAction(text="One."),
                   second[-1]: evaluation.CorrectedAction(text="Two.")}

    found = evaluation.collect_replay(store, corrections=corrections)

    replayed = {case.decision_id for case in found.cases}
    assert first[-1] in replayed and second[-1] not in replayed
    assert found.skipped["held_out"] == 1


# ── Amendment A3: a correction counts only once it has a reference ─────────


def test_a_correction_without_a_reference_is_not_held_out_and_does_not_count(work, store):
    answers = [work.turn(f"chat-{index}", f"Question {index}", f"Answer {index}.")[-1] for index in range(3)]
    _teach(store, "chat-0", answers[0], 10.0, 0, answer=None)  # no answer, and no redo: no reference
    _teach(store, "chat-1", answers[1], 20.0, 1)
    _teach(store, "chat-2", answers[2], 30.0, 2)

    report = holdout.mark_corrections(store)

    # Only the two with a reference count; the 2nd of them is held. Holding the first would waste it:
    # with nothing to check a reply against, it could be neither trained on nor tested.
    assert report.counted == 2 and report.held == (answers[2],)
    assert answers[0] not in store.held_out_ids() and answers[1] not in store.held_out_ids()
    assert holdout.waiting_for_vault(store) == {answers[2]}


def test_a_correction_counts_when_its_redo_is_accepted_in_the_order_references_came(work, store, clock):
    late = work.turn("chat-1", "Price for 6 boxes at 8?", "It's 42.", args={"expression": "6 * 7"},
                     person_message=True)[-1]
    early = work.turn("chat-2", "Question two", "Answer two.")[-1]
    _teach(store, "chat-1", late, 1.0, 1, answer=None)  # taught first, but no reference yet
    _teach(store, "chat-2", early, 2.0, 2)  # taught second, with its answer
    clock.advance(HOUR)
    first = holdout.mark_corrections(store)
    assert (first.counted, first.held) == (1, ())  # only the answered one counts so far

    # The person redoes the first turn and accepts the redo: its reference exists from now on.
    work.turn("chat-1", "No, boxes are 8 each.", "It's 48.", args={"expression": "6 * 8"}, person_message=True)
    clock.advance(HOUR)
    second = holdout.mark_corrections(store)

    assert (second.counted, second.held) == (1, (late,))  # the 2nd correction to have a reference
    assert holdout.waiting_for_vault(store) == {late}


def test_a_correction_still_without_a_reference_after_a_week_never_counts(work, store, clock):
    answers = [work.turn(f"chat-{index}", f"Question {index}", f"Answer {index}.")[-1] for index in range(3)]
    _teach(store, "chat-0", answers[0], 1.0, 0, answer=None)
    clock.advance(holdout.REFERENCE_DAYS * 86_400 + HOUR)
    _teach(store, "chat-1", answers[1], 0.0, 1)
    _teach(store, "chat-2", answers[2], 1.0, 2)

    report = holdout.mark_corrections(store)

    assert report.counted == 2 and report.held == (answers[2],)
    assert answers[0] not in store.held_out_ids()


def test_corrections_held_under_the_earlier_rule_stay_held_and_the_count_carries_on(work, store):
    answers = [work.turn(f"chat-{index}", f"Question {index}", f"Answer {index}.")[-1] for index in range(4)]
    # The earlier rule (before A3) counted two corrections with no reference and held the 2nd.
    for number in range(2):
        _teach(store, f"chat-{number}", answers[number], 1.0 + number, number, answer=None)
    held = store.get("decision", answers[1])
    store.add_marks([("decision", held.id, held.conversation_id)], MARK_HELD_OUT)
    store.add_marks([("decision", held.id, held.conversation_id)], MARK_HELD_OUT_CORRECTION)
    store.set_meta_values({"held_out_corrections.counted": json.dumps(answers[:2]),
                           "held_out_corrections.horizon": repr(store.now() + 2.0)})
    for number in (2, 3):
        _teach(store, f"chat-{number}", answers[number], 10.0 + number, number)

    report = holdout.mark_corrections(store)

    assert answers[1] in store.held_out_ids()  # append-only: held stays held
    assert report.counted == 2 and report.held == (answers[3],)  # the 4th counted, so the 2nd of these
    assert holdout.waiting_for_vault(store) == {answers[1], answers[3]}


def test_the_build_records_once_when_it_first_applied_its_rules(work, store, clock):
    answer = work.turn("chat-0", "Question", "Answer.")[-1]
    _teach(store, "chat-0", answer, 1.0, 0)
    started = store.now()

    holdout.mark_corrections(store)
    clock.advance(86_400)
    holdout.mark_corrections(store)
    evaluation.refresh_audit(store=store, asked_by="nightly")

    rules = json.loads(store.meta_value(holdout.RULES_KEY))
    assert rules == {rule: {"since": round(started, 3), "app_version": __version__} for rule in ("A3", "A5")}
    vault_rules = json.loads(store.meta_value(evaluation.AUDIT_RULES_KEY))
    assert set(vault_rules) == {"A4", "A5"} and vault_rules["A5"]["app_version"] == __version__
