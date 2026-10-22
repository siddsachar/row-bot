"""Kinds of work (L2c2, shared with L3a): the four task-pack families plus other, and a cheap,
deterministic classifier for a new request at turn start (no model call). When unsure: other."""

from __future__ import annotations

from collections import Counter

import pytest

from row_bot.learning import kinds
from row_bot.learning.packs import PACKS, visible_tasks

pytestmark = [pytest.mark.subsystem]


def test_the_taxonomy_is_the_four_pack_families_plus_other():
    assert kinds.FAMILIES == tuple(PACKS)
    assert kinds.KINDS[-1] == kinds.OTHER
    assert [kinds.label(kind) for kind in kinds.FAMILIES] == list(PACKS.values())
    assert kinds.phrase("document_lookups") == "document lookups"
    assert kinds.label("anything else") == "Other"
    assert kinds.from_label("Structured transforms") == "structured_transforms"
    assert kinds.from_label("Weekly reports") == "other"
    assert kinds.from_pack("tool_workflows") == "tool_workflows"


def test_the_visible_pack_tasks_classify_as_their_own_family_or_as_other():
    found = Counter()
    for task in visible_tasks():
        apps = [app for app in task.tools if app != "files"]
        result = kinds.classify(kinds.Request(task.prompt, apps=apps))
        found[(task.pack, result.kind)] += 1
        assert result.kind in (task.pack, kinds.OTHER), (task.id, result)  # never another family
    for pack in PACKS:
        assert found[(pack, pack)] >= 14  # at least 14 of each pack's 16


@pytest.mark.parametrize(("text", "expected"), [
    ("According to the travel policy, what is the client dinner limit?", "document_lookups"),
    ("Convert people.csv into JSON grouped by team.", "structured_transforms"),
    ("Write a function that parses ISO dates, with unit tests.", "small_coding"),
    ("Schedule a meeting with Dana on Tuesday at 3pm and reply to her email.", "tool_workflows"),
    ("How are you today?", "other"),
    ("Find the bug in my code", "other"),  # document and coding signals at once: unsure
])
def test_requests_get_their_kind_or_other_when_unsure(text, expected):
    assert kinds.classify(kinds.Request(text)).kind == expected


@pytest.mark.parametrize(("text", "expected"), [
    # L3a: the ways people ask that S1's seeded streams showed going to "other" (lookups 6 of 55, tool
    # workflows 4 of 56 before).
    ("From the donations log, what's the total from school donors from 2026-11-10 to 2026-11-16?",
     "document_lookups"),
    ("How much did business donors give between 2026-10-01 and 2026-10-03? It's in records/donations.csv.",
     "document_lookups"),
    ("How many volunteers do we need on a weekday lunch shift under the current rota policy?", "document_lookups"),
    ("When does the dairy come? Check the supplier agreements.", "document_lookups"),
    ("Min Python for the tools? Check BUILDING.md.", "document_lookups"),
    ("Email Hartwell Produce (orders@hartwell-produce.example) to confirm Friday's order.", "tool_workflows"),
    ("Send a quick email to accounts@coldharbour-dairy.example - say we're returning the butter.", "tool_workflows"),
    ("Put Grace on the weekday lunch shift on 2026-11-26 in the kitchen calendar.", "tool_workflows"),
    ("Book a code review with Ash on 2026-10-21.", "tool_workflows"),
    ("Calendar: playtest with Dee, 2026-10-22.", "tool_workflows"),
    ("Cancel my 1:1 with Priya on 2026-03-06.", "tool_workflows"),
    ("Close maintenance ticket #17 (Extractor fan is noisy) and add a comment that it's fixed.", "tool_workflows"),
    ("Label #232 as needs-repro and assign it to eli.", "tool_workflows"),
    ("Turn inbox/receipts-2026-11-07.txt into receipts/receipts-2026-11-07.csv (CSV: supplier, amount).",
     "structured_transforms"),
    ("Write donations-2026-11-12.json: a JSON object of total donations per donor_type, using records/donations.csv.",
     "structured_transforms"),
    ("Read notes.txt and write dates.txt with every date in it, one per line.", "structured_transforms"),
    # A data file moved, not transformed; a question with nothing that says where the answer lives.
    ("Move levels/incoming/level-2026-10-16.json to levels/archive/, it's been converted.", "other"),
    ("A 10 kg sack of rice makes about 8 portions per kg. How many portions from 3 sacks?", "other"),
])
def test_the_seeded_ways_of_asking_get_their_kind(text, expected):
    assert kinds.classify(kinds.Request(text)).kind == expected


@pytest.mark.parametrize("text", [
    "Write a short poem about autumn.", "What's the capital of France?", "Plan a 3-day trip to Rome.",
    "Explain how TLS works.", "How many ounces are in a pound?", "Draft a thank-you note to my team.",
    "Translate 'good morning' into Spanish.", "What should we do this weekend?", "What time is it in Tokyo now?",
    "Help me write a cover letter.", "What is 15% of 240?", "Add a review section to my essay outline.",
    "Make a list of things to pack for camping.",
])
def test_everyday_requests_stay_other(text):
    assert kinds.classify(kinds.Request(text)).kind == "other"


def test_attachments_apps_and_a_code_folder_are_signals():
    assert kinds.classify(kinds.Request("What does it say about refunds?", attachments=["terms.pdf"])).kind == \
        "document_lookups"
    assert kinds.classify(kinds.Request("Please tidy this up", attachments=["orders.csv"])).kind == \
        "structured_transforms"
    assert kinds.classify(kinds.Request("Make it faster", code_folder=True)).kind == "small_coding"
    assert kinds.classify(kinds.Request("Anything new for me?", apps=["gmail"])).kind == "tool_workflows"


def test_a_short_follow_up_keeps_the_conversations_previous_kind_only_soon_after():
    previous = kinds.Request("and the other one?", previous="document_lookups", previous_at=1000.0)

    assert kinds.classify(previous, now=1000.0 + 60).kind == "document_lookups"
    assert kinds.classify(previous, now=1000.0 + kinds.FOLLOW_UP_SECONDS + 1).kind == "other"
    long = kinds.Request(" ".join(["word"] * 20), previous="document_lookups", previous_at=1000.0)
    assert kinds.classify(long, now=1060.0).kind == "other"


def test_classify_turn_remembers_each_conversations_kind():
    kinds.RECENT.forget("chat-a")
    first = kinds.classify_turn("chat-a", "According to the handbook, how many leave days?", now=500.0)
    follow = kinds.classify_turn("chat-a", "and for part-time staff?", now=530.0)
    other = kinds.classify_turn("chat-b", "and for part-time staff?", now=530.0)

    assert (first.kind, follow.kind, follow.reason) == ("document_lookups", "document_lookups", "follow_up")
    assert other.kind == "other"


def test_a_long_message_cant_win_on_repetition():
    text = "calendar " * 200 + "According to the policy document, what is the limit?"
    result = kinds.classify(kinds.Request(text))

    assert max(result.scores.values()) <= kinds.CAP
