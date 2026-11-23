"""``row-bot run`` answers what waits for a person as nobody can, and leaves a valid ATIF trajectory.

Real Agent turns (``agent.stream_agent`` on the graph ``AgentTurns`` builds) with a scripted model and fake
tools, driven by the headless run's own turn loop (``headless.run.drive``):

- under Ask, an action that needs approval is refused, does not run, and the run goes on to its answer;
- under Auto, the reviewer decides first, and only what it would ask the person about is refused;
- under Hands-off, it runs;
- a question to the person goes on with its recommended option;
- a provider error and the round ceiling end the run with their own exit codes.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.tools import tool

from row_bot import agent_settings, approval_review, questions, threads
from row_bot.approval_review import ReviewVerdict
from row_bot.headless import atif
from row_bot.headless.cli import APPROVALS, EXIT_BUDGET, EXIT_DONE, EXIT_PROVIDER, EXIT_REFUSED, RunOptions
from row_bot.headless.run import drive
from row_bot.tools import approval_gate
from tests.helpers.atif_schema import problems
from tests.helpers.learning_turns import AgentTurns, ScriptedModel, asks, call, says

pytestmark = pytest.mark.subsystem

DELETED: list[str] = []


@tool
def delete_file(path: str) -> str:
    """Delete one of the person's files (asks first)."""
    refusal = approval_gate.gate_action({"tool": "delete_file", "label": "Delete a file",
                                         "description": f"Delete {path}"})
    if refusal:
        return refusal
    DELETED.append(path)
    return f"Deleted {path}."


TOOLS = [delete_file, questions.build_tool()]


@pytest.fixture(autouse=True)
def _nothing_deleted_yet():
    DELETED.clear()


def _run(monkeypatch, tmp_path, *replies, approval: str = "ask", model=None):
    model = model or ScriptedModel(responses=list(replies), seen=[])
    turns = AgentTurns(monkeypatch, model, TOOLS, approval_mode=APPROVALS[approval])
    options = RunOptions(task="Delete old.txt", workspace=tmp_path, model="test-model", approval=approval)
    return drive(turns.thread_id, turns.model_ref, options), model, turns


def _tool_results(model: ScriptedModel) -> list[str]:
    return [str(message.content) for message in model.seen[-1] if message.type == "tool"]


def test_under_ask_an_action_that_needs_approval_is_refused_and_the_run_goes_on(monkeypatch, tmp_path):
    result, model, _ = _run(monkeypatch, tmp_path, asks(call("delete_file", "d1", path="old.txt")),
                            says("I couldn't delete old.txt: nobody approved it."))

    assert DELETED == []
    [refusal] = _tool_results(model)
    assert refusal.startswith(approval_gate.APPROVAL_DENIED)
    assert result.answer == "I couldn't delete old.txt: nobody approved it."
    assert [item["tool"] for item in result.refused_actions] == ["delete_file"]
    assert "old.txt" in result.refused_actions[0]["what"]
    assert (result.status, result.exit_code) == ("refused", EXIT_REFUSED)


def test_under_hands_off_the_action_runs(monkeypatch, tmp_path):
    result, _, _ = _run(monkeypatch, tmp_path, asks(call("delete_file", "d1", path="old.txt")),
                        says("Deleted old.txt."), approval="hands-off")

    assert DELETED == ["old.txt"]
    assert (result.status, result.exit_code, result.refused_actions) == ("done", EXIT_DONE, [])


@pytest.mark.parametrize(("verdict", "deleted", "status"), [("approve", ["old.txt"], "done"), ("ask", [], "refused")])
def test_under_auto_the_reviewer_decides_and_what_it_would_ask_you_is_refused(monkeypatch, tmp_path,
                                                                              verdict, deleted, status):
    seen: list[dict] = []

    def review(payload, *, risks=(), **_kwargs):
        seen.append(dict(payload))
        return ReviewVerdict(verdict, "The task asks to delete old.txt." if verdict == "approve" else "Unclear.")

    monkeypatch.setattr(approval_review, "review", review)
    result, _, _ = _run(monkeypatch, tmp_path, asks(call("delete_file", "d1", path="old.txt")), says("Done."),
                        approval="auto")

    assert [item["tool"] for item in seen] == ["delete_file"]
    assert DELETED == deleted
    assert result.status == status


def test_a_question_for_the_person_goes_on_with_its_recommended_option(monkeypatch, tmp_path):
    question = {"header": "Which", "question": "Which file should go?", "multi_select": False,
                "options": [{"label": "old.txt (Recommended)"}, {"label": "new.txt"}]}
    result, model, _ = _run(monkeypatch, tmp_path, asks(call("ask_person", "q1", questions=[question])),
                            says("I went with old.txt."))

    [answer] = _tool_results(model)
    assert json.loads(answer)["outcome"] == "timed_out"
    assert result.unanswered_questions == 1
    assert (result.status, result.exit_code, result.answer) == ("done", EXIT_DONE, "I went with old.txt.")


class _FailingModel(ScriptedModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        raise RuntimeError("Error code: 401 - the key was refused")


def test_a_provider_error_ends_the_run_with_its_own_exit_code(monkeypatch, tmp_path):
    result, _, _ = _run(monkeypatch, tmp_path, model=_FailingModel(responses=[says("unused")], seen=[]))

    assert (result.status, result.exit_code) == ("provider_error", EXIT_PROVIDER)
    assert result.reason


def test_the_round_ceiling_ends_the_run_as_a_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_settings, "_round_ceiling", None)
    agent_settings.limit_rounds(1)
    result, model, _ = _run(monkeypatch, tmp_path, asks(call("ask_person", "q1", questions=[])),
                            asks(call("ask_person", "q2", questions=[])))

    assert len([request for request in model.seen if request]) >= 1
    assert (result.status, result.exit_code) == ("budget", EXIT_BUDGET)


def test_the_conversation_is_a_valid_atif_trajectory(monkeypatch, tmp_path):
    result, _, turns = _run(monkeypatch, tmp_path, asks(call("delete_file", "d1", path="old.txt")),
                            says("I couldn't delete old.txt."))
    usage = {"calls": 2, "input_tokens": 300, "output_tokens": 40, "cache_read_tokens": 100, "cost_usd": 0.0012}

    document = atif.trajectory(threads.get_latest_checkpoint_messages(turns.thread_id), session_id=turns.thread_id,
                               agent_version="9.9.9", model_name=turns.model_ref, prompts=result.prompts, usage=usage,
                               extra={"status": result.status})

    assert problems(document) == []
    people = [step for step in document["steps"] if step["source"] == "user" and "extra" not in step]
    assert [step["message"] for step in people] == ["Delete old.txt"]
    agents = [step for step in document["steps"] if step["source"] == "agent"]
    assert [call["function_name"] for call in agents[0]["tool_calls"]] == ["delete_file"]
    [observed] = agents[0]["observation"]["results"]
    assert observed["source_call_id"] == "d1" and observed["content"].startswith(approval_gate.APPROVAL_DENIED)
    assert agents[-1]["message"] == "I couldn't delete old.txt."
    assert document["final_metrics"] == {
        "total_prompt_tokens": 300, "total_completion_tokens": 40, "total_cached_tokens": 100,
        "total_cost_usd": 0.0012, "total_steps": len(document["steps"]), "extra": {"model_calls": 2}}
