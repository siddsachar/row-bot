"""Row-Bot asks the person questions with options, in cards (N4).

The real graph streams and resumes with a fake model and the real
``ask_person`` tool, through the platform's approval path: the question waits
on its card, the person answers (an option, their own words) or skips, and the
turn goes on with that answer for the question alone. An answer is never an
approval: an action asked beside the question still asks, answers sent for an
approval are refused, and no approval level answers a question.
"""
# ruff: noqa: F811 -- the imported platform fixture is requested by name.
from __future__ import annotations

import json

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langgraph.types import Command
import pytest

from tests.contracts.client_platform.test_headless_lifecycle import command, platform  # noqa: F401
from tests.subsystem.client_platform.test_finished_turns_answer_every_call import (
    ProviderShapedModel,
    _call,
    _submit,
    _trace_items,
)

pytestmark = pytest.mark.subsystem

BETA = {
    "header": "Beta timing",
    "question": "When should the beta go out?",
    "options": [
        {"label": "Beta.1 now (Recommended)", "description": "Ship the current build to testers today."},
        {"label": "After the fixes", "description": "Wait for the two open fixes, about two days."},
    ],
}
PAGES = {
    "header": "Pages",
    "question": "Which pages go in?",
    "multi_select": True,
    "options": [{"label": "Plans"}, {"label": "Ledger"}, {"label": "Docs"}],
}


class QuestionTurns:
    """The platform's stream and resume factories over the real graph, as the agent builds it (one task per call)."""

    def __init__(self, model, tools) -> None:
        from row_bot import threads
        from row_bot.questions import build_tool

        self.graph = create_react_agent(model=model, tools=[*tools, build_tool()],
                                        checkpointer=threads.checkpointer, version="v2")

    def stream(self, text, enabled_tools, config, *, stop_event=None):
        from langchain_core.messages import HumanMessage

        from row_bot.agent import _stream_graph

        submission = config["configurable"]["platform_submission_id"]
        yield from _stream_graph(self.graph, {"messages": [HumanMessage(content=text, id=submission)]},
                                 config, stop_event=stop_event)

    def resume(self, enabled_tools, config, approved, *, interrupt_ids=None, resume_values=None, stop_event=None):
        from row_bot.agent import _resume_value, _stream_graph

        yield from _stream_graph(self.graph, Command(resume=_resume_value(approved, interrupt_ids, resume_values)),
                                 config, stop_event=stop_event)


def _resolve(platform, approval_id: str, label: str, payload: dict):
    receipt = platform.execute(owner_id="fixture-owner", idempotency_key=label, target=approval_id,
                               command=command("approval.resolve", label, payload))
    handle = platform.registry.get(receipt["execution_id"])
    assert handle.producer_done.wait(10)
    return handle


def _cards(platform) -> list[dict]:
    events = platform.projection.events_since("conversation-a", "0")["events"]
    return [event["payload"] for event in events if event["type"] == "approval.required"]


def _tool_results(name: str) -> list[dict]:
    from row_bot import threads

    return [json.loads(message.content) for message in threads.get_latest_checkpoint_messages("conversation-a")
            if getattr(message, "type", "") == "tool" and message.name == name]


@pytest.fixture
def asked(platform, monkeypatch):
    """A turn paused on a question, and the Buddy labels it showed."""
    from row_bot.application import client_platform

    labels: list[str] = []
    monkeypatch.setattr(client_platform, "_buddy",
                        lambda conversation_id, event_type, label, **payload: labels.append(label))
    model = ProviderShapedModel(responses=[_call("ask_person", "ask-1", questions=[BETA]),
                                           AIMessage(content="Going with that.", id="ai-done")])
    handle = _submit(platform, QuestionTurns(model, []), "ask-beta", "Plan the beta")
    assert handle.status == "waiting_approval"
    return handle, labels


