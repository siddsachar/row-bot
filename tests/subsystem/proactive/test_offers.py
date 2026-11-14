"""Make it a workflow: the one offer about a repeated ask makes a draft workflow only when the person chooses it,
and a no is kept for good."""
from __future__ import annotations

import pytest

from row_bot.proactive.follow_ups import ChatsWatcher
from tests.subsystem.proactive.conftest import AT_DESK, at, start_day
from tests.subsystem.proactive.fakes import FakeChats

pytestmark = pytest.mark.subsystem

ASKS = ("Summarise the top AI news headlines for me", "summarise today's top AI news headlines please",
        "Can you summarise the top AI news headlines?")


# Much the same ask in other words (so not the same offer twice by its key alone).
REWORDED = ("Summarise the top tech news headlines", "summarise top tech news headlines, thanks",
            "Could you summarise the top tech news headlines?")


def _offer(proactive, chats: FakeChats, *, day: str = "2026-11-16", start: str = "a",
           asks: tuple[str, ...] = ASKS) -> dict | None:
    """The same ask in three chats, an hour apart; the offer it brings, if any."""
    before = {moment["id"] for moment in proactive.store.list_moments()}
    for hour, ask in enumerate(asks):
        chats.say(f"chat-{start}{hour}", ask)
        proactive.engine.tick(at(f"{day} {9 + hour:02d}:00"), desktop=AT_DESK)
    new = [moment for moment in proactive.store.list_moments() if moment["id"] not in before]
    return new[0] if new else None


def _workflows(proactive) -> list[dict]:
    return [task for task in proactive.tasks.list_tasks() if task["name"] not in ("Morning brief",)]


def test_acting_on_the_offer_makes_a_draft_workflow_to_review_and_it_is_never_offered_again(proactive):
    from row_bot.application.client_proactive import moment_feedback

    chats = FakeChats()
    proactive.watchers.register(ChatsWatcher(chats))
    start_day(proactive)
    offer = _offer(proactive, chats)
    assert offer["kind"] == "workflow_offer" and offer["actions"] == [{"id": "make_workflow",
                                                                       "label": "Make it a workflow"}]
    assert _workflows(proactive) == [], "nothing is made until the person chooses it"

    detail = moment_feedback(offer["id"], "act", action_id="make_workflow")

    [workflow] = _workflows(proactive)
    assert detail["workflow_id"] == workflow["id"] and detail["moment"]["state"] == "acted"
    assert workflow["prompts"] == [ASKS[2]], "the person's own words"
    assert workflow["name"] == "Can you summarise the top AI news headlines"
    assert not workflow["enabled"] and not workflow["schedule"], "a draft: off until reviewed"
    assert workflow["safety_mode"] != "full", "never Hands-off: its actions go through approval"

    assert _offer(proactive, chats, day="2026-11-17", start="b", asks=REWORDED) is None, "offered once"
    assert len(_workflows(proactive)) == 1


def test_declining_the_offer_is_kept_for_good(proactive):
    chats = FakeChats()
    proactive.watchers.register(ChatsWatcher(chats))
    start_day(proactive)
    offer = _offer(proactive, chats)

    proactive.feedback.dismiss(offer["id"], now=at("2026-11-16 12:00"))
    assert proactive.store.activity(limit=1)[0]["summary"] == "Won't offer to make that ask a workflow again."
    # Even when the watcher has forgotten everything it saw (its cursor reset), the no stands.
    proactive.store.save_cursor("chats", {}, now=at("2026-11-17 08:00"))
    proactive.engine.tick(at("2026-11-17 08:30"), desktop=AT_DESK)

    assert _offer(proactive, chats, day="2026-11-17", start="b", asks=REWORDED) is None
    assert _offer(proactive, chats, day="2026-12-20", start="c", asks=REWORDED) is None, "a month on, still no"
    assert _workflows(proactive) == []
