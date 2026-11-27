"""A tapped answer button is never a correction.

A reply can offer answer buttons (F3). Tapping one sends the button's words as the person's own message,
marked ``tapped_choice``. Those words can read like a correction ("No, keep the old one"), but they answer
the reply's question: the next-message rule never confirms one, and a redo the person answered with a tap
is one they accepted. The same words typed are still judged by the rule."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from row_bot.learning import corrections
from row_bot.learning.store import LearningStore

pytestmark = [pytest.mark.subsystem]

HOUR = 3600.0
ASK = "Tidy up the March invoices folder."
OFFER = "Shall I replace the old summary with the new one?"
ANSWER = "No, keep the old one"


def _turn(writer, chats, clock, thread_id: str, opening: HumanMessage, answer: str, *,
          history: list | None = None) -> tuple[str, list]:
    """One turn through Capture's seams, opened by ``opening``. Returns its reply's decision id and the
    conversation it leaves."""

    if thread_id not in chats.threads:
        chats.chat(thread_id)
    turn = writer.begin_turn(thread_id=thread_id, surface="normal_chat", person_message=True)
    kept = [*(history or []), opening]
    reply = AIMessage(content=answer, id=f"r-{thread_id}-{len(kept)}")
    decision = writer.model_call(turn, messages=[SystemMessage(content="You are Row-Bot.", id="sys"), *kept],
                                 response=reply, model_ref="gpt-5.6-sol", provider_id="openai")
    writer.end_turn(turn, "completed")
    writer.drain()
    clock.advance(60)
    return decision, [*kept, reply]


def _typed(thread_id: str, text: str, number: int) -> HumanMessage:
    return HumanMessage(content=text, id=f"h-{thread_id}-{number}")


def _tapped(thread_id: str, text: str, number: int, offered_by: list) -> HumanMessage:
    """A tap as the server keeps it: the button's words, and which reply offered it."""

    return HumanMessage(content=text, id=f"h-{thread_id}-{number}", additional_kwargs={
        "tapped_choice": {"message_id": offered_by[-1].id, "label": text}})


def test_a_tapped_answer_is_never_a_correction_but_the_same_words_typed_are(writer, chats, learning_on, clock):
    assert corrections.judge(ANSWER) == "correction"  # the words alone read as one
    replies = {}
    for thread_id, opening in (("tapped", _tapped), ("typed", None)):
        replies[thread_id], kept = _turn(writer, chats, clock, thread_id, _typed(thread_id, ASK, 0), OFFER)
        message = (opening(thread_id, ANSWER, 1, kept) if opening is not None
                   else _typed(thread_id, ANSWER, 1))
        _turn(writer, chats, clock, thread_id, message, "Done: the old summary stays.", history=kept)
    store = LearningStore(learning_on, clock=clock)
    clock.advance(HOUR)

    report = corrections.confirm(store)

    assert report.confirmed == (replies["typed"],)
    assert report.by_verdict.get("tapped") == 1
    assert {item.decision_id for item in store.find("signal", limit=100) if item.kind == corrections.NEXT_MESSAGE} \
        == {replies["typed"]}


def test_words_retry_sent_again_are_never_a_correction_but_the_same_words_typed_are(writer, chats, learning_on, clock):
    """FL9: Retry of a turn whose step already ran sends the person's words again as a new message, marked
    ``retried``; it is a retry signal of its own, never new words for the next-message rule."""

    replies = {}
    for thread_id, extra in (("retried", {"retried": True}), ("typed", {})):
        replies[thread_id], kept = _turn(writer, chats, clock, thread_id, _typed(thread_id, ASK, 0), OFFER)
        message = HumanMessage(content=ANSWER, id=f"h-{thread_id}-1", additional_kwargs=extra)
        _turn(writer, chats, clock, thread_id, message, "Done: the old summary stays.", history=kept)
    store = LearningStore(learning_on, clock=clock)
    clock.advance(HOUR)

    report = corrections.confirm(store)

    assert report.confirmed == (replies["typed"],)
    assert report.by_verdict.get("tapped") == 1


STORYBOARD = "Storyboard, nothing made yet: 4 scenes. Approve it, change it or comment on a card."
COMMENT = 'Scene 2 [s2] of the video "Launch" (vid-1): that is wrong, use the blue logo'


def test_a_comment_the_video_panel_sends_on_a_storyboard_is_never_a_correction(writer, chats, learning_on, clock):
    """C1: a storyboard asks for comments; the Video panel sends one as the person's message, marked
    ``video_panel``. The same words typed are still judged by the rule."""

    assert corrections.judge(COMMENT) == "correction"  # the words alone read as one
    replies = {}
    for thread_id, panel in (("panel", {"video_panel": {"action": "comment"}}), ("typed", {})):
        replies[thread_id], kept = _turn(writer, chats, clock, thread_id, _typed(thread_id, ASK, 0), STORYBOARD)
        message = HumanMessage(content=COMMENT, id=f"h-{thread_id}-1", additional_kwargs=panel)
        _turn(writer, chats, clock, thread_id, message, "Changed scene 2: the blue logo.", history=kept)
    store = LearningStore(learning_on, clock=clock)
    clock.advance(HOUR)

    report = corrections.confirm(store)

    assert report.confirmed == (replies["typed"],)
    assert report.by_verdict.get("tapped") == 1


def test_a_redo_the_person_answered_with_a_tap_is_accepted(writer, chats, learning_on, clock):
    first, kept = _turn(writer, chats, clock, "chat-1", _typed("chat-1", ASK, 0), "I moved every invoice.")
    redo, kept = _turn(writer, chats, clock, "chat-1",
                       _typed("chat-1", "No, I asked for only the paid ones.", 1), OFFER, history=kept)
    _turn(writer, chats, clock, "chat-1", _tapped("chat-1", ANSWER, 2, kept), "Done: the old summary stays.",
          history=kept)
    store = LearningStore(learning_on, clock=clock)
    clock.advance(HOUR)

    assert corrections.confirm(store).confirmed == (first,)  # the typed correction only
    found = corrections.accepted_redo(store, store.get("decision", first))

    assert found is not None and found.id == store.get("decision", redo).episode_id
