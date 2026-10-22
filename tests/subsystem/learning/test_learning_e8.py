"""E8: the approval reviewer never sees anything Learning wrote. With Learning on and data recorded
(captured turns, a Teach note, a lesson), what the reviewer is sent is exactly what it is sent with
Learning off, and none of Learning's own text is in it."""

from __future__ import annotations

import json
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from row_bot import approval_review, threads
from row_bot.learning.store import LearningStore
from tests.fixtures.learning import lesson, save_learning_settings, signal

pytestmark = pytest.mark.subsystem

CANARY_TOOL = "CANARY-tool-output-7f3a"
CANARY_REPLY = "CANARY-model-reply-91bc"
CANARY_NOTE = "CANARY-teach-note-55de"
CANARY_LESSON = "CANARY-lesson-text-0c21"


def _reviewed(thread_id: str) -> list[list[dict]]:
    sent: list[list[dict]] = []

    def reviewer(messages, runtime):
        sent.append(messages)
        return '{"decision": "ask", "reason": "Check with the person."}'

    verdict = approval_review.review(
        {"tool": "shell", "label": "Run a command", "description": "Remove ~/Downloads/tmp"},
        risks=["deletes files"],
        runtime={"thread_id": thread_id, "runtime_surface": "normal_chat"},
        call=reviewer,
    )
    assert verdict.decision == "ask"
    return sent


def test_the_reviewer_is_sent_the_same_thing_with_learning_data_as_without(writer, chats, learning_on):
    thread_id = f"e8-{uuid.uuid4().hex[:10]}"
    threads.create_thread("Tidy downloads", thread_id=thread_id, seed_default_skills=False)
    assert threads.append_checkpoint_messages(thread_id, [
        HumanMessage(content="Please tidy my Downloads folder", id=f"{thread_id}-h"),
        AIMessage(content="On it.", id=f"{thread_id}-a"),
    ])
    save_learning_settings(learning_on, enabled=False)
    without = _reviewed(thread_id)

    save_learning_settings(learning_on, enabled=True)
    chats.chat(thread_id)
    turn = writer.begin_turn(thread_id=thread_id, surface="normal_chat", person_message=True)
    writer.tool_call(turn, call={"id": "t1", "name": "list_files", "args": {"path": "~/Downloads"}},
                     result=ToolMessage(content=CANARY_TOOL, tool_call_id="t1"))
    writer.model_call(turn, messages=[HumanMessage(content="Please tidy my Downloads folder")],
                      response=AIMessage(content=CANARY_REPLY))
    writer.end_turn(turn, "completed")
    writer.drain()
    store = LearningStore(learning_on)
    store.put(signal(thread_id, "sig_e8", note=CANARY_NOTE))
    store.put(lesson("les_e8", text=CANARY_LESSON))
    assert CANARY_TOOL in "".join(
        (store.read_blob(item.result_blob) or b"").decode() for item in store.find("decision") if item.result_blob)

    with_learning = _reviewed(thread_id)

    assert with_learning == without
    text = json.dumps(with_learning)
    for canary in (CANARY_TOOL, CANARY_REPLY, CANARY_NOTE, CANARY_LESSON):
        assert canary not in text
    assert "Please tidy my Downloads folder" in text  # the person's own words are what it judges by
