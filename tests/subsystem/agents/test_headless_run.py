"""``row-bot run`` answers what waits for a person as nobody can, and leaves a valid ATIF trajectory.

Real Agent turns (``agent.stream_agent`` on the graph ``AgentTurns`` builds) with a scripted model and fake
tools, driven by the headless run's own turn loop (``headless.run.drive``):

- under Ask, an action that needs approval is refused, does not run, and the run goes on to its answer;
- under Auto, the reviewer decides first, and only what it would ask the person about is refused;
- under Hands-off, it runs;
- a question to the person goes on with its recommended option;
- a provider error and the round ceiling end the run with their own exit codes;
- the model reads the headless lines (``harness_text``) in a run, and the app's own lines in the app.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.tools import tool

import row_bot.agent as agent_module
from row_bot import agent_settings, approval_review, questions, threads
from row_bot.application import conversation_followups
from row_bot.approval_review import ReviewVerdict
from row_bot.headless import atif
from row_bot.headless.cli import APPROVALS, EXIT_BUDGET, EXIT_DONE, EXIT_PROVIDER, EXIT_REFUSED, RunOptions
from row_bot.headless.run import drive, harness_text
from row_bot.tools import approval_gate
from tests.helpers.atif_schema import problems
from tests.helpers.learning_turns import AgentTurns, ScriptedModel, asks, call, says

pytestmark = pytest.mark.subsystem

DELETED: list[str] = []
QUESTION = {"header": "Which", "question": "Which file should go?", "multi_select": False,
            "options": [{"label": "old.txt (Recommended)"}, {"label": "new.txt"}]}


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


def _turns(monkeypatch, *replies, approval: str = "ask", model=None):
    model = model or ScriptedModel(responses=list(replies), seen=[])
    return AgentTurns(monkeypatch, model, TOOLS, approval_mode=APPROVALS[approval]), model


def _run(monkeypatch, tmp_path, *replies, approval: str = "ask", model=None):
    turns, model = _turns(monkeypatch, *replies, approval=approval, model=model)
    options = RunOptions(task="Delete old.txt", workspace=tmp_path, model="test-model", approval=approval)
    return drive(turns.thread_id, turns.model_ref, options), model, turns


def _tool_results(model: ScriptedModel) -> list[str]:
    return [str(message.content) for message in model.seen[-1] if message.type == "tool"]


def test_under_ask_an_action_that_needs_approval_is_refused_and_the_run_goes_on(monkeypatch, tmp_path):
    result, model, _ = _run(monkeypatch, tmp_path, asks(call("delete_file", "d1", path="old.txt")),
                            says("I couldn't delete old.txt: nobody approved it."))

    assert DELETED == []
    assert _tool_results(model) == [approval_gate.NO_PERSON_REFUSAL]
    assert result.answer == "I couldn't delete old.txt: nobody approved it."
    assert [item["tool"] for item in result.refused_actions] == ["delete_file"]
    assert "old.txt" in result.refused_actions[0]["what"]
    assert (result.status, result.exit_code) == ("refused", EXIT_REFUSED)


def test_in_the_app_the_persons_no_keeps_the_apps_own_line(monkeypatch):
    turns, model = _turns(monkeypatch, asks(call("delete_file", "d1", path="old.txt")), says("I won't."))

    turns.answer(False, turns.send("Delete old.txt"))

    assert DELETED == []
    [refusal] = _tool_results(model)
    assert refusal.startswith(approval_gate.APPROVAL_DENIED) and refusal != approval_gate.NO_PERSON_REFUSAL


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
    result, model, _ = _run(monkeypatch, tmp_path, asks(call("delete_file", "d1", path="old.txt")), says("Done."),
                            approval="auto")

    assert [item["tool"] for item in seen] == ["delete_file"]
    assert DELETED == deleted
    assert result.status == status
    if verdict == "ask":
        assert _tool_results(model) == [approval_gate.NO_PERSON_REFUSAL]


def test_a_question_for_the_person_is_answered_that_no_person_is_there(monkeypatch, tmp_path):
    result, model, _ = _run(monkeypatch, tmp_path, asks(call("ask_person", "q1", questions=[QUESTION])),
                            says("I went with old.txt."))

    [answer] = _tool_results(model)
    assert json.loads(answer)["outcome"] == "no_person"
    assert result.unanswered_questions == 1
    assert (result.status, result.exit_code, result.answer) == ("done", EXIT_DONE, "I went with old.txt.")


def test_in_the_app_an_unanswered_question_keeps_the_apps_own_answer(monkeypatch):
    turns, model = _turns(monkeypatch, asks(call("ask_person", "q1", questions=[QUESTION])), says("Went on."))
    [waiting] = [payload for kind, payload in turns.send("Delete old.txt") if kind == "interrupt"]

    list(turns.agent.resume_stream_agent([], turns.config, False,
                                         resume_values=questions.resume_values(waiting, questions.timed_out())))

    [answer] = _tool_results(model)
    assert json.loads(answer)["outcome"] == "timed_out"


class _FailingModel(ScriptedModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        raise RuntimeError("Error code: 401 - the key was refused")


def test_a_provider_error_ends_the_run_with_its_own_exit_code(monkeypatch, tmp_path):
    result, _, _ = _run(monkeypatch, tmp_path, model=_FailingModel(responses=[says("unused")], seen=[]))

    assert (result.status, result.exit_code) == ("provider_error", EXIT_PROVIDER)
    assert result.reason


def _wrap_up_model(monkeypatch) -> ScriptedModel:
    """The model the round ceiling's wrap-up is asked of, scripted."""
    wrap_up = ScriptedModel(responses=[says("Partial: nothing was deleted.")], seen=[])
    monkeypatch.setattr(agent_module, "_chat_only_llm", lambda *args, **kwargs: wrap_up)
    monkeypatch.setattr(agent_settings, "_round_ceiling", None)
    agent_settings.limit_rounds(1)
    return wrap_up


