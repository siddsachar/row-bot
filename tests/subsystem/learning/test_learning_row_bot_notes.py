"""Row-Bot's own notes to the model are never the person's words.

From 5.3.1 the agent can end a request with a human-role note after silent tool rounds (the progress
nudge, ``additional_kwargs.row_bot_note``). Capture keeps it exactly as sent, and replay prefixes and
training rows keep it (the personal model is served with it too), but what the person said reads past it:
their newest message, next-message corrections, a vault task's text and where a training row's targets
start are the same as for the same work without the note."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, message_to_dict

from row_bot.learning import capture, corrections, evaluation
from row_bot.learning.store import LearningStore
from row_bot.learning.training import dataset, recipes

pytestmark = [pytest.mark.subsystem]

HOUR = 3600.0
NOTE = ("Row-Bot note, not from the person: you've worked through several tools without a word to the person. "
        "Before your next step, say in one short line what you found and what's next.")
TOOLS = [{"type": "function", "function": {
    "name": "calculate", "description": "Evaluate a math expression.",
    "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}}]
ASK = "Add up the March invoices."


def _note() -> HumanMessage:
    return HumanMessage(content=NOTE, additional_kwargs={"ephemeral": True, "row_bot_note": "progress"})


def _turn(writer, chats, clock, thread_id: str, question: str, answer: str, *, history: list | None = None,
          steps: int = 2, note_from: int | None = None, person_message: bool = False) -> tuple[list[str], list]:
    """One turn through Capture's seams: ``steps`` calculator calls, then the answer. From request number
    ``note_from`` on (0 is the first) each request ends with the note, as the agent sends it. Returns its
    model calls' decision ids and the conversation it leaves (the note is never part of it)."""

    if thread_id not in chats.threads:
        chats.chat(thread_id)
    turn = writer.begin_turn(thread_id=thread_id, surface="normal_chat", person_message=person_message)
    kept = [*(history or []), HumanMessage(content=question, id=f"h-{thread_id}-{len(history or [])}")]
    ids: list[str] = []
    for number in range(steps + 1):
        sent = [SystemMessage(content="You are Row-Bot.", id="sys"), *kept]
        if note_from is not None and number >= note_from:
            sent.append(_note())
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


def test_the_persons_words_read_past_a_note_at_the_end_of_a_request(writer, chats, learning_on, clock):
    plain, _ = _turn(writer, chats, clock, "chat-1", ASK, "It's 3.")
    noted, _ = _turn(writer, chats, clock, "chat-2", ASK, "It's 3.", note_from=1)
    store = LearningStore(learning_on, clock=clock)

    def request(decision_id: str):
        return capture.read_request(store, store.get("decision", decision_id))

    # The second and third requests' only new human message is the note: no new person message.
    assert [corrections.person_texts(request(item)) for item in noted] == [[ASK]] * 3
    assert [corrections.person_text(request(item)) for item in noted] == [
        corrections.person_text(request(item)) for item in plain]
    assert request(noted[-1])["messages"][-1] == message_to_dict(_note())  # captured exactly as sent


def test_a_note_never_changes_whether_the_next_message_corrected_the_reply(writer, chats, learning_on, clock):
    replies: dict[str, str] = {}
    for thread_id, follow_up, note_from in (
        ("fix-plain", "No, use the 2026 agreement.", None), ("fix-noted", "No, use the 2026 agreement.", 0),
        ("next-plain", "Thanks! Now list the suppliers.", None), ("next-noted", "Thanks! Now list the suppliers.", 0),
    ):
        ids, kept = _turn(writer, chats, clock, thread_id, "What is the fee?", "It's 70 EUR.", steps=0,
                          person_message=True)
        replies[thread_id] = ids[-1]
        # The note ends even the follow-up's first request here: the person's words are read the same.
        _turn(writer, chats, clock, thread_id, follow_up, "Done.", history=kept, steps=1, note_from=note_from,
              person_message=True)
    store = LearningStore(learning_on, clock=clock)
    clock.advance(HOUR)

    report = corrections.confirm(store)

    assert report.considered == 4
    assert set(report.confirmed) == {replies["fix-plain"], replies["fix-noted"]}


def test_a_vault_tasks_text_is_the_persons_words_and_its_prefix_keeps_the_note(writer, chats, learning_on, clock,
                                                                               monkeypatch):
    monkeypatch.setattr(evaluation.audit, "HOLD_EVERY", 1)  # every decision goes to the vault
    _turn(writer, chats, clock, "chat-1", ASK, "It's 3.")
    _turn(writer, chats, clock, "chat-2", ASK, "It's 3.", note_from=1)
    store = LearningStore(learning_on, clock=clock)
    clock.advance(HOUR)

    evaluation.refresh_audit(store=store, asked_by="test")

    with closing(sqlite3.connect(learning_on / "learning" / "audit.db")) as connection:
        held = [json.loads(content) for (content,) in connection.execute("SELECT content FROM tasks").fetchall()]
    assert len(held) == 6 and {task["text"] for task in held} == {ASK}
    assert sum(task["messages"][-1] == message_to_dict(_note()) for task in held) == 2  # replayed as it was sent


def test_a_training_rows_targets_start_after_the_persons_message_and_the_note_stays(writer, chats, learning_on,
                                                                                     clock):
    _turn(writer, chats, clock, "chat-1", ASK, "It's 3.")
    _turn(writer, chats, clock, "chat-2", ASK, "It's 3.", note_from=1)
    store = LearningStore(learning_on, clock=clock)
    clock.advance(HOUR)

    compiled = dataset.compile_dataset(store, recipes.load("16gb"), lookup=chats, refresh_vault=False,
                                       shape="captured")

    rows = {item.thread_id: item.row["messages"] for item in (*compiled.train, *compiled.validation)}
    plain, noted = rows["chat-1"], rows["chat-2"]
    assert [message["content"] for message in noted if message["role"] == "user"] == [ASK, NOTE]
    weights = [[message.get("weight") for message in row if message["role"] == "assistant"] for row in (plain, noted)]
    assert weights == [[1, 1, 1], [1, 1, 1]]  # both tool steps and the answer are targets