def test_a_question_waits_on_its_card_and_the_turn_goes_on_with_the_answer(platform, asked):
    from row_bot.application.client_monitor import read_pending_approvals

    handle, labels = asked
    view = platform.get_approval(handle.approval_id)
    assert view["action_label"] == "Row-Bot has a question"
    assert view["questions"] == [{
        "header": "Beta timing", "question": "When should the beta go out?", "multi_select": False,
        "options": [
            {"label": "Beta.1 now", "description": "Ship the current build to testers today.", "recommended": True},
            {"label": "After the fixes", "description": "Wait for the two open fixes, about two days.",
             "recommended": False},
        ],
    }]
    # Buddy and the sidebar: Row-Bot has a question, one thing that needs the person.
    assert "Row-Bot has a question" in labels
    waiting = read_pending_approvals()
    assert waiting["total"] == 1 and waiting["items"][0]["kind"] == "question"

    done = _resolve(platform, handle.approval_id, "answer-beta",
                    {"decision": "approve", "answers": [{"choices": [0], "other": ""}]})

    assert done.status == "completed"
    result = _tool_results("ask_person")[-1]
    assert result["outcome"] == "answered"
    assert result["summary"] == "Beta timing → “Beta.1 now” (recommended)"
    assert "not an approval" in result["next"]
    answered = [item for item in _trace_items(platform) if item["canonical_name"] == "ask_person"]
    assert answered[-1]["specialization"]["kind"] == "question_answered"
    assert answered[-1]["specialization"]["answers"] == [{"header": "Beta timing", "answer": "Beta.1 now"}]
    assert read_pending_approvals()["total"] == 0


def test_the_persons_own_words_answer_as_other(platform, asked):
    handle, _labels = asked
    _resolve(platform, handle.approval_id, "answer-other",
             {"decision": "approve", "answers": [{"choices": [], "other": "only plans for now"}]})

    assert _tool_results("ask_person")[-1]["summary"] == "Beta timing → Other: “only plans for now”"


def test_skipping_goes_on_with_the_recommendation_and_never_ends_the_turn(platform, asked):
    handle, labels = asked
    done = _resolve(platform, handle.approval_id, "skip-beta", {"decision": "reject"})

    assert done.status == "completed"
    result = _tool_results("ask_person")[-1]
    assert result["outcome"] == "skipped"
    assert "recommended option (Beta timing: Beta.1 now)" in result["next"]
    assert "Skipped" in labels
    from row_bot import threads

    assert threads.get_latest_checkpoint_messages("conversation-a")[-1].content == "Going with that."


@pytest.mark.parametrize("answers", [
    [{"choices": [2], "other": ""}],                  # not one of its options
    [{"choices": [0, 1], "other": ""}],               # two answers to a single choice
    [{"choices": [0], "other": "and this"}],          # an option and own words to a single choice
    [{"choices": [], "other": "   "}],                # nothing
    [{"choices": [0], "other": ""}, {"choices": [0], "other": ""}],  # more answers than questions
    None,                                             # an approval, not an answer
], ids=["unknown-option", "two-choices", "choice-and-words", "empty", "extra", "approve-alone"])
def test_answers_that_dont_fit_the_question_are_refused_and_it_still_waits(platform, asked, answers):
    from row_bot.application.client_platform import ClientPlatformError

    handle, _labels = asked
    payload = {"decision": "approve", **({"answers": answers} if answers is not None else {})}
    with pytest.raises(ClientPlatformError) as refused:
        platform.execute(owner_id="fixture-owner", idempotency_key="bad", target=handle.approval_id,
                         command=command("approval.resolve", "bad", payload))

    assert str(refused.value) == "question_answer_invalid"
    assert platform.get_approval(handle.approval_id)["status"] == "pending"
    assert _tool_results("ask_person") == []


@pytest.fixture
def gated():
    removed: list[str] = []

    @tool
    def remove_file(path: str) -> str:
        """Remove one file."""
        from row_bot.tools.approval_gate import gate_action

        refusal = gate_action({"tool": "remove_file", "label": "Remove file",
                               "description": f"Remove {path}", "args": {"path": path}})
        if refusal:
            return refusal
        removed.append(path)
        return f"Removed {path}"

    return removed, remove_file


