"""Repeats and the pre-registered paired analysis inside evaluation (O88), and malformed calls
counted as failed (O70): every problem can run several times per arm (hidden problems three), a
problem counts as its pass fraction, each arm is compared with the first by the mean paired
difference, a 95% cluster-bootstrap interval (a source chat is a cluster, a pack task its own) and a
paired permutation p-value with a recorded seed; only these aggregates leave an audit run; a
runtime that answers the same every time is recorded; the runtime's refusal of the model's own
malformed tool call is a failure, a real outage stays outside."""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from row_bot.learning import eval_worker, evaluation
from row_bot.learning.evaluation import Arm, Budget, CaseResult, ReplayCase, compare, paired_analysis
from row_bot.learning.packs import visible_tasks
from row_bot.learning.spending import TokenMeter
from tests.fixtures.learning_eval import ScriptedClient

pytestmark = [pytest.mark.subsystem]

XML_500 = "XML syntax error on line 8: element <function> closed by </parameter> (status code: 500)"


# ── The analysis itself: known inputs, known answers ───────────────────────


def test_no_difference_gives_a_zero_interval_and_a_p_value_of_one():
    result = paired_analysis([("a", 0.0), ("b", 0.0), ("c", 0.0)])

    assert (result["difference"], result["low"], result["high"], result["p_value"]) == (0.0, 0.0, 0.0, 1.0)


def test_the_same_gain_everywhere_has_no_spread_and_the_permutation_test_lists_every_sign():
    result = paired_analysis([("a", 1.0), ("b", 1.0), ("c", 1.0)])

    assert (result["difference"], result["low"], result["high"]) == (1.0, 1.0, 1.0)
    # Three clusters: all 8 sign patterns, of which only all-plus and all-minus reach |3|.
    assert result["exact"] and result["p_value"] == 0.25


def test_clusters_are_resampled_and_flipped_together_with_a_recorded_seed():
    differences = [("chat-a", 1.0), ("chat-a", 0.0), ("chat-b", -1 / 3), ("task-1", 2 / 3), ("task-2", 0.0),
                   ("task-3", 1.0)]

    result = paired_analysis(differences)

    # Six problems in five clusters; the mean is over problems.
    assert (result["problems"], result["clusters"], result["difference"]) == (6, 5, 0.388889)
    assert (result["low"], result["high"], result["p_value"]) == (0.0, 0.733333, 0.25)
    assert (result["resamples"], result["permutations"], result["seed"]) == (10_000, 10_000, evaluation.ANALYSIS_SEED)
    assert paired_analysis(differences) == result  # the same seed, the same answer


def test_many_clusters_use_ten_thousand_random_sign_flips():
    result = paired_analysis([(f"task-{index}", 1.0) for index in range(20)])

    # 2^20 patterns is too many to list: (hits + 1) / (10,000 + 1), and no flip reaches |20| here.
    assert not result["exact"] and result["p_value"] == round(1 / 10_001, 6)


def test_what_the_interval_says():
    assert evaluation.interval_verdict({"low": 0.05, "high": 0.4}) == "better"
    assert evaluation.interval_verdict({"low": -0.4, "high": -0.01}) == "worse"
    assert evaluation.interval_verdict({"low": -0.1, "high": 0.3}) == "unclear"
    assert evaluation.interval_verdict({"low": None, "high": None}) == "unclear"


# ── Repeats through a comparison ────────────────────────────────────────────


class Runner:
    """Plays pack tasks: ``steady`` passes every run the same way; ``sometimes`` passes one run in
    three, each run a little different."""

    signature = "fake-runner-1"

    def __init__(self) -> None:
        self.runs: dict[tuple[str, str], int] = {}

    def run(self, task, arm, *, budget, meter, arm_left):
        count = self.runs[(task.id, arm.name)] = self.runs.get((task.id, arm.name), 0) + 1
        passed = arm.name == "steady" or count % 3 == 1
        return CaseResult(task.id, "pack", task.kind_of_work, "passed" if passed else "failed", "answer",
                          tokens=1_000 if arm.name == "steady" else 1_000 + count, steps=2)


