"""Evaluation of pack tasks: arms on the same tasks with the same budget, TestRuns with their
denominator, the monthly token limit, the judge's calibration, and isolation from the
person's conversations and settings."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from row_bot.learning import evaluation
from row_bot.learning.contracts import TestRun, from_json, to_json
from row_bot.learning.evaluation import Arm, Budget, Calibration, CalibrationCase, compare
from row_bot.learning.packs import visible_tasks
from row_bot.learning.spending import TokenMeter
from row_bot.learning.store import LearningStore
from tests.fixtures.learning import learning_files
from tests.fixtures.learning_eval import ScriptedClient, guidance_of, oracle, results_of, says

pytestmark = pytest.mark.subsystem

LESSON = "When two documents share a title, use the current one, not the superseded or draft one."


def _docs(count: int = 16):
    return visible_tasks(["document_lookups"])[:count]


def _non_code():
    return [task for task in visible_tasks() if task.verifier.kind != "code_tests"]


def _lesson_sensitive(tasks):
    """Right on every task with the lesson; without it, right only on the first half."""

    first_half = {task.id for task in tasks[: len(tasks) // 2]}

    def when(messages, task):
        return "reference" if LESSON in guidance_of(messages) or task.id in first_half else "decoy"

    return oracle(tasks, when=when)


def test_a_change_is_compared_with_none_on_the_same_tasks_and_the_counts_carry_their_denominator(learning_on):
    tasks = _docs()
    client = ScriptedClient(_lesson_sensitive(tasks), tokens=(2_500, 150))
    result = compare([Arm("without"), Arm("with", guidance=(LESSON,), subject_kind="lesson", subject_id="les_1")],
                     tasks=tasks, client=client, meter=TokenMeter(10_000_000))

    without, with_lesson = result.arms
    assert (without.passed, without.total) == (8, 16) and (with_lesson.passed, with_lesson.total) == (16, 16)
    assert without.by_kind == {"Document lookups": (8, 16)} and with_lesson.by_kind == {"Document lookups": (16, 16)}
    assert with_lesson.describe() == "16 of 16 passed"
    assert result.paired == {"better": 8, "worse": 0, "same": 8, "p_value": pytest.approx(0.0078, abs=1e-4)}
    # Both arms ran every task in the same order, one call each, so both used the same tokens.
    assert without.tokens == with_lesson.tokens == 16 * 2_650
    assert [call["purpose"] for call in client.calls] == ["learning_test"] * 32
    assert result.note == "Testing used 85K tokens · Small test, so expect some variation."
    assert with_lesson.run.subject_kind == "lesson" and with_lesson.run.baseline_id == without.run.id
    assert with_lesson.run.comparison_set == without.run.comparison_set


def test_test_runs_are_saved_with_their_results_and_the_tasks_they_ran(learning_on):
    tasks = _docs(3)
    store = LearningStore()
    result = compare([Arm("without")], tasks=tasks, client=ScriptedClient(oracle(tasks)), meter=TokenMeter(10**7))

    [run] = store.find("test_run")
    assert isinstance(run, TestRun) and from_json(to_json(run)) == run
    assert (run.passed, run.failed, run.total, run.suite, run.pack) == (3, 0, 3, "pack", "document_lookups")
    assert run.cost.tokens == result.arms[0].tokens == 3 * 1_100 and run.status == "completed"
    body = results_of(store, run)
    assert body["metric_version"] == evaluation.METRIC_VERSION and body["budget"]["task_tokens"] == 150_000
    assert [case["task"] for case in body["cases"]] == [task.id for task in tasks]
    assert {fixture.id for fixture in store.find("fixture")} == {f"fix_{task.id}" for task in tasks}


def test_every_kind_of_pack_task_runs_through_the_harness_in_a_sandbox(learning_on):
    tasks = _non_code()
    good = compare([Arm("a")], tasks=tasks, client=ScriptedClient(oracle(tasks)), meter=TokenMeter(10**9), save=False)
    bad = compare([Arm("a")], tasks=tasks, client=ScriptedClient(oracle(tasks, solution="decoy")),
                  meter=TokenMeter(10**9), save=False)

    assert good.arms[0].passed == good.arms[0].total == len(tasks)
    assert bad.arms[0].passed == 0 and bad.arms[0].total == len(tasks)
    assert set(good.arms[0].by_kind) == {"Document lookups", "Structured transforms", "Tool workflows"}


@pytest.mark.platform
def test_a_coding_task_is_checked_by_running_its_tests_in_the_sandbox(learning_on):
    task = next(task for task in visible_tasks(["small_coding"]) if task.id == "code_01")
    good = compare([Arm("a")], tasks=[task], client=ScriptedClient(oracle([task])), meter=TokenMeter(10**9),
                   save=False, backend="local")
    bad = compare([Arm("a")], tasks=[task], client=ScriptedClient(oracle([task], solution="decoy")),
                  meter=TokenMeter(10**9), save=False, backend="local")
    assert good.arms[0].cases[0].status == "passed" and good.arms[0].cases[0].check == "code_tests"
    assert bad.arms[0].cases[0].status == "failed" and "tests failed" in bad.arms[0].cases[0].detail


def test_arms_share_one_budget_and_a_task_that_runs_out_of_it_fails(learning_on):
    tasks = _docs(4)
    budget = Budget(task_tokens=6_000, max_steps=1, arm_tokens=9_000)
    client = ScriptedClient(oracle(tasks), tokens=(2_000, 100))
    result = compare([Arm("a"), Arm("b")], tasks=tasks, client=client, budget=budget, meter=TokenMeter(10**9))

    for arm in result.arms:
        # 2,100 tokens a task: four would pass the arm's 9,000, so the last one is out of budget.
        assert [case.status for case in arm.cases] == ["passed", "passed", "passed", "budget"]
        assert (arm.passed, arm.total, arm.tokens) == (3, 4, 6_300)
        assert results_of(LearningStore(), arm.run)["budget"] == {"task_tokens": 6_000, "max_steps": 1,
                                                                   "arm_tokens": 9_000}
    assert len(client.calls) == 6

    # A task's own budget: an estimate past it is never sent.
    tight = compare([Arm("a")], tasks=tasks[:1], client=ScriptedClient(oracle(tasks)), budget=Budget(task_tokens=500),
                    meter=TokenMeter(10**9), save=False)
    assert tight.arms[0].cases[0].status == "budget" and tight.arms[0].total == 1 and tight.arms[0].passed == 0


def test_a_task_that_never_finishes_runs_out_of_steps(learning_on):
    task = next(task for task in visible_tasks(["tool_workflows"]) if task.id == "tool_01")
    looping = ScriptedClient(lambda *_: AIMessage(content="", tool_calls=[
        {"name": "calendar_list_events", "args": {}, "id": "c1", "type": "tool_call"}]))
    result = compare([Arm("a")], tasks=[task], client=looping, budget=Budget(max_steps=3), meter=TokenMeter(10**9),
                     save=False)
    assert result.arms[0].cases[0].status == "budget" and result.arms[0].cases[0].steps == 3
    assert result.arms[0].cases[0].detail == "ran out of steps"


def test_the_monthly_limit_stops_a_run_cleanly_before_the_call_that_would_pass_it(learning_on):
    tasks = _docs(6)
    client = ScriptedClient(oracle(tasks), tokens=(3_000, 200))
    # Each call is estimated at about 5.5K tokens (what is sent plus room for the answer).
    meter = TokenMeter(limit=2_000_000, used=2_000_000 - 30_000)
    result = compare([Arm("a"), Arm("b")], tasks=tasks, client=client, meter=meter)

    assert result.stopped == "Stopped: Learning reached this month's token limit (2,000,000 tokens)."
    sent = len(client.calls)
    assert 0 < sent < 12 and meter.spent == sent * 3_200 and meter.spent <= 30_000
    for arm in result.arms:
        statuses = [case.status for case in arm.cases]
        # Both arms stop at the same task: the round the limit cut is not counted for either.
        assert statuses.count("passed") == sent // 2 and statuses[sent // 2:] == ["not_run"] * (6 - sent // 2)
        assert arm.run.status == "stopped" and arm.total == sent // 2
    assert result.note.endswith(result.stopped)


def test_a_limit_of_zero_makes_no_model_calls_and_local_models_never_count(learning_on):
    tasks = _docs(2)
    cloud = ScriptedClient(oracle(tasks))
    stopped = compare([Arm("a")], tasks=tasks, client=cloud, meter=TokenMeter(limit=0), save=False)
    assert cloud.calls == [] and stopped.stopped.startswith("Stopped: Learning's monthly token limit is 0")

    local = ScriptedClient(oracle(tasks), local=True, tokens=(4_000, 500))
    meter = TokenMeter(limit=0)
    ran = compare([Arm("a", model_ref="model:ollama:qwen3:8b")], tasks=tasks, client=local, meter=meter, save=False)
    assert ran.stopped == "" and ran.arms[0].passed == 2 and meter.spent == 0 and meter.local_tokens == 9_000
    assert ran.tokens == 0 and ran.local_tokens == 9_000
    assert ran.arms[0].run.cost.tokens == 0
    assert ran.note.startswith("Testing used 0 tokens (and 9K on this computer)")


def test_the_meter_counts_this_months_learning_calls_from_the_ledger(learning_on, monkeypatch):
    from row_bot.learning import spending
    from row_bot.prompt_cache import ModelUsage
    from row_bot.providers.usage_ledger import UsageRecord, record_model_usage

    now = 1_800_000_000.0
    month = spending.month_start(now)
    rows = [("learning_test", "openai", 1_000, 200, month + 10), ("learning_teacher", "anthropic", 500, 50, month + 20),
            ("learning_test", "ollama", 9_000, 900, month + 30), ("main", "openai", 7_000, 70, month + 40),
            ("learning_meta", "openai", 4_000, 400, month - 10)]
    for purpose, provider, tokens_in, tokens_out, at in rows:
        assert record_model_usage(UsageRecord(purpose=purpose, provider=provider, model="m", created_at=at,
                                              usage=ModelUsage(input_tokens=tokens_in, output_tokens=tokens_out)))
    assert spending.used_this_month(now=now) == 1_750  # last month's, local and non-Learning calls left out
    meter = TokenMeter.for_month(now=now, limit=2_000)
    assert (meter.limit, meter.used_before, meter.remaining) == (2_000, 1_750, 250)
    assert TokenMeter(1_000_000, 0, share=0.2).remaining == 200_000  # the recursive layer's share (E13)


def test_learning_calls_are_tagged_in_the_usage_ledger_on_the_default_model_at_the_lowest_effort(learning_on,
                                                                                                    monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from row_bot import models
    from row_bot.learning import spending
    from row_bot.providers import background
    from row_bot.providers.usage_ledger import PURPOSES, attach_usage_ledger

    assert {"learning_diagnosis", "learning_test", "learning_teacher", "learning_meta"} <= set(PURPOSES)

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    asked: list[str] = []

    def get_llm_for(ref, reasoning_plan=None):
        asked.append(ref)
        reply = AIMessage(content="85", usage_metadata={"input_tokens": 1_200, "output_tokens": 30, "total_tokens": 1_230})
        return attach_usage_ledger(Model(responses=[reply] * 4), provider_id="openai", model_name="gpt-default")

    monkeypatch.setattr(models, "get_current_model", lambda: "model:openai:gpt-default")
    monkeypatch.setattr(models, "get_llm_for", get_llm_for)
    plans = []
    monkeypatch.setattr(background, "background_reasoning", lambda ref, *a, **k: plans.append(ref) or
                        background.BackgroundReasoning(background.ReasoningRequestPlan(ref)))
    tasks = _docs(1)
    result = compare([Arm("a")], tasks=tasks, meter=TokenMeter(10**9), save=False)

    assert result.arms[0].passed == 1 and result.arms[0].tokens == 1_230
    assert asked == ["model:openai:gpt-default"] and plans  # the default model, with the background (lowest) plan
    assert spending.used_this_month() == 1_230
    from tests.e2e.live_eval.ledger import rows_after

    [row] = rows_after(Path(learning_on) / "usage.db")
    assert (row.purpose, row.provider, row.input_tokens) == ("learning_test", "openai", 1_200)
    assert background.background_model_ref("learning_test", task_model="model:ollama:qwen3:8b") == "model:ollama:qwen3:8b"


def test_a_judge_decides_only_once_calibrated_and_its_calibration_is_reported(learning_on):
    class Judge:
        model_ref = None

        def __init__(self, right: bool) -> None:
            self.right = right
            self.tokens = 0

        def judge(self, task, reference, candidate):
            same = candidate == reference
            return same if self.right else True

    cases = evaluation.calibration_cases(_docs())
    assert len(cases) == 32 and sum(case.executable for case in cases) == 16
    good = evaluation.calibrate(Judge(True), cases)
    lazy = evaluation.calibrate(Judge(False), cases)
    assert (good.agreed, good.cases, good.reliable) == (32, 32, True)
    assert good.describe() == "The judge agreed with executable checks on 32 of 32 tasks (100%)."
    assert (lazy.agreed, lazy.false_pass, lazy.reliable) == (16, 16, False)

    from row_bot.learning.evaluation import Action, ReplayCase

    long_answer = ("The plan: " + "first we check the venue, then the catering, then the badges. " * 4).strip()
    case = ReplayCase("dec_1", "chat-1", "chat-1", 1.0, "Your work", "exact", False,
                      ({"type": "human", "data": {"content": "Plan the launch", "type": "human"}},), None, None,
                      Action(text=long_answer), "captured", "exact_action", "Plan the launch")
    client = ScriptedClient(says(long_answer))
    judged = compare([Arm("a")], replay=[case], client=client, judge=Judge(True), calibration=good,
                     meter=TokenMeter(10**9))
    unjudged = compare([Arm("a")], replay=[case], client=client, judge=Judge(False), calibration=lazy,
                       meter=TokenMeter(10**9), save=False)
    assert judged.arms[0].cases[0].status == "passed" and judged.arms[0].cases[0].check == "judge"
    assert results_of(LearningStore(), judged.arms[0].run)["judge"]["agreement"] == 1.0
    assert unjudged.arms[0].cases[0].status == "unverified" and unjudged.arms[0].total == 0
    with pytest.raises(evaluation.EvaluationError, match="judge_is_a_subject"):
        judge = Judge(True)
        judge.model_ref = "model:ollama:personal-v3"
        compare([Arm("v3", model_ref="model:ollama:personal-v3", subject_kind="release")], replay=[case],
                client=client, judge=judge, calibration=good, meter=TokenMeter(10**9))


def test_a_model_judge_answers_pass_or_fail_and_counts_its_tokens(learning_on):
    client = ScriptedClient(says("Pass."), tokens=(300, 2))
    judge = evaluation.ModelJudge(client, meter=TokenMeter(10**6))
    assert judge.judge("Reply 85", "85", "85 EUR") is True and judge.tokens == 302
    client.respond = says("I think so")
    assert judge.judge("Reply 85", "85", "about 85") is None
    calibration = evaluation.calibrate(judge, [CalibrationCase("t", "a", "a", True)])
    assert calibration == Calibration(1, 0, 0, 1, 906)


def test_evaluation_never_touches_the_persons_conversations_memory_or_settings(learning_on):
    root = Path(learning_on)
    for name, content in {"threads.db": b"chats", "memory.db": b"memories", "tools_config.json": b"{}",
                          "providers.json": b"{}", "settings.json": b"{}"}.items():
        (root / name).write_bytes(content)
    (root / "workspace").mkdir()
    before = {path.relative_to(root).as_posix(): (path.stat().st_mtime_ns, path.read_bytes())
              for path in root.rglob("*") if path.is_file()}
    tasks = _non_code()
    sandboxes: list[Path] = []
    real = evaluation.Sandbox

    class Watched(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            sandboxes.append(self.root)

    import row_bot.learning.evaluation as module

    original = module.Sandbox
    module.Sandbox = Watched
    try:
        compare([Arm("a"), Arm("b", guidance=(LESSON,))], tasks=tasks, client=ScriptedClient(oracle(tasks)),
                meter=TokenMeter(10**9))
    finally:
        module.Sandbox = original
    after = {path.relative_to(root).as_posix(): (path.stat().st_mtime_ns, path.read_bytes())
             for path in root.rglob("*") if path.is_file()}
    changed = {name for name in set(before) | set(after) if before.get(name) != after.get(name)}
    # Only Learning's own records changed (its TestRuns and fixtures); no chat, memory or setting.
    assert changed and all(name.startswith(("learning.db", "learning/")) for name in changed)
    assert len(sandboxes) == 2 * len(tasks)
    assert all(not path.exists() and root not in path.parents for path in sandboxes)


def test_with_learning_off_a_test_writes_nothing(data_dir):
    tasks = _docs(2)
    result = compare([Arm("a")], tasks=tasks, client=ScriptedClient(oracle(tasks)), meter=TokenMeter(10**9))
    assert result.arms[0].passed == 2 and result.arms[0].run.results_blob is None
    assert learning_files(data_dir) == []


def test_sample_statistics_follow_the_fixed_definitions() -> None:
    assert evaluation.sign_test(8, 0) == pytest.approx(0.0078125)
    assert evaluation.sign_test(3, 3) == 1.0 and evaluation.sign_test(0, 0) == 1.0
    assert evaluation.wilson_half_width(21, 24) > 0.12
    assert evaluation.wilson_half_width(950, 1000) < 0.02