def test_an_answer_never_approves_an_action_asked_beside_it(platform, gated, monkeypatch):
    from row_bot.tools import approval_gate

    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "approve")
    removed, remove_file = gated
    both = AIMessage(content="", id="ai-both", tool_calls=[
        {"name": "ask_person", "args": {"questions": [BETA]}, "id": "ask-1", "type": "tool_call"},
        {"name": "remove_file", "args": {"path": "notes.txt"}, "id": "rm-1", "type": "tool_call"},
    ])
    model = ProviderShapedModel(responses=[both, AIMessage(content="Done.", id="ai-done")])
    first = _submit(platform, QuestionTurns(model, [remove_file]), "both", "Plan and tidy")
    assert first.status == "waiting_approval"
    assert platform.get_approval(first.approval_id)["questions"]

    second = _resolve(platform, first.approval_id, "answer-both",
                      {"decision": "approve", "answers": [{"choices": [0], "other": ""}]})

    # The question went on; the removal asks for itself, on its own card.
    assert removed == []
    assert second.status == "waiting_approval"
    card = platform.get_approval(second.approval_id)
    assert "questions" not in card and card["action_label"] == "remove_file"
    _resolve(platform, second.approval_id, "approve-rm", {"decision": "approve"})
    assert removed == ["notes.txt"]
    assert _tool_results("ask_person")[-1]["summary"] == "Beta timing → “Beta.1 now” (recommended)"


def test_answers_sent_for_an_approval_are_refused(platform, gated, monkeypatch):
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.tools import approval_gate

    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "approve")
    removed, remove_file = gated
    model = ProviderShapedModel(responses=[_call("remove_file", "rm-1", path="notes.txt"),
                                           AIMessage(content="Done.", id="ai-done")])
    first = _submit(platform, QuestionTurns(model, [remove_file]), "remove", "Tidy")

    with pytest.raises(ClientPlatformError) as refused:
        platform.execute(owner_id="fixture-owner", idempotency_key="answer-approval", target=first.approval_id,
                         command=command("approval.resolve", "answer-approval",
                                         {"decision": "approve", "answers": [{"choices": [0], "other": ""}]}))

    assert str(refused.value) == "question_answer_invalid"
    assert removed == []
    assert platform.get_approval(first.approval_id)["status"] == "pending"


@pytest.mark.parametrize("level", ["auto", "full", "block"])
def test_no_approval_level_answers_a_question(platform, monkeypatch, level):
    from row_bot import approval_review, threads
    from row_bot.tools import approval_gate

    def never(*_args, **_kwargs):
        raise AssertionError("the reviewer never answers a question")

    monkeypatch.setattr(approval_review, "review", never)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: level)
    threads._set_thread_approval_mode("conversation-a", level)
    model = ProviderShapedModel(responses=[
        _call("ask_person", "ask-1", questions=[BETA]),
        _call("ask_person", "ask-2", questions=[PAGES]),
        AIMessage(content="Done.", id="ai-done"),
    ])
    first = _submit(platform, QuestionTurns(model, []), "ask-twice", "Plan the beta")
    assert first.status == "waiting_approval"
    assert platform.get_approval(first.approval_id)["questions"]

    # "Approve the rest" never reaches a question: the next one in the same turn still asks.
    second = _resolve(platform, first.approval_id, "answer-first",
                      {"decision": "approve", "scope": "turn", "answers": [{"choices": [1], "other": ""}]})
    assert second.status == "waiting_approval"
    assert platform.get_approval(second.approval_id)["questions"][0]["header"] == "Pages"
    assert len(_cards(platform)) == 2


@pytest.mark.parametrize("header, chip", [("Posting slot today", "Posting slot"), ("Supercalifragilistic", "Supercalifr…")])
def test_a_header_longer_than_its_chip_is_shortened_not_refused(header, chip):
    """S8 (18 Nov): ask_person refused a whole question because its header ran past 12 characters."""
    from row_bot import questions

    [question] = questions.normalize([{"header": header, "question": "When should it go out?",
                                       "options": [{"label": "10:30"}, {"label": "16:00"}]}])
    assert question["header"] == chip
