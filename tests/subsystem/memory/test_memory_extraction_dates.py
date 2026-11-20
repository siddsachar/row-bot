"""Extraction dates a conversation by when it was said, not by the day it is read (P1 fix 3).

The extractor worked out relative dates from "Today is <the run's date>", and a local model got
weekdays wrong ("10 July 2023 (last Friday)" from a Saturday 15 July session). Each turn's context
now keeps when the turn began; extraction states the conversation's own date and weekday, with the
weeks either side by weekday, after its fixed instructions (their cached prefix stays the same day
to day), and reconcile's "Today is" is that date too.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage

pytestmark = pytest.mark.subsystem

SATURDAY = datetime(2023, 7, 15, 11, 30, tzinfo=timezone(timedelta(hours=1)))


class _Model:
    """The default chat model as extraction sees it. It reads a relative date off the prompt's lists."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.reconciled: list[str] = []

    def bind(self, **_kwargs):
        return self

    def invoke(self, messages):
        prompt = str(messages[-1].content)
        if "CONVERSATION:\n" not in prompt:
            self.reconciled.append(prompt)
            return AIMessage(content=json.dumps({"decisions": [
                {"fact": 1, "action": "add", "memory_id": "", "subject": "Council meeting", "text": ""}]}))
        self.prompts.append(prompt)
        if "last Friday" not in prompt.split("CONVERSATION:\n", 1)[1]:
            return AIMessage(content='{"memories": []}')
        week = re.search(r"The week before it: (.+?) (\d{4})\.", prompt)
        friday = next(day for day in week.group(1).split(", ") if day.startswith("Friday"))
        return AIMessage(content=json.dumps({"memories": [{
            "category": "event", "subject": "Council meeting",
            "content": f"The user attended an adoption council meeting on {friday.removeprefix('Friday ')} {week.group(2)}",
        }]}))


@pytest.fixture
def stack(reload_for_data_dir, tmp_path, monkeypatch):
    data = tmp_path / "data"
    (kg,) = reload_for_data_dir(data, "row_bot.knowledge_graph")
    kg._skip_reindex = True
    monkeypatch.setattr(kg, "semantic_search", lambda *_args, **_kwargs: [])
    threads, _memory, _evolution, extraction = reload_for_data_dir(
        data, "row_bot.threads", "row_bot.memory", "row_bot.memory_evolution", "row_bot.memory_extraction")
    from row_bot import models
    from row_bot.providers import background

    model = _Model()
    monkeypatch.setattr(models, "get_current_model", lambda: "model:openai:gpt-5")
    monkeypatch.setattr(models, "get_llm_for", lambda _ref, num_ctx=None, *, reasoning_plan=None: model)
    monkeypatch.setattr(background, "resolve_reasoning_capabilities_for_ref", lambda _ref: None)
    monkeypatch.setattr(threads, "get_workflow_thread_ids", lambda: set())
    return threads, extraction, kg, model


def _turn(turn_id: str, person: str, answer: str, when: datetime) -> list:
    """One turn as the agent stores it: the person's message, the turn's context, the answer."""
    from row_bot import turn_context

    context = turn_context.build_message(
        turn_id=turn_id, values=turn_context.TurnContextValues(now=when),
        baseline=turn_context.Baseline(), summary_marker="")
    return [HumanMessage(id=f"{turn_id}-h", content=person), context, AIMessage(id=f"{turn_id}-a", content=answer)]


def _conversation(threads, thread_id: str, messages: list) -> None:
    threads.create_thread(thread_id, thread_id=thread_id, seed_default_skills=False)
    assert threads.append_checkpoint_messages(thread_id, messages)


def test_last_friday_in_a_conversation_dated_saturday_15_july_2023_is_saved_as_14_july(stack) -> None:
    threads, extraction, kg, model = stack
    _conversation(threads, "council", _turn(
        "t1", "I went to the adoption council meeting last Friday.", "How did it go?", SATURDAY))

    extraction.run_extraction()

    assert len(model.prompts) == 1
    prompt = model.prompts[0]
    assert prompt.count("This conversation is dated") == 1  # its own date, never the day it is read
    assert "This conversation is dated Saturday, 15 July 2023." in prompt
    assert "Friday 14 July" in prompt.split("The week before it:", 1)[1].split("\n", 1)[0]
    saved = kg.find_by_subject(None, "Council meeting")
    assert saved is not None and "14 July 2023" in saved["description"]


def test_the_date_comes_after_the_instructions_so_their_prefix_is_the_same_every_day(stack) -> None:
    _threads, extraction, _kg, model = stack

    extraction._extract_from_conversation("User: I bake bread.", dated=date(2023, 7, 15))
    extraction._extract_from_conversation("User: I bake bread.", dated=date(2026, 11, 15))

    first, second = (prompt.split("This conversation is dated", 1) for prompt in model.prompts)
    assert first[0] == second[0]
    assert first[0].rstrip().endswith("]")  # the instructions and their example, then the date
    assert second[1].startswith(" Sunday, 15 November 2026.")
    # Weekdays across a year end carry their own year.
    extraction._extract_from_conversation("User: hello", dated=date(2024, 1, 2))
    assert "The week before it: Tuesday 26 December 2023, Wednesday 27 December 2023" in model.prompts[-1]


def test_each_days_messages_are_read_with_that_days_date(stack) -> None:
    threads, extraction, _kg, model = stack
    _conversation(threads, "two-days", [
        *_turn("t1", "My sister Ana arrives tomorrow.", "Lovely!", SATURDAY),
        *_turn("t2", "Ana arrived yesterday and we went hiking.", "Sounds fun.", SATURDAY + timedelta(days=2)),
    ])

    extraction.run_extraction()

    assert len(model.prompts) == 2
    assert "dated Saturday, 15 July 2023." in model.prompts[0] and "arrives tomorrow" in model.prompts[0]
    assert "Ana arrived yesterday" not in model.prompts[0]
    assert "dated Monday, 17 July 2023." in model.prompts[1] and "Ana arrived yesterday" in model.prompts[1]


def test_a_conversation_whose_turns_carry_no_time_is_dated_when_it_last_changed(stack) -> None:
    threads, extraction, _kg, model = stack
    _conversation(threads, "imported", [
        HumanMessage(id="h1", content="I went to the adoption council meeting last Friday."),
        AIMessage(id="a1", content="How did it go?"),
    ])
    with closing(sqlite3.connect(threads.DB_PATH)) as connection, connection:
        connection.execute("UPDATE thread_meta SET created_at = ?, updated_at = ? WHERE thread_id = ?",
                           (SATURDAY.isoformat(), SATURDAY.isoformat(), "imported"))

    extraction.run_extraction()

    assert "This conversation is dated Saturday, 15 July 2023." in model.prompts[0]


def test_reconcile_judges_new_facts_on_the_conversations_date_not_todays(stack) -> None:
    threads, extraction, kg, model = stack
    from row_bot import memory

    memory.save_memory("event", "Council meeting", "The user is going to council meetings about adoption")
    _conversation(threads, "council", _turn(
        "t1", "I went to the adoption council meeting last Friday.", "How did it go?", SATURDAY))

    extraction.run_extraction()

    assert len(model.reconciled) == 1
    assert "Today is 2023-07-15." in model.reconciled[0]
