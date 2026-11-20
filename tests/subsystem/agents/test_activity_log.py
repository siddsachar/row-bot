"""D1 the activity log: every tool action, its outcome and who decided, from real gate paths.

Turns run through the Agent's real graph and its tool-call middleware (``AgentTurns``) with a
scripted model and fake tools; the reviewer is faked. These are security and privacy tests
(outcome attribution, redaction, incognito, deletion, retention, writes that never fail a call):
they stay in the pull request lane.
"""

from __future__ import annotations

import sqlite3
import time
from collections import deque

import pytest
from langchain_core.tools import tool

from row_bot import activity_log, approval_review
from row_bot.approval_review import ReviewVerdict
from row_bot.tools import approval_gate
from tests.helpers.learning_turns import AgentTurns, ScriptedModel, asks, call, says

pytestmark = pytest.mark.subsystem


@tool
def read_notes(name: str) -> str:
    """Read one of the person's notes (needs no approval)."""
    return f"The note {name} says hello."


@tool
def delete_file(path: str) -> str:
    """Delete one of the person's files (asks first)."""
    refusal = approval_gate.gate_action({"tool": "delete_file", "label": "Delete a file",
                                         "description": f"Delete {path}"})
    return refusal or f"Deleted {path}."


@tool
def run_command(command: str) -> str:
    """Run a command (asks first)."""
    refusal = approval_gate.gate_action({"tool": "run_command", "label": "Run a command",
                                         "description": f"Run {command}"})
    return refusal or "Done."


@tool
def publish_post(text: str) -> str:
    """Post on the person's account: only they approve it (their own rule)."""
    binding = approval_gate.call_binding("publish_post", {"text": text})
    approved, line = approval_gate.person_approval(
        {"tool": "publish_post", "label": "Post", "description": f"Post: {text}"}, binding=binding)
    return f"{line}\nPosted." if approved else f"{line}\nNothing was posted."


@tool
def flaky_upload(name: str) -> str:
    """Upload a file (asks first), and fails."""
    refusal = approval_gate.gate_action({"tool": "flaky_upload", "label": "Upload a file",
                                         "description": f"Upload {name}"})
    if refusal:
        return refusal
    raise RuntimeError("The upload service is down.")


@tool
def profile_refused(name: str) -> str:
    """A tool the agent profile's policy refuses."""
    return f"BLOCKED: {name} is not part of this agent's tools."


TOOLS = [read_notes, delete_file, run_command, publish_post, flaky_upload, profile_refused]


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    yield tmp_path / "data"
    activity_log.flush()


@pytest.fixture
def reviewer(monkeypatch):
    """The Auto reviewer, faked: its verdict, and each action it saw."""
    seen: list[dict] = []
    verdict = {"value": ReviewVerdict("approve", "You asked to delete old.txt.")}

    def review(payload, *, risks=(), **_kwargs):
        seen.append(dict(payload))
        return verdict["value"]

    monkeypatch.setattr(approval_review, "review", review)
    return verdict


def _turns(monkeypatch, *replies, mode: str = "approve") -> AgentTurns:
    model = ScriptedModel(responses=list(replies), seen=[])
    return AgentTurns(monkeypatch, model, TOOLS, approval_mode=mode)


def _rows(**filters) -> list[dict]:
    assert activity_log.flush()
    rows, _next = activity_log.query(limit=200, **filters)
    return rows


def _only(**filters) -> dict:
    [row] = _rows(**filters)
    return row


def test_a_read_only_call_ran_and_no_one_decided(data, monkeypatch):
    turns = _turns(monkeypatch, asks(call("read_notes", "r1", name="plan")), says("It says hello."))
    turns.send("What does my plan note say?")

    row = _only()
    assert (row["call_id"], row["tool"], row["outcome"], row["decided_by"]) == ("r1", "read_notes", "ran", "none")
    assert (row["conversation_id"], row["surface"], row["level"], row["status"]) == (
        turns.thread_id, "chat", "approve", "final")
    assert row["title"] == "Read notes"


