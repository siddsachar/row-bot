"""The scaling study's DAgger relabelling (PREREG-SCALING §4): a row is the student's own prefix with the teacher's
action, the only target; the rounds' requests are drawn seeded and stratified, the subset's own last."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.e2e.learning.scaling import dagger

pytestmark = [pytest.mark.subsystem]

ROOT = "D:/scratch/run-1"
TOOLS = [{"type": "function", "function": {"name": "workspace_read_file", "parameters": {"type": "object"}}},
         {"type": "function", "function": {"name": "workspace_write_file", "parameters": {"type": "object"}}}]
#: An envelope that leaves a row as it is (the student's prefix is captured already in its serving shape).
SERVED = SimpleNamespace(profile="full")


def _student_request() -> dict:
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
        message_to_dict,
    )

    messages = [SystemMessage(content="You are Row-Bot."),
                HumanMessage(content="Total the donations of 2026-11-02 to 2026-11-04 in ledger.csv"),
                AIMessage(content="", tool_calls=[{"name": "workspace_read_file", "args": {"file_path": "notes.txt"},
                                                   "id": "call_s1", "type": "tool_call"}]),
                ToolMessage(content="nothing here", tool_call_id="call_s1")]
    return {"messages": [message_to_dict(item) for item in messages], "tools": TOOLS, "purpose": "agent"}


def test_a_row_is_the_students_prefix_with_the_teachers_action_as_its_only_target():
    from langchain_core.messages import AIMessage

    teacher = AIMessage(content="", tool_calls=[{"name": "workspace_read_file",
                                                 "args": {"file_path": f"{ROOT}/ledger.csv"}, "id": "call_t1",
                                                 "type": "tool_call"}])

    row = dagger.row(_student_request(), teacher, envelope=SERVED, roots=(ROOT,))

    roles = [message["role"] for message in row["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    student, target = row["messages"][2], row["messages"][4]
    assert student["weight"] == 0 and json.loads(student["tool_calls"][0]["function"]["arguments"]) == {
        "file_path": "notes.txt"}
    assert row["messages"][3]["content"] == "nothing here"
    assert [message for message in row["messages"] if message.get("weight")] == [target]
    assert json.loads(target["tool_calls"][0]["function"]["arguments"]) == {"file_path": "ledger.csv"}
    assert row["tools"] == TOOLS


def test_an_empty_teacher_reply_gives_no_row():
    from langchain_core.messages import AIMessage

    assert dagger.row(_student_request(), AIMessage(content=""), envelope=SERVED) is None


def test_the_rounds_take_requests_outside_the_subset_first_seeded_and_stratified():
    requests = {"idris": [f"idris-{n}" for n in range(6)], "mara": [f"mara-{n}" for n in range(3)]}
    in_subset = {"idris-0", "idris-1", "mara-0"}

    order = dagger.order(requests, in_subset, "20261106/dagger")

    assert sorted(order) == sorted(requests["idris"] + requests["mara"])
    assert set(order[:6]) == {"idris-2", "idris-3", "idris-4", "idris-5", "mara-1", "mara-2"}
    assert sum(1 for item in order[:3] if item.startswith("mara")) == 1  # a third of the fresh ones are Mara's
    assert order == dagger.order(requests, in_subset, "20261106/dagger")
