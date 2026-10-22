"""Held-out corrections (O87): every 2nd confirmed correction from exact capture, in time order, is
held out with the mark datasets, drafting and practice already leave out, so nothing trains on it;
imported (partial) corrections are never held out and don't count; once held, always held; the
turn after a held-out correction (the person's correction and the redo) never trains either."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from row_bot.learning import evaluation, holdout
from row_bot.learning.store import LearningStore
from row_bot.learning.training import dataset, recipes
from tests.fixtures.learning import CapturedWork, signal

pytestmark = [pytest.mark.subsystem]


@pytest.fixture
def work(writer, chats, clock):
    return CapturedWork(writer, chats, clock)


def _teach(store: LearningStore, thread_id: str, decision_id: str, at: float, number: int, **fields) -> None:
    store.put(signal(thread_id, f"sig_{number}", decision_id=decision_id, created_at=at, note="Use the other file",
                     **fields))


def _partial(store: LearningStore, decision_id: str) -> None:
    store.put(replace(store.get("decision", decision_id), fidelity="partial"))


def test_every_second_correction_from_exact_capture_is_held_out_in_time_order(work, learning_on):
    answers = [work.turn(f"chat-{index}", f"Question {index}", f"Answer {index}.")[-1] for index in range(7)]
    store = LearningStore(learning_on)
    _partial(store, answers[2])  # an imported past chat's reply: never hidden, not counted
    # Taught out of order: the order that counts is when each correction was made.
    for number, (index, at) in enumerate([(4, 50.0), (0, 10.0), (1, 20.0), (2, 30.0), (3, 40.0), (5, 60.0)]):
        _teach(store, f"chat-{index}", answers[index], at, number)
    _teach(store, "chat-6", answers[6], 70.0, 9, withdrawn_at=71.0)  # undone at once: not a correction

    report = holdout.mark_corrections(store)

    # Exact corrections in time order: answers 0, 1, 3, 4, 5; every 2nd is held.
    assert report.held == (answers[1], answers[4]) and report.counted == 5
    assert {answers[1], answers[4]} <= store.held_out_ids()
    assert holdout.waiting_for_vault(store) == {answers[1], answers[4]}
    assert answers[2] not in store.held_out_ids()


def test_once_held_always_held_and_the_count_carries_on(work, learning_on):
    answers = [work.turn(f"chat-{index}", f"Question {index}", f"Answer {index}.")[-1] for index in range(5)]
    store = LearningStore(learning_on)
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


def test_a_decision_corrected_again_later_is_counted_once(work, learning_on):
    answers = [work.turn(f"chat-{index}", f"Question {index}", f"Answer {index}.")[-1] for index in range(3)]
    store = LearningStore(learning_on)
    _teach(store, "chat-0", answers[0], 10.0, 0)
    assert holdout.mark_corrections(store).held == ()  # the 1st correction: not held (its redo may train)

    _teach(store, "chat-0", answers[0], 20.0, 1)  # taught again a day later: still the same problem
    again = holdout.mark_corrections(store)
    _teach(store, "chat-1", answers[1], 30.0, 2)
    then = holdout.mark_corrections(store)

    assert (again.counted, again.held) == (0, ())
    assert then.held == (answers[1],) and answers[0] not in store.held_out_ids()


def test_training_never_sees_a_held_out_correction_or_the_turn_that_redid_it(work, learning_on, chats):
    first = work.turn("chat-1", "Question one", "Answer one is 41.")
    second = work.turn("chat-1", "Question two", "Answer two is 47.")
    redo = work.turn("chat-1", "No, use the other file", "Then it is 48.")
    work.turn("chat-1", "Question four", "Answer four is 60.")
    store = LearningStore(learning_on)
    _teach(store, "chat-1", first[-1], 1.0, 1)
    _teach(store, "chat-1", second[-1], 2.0, 2)  # the 2nd correction: held out
    redone = store.get("decision", redo[-1]).episode_id
    store.put(replace(store.get("episode", redone), follows_decision_id=second[-1]))

    compiled = dataset.compile_dataset(store, recipes.load("8gb"), lookup=chats, refresh_vault=False,
                                       shape="captured")
    text = json.dumps([item.row for item in compiled.train])

    assert second[-1] in store.held_out_ids()  # marked before the rows were chosen
    assert compiled.left_out == {"corrected_or_stopped": 2, "held_out": 1}
    assert "Answer two is 47." not in text and "Then it is 48." not in text and "use the other file" not in text
    [row] = [item.row for item in compiled.train]
    assert row["messages"][1]["content"] == "Question four"


def test_replay_for_testing_changes_leaves_a_held_out_correction_to_the_vault(work, learning_on):
    first = work.turn("chat-1", "Question one", "Answer one.")
    second = work.turn("chat-2", "Question two", "Answer two.")
    store = LearningStore(learning_on)
    _teach(store, "chat-1", first[-1], 1.0, 1)
    _teach(store, "chat-2", second[-1], 2.0, 2)
    corrections = {first[-1]: evaluation.CorrectedAction(text="One."),
                   second[-1]: evaluation.CorrectedAction(text="Two.")}

    found = evaluation.collect_replay(store, corrections=corrections)

    replayed = {case.decision_id for case in found.cases}
    assert first[-1] in replayed and second[-1] not in replayed
    assert found.skipped["held_out"] == 1