def test_a_paused_call_waits_for_you_then_becomes_one_final_row_when_you_approve(data, monkeypatch):
    turns = _turns(monkeypatch, asks(call("delete_file", "d1", path="old.txt")), says("Deleted."))
    events = turns.send("Delete old.txt")

    waiting = _only()
    assert (waiting["call_id"], waiting["outcome"], waiting["status"]) == ("d1", "asked", "pending")
    assert waiting["summary"] == "Delete old.txt"

    turns.answer(True, events)
    row = _only()
    assert (row["id"], row["call_id"]) == (waiting["id"], "d1")
    assert (row["outcome"], row["decided_by"], row["status"]) == ("approved_by_you", "person", "final")


def test_your_no_is_denied(data, monkeypatch):
    turns = _turns(monkeypatch, asks(call("delete_file", "d1", path="old.txt")), says("I won't."))
    turns.answer(False, turns.send("Delete old.txt"))

    row = _only()
    assert (row["outcome"], row["decided_by"]) == ("denied", "person")


def test_the_reviewer_approves_under_auto_with_its_reason(data, monkeypatch, reviewer):
    turns = _turns(monkeypatch, asks(call("delete_file", "d1", path="old.txt")), says("Deleted."), mode="auto")
    turns.send("Delete old.txt")

    row = _only()
    assert (row["outcome"], row["decided_by"], row["level"]) == ("approved_by_reviewer", "reviewer", "auto")
    assert row["reason"] == "You asked to delete old.txt."


def test_the_reviewer_asks_and_you_approve_with_its_reason_kept(data, monkeypatch, reviewer):
    reviewer["value"] = ReviewVerdict("ask", "You didn't mention this file.")
    turns = _turns(monkeypatch, asks(call("delete_file", "d1", path="old.txt")), says("Deleted."), mode="auto")
    events = turns.send("Tidy up")
    assert _only()["reason"] == "You didn't mention this file."

    turns.answer(True, events)
    row = _only()
    assert (row["outcome"], row["decided_by"]) == ("approved_by_you", "person")
    assert row["reason"] == "You didn't mention this file."


def test_hands_off_allows_without_asking(data, monkeypatch):
    turns = _turns(monkeypatch, asks(call("delete_file", "d1", path="old.txt")), says("Deleted."), mode="full")
    turns.send("Delete old.txt")

    row = _only()
    assert (row["outcome"], row["decided_by"], row["level"]) == ("hands_off", "hands_off", "full")


def test_block_blocks_and_a_policy_refusal_is_blocked_by_policy(data, monkeypatch):
    turns = _turns(monkeypatch, asks(call("delete_file", "d1", path="old.txt"),
                                     call("profile_refused", "p1", name="x")), says("Couldn't."), mode="block")
    turns.send("Delete old.txt")

    by_call = {row["call_id"]: row for row in _rows()}
    assert (by_call["d1"]["outcome"], by_call["d1"]["decided_by"]) == ("blocked", "block")
    assert (by_call["p1"]["outcome"], by_call["p1"]["decided_by"]) == ("blocked", "policy")


def test_your_own_rule_asks_you_even_under_hands_off(data, monkeypatch):
    turns = _turns(monkeypatch, asks(call("publish_post", "x1", text="Launch day!")), says("Posted."), mode="full")
    events = turns.send("Post it")

    waiting = _only()
    assert (waiting["outcome"], waiting["person_only"]) == ("asked", 1)

    turns.answer(True, events)
    row = _only()
    assert (row["outcome"], row["decided_by"], row["person_only"]) == ("approved_by_you", "person", 1)


def test_an_approval_of_other_details_is_changed_and_did_not_run(data, monkeypatch):
    turns = _turns(monkeypatch, asks(call("publish_post", "x1", text="Launch day!")), says("Not posted."))
    events = turns.send("Post it")
    [interrupts] = [payload for kind, payload in events if kind == "interrupt"]
    [interrupt_id] = [item["__interrupt_id"] for item in interrupts]
    other = approval_gate.call_binding("publish_post", {"text": "Something else"})
    list(turns.agent.resume_stream_agent([], turns.config, True, interrupt_ids=[interrupt_id],
                                         resume_values={interrupt_id: approval_gate.bound_answer(True, other)}))

    row = _only()
    assert (row["outcome"], row["decided_by"]) == ("changed", "person")


def test_a_tool_that_fails_after_your_approval_did_not_work(data, monkeypatch):
    turns = _turns(monkeypatch, asks(call("flaky_upload", "u1", name="report.pdf")), says("It failed."), mode="full")
    turns.send("Upload the report")

    row = _only()
    assert (row["outcome"], row["decided_by"]) == ("failed", "hands_off")