def _wrap_up_text(wrap_up: ScriptedModel) -> str:
    return "\n".join(str(message.content) for request in wrap_up.seen for message in request)


def test_the_round_ceiling_ends_the_run_as_a_budget_with_the_headless_wrap_up(monkeypatch, tmp_path):
    wrap_up = _wrap_up_model(monkeypatch)
    result, _, _ = _run(monkeypatch, tmp_path, asks(call("ask_person", "q1", questions=[])),
                        asks(call("ask_person", "q2", questions=[])))

    assert (result.status, result.exit_code) == ("budget", EXIT_BUDGET)
    assert agent_module.HEADLESS_WRAP_UP in _wrap_up_text(wrap_up)


def test_in_the_app_the_round_ceilings_wrap_up_is_the_apps_own(monkeypatch):
    wrap_up = _wrap_up_model(monkeypatch)
    turns, _ = _turns(monkeypatch, asks(call("ask_person", "q1", questions=[])))

    assert any(kind == "terminal" for kind, _payload in turns.send("Delete old.txt"))

    assert wrap_up.seen and agent_module.HEADLESS_WRAP_UP not in _wrap_up_text(wrap_up)


def test_a_reply_that_stops_on_its_next_step_gets_the_headless_carry_on(monkeypatch, tmp_path):
    result, model, turns = _run(monkeypatch, tmp_path, says("Next, I'll delete old.txt."), says("Done."))

    assert result.prompts == ["Delete old.txt", conversation_followups.HEADLESS_CARRY_ON_PROMPT]
    assert any(message.type == "human" and conversation_followups.HEADLESS_CARRY_ON_PROMPT in str(message.content)
               for message in model.seen[-1])
    app = conversation_followups.followup_after(turns.thread_id, "Next, I'll delete old.txt.", turns.model_ref)
    assert app is not None and app.prompt == conversation_followups.CARRY_ON_PROMPT


def test_a_run_carries_on_each_time_a_reply_stops_on_its_next_step(monkeypatch, tmp_path):
    result, _, _ = _run(monkeypatch, tmp_path, says("Next, I'll delete old.txt."),
                        says("Next, I'll check the folder is empty."), says("Done."))

    carry_on = conversation_followups.HEADLESS_CARRY_ON_PROMPT
    assert result.prompts == ["Delete old.txt", carry_on, carry_on]
    assert result.status == "done" and result.answer == "Done."


def test_the_conversation_is_a_valid_atif_trajectory_that_names_its_harness_text(monkeypatch, tmp_path):
    result, _, turns = _run(monkeypatch, tmp_path, asks(call("delete_file", "d1", path="old.txt")),
                            says("I couldn't delete old.txt."))
    usage = {"calls": 2, "input_tokens": 300, "output_tokens": 40, "cache_read_tokens": 100, "cost_usd": 0.0012}
    text = harness_text()

    document = atif.trajectory(threads.get_latest_checkpoint_messages(turns.thread_id), session_id=turns.thread_id,
                               agent_version="9.9.9", model_name=turns.model_ref, prompts=result.prompts, usage=usage,
                               extra={"status": result.status}, agent_extra={"harness_text": text})

    assert problems(document) == []
    assert document["agent"]["extra"]["harness_text"] == text
    assert approval_gate.NO_PERSON_REFUSAL in text["lines"].values() and len(text["sha256"]) == 64
    people = [step for step in document["steps"] if step["source"] == "user" and "extra" not in step]
    assert [step["message"] for step in people] == ["Delete old.txt"]
    agents = [step for step in document["steps"] if step["source"] == "agent"]
    assert [call["function_name"] for call in agents[0]["tool_calls"]] == ["delete_file"]
    [observed] = agents[0]["observation"]["results"]
    assert (observed["source_call_id"], observed["content"]) == ("d1", approval_gate.NO_PERSON_REFUSAL)
    assert agents[-1]["message"] == "I couldn't delete old.txt."
    assert document["final_metrics"] == {
        "total_prompt_tokens": 300, "total_completion_tokens": 40, "total_cached_tokens": 100,
        "total_cost_usd": 0.0012, "total_steps": len(document["steps"]), "extra": {"model_calls": 2}}
