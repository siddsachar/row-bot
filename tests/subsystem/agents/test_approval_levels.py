"""Who gives an approval at each level (A0): the person under Ask, the reviewer first under Auto, the level
itself under Hands-off; Block refuses. Every decision is recorded on the action's result."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

import pytest

from row_bot import approval_review
from row_bot.approval_review import ReviewVerdict
from row_bot.tools import approval_gate

pytestmark = pytest.mark.subsystem

_PAYLOAD = {"tool": "send_gmail_message", "label": "Send email", "description": "Send email: to pat@example.com"}


@pytest.fixture
def reviewer(monkeypatch):
    """A stand-in reviewer: its decision, and every action it was shown."""
    state = {"verdict": ReviewVerdict("approve", "You asked to email Pat."), "seen": []}

    def review(payload, *, risks=(), **_kwargs):
        state["seen"].append((dict(payload), tuple(risks)))
        return state["verdict"]

    monkeypatch.setattr(approval_review, "review", review)
    return state


def _person(answer):
    asked: list[dict] = []

    def callback(payload):
        asked.append(payload)
        return answer
    return asked, callback


def test_block_refuses_without_asking_anyone(reviewer):
    asked, callback = _person(True)
    outcome, line = approval_gate.resolve_approval_line(dict(_PAYLOAD), approval_mode="block",
                                                        approval_callback=callback)
    assert (outcome, line) == ("block", "")
    assert asked == [] and reviewer["seen"] == []


@pytest.mark.parametrize(("answer", "outcome", "line"), [
    (True, "allow", "Approval: asked; approved by you"),
    (False, "deny", "Approval: asked; denied by you — did not run"),
])
def test_ask_asks_the_person_and_never_the_reviewer(reviewer, answer, outcome, line):
    asked, callback = _person(answer)
    assert approval_gate.resolve_approval_line(dict(_PAYLOAD), approval_mode="approve",
                                               approval_callback=callback) == (outcome, line)
    assert [payload["label"] for payload in asked] == ["Send email"]
    assert "review" not in asked[0]
    assert reviewer["seen"] == []


def test_auto_runs_what_the_reviewer_approves_without_asking_and_records_why(reviewer):
    asked, callback = _person(False)
    outcome, line = approval_gate.resolve_approval_line(dict(_PAYLOAD), approval_mode="auto",
                                                        risks=("reaches other people",), approval_callback=callback)
    assert outcome == "allow"
    assert line == "Approval: approved by the reviewer: You asked to email Pat"
    assert asked == []
    [(seen, risks)] = reviewer["seen"]
    assert seen["description"] == _PAYLOAD["description"] and risks == ("reaches other people",)


@pytest.mark.parametrize(("answer", "outcome", "first"), [
    (True, "allow", "Approval: asked; approved by you"),
    (False, "deny", "Approval: asked; denied by you — did not run"),
])
def test_auto_asks_the_person_when_the_reviewer_asks_and_says_why(reviewer, answer, outcome, first):
    reviewer["verdict"] = ReviewVerdict("ask", "You didn't ask to email anyone.")
    asked, callback = _person(answer)
    result, line = approval_gate.resolve_approval_line(dict(_PAYLOAD), approval_mode="auto",
                                                       approval_callback=callback)
    assert result == outcome
    assert line == f"{first}. The reviewer asked you: You didn't ask to email anyone"
    # The card carries the reviewer's reason.
    assert asked[0]["review"] == {"decision": "ask", "reason": "You didn't ask to email anyone."}


def test_full_autonomy_allows_without_asking_anyone_and_records_it(reviewer):
    asked, callback = _person(False)
    outcome, line = approval_gate.resolve_approval_line(dict(_PAYLOAD), approval_mode="full",
                                                        approval_callback=callback)
    assert (outcome, line) == ("allow", "Approval: allowed by Hands-off")
    assert asked == [] and reviewer["seen"] == []


@pytest.mark.parametrize("mode", ["approve", "auto", "full"])
def test_read_only_actions_never_reach_the_reviewer_or_the_person(reviewer, mode):
    asked, callback = _person(False)
    outcome, line = approval_gate.resolve_approval_line(dict(_PAYLOAD), approval_mode=mode, read_only=True,
                                                        approval_callback=callback)
    assert (outcome, line) == ("allow", "Approval: not needed (read-only)")
    assert asked == [] and reviewer["seen"] == []


def test_the_reviewers_decision_is_recorded_on_a_tool_result_that_has_no_line_of_its_own(reviewer):
    with approval_gate.collect_decisions() as decisions:
        assert approval_gate.request_approval(dict(_PAYLOAD), approval_mode="auto") is True
    assert approval_gate.with_decisions(decisions, "Pushed main.") == (
        "Approval: approved by the reviewer: You asked to email Pat\nPushed main.")
    # A result that leads with its own line keeps it; structured results stay data.
    assert approval_gate.with_decisions(decisions, "Approval: asked; approved by you\nx").count("Approval:") == 1
    assert approval_gate.with_decisions(decisions, '{"ok": true}') == '{"ok": true}'

    with approval_gate.collect_decisions() as decisions:
        approval_gate.request_approval(dict(_PAYLOAD), approval_mode="full")
    assert approval_gate.with_decisions(decisions, "Done.") == "Approval: allowed by Hands-off\nDone."


def test_a_resumed_run_takes_the_persons_answer_and_never_asks_the_reviewer_again(reviewer):
    """LangGraph runs a tool again from the start once the person answers: the reviewer must not decide on that
    re-run, or it could approve what the person denied."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command

    class State(TypedDict):
        results: Annotated[list, operator.add]

    def node(_state):
        allowed = approval_gate.request_approval(dict(_PAYLOAD), approval_mode="auto")
        return {"results": [allowed]}

    builder = StateGraph(State)
    builder.add_node("act", node)
    builder.add_edge(START, "act")
    builder.add_edge("act", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "resume-check"}}

    reviewer["verdict"] = ReviewVerdict("ask", "Not what you asked for.")
    first = graph.invoke({"results": []}, config)
    [pending] = first["__interrupt__"]
    assert pending.value["review"] == {"decision": "ask", "reason": "Not what you asked for."}
    assert len(reviewer["seen"]) == 1

    # Were the reviewer asked again it would now approve; the person's "no" must stand.
    reviewer["verdict"] = ReviewVerdict("approve", "Looks fine.")
    final = graph.invoke(Command(resume=False), config)
    assert final["results"] == [False]
    assert len(reviewer["seen"]) == 1