def test_summaries_are_redacted_bounded_and_filters_find_them(data, monkeypatch):
    long = "x" * 400
    turns = _turns(monkeypatch, asks(call("run_command", "c1", command="curl -H token=abc123 https://example.test"),
                                     call("run_command", "c2", command=long)), says("Done."), mode="full")
    turns.send("Check the API")

    by_call = {row["call_id"]: row for row in _rows()}
    assert "abc123" not in by_call["c1"]["summary"] and "token=<redacted>" in by_call["c1"]["summary"]
    assert len(by_call["c2"]["summary"]) <= activity_log.SUMMARY_MAX
    assert [row["call_id"] for row in _rows(q="curl")] == ["c1"]
    assert [row["call_id"] for row in _rows(q="100%")] == []
    assert {row["call_id"] for row in _rows(outcome="needed")} == {"c1", "c2"}
    assert _rows(outcome="blocked") == []


def test_an_incognito_chat_is_logged_without_its_words(data, monkeypatch, reviewer):
    from row_bot import threads

    reviewer["value"] = ReviewVerdict("approve", "You asked to delete secret-plans.txt.")
    turns = _turns(monkeypatch, asks(call("delete_file", "d1", path="secret-plans.txt")), says("Deleted."),
                   mode="auto")
    threads.set_thread_incognito(turns.thread_id, True)
    turns.send("Delete secret-plans.txt")

    row = _only()
    assert row["outcome"] == "approved_by_reviewer"
    assert (row["summary"], row["reason"]) == (activity_log.INCOGNITO_SUMMARY, "")
    assert "secret" not in str(row)


def test_deleting_a_conversation_deletes_its_rows(data, monkeypatch):
    from row_bot import threads
    from row_bot.thread_cleanup import delete_thread

    kept = _turns(monkeypatch, asks(call("read_notes", "r1", name="a")), says("ok"))
    kept.send("Read a")
    gone = _turns(monkeypatch, asks(call("read_notes", "r2", name="b")), says("ok"))
    gone.send("Read b")
    assert len(_rows()) == 2

    assert delete_thread(gone.thread_id).deleted is True
    assert threads._thread_exists(gone.thread_id) is False
    assert [row["call_id"] for row in _rows()] == ["r1"]


def test_idle_pruning_drops_rows_older_than_90_days_and_beyond_the_cap(data):
    now = time.time()
    for index, age_days in enumerate((120, 91, 10, 1, 0)):
        activity_log.record(activity_log.ToolCall(call_id=f"c{index}", tool="read_notes", thread_id="t",
                                                  at=now - age_days * 86_400))
    activity_log.flush()

    assert activity_log.prune(now=now) == 2
    assert [row["call_id"] for row in _rows()] == ["c4", "c3", "c2"]
    assert activity_log.prune(now=now, max_rows=2) == 1
    assert [row["call_id"] for row in _rows()] == ["c4", "c3"]


def test_a_full_queue_drops_the_oldest_without_raising(data, monkeypatch):
    monkeypatch.setattr(activity_log, "_queue", deque(maxlen=3))
    monkeypatch.setattr(activity_log, "QUEUE_LIMIT", 3)
    monkeypatch.setattr(activity_log, "_start_writer", lambda: None)  # The writer is busy elsewhere.
    for index in range(5):
        activity_log.record(activity_log.ToolCall(call_id=f"c{index}", tool="read_notes"))

    assert [payload.call_id for _kind, _path, payload in activity_log._queue] == ["c2", "c3", "c4"]
    monkeypatch.undo()  # The real queue and writer again, for the fixture's flush.


def test_a_failing_write_never_fails_the_tool_call(data, monkeypatch):
    def broken(_path):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(activity_log, "_connect", broken)
    turns = _turns(monkeypatch, asks(call("delete_file", "d1", path="old.txt")), says("Deleted."), mode="full")

    events = turns.send("Delete old.txt")

    assert ("done", "Deleted.") in events
    assert activity_log.flush()


def test_what_you_allowed_in_this_chat_is_its_own_outcome(data, monkeypatch):
    from row_bot.application import approval_grants

    turns = _turns(monkeypatch, asks(call("run_command", "g1", command="npm test")), says("Ran."))
    kinds = approval_grants.approval_kinds({"tool": "run_command", "label": "Run a command"})
    approval_grants.grant(turns.thread_id, kinds, "chat")
    try:
        turns.send("Run the tests")
    finally:
        approval_grants.end_chat(turns.thread_id)

    row = _only()
    assert (row["outcome"], row["decided_by"], row["status"]) == ("allowed_in_chat", "chat_grant", "final")


