"""Row-Bot's own prompts are never the person's words.

A turn Row-Bot starts itself (its carry-on after a reply that says work is still under way, a goal's next
step, a file check) begins with a human-role prompt marked ``platform_note``, as do a message linked from
another chat, a page's decisions and a delegated agent's task. Capture never takes such a turn as the
person's. What the person said reads past these prompts too: their newest message, the opening a
next-message correction is judged on, and a vault task's text. A training row keeps the prompt as sent and
its targets still start after it."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from row_bot.learning import capture, corrections, evaluation
from row_bot.learning.store import LearningStore
from row_bot.learning.training import dataset, recipes

pytestmark = [pytest.mark.subsystem]

HOUR = 3600.0
TOOLS = [{"type": "function", "function": {
    "name": "calculate", "description": "Evaluate a math expression.",
    "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}}]
ASK = "Add up the March invoices."
CARRY_ON = "Carry on with the next step you said you would take."


def _carry_on() -> HumanMessage:
    """Row-Bot's carry-on prompt as the server sends it: the chat shows the note, the model reads the prompt."""

    return HumanMessage(content=CARRY_ON, additional_kwargs={
        "platform_public_content": "Carrying on with the next step", "platform_note": "continuation"})


def _turn(writer, chats, clock, thread_id: str, opening: HumanMessage, answer: str, *, history: list | None = None,
          steps: int = 1, person_message: bool = True) -> tuple[list[str], list]:
    """One turn through Capture's seams, opened by ``opening``: ``steps`` calculator calls, then the answer.
    Returns its model calls' decision ids and the conversation it leaves."""

    if thread_id not in chats.threads:
        chats.chat(thread_id)
    turn = writer.begin_turn(thread_id=thread_id, surface="normal_chat", person_message=person_message)
    kept = [*(history or []), opening]
    ids: list[str] = []
    for number in range(steps + 1):
        sent = [SystemMessage(content="You are Row-Bot.", id="sys"), *kept]
        mark = f"{thread_id}-{len(kept)}"
        if number < steps:
            call = {"id": f"c-{mark}", "name": "calculate", "args": {"expression": f"{number} + 1"}}
            step = AIMessage(content="", id=f"a-{mark}", tool_calls=[call])
            ids.append(writer.model_call(turn, messages=sent, response=step, tools=TOOLS, model_ref="gpt-5.6-sol",
                                         provider_id="openai"))
            result = ToolMessage(content=str(number + 1), tool_call_id=call["id"], id=f"t-{mark}")
            writer.tool_call(turn, call=call, result=result)
            kept += [step, result]
        else:
            reply = AIMessage(content=answer, id=f"r-{mark}")
            ids.append(writer.model_call(turn, messages=sent, response=reply, tools=TOOLS, model_ref="gpt-5.6-sol",
                                         provider_id="openai"))
            kept.append(reply)
    writer.end_turn(turn, "completed")
    writer.drain()
    clock.advance(60)
    return ids, kept


def _asked(thread_id: str, text: str = ASK, number: int = 0) -> HumanMessage:
    return HumanMessage(content=text, id=f"h-{thread_id}-{number}")


def test_the_persons_words_read_past_row_bot_prompts(writer, chats, learning_on, clock):
    _ids, kept = _turn(writer, chats, clock, "chat-1", _asked("chat-1"), "I'm adding them up now.", steps=0)
    carried, _ = _turn(writer, chats, clock, "chat-1", _carry_on(), "It's 3.", history=kept, person_message=False)
    store = LearningStore(learning_on, clock=clock)

    requests = [capture.read_request(store, store.get("decision", item)) for item in carried]

    assert [corrections.person_texts(request) for request in requests] == [[ASK], [ASK]]
    assert [corrections.person_text(request) for request in requests] == [ASK, ASK]
    assert corrections.opening_texts(requests[0]) == [ASK]  # the request the work Row-Bot carried on continues


def test_a_turn_row_bot_carried_on_reads_as_the_persons_request_it_continues(writer, chats, learning_on, clock):
    _ids, kept = _turn(writer, chats, clock, "chat-1", _asked("chat-1"), "I'm adding them up now.", steps=0)
    _turn(writer, chats, clock, "chat-1", _carry_on(), "It's 3.", history=kept, person_message=False)
    store = LearningStore(learning_on, clock=clock)

    book = corrections.Book(store)

    # Both episodes are described (in the diagnosis, by kind of work) by what the person asked.
    assert sorted(book.text_of(item) for item in book.episodes.values()) == [ASK, ASK]


def test_a_vault_tasks_text_is_the_persons_request_not_row_bot_prompt(writer, chats, learning_on, clock,
                                                                       monkeypatch):
    monkeypatch.setattr(evaluation.audit, "HOLD_EVERY", 1)  # every decision goes to the vault
    _ids, kept = _turn(writer, chats, clock, "chat-1", _asked("chat-1"), "I'm adding them up now.", steps=0)
    _turn(writer, chats, clock, "chat-1", _carry_on(), "It's 3.", history=kept, person_message=False)
    store = LearningStore(learning_on, clock=clock)
    clock.advance(HOUR)

    evaluation.refresh_audit(store=store, asked_by="test")

    with closing(sqlite3.connect(learning_on / "learning" / "audit.db")) as connection:
        held = [json.loads(content) for (content,) in connection.execute("SELECT content FROM tasks").fetchall()]
    assert held and {task["text"] for task in held} == {ASK}


def test_a_carry_on_rows_targets_start_after_row_bot_prompt_which_stays(writer, chats, learning_on, clock):
    _ids, kept = _turn(writer, chats, clock, "chat-1", _asked("chat-1"), "I'm adding them up now.", steps=0)
    _turn(writer, chats, clock, "chat-1", _carry_on(), "It's 3.", history=kept, person_message=False)
    store = LearningStore(learning_on, clock=clock)
    clock.advance(HOUR)

    compiled = dataset.compile_dataset(store, recipes.load("16gb"), lookup=chats, refresh_vault=False,
                                       shape="captured")

    rows = [item.row["messages"] for item in (*compiled.train, *compiled.validation)]
    carried = next(row for row in rows if any(message["content"] == CARRY_ON for message in row))
    assert [message["content"] for message in carried if message["role"] == "user"] == [ASK, CARRY_ON]
    # The earlier reply is history; the carry-on's tool step and answer are its targets.
    assert [message.get("weight") for message in carried if message["role"] == "assistant"] == [0, 1, 1]
