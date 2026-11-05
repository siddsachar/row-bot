"""The stale-skills arms count only trusted changes: a new value the person said, or one the world's own sources gave
(documents, the inbox, app data), never one from the agent's own actions or replies (the user's decision, 26 Oct)."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from row_bot import stale_skills
from tests.fixtures.stale_skills import stale_world

pytestmark = pytest.mark.subsystem

DEEP_CLEAN = ("Use this when Mara asks for the deep clean.\n1. Create one event with `calendar_create_event`: title "
              "`Deep clean`, start 05:30, end 07:30.\n2. Tell Mara it's done.")
BOOKED = "Household deep cleaning session scheduled for November 4, 2026, from 6:00 PM to 8:00 PM."


@pytest.fixture
def world(tmp_path, monkeypatch, reload_for_data_dir):
    found = stale_world(tmp_path, monkeypatch, reload_for_data_dir, arm="R")
    (threads,) = reload_for_data_dir(found.data, "row_bot.threads")
    monkeypatch.setattr(threads, "get_workflow_thread_ids", lambda: set())
    found.threads = threads
    found.skill("deep_clean_procedure", DEEP_CLEAN, display="Deep clean")
    return found


class _Extraction:
    """The default model as memory extraction sees it: it saves what the conversation says about the deep clean
    (only from a conversation with ``only`` in it, when given)."""

    def __init__(self, only: str = "") -> None:
        self.only = only

    def bind(self, **_kwargs):
        return self

    def invoke(self, messages):
        if self.only and self.only not in str(messages[-1].content):
            return AIMessage(content='{"memories": []}')
        return AIMessage(content=json.dumps({"memories": [
            {"category": "event", "subject": "Deep Clean", "content": BOOKED}]}))


def _extract(world, monkeypatch, only: str = "") -> None:
    from row_bot import memory_extraction, models
    from row_bot.providers import background

    model = _Extraction(only)
    monkeypatch.setattr(models, "get_current_model", lambda: "model:openai:gpt-5")
    monkeypatch.setattr(models, "get_llm_for", lambda _ref, num_ctx=None, *, reasoning_plan=None: model)
    monkeypatch.setattr(background, "resolve_reasoning_capabilities_for_ref", lambda _ref: None)
    memory_extraction.run_extraction()


def _conversation(world, thread_id: str, messages: list) -> None:
    world.threads.create_thread(thread_id, thread_id=thread_id, seed_default_skills=False)
    assert world.threads.append_checkpoint_messages(thread_id, messages)


class _Drafts:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str, *, answer=None, max_tokens=None) -> str:
        self.prompts.append(prompt)
        skill = prompt.split("<<<\n", 1)[1].split("\n>>>", 1)[0]
        return json.dumps({"patch": True, "instructions": skill.replace("05:30", "18:00").replace("07:30", "20:00"),
                           "reason": "the time changed"})


def _events(name: str) -> list[dict]:
    return [item for item in stale_skills.operations() if item["event"] == name]


AGENTS_BOOKING = [  # smoke R, 26 Oct: the 9B booked the deep clean at the wrong time; nobody said 18:00
    HumanMessage(id="h1", content="Pop the deep clean in the calendar for 2026-11-04."),
    AIMessage(id="a1", content="", tool_calls=[{"name": "calendar_create_event", "id": "c1", "args": {
        "title": "Deep Clean", "date": "2026-11-04", "start": "18:00", "end": "20:00"}}]),
    ToolMessage(id="t1", name="calendar_create_event", tool_call_id="c1", content=json.dumps({"created": {
        "id": "evt_38", "title": "Deep Clean", "date": "2026-11-04", "start": "18:00", "end": "20:00"}})),
    AIMessage(id="a2", content="Done! I've added Deep Clean for November 4, 2026 from 6:00 PM to 8:00 PM."),
]
PERSON_SAYS = [
    HumanMessage(id="h1", content="From now on the deep clean is 18:00-20:00, not 05:30-07:30."),
    AIMessage(id="a1", content="Got it: the deep clean is 6:00 PM to 8:00 PM from now on."),
]


@pytest.mark.parametrize("messages, drafted", [(AGENTS_BOOKING, False), (PERSON_SAYS, True)],
                         ids=["the agent's own booking", "the person said it"])
def test_r_drafts_a_change_the_person_said_but_never_one_learned_from_the_agents_own_booking(
        world, monkeypatch, messages, drafted):
    from row_bot.stale_skills import arms

    _conversation(world, "deep-clean-run", messages)
    world.at(datetime(2026, 11, 3, 21, 0))
    _extract(world, monkeypatch)
    [write] = [item for item in _events("write") if item.get("subject") == "Deep Clean"]
    assert (write["origin"], write["text"]) == ("thread:deep-clean-run", BOOKED)

    world.at(datetime(2026, 11, 4, 1, 0))
    model = _Drafts()
    result = arms.reconcile(model)
    world.set_arm("D")
    line = arms.date_note("deep_clean_procedure")

    if drafted:
        assert (result["candidates"], result["proposed"]) == (1, 1)
        assert _events("patch_candidate")[0]["fresh"] == ["18:00", "20:00"]
        assert line.endswith("follow the newer memory (6:00 PM, 8:00 PM), not the skill.")
        assert _events("untrusted_source") == []
    else:
        assert (result["candidates"], model.prompts, line) == (0, [], "")
        [untrusted] = _events("untrusted_source")
        assert (untrusted["skill"], untrusted["origin"], untrusted["fresh"]) == (
            "deep_clean_procedure", "thread:deep-clean-run", ["18:00", "20:00"])
        assert _events("patch_candidate") == []


@pytest.mark.parametrize("tool, trusted", [("mail_read", True), ("search_memory", False)],
                         ids=["read from the inbox", "read back from memory"])
def test_a_value_the_agent_read_counts_only_from_the_world_never_from_its_own_memory(world, monkeypatch, tool,
                                                                                    trusted):
    from row_bot import agent
    from row_bot.stale_skills import arms

    _conversation(world, "deep-clean-letter", [
        HumanMessage(id="h1", content="Did anything change about the deep clean?"),
        AIMessage(id="a1", content="", tool_calls=[{"name": tool, "id": "c1", "args": {"query": "deep clean"}}]),
        ToolMessage(id="t1", name=tool, tool_call_id="c1", content="The deep clean moves to 18:00-20:00 from today."),
    ])
    world.at(datetime(2026, 11, 3, 10, 0))
    # The agent saves it during its turn: the write comes from the turn's conversation.
    monkeypatch.setattr(agent, "get_current_thread_id", lambda: "deep-clean-letter")
    world.kg.save_entity("event", "Deep Clean", "The deep clean is 18:00-20:00 from 3 November.")
    monkeypatch.setattr(agent, "get_current_thread_id", lambda: "")

    world.at(datetime(2026, 11, 4, 1, 0))
    result = arms.reconcile(_Drafts())

    assert result["proposed"] == (1 if trusted else 0)
    assert len(_events("untrusted_source")) == (0 if trusted else 1)


def test_a_document_or_the_persons_own_edit_outside_any_conversation_is_trusted(world):
    from row_bot.stale_skills import arms

    world.at(datetime(2026, 11, 3, 10, 0))
    with stale_skills.origin("document:cleaning-rota"):
        world.kg.save_entity("event", "Deep Clean", "The deep clean is 18:00-20:00 from 3 November.")
    world.at(datetime(2026, 11, 4, 1, 0))
    assert arms.reconcile(_Drafts())["proposed"] == 1

    world.kg.save_entity("organisation", "Sandgate Bakery", "Orders go to hello@sandgate-bakery.example.")
    world.skill("bread_order_procedure", "Send the bread order to hello@sandgate-bakery.example.", display="Bread")
    world.at(datetime(2026, 11, 5, 10, 0))
    [bakery] = [item for item in world.kg.list_entities() if item["subject"] == "Sandgate Bakery"]
    world.kg.update_entity(bakery["id"], "Orders go to orders@sandgate-bakery.example now.")  # the Memory page
    world.at(datetime(2026, 11, 6, 1, 0))
    assert arms.reconcile(_Drafts())["candidates"] == 1
    assert [item["origin"] for item in _events("supersede") if item.get("subject") == "Sandgate Bakery"] == [""]


def test_a_value_read_back_from_the_world_in_a_later_chat_is_still_the_agents_own(world, monkeypatch):
    """The agent booked the wrong time in one chat; a later chat reads it back from the calendar and memory keeps it:
    the calendar only echoes the agent's booking, so neither R nor D acts on it (the user's decision, 26 Oct)."""

    from row_bot.stale_skills import arms

    _conversation(world, "deep-clean-run", AGENTS_BOOKING)
    _conversation(world, "deep-clean-check", [
        HumanMessage(id="h1", content="Check the calendar: when is the deep clean on the 4th?"),
        AIMessage(id="a1", content="", tool_calls=[{"name": "calendar_list_events", "id": "c1",
                                                     "args": {"date": "2026-11-04"}}]),
        ToolMessage(id="t1", name="calendar_list_events", tool_call_id="c1", content=json.dumps({"events": [{
            "id": "evt_38", "title": "Deep Clean", "date": "2026-11-04", "start": "18:00", "end": "20:00"}]})),
        AIMessage(id="a2", content="The deep clean is on 4 November from 6:00 PM to 8:00 PM."),
    ])
    world.at(datetime(2026, 11, 3, 21, 0))
    _extract(world, monkeypatch, only="Check the calendar")
    [write] = [item for item in _events("write") if item.get("subject") == "Deep Clean"]
    assert write["origin"] == "thread:deep-clean-check"

    world.at(datetime(2026, 11, 4, 1, 0))
    model = _Drafts()
    result = arms.reconcile(model)
    world.set_arm("D")

    assert (result["candidates"], model.prompts, arms.date_note("deep_clean_procedure")) == (0, [], "")
    [untrusted] = _events("untrusted_source")
    assert (untrusted["origin"], untrusted["fresh"]) == ("thread:deep-clean-check", ["18:00", "20:00"])
    assert _events("patch_candidate") == []