_WRITE = {"tool": "workspace_write_file", "label": "Write a file", "description": "Write notes/summary.md"}
_PUSH = {"tool": "run_command", "label": "Run a command", "description": "git push origin main"}


def _one_call_asking_twice(questions, mode):
    """A graph whose one task (one tool call) asks about each question in turn through the gate."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    class State(TypedDict):
        results: Annotated[list, operator.add]

    def node(_state):
        with approval_gate.collect_decisions() as lines:
            outcomes = [approval_gate.resolve_approval_line(dict(question), approval_mode=mode)[0]
                        for question in questions()]
        return {"results": [(outcomes, list(lines))]}

    builder = StateGraph(State)
    builder.add_node("act", node)
    builder.add_edge(START, "act")
    builder.add_edge("act", END)
    return builder.compile(checkpointer=InMemorySaver())


def _answer_every_card(graph, config, answers, *, by_id):
    """Run to the end, the person answering each card by what it asks; the cards in the order shown."""
    from langgraph.types import Command

    shown: list[str] = []
    state = graph.invoke({"results": []}, config)
    while state.get("__interrupt__"):
        [pending] = state["__interrupt__"]
        shown.append(pending.value["label"])
        answer = answers[pending.value["label"]]
        state = graph.invoke(Command(resume={pending.id: answer} if by_id else answer), config)
    [(outcomes, lines)] = state["results"]
    return outcomes, lines, shown


@pytest.mark.parametrize("by_id", [False, True], ids=["answer", "answer-by-interrupt-id"])
@pytest.mark.parametrize(("mode", "verdicts", "answers", "outcomes", "shown"), [
    ("block", {}, {}, ["block", "block"], []),
    ("approve", {}, {"Write a file": True, "Run a command": False}, ["allow", "deny"],
     ["Write a file", "Run a command"]),
    ("approve", {}, {"Write a file": False, "Run a command": True}, ["deny", "allow"],
     ["Write a file", "Run a command"]),
    ("auto", {"Write a file": "ask", "Run a command": "ask"}, {"Write a file": True, "Run a command": False},
     ["allow", "deny"], ["Write a file", "Run a command"]),
    ("auto", {"Write a file": "ask", "Run a command": "ask"}, {"Write a file": False, "Run a command": True},
     ["deny", "allow"], ["Write a file", "Run a command"]),
    # The reviewer approves one and asks about the other: the person's answer is only ever the asked one's.
    ("auto", {"Write a file": "approve", "Run a command": "ask"}, {"Run a command": False}, ["allow", "deny"],
     ["Run a command"]),
    ("auto", {"Write a file": "approve", "Run a command": "ask"}, {"Run a command": True}, ["allow", "allow"],
     ["Run a command"]),
    ("auto", {"Write a file": "ask", "Run a command": "approve"}, {"Write a file": False}, ["deny", "allow"],
     ["Write a file"]),
    ("auto", {"Write a file": "ask", "Run a command": "approve"}, {"Write a file": True}, ["allow", "allow"],
     ["Write a file"]),
    ("full", {}, {}, ["allow", "allow"], []),
])
def test_each_answer_in_one_tool_call_goes_to_the_question_it_answers(
        reviewer, monkeypatch, by_id, mode, verdicts, answers, outcomes, shown):
    """One tool call that asks twice: each of the person's answers binds to the card it answered, at every level,
    and the reviewer decides each question once (a resumed run replays its verdicts, never asks it again)."""
    reasons = {"approve": "You asked for it.", "ask": "You didn't ask for this."}
    reviewed: list[str] = []

    def review(payload, *, risks=(), **_kwargs):
        reviewed.append(payload["label"])
        # Asked again, it would decide the other way: a re-run must never depend on it.
        decision = verdicts[payload["label"]]
        if reviewed.count(payload["label"]) > 1:
            decision = "approve" if decision == "ask" else "ask"
        return ReviewVerdict(decision, reasons[decision])

    monkeypatch.setattr(approval_review, "review", review)
    graph = _one_call_asking_twice(lambda: (_WRITE, _PUSH), mode)
    config = {"configurable": {"thread_id": f"asks-twice-{mode}-{by_id}"}}

    got, lines, cards = _answer_every_card(graph, config, answers, by_id=by_id)

    assert got == outcomes
    assert cards == shown  # Each card once, never a card for what the reviewer approved.
    assert reviewed == (["Write a file", "Run a command"] if mode == "auto" else [])
    if mode == "auto":
        expected = []
        for question, outcome in zip(("Write a file", "Run a command"), outcomes):
            if verdicts[question] == "approve":
                expected.append("Approval: approved by the reviewer: You asked for it")
            else:
                said = "approved" if outcome == "allow" else "denied"
                expected.append(f"Approval: asked; {said} by you{' — did not run' if said == 'denied' else ''}"
                                f". The reviewer asked you: You didn't ask for this")
        assert lines == expected  # The reviewer's reasons survive the pause with the decisions.


@pytest.mark.parametrize("by_id", [False, True], ids=["answer", "answer-by-interrupt-id"])
def test_an_answer_is_never_used_for_a_question_that_changed_while_it_waited(reviewer, by_id):
    """The person approved what the card said; if the re-run asks about something else, it asks about that."""
    from langgraph.types import Command

    target = {"description": "git push origin main"}
    graph = _one_call_asking_twice(lambda: ({**_PUSH, **target},), "approve")
    config = {"configurable": {"thread_id": f"changed-question-{by_id}"}}

    [pending] = graph.invoke({"results": []}, config)["__interrupt__"]
    assert pending.value["description"] == "git push origin main"
    target["description"] = "git push --force origin main"
    again = graph.invoke(Command(resume={pending.id: True} if by_id else True), config)
    [pending] = again["__interrupt__"]
    assert pending.value["description"] == "git push --force origin main"
    final = graph.invoke(Command(resume={pending.id: False} if by_id else False), config)
    [(outcomes, _lines)] = final["results"]
    assert outcomes == ["deny"]


def test_a_question_the_tool_no_longer_asks_leaves_the_others_their_own_answers(reviewer):
    """A tool that doesn't ask again what it already has (an app already allowed, a tool group already on): the
    question it does ask still gets the person's answer to that question."""
    from langgraph.types import Command

    already: set[str] = set()

    def questions():
        return tuple(question for question in (_WRITE, _PUSH) if question["label"] not in already)

    def node(_state):
        outcomes = []
        for question in questions():
            outcome, _line = approval_gate.resolve_approval_line(dict(question), approval_mode="approve")
            if outcome == "allow":
                already.add(question["label"])  # Allowed for the session, as an app's access is.
            outcomes.append((question["label"], outcome))
        return {"results": [outcomes]}

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    class State(TypedDict):
        results: Annotated[list, operator.add]

    builder = StateGraph(State)
    builder.add_node("act", node)
    builder.add_edge(START, "act")
    builder.add_edge("act", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "already-allowed"}}

    assert graph.invoke({"results": []}, config)["__interrupt__"][0].value["label"] == "Write a file"
    assert graph.invoke(Command(resume=True), config)["__interrupt__"][0].value["label"] == "Run a command"
    final = graph.invoke(Command(resume=False), config)  # The write isn't asked about again.
    assert final["results"] == [[("Run a command", "deny")]]


