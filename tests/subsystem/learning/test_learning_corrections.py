"""Next-message corrections (O12) and held-out corrections in the vault (O87).

A message sent right after a reply becomes a ``next_message_correction`` Signal only when its opening
says the reply was wrong (false corrections are worse than missed ones); ambiguous, withdrawn, late and
still-open ones stay out. Every 2nd confirmed correction is held out (``holdout``); once the person
accepted a redo, the vault stores it with the redo's first move as the reference, sealed like the
vault's other problems: only aggregates come out, and drafting, datasets and practice never see it."""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from row_bot.learning import corrections, evaluation, holdout
from row_bot.learning.evaluation import Arm
from row_bot.learning.store import LearningStore
from row_bot.learning.training import dataset, recipes
from tests.fixtures.learning import CapturedWork, signal
from tests.fixtures.learning_eval import ScriptedClient

pytestmark = [pytest.mark.subsystem]

HOUR = 3600.0

# Seeded and written-for-the-test follow-ups: the rule's labelled examples.
CORRECTIONS = [
    "No, use the 2026 agreement.",
    "That's not right - the fee is 85 EUR.",
    "Hmm, not quite: the rota changed in March.",
    "Wrong file. I asked for the approved policy.",
    "You used the old policy.",
    "That is the draft, not the approved handbook.",
    "Coldharbour Dairy delivers Mondays and Thursdays - Thursday was missing.",
    "It's 5C max. I need the approved limit, not the unapproved draft.",
    "I asked for one event, not two.",
    "It should be 5C, not 8C.",
    "`tools/release_6.py` wasn't written. Implement it there.",
    "Nope, the meeting is at 11.",
]
NOT_CORRECTIONS = [
    "Now do the same for March.",
    "Thanks! Can you also add the totals?",
    "No worries, next: archive the newsletter.",
    "No thanks, that is all.",
    "What about the other client?",
    "Is the total including VAT?",
    "Great. Which entries are missing from the rota?",
    "Find the wrong date in the invoice log.",
    "Check which deliveries were missing last week.",
    "Perfect, that works.",
    "Can you schedule a meeting with Eli on Friday?",
    "Not now, maybe later.",
    "Lovely - now turn it into a CSV.",
    "The supplier list was updated yesterday, please read it again and tell me who delivers dairy.",
]
AMBIGUOUS = [
    "Actually, can you also include Mondays?",
    "Wait, before that, read the handbook.",
    "Is that right? It seems high.",
    "No, you were right, never mind.",
]


@pytest.fixture
def work(writer, chats, clock):
    return CapturedWork(writer, chats, clock)


def test_the_rule_confirms_only_messages_that_say_the_reply_was_wrong():
    assert [text for text in CORRECTIONS if corrections.judge(text) != "correction"] == []
    assert [text for text in NOT_CORRECTIONS if corrections.judge(text) == "correction"] == []
    assert {corrections.judge(text) for text in AMBIGUOUS} == {"ambiguous"}
    measured = corrections.precision([(text, True) for text in CORRECTIONS]
                                     + [(text, False) for text in NOT_CORRECTIONS + AMBIGUOUS])
    assert measured["precision"] == 1.0 and measured["false_positive"] == 0


def test_the_persons_words_leave_out_rowbots_turn_context():
    request = {"messages": [
        {"type": "human", "data": {"content": "Earlier question"}},
        {"type": "ai", "data": {"content": "Earlier answer"}},
        {"type": "human", "data": {"content": "No, the other file.\n\n<row_bot_turn_context>\nNow: 10:00"}},
        {"type": "human", "data": {"content": "<row_bot_turn_context>x</row_bot_turn_context>",
                                   "additional_kwargs": {"row_bot_turn_context": {"turn_id": "t1"}}}},
    ]}

    assert corrections.person_texts(request) == ["Earlier question", "No, the other file."]
    assert corrections.person_text(request) == "No, the other file."


def test_a_correction_in_the_next_message_becomes_a_signal_on_the_reply(work, learning_on, clock):
    first = work.turn("chat-1", "What is the fee?", "It's 70 EUR.", tool=False, person_message=True)
    work.turn("chat-1", "No, use the 2026 agreement.", "Then it's 85 EUR.", tool=False, person_message=True)
    other = work.turn("chat-2", "What is the rota?", "Mondays.", tool=False, person_message=True)
    work.turn("chat-2", "Thanks! Now list the suppliers.", "Coldharbour and Fenn.", tool=False, person_message=True)
    store = LearningStore(learning_on, clock=clock)

    clock.advance(HOUR)
    report = corrections.confirm(store)

    assert report.confirmed == (first[-1],) and report.considered == 2
    [found] = [item for item in store.find("signal") if item.kind == "next_message_correction"]
    assert found.decision_id == first[-1] and found.thread_id == "chat-1"
    assert clock.now - 1 < found.created_at < clock.now  # dated when Learning confirmed it: holdout never skips it
    assert other[-1] not in {item.decision_id for item in store.find("signal")}
    assert corrections.confirm(store).confirmed == ()  # each message is judged once