def test_a_card_left_unanswered_settles_and_an_answer_still_finishes_it(data):
    activity_log.record(activity_log.ToolCall(call_id="s1", tool="run_command", thread_id="chat-1",
                                              decisions=({"stage": "asking", "level": "approve"},), paused=True))
    activity_log.settle_pending("chat-1")
    assert (_only()["outcome"], _only()["status"]) == ("asked", "final")

    activity_log.record(activity_log.ToolCall(call_id="s1", tool="run_command", thread_id="chat-1", decisions=(
        {"stage": "decided", "level": "approve", "outcome": "deny", "by": "person"},)))
    assert _only()["outcome"] == "denied"


def test_a_helper_agents_calls_belong_to_the_conversation_that_started_it(data, reload_for_data_dir):
    tasks, agent_runs = reload_for_data_dir(data, "row_bot.tasks", "row_bot.agent_runs")
    run = agent_runs.create_agent_run(kind="subagent", parent_thread_id="parent-chat", thread_id="helper-thread")
    activity_log.record(activity_log.ToolCall(call_id="h1", tool="read_notes", thread_id="helper-thread",
                                              surface="agent_child", agent_run_id=run["id"]))

    row = _only(conversation="parent-chat")
    assert (row["surface"], row["source_id"]) == ("agent", run["id"])


def test_a_workflow_runs_calls_are_its_own(data, reload_for_data_dir):
    tasks, = reload_for_data_dir(data, "row_bot.tasks")
    task_id = tasks.create_task("Weekly notes", prompts=["Tidy my notes"], channels=[], enabled=False)
    conn = tasks._get_conn()
    conn.execute("INSERT INTO task_runs (id, task_id, thread_id, started_at, status) "
                 "VALUES ('run-1', ?, 'wf-thread', 'now', 'running')", (task_id,))
    conn.commit()
    conn.close()
    activity_log.record(activity_log.ToolCall(call_id="w1", tool="read_notes", thread_id="wf-thread",
                                              surface="normal_chat"))

    row = _only(surface="workflow")
    assert (row["conversation_id"], row["source_id"]) == ("wf-thread", task_id)


def test_clear_removes_every_row(data):
    for index in range(3):
        activity_log.record(activity_log.ToolCall(call_id=f"c{index}", tool="read_notes"))
    assert activity_log.flush()

    assert activity_log.clear() == 3
    assert _rows() == []


# ── Learning's seam sees exactly what it saw before (E8) ────────────────────


def _learning_calls(monkeypatch, collect: bool) -> list[tuple]:
    from row_bot import agent, learning

    seen: list[tuple] = []
    monkeypatch.setattr(learning, "is_enabled", lambda: True)
    monkeypatch.setattr(agent, "note_learning_approval",
                        lambda payload, mode, response, line, *, blocked=False:
                        seen.append((dict(payload), mode, response, line, blocked)))
    monkeypatch.setattr(approval_review, "review",
                        lambda payload, *, risks=(), **_kw: ReviewVerdict("approve", "It matches."))
    payload = {"tool": "delete_file", "label": "Delete a file", "description": "Delete old.txt"}

    def run() -> None:
        for mode in ("approve", "auto", "full", "block"):
            approval_gate.resolve_approval_line(payload, approval_mode=mode, approval_callback=lambda _p: True)
            approval_gate.resolve_approval_line(payload, approval_mode=mode, approval_callback=lambda _p: False)
            approval_gate.resolve_approval_line(payload, approval_mode=mode, read_only=True)
            approval_gate.request_approval(payload, approval_mode=mode, approval_callback=lambda _p: True)

    if collect:
        with approval_gate.collect_activity() as notes:
            run()
        assert notes  # The activity log saw the decisions too.
    else:
        run()
    return seen


def test_learnings_seam_sees_the_same_decisions_with_or_without_the_activity_log(data, monkeypatch):
    without = _learning_calls(monkeypatch, collect=False)
    with_log = _learning_calls(monkeypatch, collect=True)

    assert with_log == without
    assert len(without) == 12  # Per level: the approval, the "no" and the request (a read needs none).