@pytest.mark.parametrize("by_id", [False, True], ids=["answer", "answer-by-interrupt-id"])
def test_a_run_paused_before_answers_were_bound_asks_its_card_again(reviewer, by_id):
    """A run paused by an earlier version (the card asked straight through ``interrupt()``): its answer can't be
    tied to a question, so it isn't used; the card is asked again, and that answer is."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    class State(TypedDict):
        results: Annotated[list, operator.add]

    runs: list[str] = []

    def node(_state):
        runs.append("run")
        if len(runs) == 1:
            interrupt(dict(_PUSH))  # How the earlier version asked.
        outcome, _line = approval_gate.resolve_approval_line(dict(_PUSH), approval_mode="approve")
        return {"results": [outcome]}

    builder = StateGraph(State)
    builder.add_node("act", node)
    builder.add_edge(START, "act")
    builder.add_edge("act", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": f"paused-before-{by_id}"}}

    [pending] = graph.invoke({"results": []}, config)["__interrupt__"]
    again = graph.invoke(Command(resume={pending.id: True} if by_id else True), config)
    [pending] = again["__interrupt__"]
    assert pending.value["label"] == "Run a command"
    final = graph.invoke(Command(resume={pending.id: True} if by_id else True), config)
    assert final["results"] == ["allow"]
    assert not final.get("__interrupt__")


def test_the_card_and_a_paused_runs_message_say_why_the_reviewer_asked():
    from row_bot.application.approval_projection import project_approval_context
    from row_bot.approval_messages import compact_message, normalize_interrupts

    asked = {**_PAYLOAD, "review": {"decision": "ask", "reason": "You asked for a draft, not to send it."}}
    assert project_approval_context(asked)["reviewer_reason"] == "You asked for a draft, not to send it."
    assert "reviewer_reason" not in project_approval_context(dict(_PAYLOAD))  # Ask: nothing to say.
    message = compact_message(normalize_interrupts([asked], source_label="Helper"))
    assert "The reviewer asked you: You asked for a draft, not to send it." in message


def test_the_activity_reads_who_let_an_action_run():
    """The conversation's activity shows the result's approval line apart from the result (B235)."""
    from row_bot.application.conversation_traces import classify_tool_result

    for line in ("Approval: approved by the reviewer: You asked for it", "Approval: allowed by Hands-off"):
        assert classify_tool_result(f"{line}\nSent.") == "succeeded"
    assert classify_tool_result(
        "Approval: asked; denied by you — did not run. The reviewer asked you: Not asked\nAction cancelled by user."
    ) == "cancelled"
