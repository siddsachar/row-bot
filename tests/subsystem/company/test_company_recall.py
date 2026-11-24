"""The company brain in a chat (B3): the ``company_brain_search`` tool, offered only while a company is joined and
switched off by its policy, answering with citations within its bounds; and a turn's recall with the company's
hits added, labelled apart from the person's own notes, deduplicated, within its budget and deadline, and
skipped when locked down. With no company joined nothing is asked. The server is a fake (``httpx.MockTransport``).
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import httpx
import pytest

from row_bot import turn_context
from row_bot.application import client_company
from row_bot.company import brain_tool, documents, enforce, policy, recall
from row_bot.memory_policy import RECALL_BLOCK_HEADER, format_recall_block
from row_bot.tools import registry
from tests.fixtures.company_server import ACCOUNTS, COMPANY_SPACE, HANDBOOK, RENEWAL_FACT, SALES_SPACE

pytestmark = [pytest.mark.subsystem]

QUESTION = "How many holiday days do we get?"
MINE = [{"id": "m1", "category": "preference", "subject": "Holidays", "content": "I like to take holidays in June."}]


def own(memories: list[dict]) -> str:
    """The person's own recall block, as the turn's recall writes it."""
    return format_recall_block(memories, query=QUESTION)


def _recalls(server, since: int) -> list[httpx.Request]:
    return [request for request in server.requests[since:] if request.url.path == recall.RECALL_PATH]


def _hit(number: int, excerpt: str, *, space=(SALES_SPACE, "Sales"), chunk: int | None = None) -> dict:
    return {"hit": {"score": 0.5}, "citation": {
        "type": "document", "space": {"id": space[0], "name": space[1]},
        "document": {"id": f"d0000000-0000-4000-8000-0000000001{number:02d}", "title": f"Notes {number}",
                     "file_name": f"notes-{number}.md"}, "chunk": number if chunk is None else chunk,
        "excerpt": excerpt}}


# -- no company -------------------------------------------------------------------------------------

def test_with_no_company_there_is_no_tool_no_recall_and_nothing_is_asked(company_server):
    brain_tool.sync()
    assert registry.get_tool(recall.GROUP) is None
    assert recall.GROUP not in [tool.name for tool in registry.get_enabled_tools()]
    # A registration left from an earlier join is never offered either.
    registry.register(brain_tool.TOOL)
    assert registry.is_enabled(recall.GROUP) is False

    memories = list(MINE)
    merged, block = recall.merge(QUESTION, "selected", memories, own)
    assert merged is memories and block is own
    assert recall.tool_text("holidays") == recall.UNAVAILABLE["not_joined"]
    assert documents.overview() is None and documents.upload(SALES_SPACE, "notes.txt", b"Notes") is None
    assert company_server.requests == []


# -- the tool ---------------------------------------------------------------------------------------

def test_the_tool_is_offered_only_while_joined_and_the_company_can_switch_it_off(company_server):
    company_server.join_here()  # Its first policy registers the tool.
    assert registry.get_tool(recall.GROUP) is brain_tool.TOOL
    assert registry.is_enabled(recall.GROUP) is True
    (search,) = brain_tool.TOOL.as_langchain_tools()
    assert search.name == recall.TOOL and recall.TOOL in registry.get_untrusted_tool_names()
    registry.unregister(recall.GROUP)
    brain_tool.sync()  # As at start-up while joined.
    assert registry.get_tool(recall.GROUP) is brain_tool.TOOL

    company_server.policy["tools"]["disabled_groups"] = [recall.GROUP]
    policy.refresh()
    assert registry.is_enabled(recall.GROUP) is False
    assert enforce.tool_refusal(recall.TOOL, parent=recall.GROUP) is not None
    since = len(company_server.requests)
    memories = list(MINE)
    assert recall.merge(QUESTION, "selected", memories, own) == (memories, own)
    assert _recalls(company_server, since) == []

    company_server.policy["tools"].update(disabled_groups=[], disabled_tools=[recall.TOOL])
    policy.refresh()
    assert registry.is_enabled(recall.GROUP) is True  # The group is on; its one tool isn't.
    assert enforce.tool_off(recall.TOOL, parent=recall.GROUP)
    assert recall.merge(QUESTION, "selected", memories, own) == (memories, own)
    assert _recalls(company_server, since) == []

    client_company.leave()
    assert registry.get_tool(recall.GROUP) is None


def test_the_tools_answer_cites_each_space_and_document(company_server):
    company_server.join_here()
    since = len(company_server.requests)

    text = search_tool()("holiday policy")

    (asked,) = _recalls(company_server, since)
    assert asked.headers["authorization"].startswith("Bearer rboa_") and "cookie" not in asked.headers
    assert company_server.recall_bodies == [{"query": "holiday policy", "k": recall.TOOL_HITS, "spaces": None}]
    assert text == (
        'Company brain: 3 results for "holiday policy". Say which space and document an answer comes from.\n\n'
        "[1] From Company: Staff handbook\nEveryone gets 25 days of holiday a year, plus public holidays.\n\n"
        "[2] From Sales: a fact (from Accounts 2026)\nOrion Foods renews its contract every March.\n\n"
        "[3] From Sales: Accounts 2026\nOrion Foods is our largest customer in the north.")

    # Spaces by name or id, whatever the case; one the person can't read is named, and nothing is searched.
    by_name = search_tool()("orion", ["sales"])
    assert company_server.recall_bodies[-1]["spaces"] == [SALES_SPACE]
    assert "Staff handbook" not in by_name and "[2] From Sales: Accounts 2026" in by_name
    search_tool()("orion", [COMPANY_SPACE])
    assert company_server.recall_bodies[-1]["spaces"] == [COMPANY_SPACE]
    asked_before = len(company_server.recall_bodies)
    assert search_tool()("orion", ["Marketing"]) == (
        'There is no space the person can read named "Marketing". The spaces they can read: Company, Sales.')
    assert len(company_server.recall_bodies) == asked_before

    company_server.recall_search = "text"
    assert recall.TEXT_ONLY in search_tool()("holiday policy")
    company_server.brain_hits = []
    assert search_tool()("parking") == f'The company brain has nothing for "parking".\n{recall.TEXT_ONLY}'


def search_tool():
    (tool,) = brain_tool.TOOL.as_langchain_tools()
    return lambda query, spaces=None: tool.invoke({"query": query, "spaces": spaces})


def test_the_tools_answer_is_bounded(company_server, monkeypatch):
    company_server.join_here()
    company_server.brain_hits = [_hit(number, f"Part {number}: " + "word " * 400) for number in range(20)]

    text = recall.tool_text("everything")

    assert company_server.recall_bodies[-1]["k"] == recall.TOOL_HITS
    assert [line for line in text.splitlines() if line.startswith("[")] == [
        f"[{number + 1}] From Sales: Notes {number}" for number in range(recall.TOOL_HITS)]
    assert all(len(line) <= recall.MAX_EXCERPT for line in text.splitlines())  # Each excerpt is cut.
    assert len(text) <= recall.MAX_TOOL_CHARS

    monkeypatch.setattr(recall, "MAX_TOOL_CHARS", 1000)
    short = recall.tool_text("everything")
    assert len(short) <= 1000 and short.endswith("more not shown.)")


def test_the_tool_says_plainly_why_the_company_brain_cant_be_searched(company_server):
    record = company_server.join_here()
    assert recall.tool_text("   ") == recall.UNAVAILABLE["invalid_query"]

    company_server.offline = True
    assert recall.tool_text("holidays") == recall.UNAVAILABLE["unreachable"]
    company_server.offline = False

    spaces, company_server.brain_spaces = company_server.brain_spaces, None  # A server without the brain.
    assert recall.tool_text("holidays") == recall.UNAVAILABLE["no_brain"]
    company_server.brain_spaces = spaces

    company_server.brain_answers.append(("POST", httpx.Response(
        429, json={"error": "too_many_requests"}, headers={"Retry-After": "30"})))
    assert recall.tool_text("holidays") == "The company's server asked Row-Bot to wait 30 seconds. Try again after that."

    company_server.revoke(record.device_id)
    monkeypatch_backoff()
    assert recall.tool_text("holidays") == recall.UNAVAILABLE["disconnected"]


def monkeypatch_backoff() -> None:
    """The 429's wait is over (the fixture puts the client's backoff back after each test)."""
    from row_bot.company import client

    client._backoff_until = 0.0


# -- a turn's recall --------------------------------------------------------------------------------

def test_a_turns_recall_adds_the_company_hits_labelled_apart_from_the_persons_notes(company_server):
    company_server.join_here()
    since = len(company_server.requests)

    memories, block = recall.merge(QUESTION, "selected", list(MINE), own)

    (asked,) = _recalls(company_server, since)
    assert asked.headers["authorization"].startswith("Bearer rboa_")
    assert company_server.recall_bodies == [{"query": QUESTION, "k": recall.TURN_HITS, "spaces": None}]
    ids = ["m1", f"company:document:{HANDBOOK}#2", f"company:fact:{RENEWAL_FACT}", f"company:document:{ACCOUNTS}#0"]
    assert [memory["id"] for memory in memories] == ids
    text = block(memories)
    assert text.startswith(RECALL_BLOCK_HEADER + "Your notes:\n- [id=m1] [preference] Holidays: I like to take")
    assert text.index("Your notes:") < text.index(recall.COMPANY_INTRO) < text.index('<EXTERNAL_CONTENT source="company_brain">')
    assert recall.COMPANY_NOTE in text
    assert "- Company brain: Company · Staff handbook: Everyone gets 25 days of holiday a year" in text
    assert "- Company brain: Sales · Accounts 2026: Orion Foods renews its contract every March." in text
    # Only the person's notes (the company's shown in an earlier turn): exactly their own block.
    assert block(memories[:1]) == own(memories[:1])
    assert "Your notes:" not in block(memories[1:]) and block(memories[1:]).startswith(RECALL_BLOCK_HEADER)

    # In the turn's context: shown once, then remembered by id like the person's own.
    values = turn_context.TurnContextValues(now=datetime(2026, 11, 24, 9, tzinfo=timezone.utc),
                                            memories=tuple(memories), recall_block=block)
    shown, data = turn_context.render(values, turn_context.Baseline())
    assert "Company brain: Company · Staff handbook" in shown and data["memory_ids"] == ids
    again, data = turn_context.render(values, turn_context.Baseline(memory_ids=frozenset(ids), full=False))
    assert "Company brain" not in again and data["memory_ids"] == []

    # No memories of the person's own: the company's alone.
    memories, block = recall.merge(QUESTION, "no_memories", [], None)
    assert [memory["id"] for memory in memories] == ids[1:]
    assert block(memories).startswith(RECALL_BLOCK_HEADER + recall.COMPANY_INTRO)


def test_a_turns_recall_is_deduplicated_and_kept_to_its_budget(company_server):
    company_server.join_here()
    same = "Orion Foods is our largest customer in the north."
    company_server.brain_hits = [
        _hit(1, same), _hit(1, same),  # The same part twice.
        _hit(2, "I like to take holidays in June…"),  # What the person's own memory says.
        _hit(3, "  ORION foods is our largest customer in the north. "),  # What another hit says.
        _hit(4, "Orion Foods pays within 30 days."), _hit(5, "Orion Foods has three sites."),
        _hit(6, "Orion Foods asked for a discount."),
    ]

    memories, _ = recall.merge("Tell me about Orion Foods", "selected", list(MINE), own)

    assert company_server.recall_bodies[-1]["k"] == recall.TURN_HITS
    assert [memory["content"] for memory in memories] == [MINE[0]["content"], same]
    company_server.brain_hits = [_hit(number, f"Fact number {number} about Orion.") for number in range(10)]
    memories, _ = recall.merge("Tell me about Orion Foods", "selected", [], None)
    assert len(memories) == recall.TURN_HITS


def test_a_slow_company_server_never_holds_a_turn_up_past_its_deadline(company_server, monkeypatch):
    company_server.join_here()
    monkeypatch.setattr(recall, "TURN_SECONDS", 0.2)
    company_server.recall_hold = threading.Event()
    memories = list(MINE)
    try:
        started = time.monotonic()
        merged, block = recall.merge(QUESTION, "selected", memories, own)
        took = time.monotonic() - started
    finally:
        company_server.recall_hold.set()
    assert merged is memories and block is own
    assert took < 2.0


def test_a_turns_recall_asks_nothing_for_a_greeting_offline_or_while_locked_down(company_server):
    company_server.policy["offline"]["max_days"] = 0
    company_server.join_here()
    memories = list(MINE)
    since = len(company_server.requests)
    assert recall.merge("hi!", "greeting_only", memories, own) == (memories, own)
    assert recall.merge("   ", "selected", memories, own) == (memories, own)
    assert _recalls(company_server, since) == []

    company_server.offline = True
    assert recall.merge(QUESTION, "selected", memories, own) == (memories, own)  # Unreachable: as it was.
    assert policy.refresh().state == "locked_down"  # A failed refresh with no offline days.
    company_server.offline = False
    since = len(company_server.requests)

    assert recall.merge(QUESTION, "selected", memories, own) == (memories, own)
    assert recall.tool_text("holidays") == recall.UNAVAILABLE["locked_down"]
    assert registry.is_enabled(recall.GROUP) is False  # Not offered while locked down.
    assert company_server.requests[since:] == []
