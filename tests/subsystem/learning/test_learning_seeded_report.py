"""The release check's report (S1): the arithmetic of rates, intervals and denominators on fixed inputs.

Every number the report shows carries its denominator and a 95% interval; the paired differences are
the product's own analysis, passed through unchanged; stages not built yet are named, never shown as zeros.
"""

from __future__ import annotations

import pytest

from tests.e2e.learning.seeded import report

pytestmark = [pytest.mark.subsystem]


def test_wilson_intervals_match_known_values():
    assert report.wilson(0, 0) == (None, None)
    low, high = report.wilson(5, 10)
    assert (round(low, 4), round(high, 4)) == (0.2366, 0.7634)
    low, high = report.wilson(0, 10)
    assert (low, round(high, 4)) == (0.0, 0.2775)
    low, high = report.wilson(10, 10)
    assert (round(low, 4), high) == (0.7225, 1.0)
    assert report.rate(3, 4) == {"passed": 3, "total": 4, "rate": 0.75, "low": 0.3006, "high": 0.9544}


def _comparison(new_visible, new_own, main_visible):
    return {
        "arms": [{"key": "base", "role": "baseline", "label": "the starting model", "seconds_per_task": 3.1,
                  "cloud_tokens_per_task": 0},
                 {"key": "v1", "role": "new", "label": "v1 (new)", "seconds_per_task": 2.9, "cloud_tokens_per_task": 0},
                 {"key": "main", "role": "main", "label": "GPT-6.1 Sol", "seconds_per_task": 9.5,
                  "cloud_tokens_per_task": 31000}],
        "details": {  # keyed as the product keys them: by label, the person's own work as "Your work"
            "visible": {"base": {"Document lookups": [4, 16], "Small coding tasks": [2, 16]},
                        "v1": {"Document lookups": [new_visible, 16], "Small coding tasks": [3, 16]},
                        "main": {"Document lookups": [main_visible, 16], "Small coding tasks": [16, 16]}},
            "hidden": {"base": {"Document lookups": [2, 8], "Your work": [3, 12]},
                       "v1": {"Document lookups": [3, 8], "Your work": [new_own, 12]},
                       "main": {"Document lookups": [8, 8], "Your work": [11, 12]}},
        },
        "analysis": {"seed": 20261022, "combined": {
            "v1": {"overall": {"difference": 0.05, "low": -0.02, "high": 0.12, "p_value": 0.21, "problems": 52,
                               "clusters": 41, "baseline": 0.2, "arm": 0.25, "enough": True,
                               "interval_verdict": "unclear", "not_worse": True},
                   "by_kind": {"own_work": {"difference": 0.25, "low": 0.08, "high": 0.42, "p_value": 0.01,
                                            "problems": 12, "clusters": 9}}},
            "main": {"overall": {"difference": 0.6, "low": 0.5, "high": 0.7, "p_value": 0.0001, "problems": 52},
                     "by_kind": {}}}},
        "overall": {"verdict": "better", "line": "Better at your own work than the starting model."},
    }


def _result(person, *, first, final, new_visible=6, new_own=6, main_visible=15, tier="8gb"):
    requests = [{"id": f"{person}-{index}", "label": "stream", "intended": "document_lookups" if index % 2 else "other",
                 "kind": "other", "passed_first": index < first, "passed_final": index < final,
                 "attempts": [{"passed": index < first, "seconds": 10.0, "approvals": [{"approved": True}],
                               "routed": None, "stopped": False}],
                 "reaction": [] if index < first else [{"action": "teach", "linked": True, "source": "model"}]}
                for index in range(10)]
    return {
        "person": person, "name": person.title(), "tier": tier, "days": 30, "history": "abc", "model": "gpt-6.1-sol",
        "requests": requests, "past": {"conversations": 100, "turns": 160, "import": {"state": "ready"}},
        "versions": {"1": {"version": 1, "trained_after_day": 1, "state": "evaluating", "steps": 50,
                           "comparison": _comparison(new_visible, new_own, main_visible), "compared_on_day": 1}},
        "benchmark": None, "uses": [],
        "survival": {"crash_mid_training": {"state": "passed"}, "negative_result": {"state": "not_observed"}},
        "gpu": {"seconds": 3600, "phases": [{"phase": "training", "seconds": 1800}, {"phase": "comparison",
                                                                                    "seconds": 1800}]},
        "tokens": {"ledger": {"main:codex": {"input": 900_000, "output": 10_000, "calls": 90},
                              "learning_test:codex": {"input": 400_000, "output": 5_000, "calls": 30},
                              "learning_test:ollama": {"input": 2_000_000, "output": 50_000, "calls": 300}},
                   "sim_person": {"total": 12_000}, "cloud_total": 1_327_000},
    }