def test_open_ambiguous_withdrawn_and_late_messages_stay_out(work, learning_on, clock):
    store = LearningStore(learning_on, clock=clock)
    ambiguous = work.turn("chat-1", "Total?", "It's 12.", tool=False, person_message=True)
    work.turn("chat-1", "Actually, can you also include Mondays?", "With Mondays it's 15.", tool=False,
              person_message=True)
    withdrawn = work.turn("chat-2", "Total?", "It's 12.", tool=False, person_message=True)
    work.turn("chat-2", "No, that's wrong.", "Sorry, it's 13.", tool=False, person_message=True)
    work.turn("chat-2", "Never mind, you were right the first time.", "OK, 12 it is.", tool=False,
              person_message=True)
    late = work.turn("chat-3", "Total?", "It's 12.", tool=False, person_message=True)
    clock.advance(corrections.MAX_GAP_SECONDS + 60)
    work.turn("chat-3", "No, that's wrong.", "It's 14.", tool=False, person_message=True)

    early = corrections.confirm(store, clock=lambda: clock.now + 60)
    assert (early.ambiguous, early.withdrawn, early.waiting) == (2, 1, 1)  # the newest turn isn't settled yet
    clock.advance(HOUR)
    later = corrections.confirm(store)

    corrected = {item.decision_id for item in store.find("signal") if item.kind == "next_message_correction"}
    assert not corrected & {ambiguous[-1], withdrawn[-1], late[-1]}
    assert (later.considered, later.late) == (1, 1)


def test_nothing_is_confirmed_while_learning_is_paused(work, learning_on, clock):
    from tests.fixtures.learning import save_learning_settings

    work.turn("chat-1", "Total?", "It's 12.", tool=False, person_message=True)
    work.turn("chat-1", "No, it's 13.", "Sorry, 13.", tool=False, person_message=True)
    save_learning_settings(learning_on, enabled=True, paused=True)
    clock.advance(HOUR)

    assert corrections.confirm(LearningStore(learning_on, clock=clock)).confirmed == ()


def test_next_message_corrections_count_toward_the_held_out_corrections(work, learning_on, clock):
    replies = []
    for index in range(4):
        replies.append(work.turn(f"chat-{index}", f"Total {index}?", f"It's {index}.", tool=False,
                                 person_message=True)[-1])
        work.turn(f"chat-{index}", "No, that's not right.", f"It's {index + 10}.", tool=False, person_message=True)
    store = LearningStore(learning_on, clock=clock)
    clock.advance(HOUR)

    report = holdout.mark_corrections(store)

    assert report.counted == 4 and report.held == (replies[1], replies[3])
    assert holdout.waiting_for_vault(store) == {replies[1], replies[3]}


def _corrected_twice(work, store, clock):
    """Two chats where the person corrected a step in the next message and the redo got it right."""

    wrong_args = {"expression": "6 * 7"}
    right_args = {"expression": "6 * 8"}
    first = work.turn("chat-1", "Price for 6 boxes at 8?", "It's 42.", args=wrong_args, person_message=True)
    redo1 = work.turn("chat-1", "No, boxes are 8 each.", "It's 48.", args=right_args, person_message=True)
    second = work.turn("chat-2", "Price for 6 crates at 8?", "It's 42.", args=wrong_args, person_message=True)
    redo2 = work.turn("chat-2", "That's wrong, crates are 8 each.", "It's 48.", args=right_args,
                      person_message=True)
    clock.advance(HOUR)
    return first, redo1, second, redo2


def test_a_held_out_correction_goes_into_the_vault_with_its_corrected_action(work, learning_on, clock):
    store = LearningStore(learning_on, clock=clock)
    first, _redo1, second, redo2 = _corrected_twice(work, store, clock)

    report = evaluation.refresh_audit(store=store, asked_by="nightly")

    assert report["corrections_added"] == 1 and report["corrections_held"] == 1
    assert report["corrections_waiting"] == 0
    assert holdout.waiting_for_vault(store) == {second[-1]}
    assert evaluation.audit_summary(asked_by="t", store=store)["correction_tasks"] == 1
    assert evaluation.audit_size(asked_by="t", store=store, include_replay=True, packs=(), corrected=True) == 1
    # Stored once: a second refresh adds nothing.
    assert evaluation.refresh_audit(store=store, asked_by="nightly")["corrections_added"] == 0