def test_every_problem_runs_as_often_as_asked_and_counts_as_its_pass_fraction():
    tasks = [task for task in visible_tasks() if task.pack == "document_lookups"][:4]
    runner = Runner()

    result = compare([Arm("steady"), Arm("sometimes")], tasks=tasks, runner=runner, meter=TokenMeter(10**9),
                     save=False, repeats=3)

    assert set(runner.runs.values()) == {3}
    steady, sometimes = result.arms
    assert (steady.total, sometimes.total) == (12, 12)  # runs
    assert steady.by_kind == {"Document lookups": (4, 4)} and sometimes.by_kind == {"Document lookups": (1, 4)}
    analysis = result.analysis["arms"]["sometimes"]["overall"]
    assert (analysis["problems"], analysis["clusters"], analysis["baseline"], analysis["arm"]) == (4, 4, 1.0, 0.333333)
    assert analysis["difference"] == -0.666667 and analysis["interval_verdict"] == "worse"
    assert analysis["enough"] is False  # fewer than 30 problems: descriptive only
    assert result.analysis["arms"]["sometimes"]["by_kind"]["Document lookups"]["problems"] == 4
    # The steady arm answered the same every time (as greedy decoding would); the other didn't.
    assert result.repeated == {"steady": {"problems": 4, "identical": 4}, "sometimes": {"problems": 4, "identical": 0}}


def test_an_ablation_names_the_pairs_it_contrasts_beyond_each_arm_against_the_first():
    tasks = [task for task in visible_tasks() if task.pack == "document_lookups"][:4]

    result = compare([Arm("sometimes"), Arm("steady"), Arm("other")], tasks=tasks, runner=Runner(),
                     meter=TokenMeter(10**9), save=False, repeats=3, pairs=[("other", "steady")])

    assert set(result.analysis["arms"]) == {"steady", "other"}  # each against the first, as before
    contrast = result.analysis["pairs"]["steady vs other"]["overall"]
    assert (contrast["baseline"], contrast["arm"], contrast["difference"]) == (0.333333, 1.0, 0.666667)
    with pytest.raises(evaluation.EvaluationError, match="unknown_arm"):
        compare([Arm("sometimes"), Arm("steady")], tasks=tasks, runner=Runner(), meter=TokenMeter(10**9),
                save=False, pairs=[("sometimes", "missing")])


def test_a_replayed_problem_belongs_to_its_source_chat():
    def case(decision: str, chat: str) -> ReplayCase:
        return ReplayCase(decision, chat, chat, 1.0, "Your work", "exact", False,
                          ({"type": "human", "data": {"content": "Which file?"}},), None, None,
                          evaluation.Action(text="notes.txt"), "captured", "exact_action", "Which file?")

    cases = [case("dec_1", "chat-a"), case("dec_2", "chat-a"), case("dec_3", "chat-b")]
    client = ScriptedClient(lambda _messages, _tools, model: AIMessage(content="notes.txt" if model == "good" else "x"))

    result = compare([Arm("bad", model_ref="bad"), Arm("good", model_ref="good")], replay=cases, client=client,
                     meter=TokenMeter(10**9), save=False, repeats=2)

    overall = result.analysis["arms"]["good"]["overall"]
    assert (overall["problems"], overall["clusters"], overall["difference"]) == (3, 2, 1.0)
    assert len(client.calls) == 3 * 2 * 2


def test_a_cached_arms_repeats_are_each_kept_and_reused(data_dir):
    tasks = [task for task in visible_tasks() if task.pack == "document_lookups"][:2]
    cache = evaluation.ArmCache(data_dir)
    first = Runner()
    compare([Arm("sometimes"), Arm("steady", cache=True)], tasks=tasks, runner=first, cache=cache,
            meter=TokenMeter(10**9), save=False, repeats=3)
    again = Runner()

    result = compare([Arm("sometimes"), Arm("steady", cache=True)], tasks=tasks, runner=again,
                     cache=evaluation.ArmCache(data_dir), meter=TokenMeter(10**9), save=False, repeats=3)

    assert first.runs[(tasks[0].id, "steady")] == 3 and (tasks[0].id, "steady") not in again.runs
    assert result.arms[1].cached == 6 and result.arms[1].by_kind == {"Document lookups": (2, 2)}


