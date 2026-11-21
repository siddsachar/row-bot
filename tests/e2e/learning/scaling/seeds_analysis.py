"""The training-seed study's analysis (``PREREG-SEEDS.md`` §6-§7), computed only when every arm has finished.

**Unit**: a fresh task, paired across the six arms; ``s(i, a) = passed / counted runs`` (passed, failed and out of
budget count; an outage after its one rerun and a run no check could decide don't). SA2: a run whose model call the
serving context refused is out of budget (the records are recoded as each run is kept; ``explore.recode`` again here).
**Clusters**: a person's simulated day (``mara/d401``).

**Confirmatory** (§5, §7): **T1**, ``d_i = mean_k s(i, dagger-k) - mean_k s(i, sft-k)`` on the tasks counted in all six
arms, with the study's own functions (``analysis.paired``: ``evaluation.paired_analysis``, a 95% cluster bootstrap of
10,000 resamples and a two-sided paired permutation test by cluster of 10,000, seed 20261022). The permutation flips the
sign of a cluster's summed ``d_i``: that is swapping the method labels of the cluster's three seed pairs (``dagger-k``
with ``sft-k``, k = a, b, c) together, on every task of the cluster, since ``d_i`` is the mean of the three pairs'
differences. One hypothesis, so no correction. T1 holds with p below 0.05 and an interval that excludes zero, in the
direction the interval shows; with fewer than 60 counted tasks it is "not tested" and described only.

**Secondary** (§6, unadjusted and labelled): every arm's success with its interval and its runs; the nine
``dagger-k - sft-j`` pairings (the original pair, ``dagger-a - sft-a``, marked); the spread between seeds within each
method (the range and the sample standard deviation of the three arms' rates on T1's tasks) beside T1's difference; T1
on the new-wording requests; runs ending in an overflow or out of budget, failure causes, the unrecovered tool calls and
the ``skill_load`` habit (paper §4.5; counts only); DAgger's relabelling (rollouts, labelled decisions, teacher tokens);
training time, tokens, GPU-hours and wall time per arm. Every number carries the counted tasks, runs and clusters
behind it.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from tests.e2e.learning.scaling import analysis
from tests.e2e.learning.scaling.explore import recode, unrecovered_table
from tests.e2e.learning.scaling.replicate_analysis import arm_summary, behind, habit
from tests.e2e.learning.scaling.seeds import ARMS, DAGGER, SFT, STUDY_ARM, SeedsDesign

SECONDARY = "secondary: unadjusted, descriptive"
QUESTION = ("mean over the three dagger arms − mean over the three sft arms, per task (on the tasks counted in all "
            "six)")
ORIGINAL = ("dagger-a", "sft-a")


def _mean(values: Iterable[float]) -> float:
    found = list(values)
    return sum(found) / len(found)


def counted_in_all(scores: Mapping[str, Mapping[str, float]], tasks: Iterable[str] | None = None) -> set[str]:
    """The tasks every arm counted (within ``tasks`` when given)."""

    found = set.intersection(*(set(scores.get(arm) or {}) for arm in ARMS))
    return found if tasks is None else found & set(tasks)


def method_means(scores: Mapping[str, Mapping[str, float]], tasks: Iterable[str]) -> tuple[dict[str, float],
                                                                                         dict[str, float]]:
    """Per task, the mean of the three dagger arms' ``s`` and of the three sft arms'."""

    chosen = sorted(tasks)
    return ({task: _mean(scores[arm][task] for arm in DAGGER) for task in chosen},
            {task: _mean(scores[arm][task] for arm in SFT) for task in chosen})


def verdict(entry: Mapping[str, Any]) -> str:
    """§7: it holds with p below 0.05 and an interval that excludes zero, in the direction the interval shows."""

    if not entry["tested"]:
        return "not tested"
    found = entry["analysis"]
    p = found.get("p_value")
    if p is not None and p < 0.05 and found["low"] is not None and found["low"] > 0:
        return "holds: the dagger arms higher"
    if p is not None and p < 0.05 and found["high"] is not None and found["high"] < 0:
        return "holds: the sft arms higher"
    return "does not hold"


def t1(scores: Mapping[str, Mapping[str, float]], cluster_of: Mapping[str, str], design: Any,
       runs: Mapping[str, Sequence[Mapping[str, Any]]], *, tasks: Iterable[str] | None = None,
       judge: bool = True) -> dict[str, Any]:
    """T1 (see the module's docstring), on ``tasks`` only when given; ``judge``: with its verdict."""

    common = counted_in_all(scores, tasks)
    dagger, sft = method_means(scores, common)
    entry: dict[str, Any] = {"question": QUESTION, "arms": list(ARMS),
                             "analysis": analysis.paired(dagger, sft, cluster_of, design),
                             "behind": behind(common, ARMS, runs, cluster_of)}
    entry["tested"] = entry["analysis"]["problems"] >= design.min_tasks
    if not entry["tested"]:
        entry["why"] = (f"{entry['analysis']['problems']} counted paired tasks, fewer than {design.min_tasks}: "
                        "described only")
    if judge:
        entry["verdict"] = verdict(entry)
    return entry


def spread(scores: Mapping[str, Mapping[str, float]], tasks: Iterable[str]) -> dict[str, Any]:
    """Within each method, the three arms' rates on ``tasks``, their range and their sample standard deviation."""

    chosen = sorted(tasks)
    found: dict[str, Any] = {"tasks": len(chosen)}
    for method, arms in (("sft", SFT), ("dagger", DAGGER)):
        rates = {arm: round(_mean(scores[arm][task] for task in chosen), 6) if chosen else None for arm in arms}
        values = [value for value in rates.values() if value is not None]
        found[method] = {"rates": rates, "range": round(max(values) - min(values), 6) if values else None,
                         "sd": round(statistics.stdev(values), 6) if len(values) > 1 else None}
    return found


def pairings(scores: Mapping[str, Mapping[str, float]], cluster_of: Mapping[str, str], design: Any,
             runs: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    """The nine ``dagger-k - sft-j`` pairings, each on the tasks both counted; the original pair marked."""

    return {f"{first} − {second}": {"arm": first, "against": second, "original": (first, second) == ORIGINAL,
                                    **analysis.paired(scores[first], scores[second], cluster_of, design),
                                    "behind": behind(set(scores[first]) & set(scores[second]), [first, second], runs,
                                                     cluster_of)}
            for first in DAGGER for second in SFT}


def build_report(runs: Sequence[Mapping[str, Any]], *, design: SeedsDesign, fresh: Mapping[str, Mapping[str, Any]],
                 local: Mapping[str, Any], training: Mapping[str, Any]) -> dict[str, Any]:
    """§6-§7 on every arm's runs; ``fresh``: each fresh request's cluster and repeated-wording flag (``fresh.json``);
    ``local``: the evaluation child's state (tokens, GPU, serving and wall seconds); ``training``: each arm's training
    records (``seeds.training_records``)."""

    study = design.scaling()
    recoded = [recode(item) for item in runs if item["arm"] in design.arms and item["task"] in fresh]
    by_arm = {arm: [item for item in recoded if item["arm"] == arm] for arm in design.arms}
    cluster_of = {task: str(item["cluster"]) for task, item in fresh.items()}
    scores = {arm: analysis.scores(by_arm[arm]) for arm in design.arms}
    new_wording = {task for task, item in fresh.items() if not item["repeated"]}
    confirmatory = t1(scores, cluster_of, study, by_arm)
    common = counted_in_all(scores)

    def costs(arm: str) -> dict[str, Any]:
        gpu = float((local.get("gpu_seconds") or {}).get(arm) or 0.0)
        serving = float((local.get("serving_seconds") or {}).get(arm) or 0.0)
        trained = training.get(arm) or {}
        relabelling = trained.get("relabelling") or {}
        return {"evaluation": {"tokens": dict((local.get("tokens") or {}).get(arm) or {"cloud": 0, "local": 0}),
                               "gpu_hours": round((gpu + serving) / 3600, 3), "serving_hours": round(serving / 3600, 3),
                               "wall_hours": round(float((local.get("wall_seconds") or {}).get(arm) or 0.0) / 3600, 3),
                               "run_hours": round(sum(float(item.get("seconds") or 0) for item in by_arm[arm]) / 3600,
                                                  3)},
                "training": {"from": trained.get("from"), "gpu_hours": trained.get("gpu_hours"),
                             "teacher_tokens": relabelling.get("teacher_tokens"),
                             "cloud_tokens": relabelling.get("cloud_tokens")}}

    def seed(arm: str) -> Any:
        """The training seed of the model the arm served: the study's file's, or this study's (after §4's retraining
        when there was one)."""

        trained = training.get(arm) or {}
        return ((trained.get("models") or {}).get(trained.get("evaluated_as") or STUDY_ARM.get(arm, "")) or {}).get(
            "seed")

    served = local.get("serving") or {}
    return {
        "format": "row_bot.learning.scaling_seeds_report", "version": 1,
        "design": design.to_dict(),
        "arms_served": {arm: {"model": STUDY_ARM.get(arm, "new"), "context": design.context, "training_seed": seed(arm),
                              **{key: (served.get(arm) or {}).get(key)
                                 for key in ("model_ref", "name", "gguf", "effective_context", "small_profile",
                                             "small_profile_tokens")}}
                        for arm in design.arms},
        "budget": {"tokens": design.task_tokens, "steps": "the task's own step limit × 3 tool calls",
                   "seconds": design.seconds, "runs": design.runs},
        "rule": "SA2: a context overflow counts as out of budget (in the denominator, failed); a model-call error is "
                "rerun once at once",
        "fresh_block": {"requests": len(fresh), "person_days": len(set(cluster_of.values())),
                        "repeated_wording": len(fresh) - len(new_wording), "new_wording": len(new_wording)},
        "arms": {arm: arm_summary(by_arm[arm], scores[arm], cluster_of, study) for arm in design.arms},
        "confirmatory": {"T1": confirmatory},
        "secondary": {
            "label": SECONDARY,
            "pairings": pairings(scores, cluster_of, study, by_arm),
            "spread": {**spread(scores, common), "between_methods": confirmatory["analysis"].get("difference")},
            "new_wording": {"label": f"T1 on the {len(new_wording)} requests whose prompt text appears nowhere in "
                                     f"days {design.study_days[0]}-{design.study_days[1]} (unadjusted)",
                            "T1": t1(scores, cluster_of, study, by_arm, tasks=new_wording, judge=False)},
            "unrecovered": unrecovered_table(recoded, list(design.arms)),
            "skill_load_habit": habit(by_arm, list(design.arms)),
            "training": dict(training),
            "costs": {arm: costs(arm) for arm in design.arms},
        },
    }


# ── report.md ────────────────────────────────────────────────────────────────


def _behind(entry: Mapping[str, Any]) -> str:
    found = entry.get("behind") or {}
    runs = found.get("runs") or {}
    return (f"{found.get('tasks', '—')} tasks, {found.get('clusters', '—')} clusters, runs "
            + ", ".join(f"{arm} {count}" for arm, count in runs.items()))


def markdown(report: Mapping[str, Any]) -> str:
    """The report for people: every number with the tasks, runs and clusters behind it; secondaries labelled."""

    from tests.e2e.learning.scaling.analysis import _interval, _number, _rate, _table

    design, build, fresh = report["design"], report.get("build") or {}, report["fresh_block"]
    arms = list(design["arms"])
    teacher = report.get("teacher") or {}
    models = (report.get("models") or {}).get("models") or {}
    prereg = report.get("prereg") or {}
    lines = ["# The training-seed study: on-policy relabelling against plain fine-tuning across seeds", "",
             f"Pre-registration `{prereg.get('file', '—')}` v{prereg.get('version', '—')} (SHA-256 "
             f"`{str(prereg.get('sha256', ''))[:16]}…`); build `{str(build.get('commit', ''))[:12]}`, code "
             f"`{str(build.get('code_hash', ''))[:12]}`, pinned {build.get('pinned_at', '—')}; the study's build "
             f"`{str((build.get('study') or {}).get('commit', ''))[:12]}`. DAgger's teacher "
             f"`{teacher.get('model') or teacher.get('model_ref') or '—'}`.", "",
             f"Fresh block: days {design['fresh_days'][0]}–{design['fresh_days'][1]}, {fresh['requests']} requests in "
             f"{fresh['person_days']} person-days ({fresh['repeated_wording']} with a prompt text seen in days "
             f"{design['study_days'][0]}–{design['study_days'][1]}, {fresh['new_wording']} new). Every arm at "
             f"{_number(design['context'])} tokens with the study's small-context prompt kept; "
             f"{report['budget']['runs']} run{'' if report['budget']['runs'] == 1 else 's'} per arm per task, budget "
             f"{_number(report['budget']['tokens'])} tokens, {report['budget']['steps']} and "
             f"{_number(report['budget']['seconds'], 0)} s. {report['rule']}.", ""]
    if models:
        lines += ["Model files (SHA-256, logged before the first run): "
                  + "; ".join(f"`{arm}` `{item.get('sha256', '')}` ({_number(item.get('bytes'))} bytes)"
                              for arm, item in models.items()) + ".", ""]
    lines += ["## Arms as served", ""]
    lines += _table(["Arm", "Model", "Training seed", "Served as", "Row-Bot sees", "Small-context profile"],
                    [(arm, item.get("model"), item.get("training_seed") or "—", item.get("name") or "—",
                      _number(item.get("effective_context")), item.get("small_profile"))
                     for arm, item in report["arms_served"].items()])
    lines += ["## Success on the fresh tasks (primary measure; every arm's rate is secondary)", ""]
    lines += _table(["Arm", "Tasks", "Clusters", "Mean s(i, a)", "95% interval (cluster bootstrap)", "Runs passed",
                     "Outages / undecided (outside)"],
                    [(arm, item["tasks"], item["clusters"], _number(item["rate"]), _interval(item),
                      _rate(item["runs_passed"]),
                      " / ".join(str(item["outside_denominator"].get(key, 0)) for key in analysis.OUTSIDE))
                     for arm, item in ((arm, report["arms"][arm]) for arm in arms)])
    entry = report["confirmatory"]["T1"]
    lines += ["## Confirmatory: T1, method across seeds (§5, §7; one hypothesis, no correction)", ""]
    lines += _table(["", "Comparison", "Behind it", "Difference", "95% interval", "p", "Verdict"],
                    [("T1", entry["question"], _behind(entry), _number(entry["analysis"].get("difference")),
                      _interval(entry["analysis"]), _number(entry["analysis"].get("p_value")),
                      entry["verdict"] + (f" ({entry['why']})" if entry.get("why") else ""))])
    secondary = report["secondary"]
    lines += ["## Secondary (unadjusted, descriptive)", "", "### The nine seed pairings (the original pair marked)", ""]
    lines += _table(["Comparison", "Behind it", "Difference", "95% interval", "p (unadjusted)"],
                    [(name + (" (the original pair)" if item["original"] else ""), _behind(item),
                      _number(item.get("difference")), _interval(item), _number(item.get("p_value")))
                     for name, item in secondary["pairings"].items()])
    found = secondary["spread"]
    lines += [f"### Spread between seeds within each method (on T1's {found['tasks']} tasks)", ""]
    lines += _table(["Method", "Arm rates", "Range", "Standard deviation (sample, n − 1)"],
                    [(method, ", ".join(f"{arm} {_number(value)}" for arm, value in found[method]["rates"].items()),
                      _number(found[method]["range"]), _number(found[method]["sd"])) for method in ("sft", "dagger")])
    lines += [f"Between methods (T1's difference): {_number(found['between_methods'])}.", ""]
    words = secondary["new_wording"]
    lines += [f"### {words['label']}", ""]
    lines += _table(["", "Behind it", "Difference", "95% interval", "p (unadjusted)"],
                    [("T1", _behind(words["T1"]), _number(words["T1"]["analysis"].get("difference")),
                      _interval(words["T1"]["analysis"]), _number(words["T1"]["analysis"].get("p_value"))
                      + ("" if words["T1"]["tested"] else f" ({words['T1']['why']})"))])
    lines += ["### Overflows and out-of-budget runs", ""]
    lines += _table(["Arm", "Runs", "Runs ending in an overflow", "Attempts that overflowed", "Runs out of budget",
                     "of them not an overflow", "Reruns after an error"],
                    [(arm, item["runs"], item["overflows"], _number(item["overflowed_attempts"]), item["out_of_budget"],
                      item["out_of_budget_not_overflow"], item["reran_after_outage"])
                     for arm, item in ((arm, report["arms"][arm]) for arm in arms)])
    lines += ["### Failure causes (counted runs that didn't pass)", ""]
    lines += _table(["Arm", "Causes"], [(arm, ", ".join(f"{key or 'unknown'} {value}" for key, value in
                                                         report["arms"][arm]["causes"].items()) or "—") for arm in arms])
    table, habits = secondary["unrecovered"], secondary["skill_load_habit"]
    lines += ["### Unrecovered tool calls (tool, problem class; counts only)", "",
              "Each cell: calls in the runs that failed as `bad_call` / calls in every run. Runs that failed as "
              "`bad_call`: " + ", ".join(f"{arm} {table['bad_call_runs'][arm]}" for arm in arms) + ".", ""]
    lines += _table(["Tool", "Problem", *arms],
                    [(row["tool"], row["problem"], *(f"{row['in_bad_call_runs'][arm]} / {row['in_every_run'][arm]}"
                                                     for arm in arms)) for row in table["rows"]]
                    or [("—", "—", *("—" for _ in arms))])
    lines += ["`skill_load` asking for a file inside a skill that doesn't exist (`invalid_reference`, paper §4.5):", ""]
    lines += _table(["Arm", "Calls", "Runs with one", "of them passed", "Runs with three or more",
                     "Calls in `bad_call` runs", "Runs"],
                    [(arm, item["calls"], item["runs"], item["runs_passed"], item["runs_three_or_more"],
                      item["calls_in_bad_call_runs"], item["of_runs"]) for arm, item in habits.items()])
    trained = secondary["training"]
    lines += ["### Training (the study's recipe; the seed the only change)", ""]
    lines += _table(["Arm", "From", "Models (seed, steps, targets after the split, pass kept)", "Retrained (§4)",
                     "Training GPU-hours"],
                    [(arm, item.get("from"), "; ".join(
                        f"{key} ({model.get('seed') or '—'}, {_number(model.get('steps'))}, "
                        f"{_number(model.get('trained_targets'))}, "
                        f"{(model.get('kept_pass') or {}).get('pass', '—')} of {(model.get('kept_pass') or {}).get('of', '—')})"
                        for key, model in (item.get("models") or {}).items()),
                      ", ".join(f"{key} as {value.get('as')} ({value.get('why')})"
                                for key, value in (item.get("retrained") or {}).items()) or "—",
                      _number(item.get("gpu_hours")))
                     for arm, item in ((arm, trained.get(arm) or {}) for arm in arms)])
    lines += ["### DAgger's relabelling", ""]
    rows = []
    for arm in DAGGER:
        found = (trained.get(arm) or {}).get("relabelling") or {}
        for name, item in (found.get("rounds") or {}).items():
            same = (f"{item['same']} of the study's {item['the_studys_requests']} (not run {item['not_run']}, added "
                    f"{item['added']})" if "same" in item else "the study's own")
            rows.append((arm, name.replace("_", " "), item.get("rollouts"), item.get("rows"),
                         ", ".join(f"{key} {value}" for key, value in (item.get("left_out") or {}).items()) or "—",
                         same))
        rows.append((arm, "both", "", "", f"teacher tokens {_number(found.get('teacher_tokens'))}",
                     f"student runs {_number(round(float(found.get('rollout_gpu_seconds') or 0) / 3600, 3))} GPU-h"))
    lines += _table(["Arm", "Round", "Rollouts (requests run)", "Labelled decisions (rows kept)", "Left out",
                     "Requests"], rows)
    costs = secondary["costs"]
    lines += ["### Tokens, GPU-hours and time", ""]
    lines += _table(["Arm", "Evaluation local tokens", "Mean tokens a run", "Mean tool calls",
                     "Evaluation GPU-hours (runs + serving)", "Wall hours", "Run hours", "Training GPU-hours",
                     "Relabelling ChatGPT-lane tokens"],
                    [(arm, _number(costs[arm]["evaluation"]["tokens"].get("local")),
                      _number(report["arms"][arm]["mean_tokens"]), _number(report["arms"][arm]["mean_steps"], 1),
                      _number(costs[arm]["evaluation"]["gpu_hours"]), _number(costs[arm]["evaluation"]["wall_hours"]),
                      _number(costs[arm]["evaluation"]["run_hours"]), _number(costs[arm]["training"]["gpu_hours"]),
                      _number(costs[arm]["training"]["cloud_tokens"])) for arm in arms])
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["build_report", "counted_in_all", "markdown", "method_means", "pairings", "spread", "t1", "verdict"]