def test_replay_tests_a_held_out_correction_against_the_redos_move_and_says_only_counts(work, learning_on, clock):
    store = LearningStore(learning_on, clock=clock)
    _corrected_twice(work, store, clock)
    evaluation.refresh_audit(store=store, asked_by="nightly")

    def fixed(messages, tools, model_ref):
        return AIMessage(content="", tool_calls=[{"name": "calculate", "args": {"expression": "6 * 8"},
                                                  "id": "c1", "type": "tool_call"}])

    def same_mistake(messages, tools, model_ref):
        return AIMessage(content="", tool_calls=[{"name": "calculate", "args": {"expression": "6 * 7"},
                                                  "id": "c1", "type": "tool_call"}])

    for respond, passed in ((fixed, 1), (same_mistake, 0)):
        result = evaluation.audit_check([Arm("main")], asked_by="test", client=ScriptedClient(respond), packs=(),
                                        store=store, save=False, repeats=1, corrected=True)
        [arm] = result.arms
        assert (arm.passed, arm.total) == (passed, 1)
        assert arm.cases == [] and result.clusters == {}  # aggregates only (E10)


def test_a_teach_with_no_redo_waits_for_one(work, learning_on, clock):
    store = LearningStore(learning_on, clock=clock)
    answers = [work.turn(f"chat-{index}", f"Total {index}?", f"It's {index}.", tool=False, person_message=True)[-1]
               for index in range(2)]
    for number, answer in enumerate(answers):
        store.put(signal(f"chat-{number}", f"sig_{number}", decision_id=answer, created_at=clock.now + number,
                         note="Wrong total"))
    clock.advance(HOUR)

    report = evaluation.refresh_audit(store=store, asked_by="nightly")

    assert report["corrections_added"] == 0 and report["corrections_waiting"] == 1
    assert answers[1] in store.held_out_ids()  # still held out: nothing trains or drafts on it


def test_a_vault_task_corrected_later_retires_or_becomes_a_correction(work, learning_on, clock, monkeypatch):
    store = LearningStore(learning_on, clock=clock)
    monkeypatch.setattr(evaluation.audit, "HOLD_EVERY", 1)  # every uncorrected decision goes to the vault
    taken = work.turn("chat-1", "Total?", "It's 12.", tool=False, person_message=True)[-1]
    clock.advance(HOUR)
    evaluation.refresh_audit(store=store, asked_by="nightly")
    assert evaluation.audit_summary(asked_by="t", store=store)["replay_tasks"] == 1

    store.put(signal("chat-1", "sig_stop", kind="stop", decision_id=taken, created_at=clock.now))
    report = evaluation.refresh_audit(store=store, asked_by="nightly")

    assert report["retired"] == 1
    assert evaluation.audit_summary(asked_by="t", store=store)["replay_tasks"] == 0


def test_drafting_datasets_and_practice_never_see_a_held_out_correction(work, learning_on, clock, chats):
    store = LearningStore(learning_on, clock=clock)
    _first, _redo1, second, redo2 = _corrected_twice(work, store, clock)
    evaluation.refresh_audit(store=store, asked_by="nightly")

    assert second[-1] in store.held_out_ids() and second[0] not in store.held_out_ids()
    compiled = dataset.compile_dataset(store, recipes.load("8gb"), lookup=chats, refresh_vault=False,
                                       shape="captured")
    rows = str([item.row for item in compiled.train])
    assert "crates are 8 each" not in rows  # the redo of a held-out correction never trains (O87)
    offered = {case.decision_id for case in evaluation.collect_replay(store, refresh=False).cases}
    assert second[-1] not in offered


def test_a_redo_must_be_accepted_before_it_is_a_reference(work, learning_on, clock):
    store = LearningStore(learning_on, clock=clock)
    first = work.turn("chat-1", "Total?", "It's 12.", tool=False, person_message=True)
    work.turn("chat-1", "No, that's wrong.", "It's 13.", tool=False, person_message=True)
    work.turn("chat-1", "Still wrong - it's 14.", "Sorry, it's 14.", tool=False, person_message=True)
    clock.advance(HOUR)
    corrections.confirm(store)
    book = corrections.Book(store)

    redo = corrections.accepted_redo(store, store.get("decision", first[-1]))

    # The first redo was corrected again, so the reference is the second redo's move.
    assert redo is not None and book.text_of(redo) == "Still wrong - it's 14."


def test_imported_corrections_are_evidence_never_hidden(work, learning_on, clock):
    store = LearningStore(learning_on, clock=clock)
    first = work.turn("chat-1", "Total?", "It's 12.", tool=False, person_message=True)
    work.turn("chat-1", "No, it's 13.", "13.", tool=False, person_message=True)
    for decision in store.find("decision"):
        store.put(replace(decision, fidelity="partial"))
    for episode in store.find("episode"):
        store.put(replace(episode, fidelity="partial", historical=True))
    clock.advance(HOUR)

    holdout.mark_corrections(store)

    assert first[-1] in {item.decision_id for item in store.find("signal") if item.kind == "next_message_correction"}
    assert first[-1] not in store.held_out_ids()