def test_one_persons_report_has_denominators_intervals_and_the_products_paired_analysis():
    person = report.person_report(_result("mara", first=8, final=9))
    assert person["live"]["requests"] == 10
    assert person["live"]["first_try"]["overall"] == report.rate(8, 10)
    assert person["live"]["final"]["overall"]["passed"] == 9
    assert person["live"]["teach"] == 2 and person["live"]["teach_linked"] == 2
    assert person["live"]["approvals"] == {"asked": 10, "approved": 10}
    models = person["models"]
    assert models["baseline"]["visible"]["overall"] == report.rate(6, 32)
    assert models["new"]["own_work"]["overall"] == report.rate(6, 12)
    assert models["main"]["hidden_packs"]["overall"] == report.rate(8, 8)
    assert "own_work" not in models["new"]["hidden_packs"]  # the held-out seeded work is its own row
    assert models["new"]["visible"]["small_coding"] == report.rate(3, 16)
    assert person["paired"]["new"]["combined"]["overall"]["difference"] == 0.05
    assert person["paired"]["new"]["combined"]["by_kind"]["own_work"]["low"] == 0.08
    assert person["paired"]["baseline"] == "the starting model"
    assert person["tokens"]["learning"] == 405_000  # local tokens are free and never counted as cloud
    assert person["tokens"]["main_model_turns"] == 910_000
    assert person["gpu_hours"] == 1.0 and person["gpu_hours_by_phase"] == {"comparison": 0.5, "training": 0.5}
    assert person["survival"]["pause_on_use"] == {"state": "not_run"}


def test_overall_pools_counts_over_people_and_tiers_stay_apart():
    built = report.build([_result("mara", first=8, final=9), _result("idris", first=5, final=10, new_own=9),
                          _result("wren", first=10, final=10, tier="32gb")], run={"build": "abc1234"},
                         tiers={"8gb": "ran", "32gb": "ran"}, cap_tokens=4_000_000)
    eight = built["overall"]["8gb"]
    assert eight["people"] == 2
    assert eight["live_first_try"] == report.rate(13, 20)
    assert eight["live_final"] == report.rate(19, 20)
    assert eight["models"]["new"]["own_work"]["overall"] == report.rate(15, 24)
    assert eight["survival"]["crash_mid_training"] == {"passed": 2}
    assert eight["tokens"]["total_cloud"] == 2 * 1_327_000
    assert built["overall"]["32gb"]["people"] == 1
    assert built["tokens"] == {"total_cloud": 3 * 1_327_000, "cap": 4_000_000, "within_cap": True}


def test_a_benchmark_after_the_last_day_is_the_reports_comparison():
    result = _result("mara", first=8, final=9)
    result["benchmark"] = {"state": "done", "version": 1, "day": 30, "fresh": True,
                           "comparison": _comparison(9, 11, 15)}
    person = report.person_report(result)
    assert person["models"]["new"]["own_work"]["overall"]["passed"] == 11
    assert person["benchmark"]["day"] == 30


def test_the_markdown_shows_rates_with_intervals_and_names_what_isnt_built():
    result = _result("mara", first=8, final=9)
    result["versions"]["2"] = {"version": 2, "trained_after_day": 12, "state": "evaluating", "steps": 60,
                               "phase": "runtime_failed", "failure": "tool_turn_failed"}
    result["versions"]["3"] = {"version": 3, "trained_after_day": 20, "state": "evaluating", "steps": 90,
                               "phase": "ready", "kept_pass": {"pass": 2, "of": 3, "failed": [{"pass": 3}]}}
    result["profile"] = {"container": True}
    for index, request in enumerate(result["requests"]):  # days 1-10; a build recorded from day 6
        request["day"] = index + 1
        if index >= 5:
            request["build"] = "abc1234"
    built = report.build([result], run={"build": "abc1234", "seed": 1, "days": 30},
                         tiers={"8gb": "ran", "32gb": "not certified yet (L2x certifies the larger tiers)"},
                         cap_tokens=25_000_000)
    text = report.markdown(built)
    assert "- v2: trained after day 12, 60 steps, runtime_failed (tool_turn_failed); comparison: –" in text
    assert "- v3: trained after day 20, 90 steps, ready, kept pass 2 of 3 (pass 3 couldn't finish a short task)" in text
    # Row-Bot routed every request as "other": half were meant as document lookups.
    assert "document lookups 0/5 = 0.0% [0.0, 43.5]; other 5/5 = 100.0% [56.5, 100.0]" in text
    assert "Coding pack: ran with a container engine (Docker)." in text
    assert "- Builds that answered the requests: before builds were recorded days 1-5; `abc1234` days 6-10." in text
    assert "8/10 = 80.0% [49.0, 94.3]" in text
    assert "Tier **32gb**: not certified yet" in text
    assert "| Harder split (O31) | not built yet (L3b)" in text
    for item in report.NOT_BUILT:
        assert f"{item['stage']}: **not built yet** ({item['work_package']})" in text
    assert "+5.0 pt [-2.0, +12.0], p 0.21, n 52" in text
    assert "of a 25,000,000 cap" in text