def test_a_cached_result_keeps_why_it_didnt_pass(data_dir):
    tasks = [task for task in visible_tasks() if task.pack == "document_lookups"][:2]

    class Malformed(Runner):
        def run(self, task, arm, *, budget, meter, arm_left):
            super().run(task, arm, budget=budget, meter=meter, arm_left=arm_left)
            return CaseResult(task.id, "pack", task.kind_of_work, "failed", "answer", tokens=900, steps=3,
                              cause=evaluation.MALFORMED_CALL)

    arms = [Arm("start"), Arm("main", cache=True)]
    first = compare(arms, tasks=tasks, runner=Malformed(), cache=evaluation.ArmCache(data_dir),
                    meter=TokenMeter(10**9), save=False)

    again = compare(arms, tasks=tasks, runner=Malformed(), cache=evaluation.ArmCache(data_dir),
                    meter=TokenMeter(10**9), save=False)

    assert again.arms[1].cached == 2
    assert again.arms[1].causes == first.arms[1].causes == {evaluation.MALFORMED_CALL: 2}


def test_a_local_main_models_kept_results_cost_nothing_the_second_time(data_dir):
    tasks = [task for task in visible_tasks() if task.pack == "document_lookups"][:2]

    class Local(Runner):  # a main model on this computer: every token it uses is local
        def run(self, task, arm, *, budget, meter, arm_left):
            return replace(super().run(task, arm, budget=budget, meter=meter, arm_left=arm_left),
                           local_tokens=1_000 if arm.name == "steady" else 0)

    arms = [Arm("sometimes"), Arm("steady", cache=True)]
    compare(arms, tasks=tasks, runner=Local(), cache=evaluation.ArmCache(data_dir), meter=TokenMeter(10**9),
            save=False)

    again = compare(arms, tasks=tasks, runner=Local(), cache=evaluation.ArmCache(data_dir), meter=TokenMeter(10**9),
                    save=False)

    steady = again.arms[1]
    assert (steady.cached, steady.tokens, steady.local_tokens, steady.run.cost.tokens) == (2, 0, 0, 0)
    assert steady.measured_tokens == 2_000  # what the kept results measured is still reported


# ── O70: a malformed call is the model's, an outage isn't ──────────────────


def _raises(error: Exception):
    def respond(_messages, _tools, _model):
        raise error

    return respond


def test_a_runtime_refusing_the_models_malformed_tool_call_counts_as_failed():
    task = next(task for task in visible_tasks() if task.pack == "document_lookups")

    malformed = evaluation.run_pack_task(task, Arm("v1"), client=ScriptedClient(_raises(RuntimeError(XML_500))),
                                         budget=Budget())
    outage = evaluation.run_pack_task(task, Arm("v1"), client=ScriptedClient(_raises(ConnectionError("refused"))),
                                      budget=Budget())

    assert (malformed.status, malformed.cause, malformed.counted) == ("failed", evaluation.MALFORMED_CALL, True)
    assert (outage.status, outage.counted) == ("error", False)  # a real outage stays outside the denominator


def test_a_replayed_decision_whose_call_the_runtime_refused_failed():
    case = ReplayCase("dec_1", "chat-a", "chat-a", 1.0, "Your work", "exact", False,
                      ({"type": "human", "data": {"content": "Which file?"}},), None, None,
                      evaluation.Action(text="notes.txt"), "captured", "exact_action", "Which file?")

    result = evaluation.run_replay_case(case, Arm("v1"), client=ScriptedClient(
        _raises(RuntimeError("error parsing tool call: raw='{\"name\": ', err=unexpected end of JSON input"))))

    assert (result.status, result.cause) == ("failed", evaluation.MALFORMED_CALL)


def test_the_worker_tells_a_malformed_call_from_an_outage_by_the_runtimes_own_error():
    gate = eval_worker._TaskGate()  # noqa: SLF001 - the worker's per-task gate sees each failed call's error
    gate.reset(150_000, None)
    gate.failed(ConnectionError("Failed to connect to Ollama"))
    assert not gate.malformed_call
    gate.failed(RuntimeError(XML_500))
    assert gate.malformed_call
    gate.reset(150_000, None)
    assert not gate.malformed_call  # each task starts clean

    record = eval_worker.TurnRecord()
    assert eval_worker.failure_cause("failed", eval_worker.MALFORMED_DETAIL, record) == "malformed_call"
    assert eval_worker.CAUSES["malformed_call"] and evaluation.MALFORMED_DETAIL == eval_worker.MALFORMED_DETAIL

