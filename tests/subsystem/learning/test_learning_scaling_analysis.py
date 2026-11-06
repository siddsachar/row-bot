"""The scaling study's analysis (PREREG-SCALING §6-§8) on made-up results: Holm across S1-S3, "not tested", the
unbounded gap closed (A12's rule), the dose-response slope and the agreement-outcome correlation."""

from __future__ import annotations

import math

import pytest

from tests.e2e.learning.scaling import analysis
from tests.e2e.learning.scaling.design import Design

pytestmark = [pytest.mark.subsystem]

TASKS = [f"mara-d{101 + n}-0" for n in range(12)]
CLUSTERS = {task: f"mara/d{101 + n}" for n, task in enumerate(TASKS)}
DESIGN = Design(resamples=400, permutations=400, min_tasks=10)


def _runs(passing: set[int], *, runs: int = 3, outages: int = 0) -> list[dict]:
    found = [{"task": task, "status": "passed" if number in passing else "failed", "cause": "" if number in passing
              else "wrong_answer"} for number, task in enumerate(TASKS) for _ in range(runs)]
    return found + [{"task": TASKS[0], "status": "error", "cause": "provider_error"}] * outages


def _arm(passing: set[int], **extra: object) -> dict:
    return {"state": "done", "runs": _runs(passing), "replay": [], "hidden": {}, **extra}


def _arms(**overrides: dict) -> dict:
    arms = {"start": _arm(set()), "sft-100": _arm({0}, n=100), "sft-300": _arm({0, 1}, n=300),
            "sft-1000": _arm({0, 1, 2, 3, 4, 5}, n=1000), "sft-3000": _arm(set(range(12)), n=3000),
            "sft-1000b": _arm({0, 1, 2, 3, 4}, n=1000), "dagger-1000": _arm({0, 1, 2, 3, 4, 6}, n=1000),
            "retrieval": _arm({0}), "local-27b": _arm({0, 1}), "teacher": _arm(set(range(12)))}
    arms.update(overrides)
    return arms


def test_the_three_hypotheses_are_holm_adjusted_together():
    from row_bot.learning.evaluation import holm

    found = analysis.analyse(_arms(), CLUSTERS, DESIGN)["confirmatory"]

    raw = {name: item["analysis"]["p_value"] for name, item in found.items()}
    assert {name: item["holm_p"] for name, item in found.items()} == holm(raw, family=["S1", "S2", "S3"])
    assert found["S1"]["analysis"]["difference"] == 1.0 and found["S1"]["verdict"] == "holds"
    assert found["S2"]["analysis"]["difference"] == 0.0 and found["S2"]["verdict"] == "no difference shown"
    assert found["S3"]["analysis"]["difference"] == pytest.approx(5 / 12, abs=1e-6)


def test_fewer_counted_tasks_than_the_minimum_is_not_tested():
    found = analysis.analyse(_arms(), CLUSTERS, Design(resamples=200, permutations=200, min_tasks=60))["confirmatory"]

    for item in found.values():
        assert item["tested"] is False and item["verdict"] == "not tested" and item["holm_p"] is None
        assert "fewer than 60" in item["why"] and item["analysis"]["problems"] == 12


def test_an_arm_that_couldnt_start_leaves_its_hypothesis_untested_and_in_the_family():
    from row_bot.learning.evaluation import holm

    arms = _arms(**{"sft-3000": {"state": "couldnt_start", "why": "no pass passed the ready check"}})

    found = analysis.analyse(arms, CLUSTERS, DESIGN)["confirmatory"]

    assert found["S1"]["tested"] is False and "no pass passed the ready check" in found["S1"]["why"]
    expected = holm({"S1": None, "S2": found["S2"]["analysis"]["p_value"], "S3": found["S3"]["analysis"]["p_value"]},
                    family=["S1", "S2", "S3"])
    assert found["S3"]["holm_p"] == expected["S3"] and found["S2"]["holm_p"] == expected["S2"]


def test_outages_and_undecided_runs_stay_outside_the_denominator():
    arms = _arms(start=_arm(set(), runs=_runs(set(), outages=2)))

    rates = analysis.analyse(arms, CLUSTERS, DESIGN)["rates"]["start"]

    assert rates["tasks"] == 12 and rates["outside_denominator"] == {"error": 2, "unverified": 0}
    assert rates["runs_passed"]["total"] == 36


def test_gap_closed_is_unbounded_when_the_teacher_rarely_beats_the_start():
    start, teacher, arm = (analysis.scores(_runs(passing)) for passing in (set(), {0}, {0, 1, 2}))

    found = analysis.gap_closed(arm, start, teacher, CLUSTERS, DESIGN)

    assert found["gap_closed"] == 3.0
    assert found["unbounded"] is True and found["low"] is None and found["high"] is None
    assert found["non_positive_share"] > DESIGN.unbounded_share


def test_gap_closed_has_an_interval_when_the_teacher_beats_the_start_everywhere():
    start, teacher, arm = (analysis.scores(_runs(passing)) for passing in (set(), set(range(12)), {0, 1, 2, 3, 4, 5}))

    found = analysis.gap_closed(arm, start, teacher, CLUSTERS, DESIGN)

    assert found["unbounded"] is False and found["non_positive_share"] == 0.0
    assert found["gap_closed"] == 0.5 and found["low"] <= 0.5 <= found["high"] and found["low"] < found["high"]


def test_the_dose_response_slope_is_on_log10_n():
    sizes = {"sft-100": 100, "sft-300": 300, "sft-1000": 1000, "sft-3000": 3000}
    scores = {arm: {task: 0.1 + 0.2 * math.log10(n) - 0.4 for task in TASKS} for arm, n in sizes.items()}

    found = analysis.dose_response(scores, sizes, CLUSTERS, DESIGN)

    assert found["slope"] == pytest.approx(0.2) and found["low"] == pytest.approx(0.2)
    assert found["high"] == pytest.approx(0.2) and found["tasks"] == 12


def test_spearman_relates_the_ranks():
    assert analysis.spearman([(0.1, 0.2), (0.2, 0.4), (0.3, 0.5), (0.4, 0.9)]) == 1.0
    assert analysis.spearman([(0.1, 0.9), (0.2, 0.4), (0.3, 0.2)]) == -1.0
    assert analysis.spearman([(0.1, 0.2), (0.2, 0.3)]) is None


def test_the_report_reads_every_part():
    arms = _arms()
    report = {"prereg": {"file": "PREREG-SCALING.md", "sha256": "0" * 64}, "build": {"commit": "abc", "code_hash": "d"},
              "pool": {"rows": 12, "people": {}}, "subsets": {"subsets": {}}, "training": {}, "dagger": {},
              "analysis": analysis.analyse(arms, CLUSTERS, DESIGN)}

    text = analysis.markdown(report)

    assert "| S1 | sft-3000 − start (superiority) | 12 | 1.000 |" in text
    assert "Holm" in text and "Secondary (unadjusted, descriptive)" in text
