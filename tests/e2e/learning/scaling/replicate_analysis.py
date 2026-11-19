"""The replication's analysis (``PREREG-REPLICATION.md`` §6-§7), computed only when every arm has finished.

**Unit**: a fresh task, paired across arms; ``s(i, a) = passed / counted runs`` (passed, failed and out of budget
count; an outage after its one rerun and a run no check could decide don't). SA2: a run whose model call the serving
context refused is out of budget (the records are recoded as each run is kept; :func:`explore.recode` again here).
**Clusters**: a person's simulated day (``mara/d301``).

**Confirmatory** (§5, §7), with the study's own functions (``analysis.paired``: ``evaluation.paired_analysis``, a 95%
cluster bootstrap of 10,000 resamples and a two-sided paired permutation test by cluster, seed 20261022):

- **R1**: ``d_i = s(i, dagger-64k) - s(i, sft-64k)`` on the tasks counted in both;
- **R2**: ``e_i = [s(i, dagger-64k) - s(i, sft-64k)] - [s(i, dagger-32k) - s(i, sft-32k)]`` on the tasks counted in all
  four (``analysis.paired`` of the two per-task differences, restricted to those tasks);
- **Holm** across {R1, R2} at 0.05 (``evaluation.holm``). A hypothesis with fewer than 60 counted paired tasks is "not
  tested": it enters Holm as 1 and is described only. One holds with a Holm p below 0.05 and an interval that excludes
  zero, in the direction the interval shows.

**Secondary** (§6, unadjusted and labelled): every arm's success with its interval (the same cluster bootstrap of the
mean over tasks) and its runs passed; gap closed at 64K for ``sft-64k`` and ``dagger-64k`` against ``start-64k`` and
the teacher (``analysis.gap_closed``, A12's rule); ``dagger-32k - sft-32k`` and ``sft-64k - start-64k``; runs ending in
an overflow and runs out of budget per arm; failure causes; the unrecovered tool calls as (tool, problem class) counts
(``explore.unrecovered_table``) with the ``skill_load`` habit of paper §4.5 (a file inside a skill that doesn't exist,
``invalid_reference``); R1 and R2 on the new-wording requests; tokens, GPU-hours and wall time per arm. Every number
carries the counted tasks, runs and clusters behind it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from tests.e2e.learning.scaling import analysis
from tests.e2e.learning.scaling.explore import recode, unrecovered_table
from tests.e2e.learning.scaling.replicate import CONTEXT_OF, MODEL_OF, ReplicationDesign

#: The habit paper §4.5 found after training: ``skill_load`` asked for a file inside a skill that doesn't exist.
HABIT = ("skill_load", "invalid_reference")
SECONDARY = "secondary: unadjusted, descriptive"
#: (the arms, against) of the secondary paired differences.
PAIRS = (("dagger-32k", "sft-32k"), ("sft-64k", "start-64k"))
GAP_CLOSED = ("sft-64k", "dagger-64k")
WORDS = {"R1": "dagger-64k − sft-64k (on the tasks counted in both)",
         "R2": "(dagger-64k − sft-64k) − (dagger-32k − sft-32k) (on the tasks counted in all four)"}
DIRECTIONS = {"R1": ("dagger-64k higher", "sft-64k higher"),
              "R2": ("the dagger − sft difference is larger at 64K", "the dagger − sft difference is larger at 32K")}


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def behind(tasks: Iterable[str], arms: Sequence[str], runs: Mapping[str, Sequence[Mapping[str, Any]]],
           cluster_of: Mapping[str, str]) -> dict[str, Any]:
    """The counted tasks, clusters and runs (per arm) behind a number on ``tasks``."""

    chosen = set(tasks)
    return {"tasks": len(chosen), "clusters": len({cluster_of.get(task, task) for task in chosen}),
            "runs": {arm: sum(1 for item in runs.get(arm) or () if item["task"] in chosen
                              and item["status"] in analysis.COUNTED) for arm in arms}}


def arm_summary(mine: Sequence[Mapping[str, Any]], scores: Mapping[str, float], cluster_of: Mapping[str, str],
                design: Any) -> dict[str, Any]:
    """One arm: its mean ``s`` with a cluster bootstrap interval, its runs, overflows, out-of-budget runs, causes,
    tokens, steps and run time."""

    from row_bot.learning.evaluation import paired_analysis

    found = paired_analysis([(cluster_of.get(task, task), value) for task, value in sorted(scores.items())],
                            resamples=design.resamples, permutations=0, seed=design.analysis_seed)
    attempts = [item for item in mine if "overflowed_attempts" in item]
    return {
        "tasks": found["problems"], "clusters": found["clusters"], "rate": found["difference"],
        "low": found["low"], "high": found["high"], **analysis._runs_summary(mine),  # noqa: SLF001 - the study's own
        "overflows": sum(1 for item in mine if item.get("overflow")),
        "overflowed_attempts": sum(int(item["overflowed_attempts"]) for item in attempts) if attempts else None,
        "out_of_budget": sum(1 for item in mine if item["status"] == "budget"),
        "out_of_budget_not_overflow": sum(1 for item in mine if item["status"] == "budget" and not item.get("overflow")),
        "reran_after_outage": sum(1 for item in mine if item.get("reran_after_outage")),
        "causes": dict(sorted(Counter(str(item.get("cause") or "") for item in mine
                                      if item["status"] in ("failed", "budget")).items())),
        "mean_tokens": round(_mean([float(item.get("tokens") or 0) for item in mine]) or 0),
        "mean_steps": round(_mean([float(item.get("steps") or 0) for item in mine]) or 0.0, 1),
        "run_hours": round(sum(float(item.get("seconds") or 0) for item in mine) / 3600, 3),
    }


def hypotheses(scores: Mapping[str, Mapping[str, float]], cluster_of: Mapping[str, str], design: Any,
               runs: Mapping[str, Sequence[Mapping[str, Any]]], *, tasks: set[str] | None = None,
               adjust: bool = True) -> dict[str, Any]:
    """R1 and R2 (see the module's docstring), on ``tasks`` only when given; with ``adjust``, Holm and the verdicts."""

    from row_bot.learning.evaluation import holm

    def only(arm: str) -> dict[str, float]:
        return {task: value for task, value in (scores.get(arm) or {}).items() if tasks is None or task in tasks}

    d64, s64, d32, s32 = only("dagger-64k"), only("sft-64k"), only("dagger-32k"), only("sft-32k")
    both = set(d64) & set(s64)
    four = both & set(d32) & set(s32)
    found = {
        "R1": {"question": WORDS["R1"], "arms": ["dagger-64k", "sft-64k"],
               "analysis": analysis.paired(d64, s64, cluster_of, design),
               "behind": behind(both, ["dagger-64k", "sft-64k"], runs, cluster_of)},
        "R2": {"question": WORDS["R2"], "arms": ["dagger-64k", "sft-64k", "dagger-32k", "sft-32k"],
               "analysis": analysis.paired({task: d64[task] - s64[task] for task in four},
                                           {task: d32[task] - s32[task] for task in four}, cluster_of, design),
               "behind": behind(four, ["dagger-64k", "sft-64k", "dagger-32k", "sft-32k"], runs, cluster_of)},
    }
    for entry in found.values():
        entry["tested"] = entry["analysis"]["problems"] >= design.min_tasks
        if not entry["tested"]:
            entry["why"] = (f"{entry['analysis']['problems']} counted paired tasks, fewer than {design.min_tasks}: "
                            "described only")
    if not adjust:
        return found
    adjusted = holm({name: entry["analysis"]["p_value"] if entry["tested"] else None for name, entry in found.items()},
                    family=list(found))
    for name, entry in found.items():
        entry["holm_p"] = adjusted[name] if entry["tested"] else None
        entry["entered_holm_as"] = adjusted[name] if entry["tested"] else 1.0
        entry["verdict"] = verdict(name, entry)
    return found


def verdict(name: str, entry: Mapping[str, Any]) -> str:
    """§7: it holds with a Holm p below 0.05 and an interval that excludes zero, in the direction the interval shows."""

    if not entry["tested"]:
        return "not tested"
    found, p = entry["analysis"], entry["holm_p"]
    higher, lower = DIRECTIONS[name]
    if p is not None and p < 0.05 and found["low"] is not None and found["low"] > 0:
        return f"holds: {higher}"
    if p is not None and p < 0.05 and found["high"] is not None and found["high"] < 0:
        return f"holds: {lower}"
    return "does not hold"


def habit(runs: Mapping[str, Sequence[Mapping[str, Any]]], arms: Sequence[str]) -> dict[str, Any]:
    """Per arm: the ``skill_load`` calls for a file inside a skill that doesn't exist the turns never recovered from,
    the runs with one, how many of those passed, how many repeated it three or more times, and the calls in the runs
    that failed as ``bad_call``. Counts only."""

    found: dict[str, Any] = {}
    for arm in arms:
        mine = runs.get(arm) or ()
        counts = [sum(1 for pair in item.get("unrecovered") or () if tuple(pair) == HABIT) for item in mine]
        with_it = [(item, count) for item, count in zip(mine, counts) if count]
        found[arm] = {"calls": sum(counts), "runs": len(with_it),
                      "runs_passed": sum(1 for item, _ in with_it if item["status"] == "passed"),
                      "runs_three_or_more": sum(1 for _, count in with_it if count >= 3),
                      "calls_in_bad_call_runs": sum(count for item, count in with_it if item.get("cause") == "bad_call"),
                      "of_runs": len(mine)}
    return found


def build_report(runs: Sequence[Mapping[str, Any]], *, design: ReplicationDesign, fresh: Mapping[str, Mapping[str, Any]],
                 states: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """§6-§7 on every arm's runs; ``fresh``: each fresh request's cluster and repeated-wording flag (``fresh.json``);
    ``states``: the two children's states (tokens, GPU and wall seconds, serving)."""

    study = design.scaling()
    recoded = [recode(item) for item in runs if item["arm"] in design.arms and item["task"] in fresh]
    by_arm = {arm: [item for item in recoded if item["arm"] == arm] for arm in design.arms}
    cluster_of = {task: str(item["cluster"]) for task, item in fresh.items()}
    scores = {arm: analysis.scores(by_arm[arm]) for arm in design.arms}
    new_wording = {task for task, item in fresh.items() if not item["repeated"]}
    teacher, local = states.get("teacher") or {}, states.get("local") or {}

    def costs(arm: str) -> dict[str, Any]:
        state = teacher if arm == "teacher" else local
        gpu = float((state.get("gpu_seconds") or {}).get(arm) or 0.0)
        serving = float((state.get("serving_seconds") or {}).get(arm) or 0.0)
        return {"tokens": dict((state.get("tokens") or {}).get(arm) or {"cloud": 0, "local": 0}),
                "gpu_hours": round((gpu + serving) / 3600, 3) if arm != "teacher" else 0.0,
                "serving_hours": round(serving / 3600, 3),
                "wall_hours": round(float((state.get("wall_seconds") or {}).get(arm) or 0.0) / 3600, 3),
                "run_hours": round(sum(float(item.get("seconds") or 0) for item in by_arm[arm]) / 3600, 3)}

    gap = {}
    for arm in GAP_CLOSED:
        found = analysis.gap_closed(scores[arm], scores["start-64k"], scores["teacher"], cluster_of, study)
        shared = set(scores[arm]) & set(scores["start-64k"]) & set(scores["teacher"])
        gap[arm] = {**found, "behind": behind(shared, [arm, "start-64k", "teacher"], by_arm, cluster_of)}
    pairs = {f"{arm} − {against}": {"arm": arm, "against": against,
                                    **analysis.paired(scores[arm], scores[against], cluster_of, study),
                                    "behind": behind(set(scores[arm]) & set(scores[against]), [arm, against], by_arm,
                                                     cluster_of)}
             for arm, against in PAIRS}
    return {
        "format": "row_bot.learning.scaling_replication_report", "version": 1,
        "design": design.to_dict(),
        "arms_served": {arm: ({"model": "teacher", "context": None,
                               **{key: teacher.get(key) for key in ("model_ref", "effective_context", "small_profile")}}
                              if arm == "teacher" else
                              {"model": MODEL_OF[arm], "context": CONTEXT_OF[arm],
                               **{key: (local.get("serving") or {}).get(arm, {}).get(key)
                                  for key in ("model_ref", "name", "gguf", "effective_context", "small_profile",
                                              "small_profile_tokens")}})
                        for arm in design.arms},
        "budget": {"tokens": design.task_tokens, "steps": "the task's own step limit × 3 tool calls",
                   "seconds": design.seconds, "runs": design.runs},
        "rule": "SA2: a context overflow counts as out of budget (in the denominator, failed); a model-call error is "
                "rerun once at once",
        "fresh_block": {"requests": len(fresh), "person_days": len(set(cluster_of.values())),
                        "repeated_wording": len(fresh) - len(new_wording), "new_wording": len(new_wording)},
        "arms": {arm: arm_summary(by_arm[arm], scores[arm], cluster_of, study) for arm in design.arms},
        "confirmatory": hypotheses(scores, cluster_of, study, by_arm),
        "secondary": {
            "label": SECONDARY,
            "gap_closed": gap,
            "paired": pairs,
            "new_wording": {"label": f"R1 and R2 on the {len(new_wording)} requests whose prompt text appears "
                                     "nowhere in the study's days (unadjusted)",
                            **hypotheses(scores, cluster_of, study, by_arm, tasks=new_wording, adjust=False)},
            "unrecovered": unrecovered_table(recoded, list(design.arms)),
            "skill_load_habit": habit(by_arm, list(design.arms)),
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
    lines = ["# The replication: on-policy relabelling against plain fine-tuning on fresh tasks", "",
             f"Pre-registration `{prereg.get('file', '—')}` v{prereg.get('version', '—')} (SHA-256 "
             f"`{str(prereg.get('sha256', ''))[:16]}…`); build `{str(build.get('commit', ''))[:12]}`, code "
             f"`{str(build.get('code_hash', ''))[:12]}`, pinned {build.get('pinned_at', '—')}; the study's build "
             f"`{str((build.get('study') or {}).get('commit', ''))[:12]}`. Teacher "
             f"`{teacher.get('model') or teacher.get('model_ref') or '—'}`.", "",
             f"Fresh block: days {design['fresh_days'][0]}–{design['fresh_days'][1]}, {fresh['requests']} requests in "
             f"{fresh['person_days']} person-days ({fresh['repeated_wording']} with a prompt text seen in days "
             f"{design['study_days'][0]}–{design['study_days'][1]}, {fresh['new_wording']} new). "
             f"{report['budget']['runs']} run{'' if report['budget']['runs'] == 1 else 's'} per arm per task, budget "
             f"{_number(report['budget']['tokens'])} tokens, {report['budget']['steps']} and "
             f"{_number(report['budget']['seconds'], 0)} s. {report['rule']}.", ""]
    if models:
        lines += ["Model files (SHA-256, logged before the first run): "
                  + "; ".join(f"`{model}` `{item.get('sha256', '')}` ({_number(item.get('bytes'))} bytes)"
                              for model, item in models.items()) + ".", ""]
    lines += ["## Arms as served", ""]
    lines += _table(["Arm", "Model", "Context", "Row-Bot sees", "Small-context profile"],
                    [(arm, item.get("model"), _number(item.get("context")) if item.get("context") else "its own",
                      _number(item.get("effective_context")), item.get("small_profile"))
                     for arm, item in report["arms_served"].items()])
    lines += ["## Success on the fresh tasks (primary measure; every arm's rate is secondary)", ""]
    lines += _table(["Arm", "Tasks", "Clusters", "Mean s(i, a)", "95% interval (cluster bootstrap)", "Runs passed",
                     "Outages / undecided (outside)"],
                    [(arm, item["tasks"], item["clusters"], _number(item["rate"]), _interval(item),
                      _rate(item["runs_passed"]),
                      " / ".join(str(item["outside_denominator"].get(key, 0)) for key in analysis.OUTSIDE))
                     for arm, item in ((arm, report["arms"][arm]) for arm in arms)])
    lines += ["## Confirmatory: R1 and R2, Holm across the two (§5, §7)", ""]
    lines += _table(["", "Comparison", "Behind it", "Difference", "95% interval", "p", "Holm p", "Verdict"],
                    [(name, item["question"], _behind(item), _number(item["analysis"].get("difference")),
                      _interval(item["analysis"]), _number(item["analysis"].get("p_value")), _number(item.get("holm_p")),
                      item["verdict"] + (f" ({item['why']})" if item.get("why") else ""))
                     for name, item in report["confirmatory"].items()])
    secondary = report["secondary"]
    lines += ["## Secondary (unadjusted, descriptive)", "", "### Gap closed at 64K, (arm − start-64k) / (teacher − "
              "start-64k)", ""]
    lines += _table(["Arm", "Behind it", "Gap closed", "95% interval", "Resamples with a non-positive denominator"],
                    [(arm, _behind(item), _number(item["gap_closed"]), _interval(item),
                      _number(item["non_positive_share"])) for arm, item in secondary["gap_closed"].items()])
    lines += ["### Paired differences", ""]
    lines += _table(["Comparison", "Behind it", "Difference", "95% interval", "p (unadjusted)"],
                    [(name, _behind(item), _number(item.get("difference")), _interval(item), _number(item.get("p_value")))
                     for name, item in secondary["paired"].items()])
    words = secondary["new_wording"]
    lines += [f"### {words['label']}", ""]
    lines += _table(["", "Comparison", "Behind it", "Difference", "95% interval", "p (unadjusted)"],
                    [(name, words[name]["question"], _behind(words[name]),
                      _number(words[name]["analysis"].get("difference")), _interval(words[name]["analysis"]),
                      _number(words[name]["analysis"].get("p_value"))
                      + ("" if words[name]["tested"] else f" ({words[name]['why']})")) for name in ("R1", "R2")])
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
    costs = secondary["costs"]
    lines += ["### Tokens, GPU-hours and time", ""]
    lines += _table(["Arm", "ChatGPT-lane tokens", "Local tokens", "Mean tokens a run", "Mean tool calls",
                     "GPU-hours (runs + serving)", "Wall hours", "Run hours"],
                    [(arm, _number(costs[arm]["tokens"].get("cloud")), _number(costs[arm]["tokens"].get("local")),
                      _number(report["arms"][arm]["mean_tokens"]), _number(report["arms"][arm]["mean_steps"], 1),
                      _number(costs[arm]["gpu_hours"]), _number(costs[arm]["wall_hours"]),
                      _number(costs[arm]["run_hours"])) for arm in arms])
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["HABIT", "arm_summary", "behind", "build_report", "habit", "hypotheses", "markdown", "verdict"]
