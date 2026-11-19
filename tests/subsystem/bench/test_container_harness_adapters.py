"""Row-Bot as the harness inside a task container (PawBench, Harbor), up to the container step.

No Docker, no model: the transcript PawBench's grader reads is checked against the format its own
parser accepts (a hand-made fixture, no PawBench code), PawBench's scoring runs on made-up metrics files,
and the one follow-up the app makes after a turn runs on a scripted turn.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from tests.e2e.bench.agent_evals import product
from tests.e2e.bench.agent_evals.pawbench import score, transcript
from tests.e2e.bench.runtime import BenchEnvironmentError
from tests.e2e.bench.trajectory import Step

pytestmark = pytest.mark.subsystem


def test_a_conversation_becomes_the_openclaw_session_pawbenchs_grader_reads() -> None:
    messages = transcript.from_langchain([
        SystemMessage(content="You are Row-Bot."),
        HumanMessage(content="Summarise sales.csv into report.md"),
        AIMessage(content="", tool_calls=[{"name": "workspace_read_file", "args": {"path": "sales.csv"}, "id": "c1"}]),
        ToolMessage(content="region,total\nEU,10", tool_call_id="c1"),
        AIMessage(content=[{"type": "text", "text": "Saved report.md in the workspace."}]),
    ])
    lines = [json.loads(line) for line in transcript.session_jsonl(
        "rowbot-1", messages, model="ollama:qwen3.6:27b-q4_K_M", cwd="/app/working/workspaces/default").splitlines()]
    assert lines[0]["type"] == "session" and lines[0]["id"] == "rowbot-1"
    # What PawBench's _parse_openclaw_session_jsonl keeps: message events with user/assistant/toolResult roles.
    assert [line["message"]["role"] for line in lines[1:]] == ["user", "assistant", "toolResult", "assistant"]
    assert all(line["type"] == "message" for line in lines[1:])
    assert lines[2]["message"]["content"] == [{"type": "toolCall", "name": "workspace_read_file",
                                               "arguments": {"path": "sales.csv"}}]
    assert lines[3]["message"]["content"] == [{"type": "text", "text": "region,total\nEU,10"}]
    assert lines[4]["message"]["content"] == [{"type": "text", "text": "Saved report.md in the workspace."}]


def _metrics(folder, task: str, **doc) -> None:
    path = folder / f"{task}_some_task" / "output" / "metrics.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_scores_follow_pawbenchs_aggregator_on_a_task_subset(tmp_path) -> None:
    _metrics(tmp_path, "T001", task_score=1.0, grading_type="automated", breakdown={})
    _metrics(tmp_path, "T002", task_score=0.5, grading_type="hybrid",
             breakdown={"automated.files": 1.0, "automated.format": 0.6, "llm_judge.quality": 0.4})
    _metrics(tmp_path, "T003", task_score=0.9, grading_type="llm_judge", breakdown={})
    _metrics(tmp_path, "T004", task_score=0.7, grading_type="error", status="error")
    found = score.summarize(score.read_metrics(tmp_path), ["T001", "T002", "T003", "T004", "T005"])
    assert found["overall"] == pytest.approx((1.0 + 0.5 + 0.9 + 0 + 0) / 5)  # errored and missing count 0
    assert found["automated"] == pytest.approx((1.0 + 0.8) / 2)
    assert found["judge"] == pytest.approx((0.4 + 0.9) / 2)
    assert (found["tasks_errored"], found["tasks_missing"]) == (["T004"], ["T005"])


def test_only_pawbench_may_use_qwen36_and_never_a_personal_model() -> None:
    product.check_local_model("pawbench", "qwen3.6:27b-q4_K_M")
    product.check_local_model("terminal-bench", "qwen3.8:27b")
    for benchmark, model in (("terminal-bench", "qwen3.6:27b-q4_K_M"), ("pawbench", "gpt-oss:20b"),
                             ("pawbench", "row-bot-personal-v4:ee194a85")):
        with pytest.raises(BenchEnvironmentError):
            product.check_local_model(benchmark, model)


def _runtime(answers: list[str]) -> product.ProductRuntime:
    runtime = product.ProductRuntime(SimpleNamespace(), SimpleNamespace())  # type: ignore[arg-type]
    prompts: list[str] = []

    def turn(thread_id, prompt, name=""):
        prompts.append(prompt)
        return Step(kind="turn", name=name, prompt=prompt, answer=answers.pop(0))

    runtime.turn = turn  # type: ignore[method-assign]
    runtime.prompts = prompts  # type: ignore[attr-defined]
    return runtime


def test_after_the_task_the_app_s_one_follow_up_runs_and_never_chains(tmp_path) -> None:
    (tmp_path / "chart.png").write_bytes(b"png")
    claimed = _runtime(["I saved `report.md` and `chart.png` in the workspace.", "Saved report.md now."])
    steps = claimed.turn_with_followups("t1", "Make the report", [tmp_path])
    assert [step.name for step in steps] == ["task", "file check"]
    assert "`report.md`" in claimed.prompts[1] and "chart.png" not in claimed.prompts[1]
    assert claimed.followups == ["file check: ['report.md']"]

    carrying = _runtime(["Read the data. Next, I'll write the summary.", "Wrote summary.md."])
    assert [step.name for step in carrying.turn_with_followups("t1", "Summarise", [tmp_path])] == ["task", "carry on"]

    (tmp_path / "report.md").write_text("done", encoding="utf-8")
    done = _runtime(["I saved `report.md`."])
    assert [step.name for step in done.turn_with_followups("t1", "Make the report", [tmp_path])] == ["task"]


def test_a_benchmark_turn_binds_every_tool_but_the_ones_that_wait_for_a_person() -> None:
    import langgraph.prebuilt as prebuilt
    from langchain_core.tools import StructuredTool

    withheld: set[str] = set()
    original = prebuilt.ToolNode
    undo = product.withhold_person_tools(withheld)
    try:
        bound = prebuilt.ToolNode([StructuredTool.from_function(lambda query="": "", name=name, description=name)
                                   for name in ("run_command", "web_search", "ask_person")])
    finally:
        undo()
    assert set(bound.tools_by_name) == {"run_command", "web_search"} and withheld == {"ask_person"}
    assert prebuilt.ToolNode is original
