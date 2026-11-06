"""The study's analysis (§6-§8), computed only when every arm has finished.

**Unit**: a held-out task, paired across arms; its score in an arm is ``s(i, a) = passed / counted runs`` (§6:
passed, failed and out of budget count; a provider outage and a run no check could decide don't). **Clusters**: a
person's simulated day.

**Confirmatory** (§5, §7): S1 ``sft-3000`` against ``start`` (superiority), S2 ``dagger-1000`` against ``sft-1000``
and S3 ``sft-1000`` against ``retrieval`` (two-sided). Each is the mean paired difference with a 95% cluster
bootstrap interval and a paired permutation test by cluster (``evaluation.paired_analysis``: 10,000 each, seed
20261022), then Holm across {S1, S2, S3} (``evaluation.holm``: a hypothesis not tested enters as 1). A hypothesis is
**not tested** (descriptive only) with fewer than 60 counted paired tasks, or when an arm it needs couldn't start
or didn't run. S1 holds with a Holm p below 0.05 and an interval above zero; S2 and S3 "differ" with a Holm p below
0.05, and a direction is claimed only when the interval excludes zero.

**Secondary** (unadjusted, labelled): gap closed for every trained arm, ``(arm - start) / (teacher - start)`` on the
tasks the three share, with a cluster bootstrap interval that is **unbounded** when more than 2.5% of resamples have
a non-positive denominator (A12's rule; otherwise the percentile interval of the resamples with a positive one);
transfer to the packs' hidden tasks (non-inferiority at -10 points against ``start``, the vault's own paired
analysis); replay agreement per arm and its Spearman correlation with outcome success across arms; failure causes;
tokens and GPU-hours per arm; ``sft-1000`` against ``sft-1000b``; and the dose-response slope of ``s`` on log10(N)
over the four ``sft`` arms with a cluster bootstrap interval, N in training targets (SA1), each arm's targets after the
product's validation split reported beside it.
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from tests.e2e.learning.scaling.design import (
    DAGGER_ROUND_1,
    HYPOTHESES,
    SFT,
    TRAINED,
    Design,
)

COUNTED = ("passed", "failed", "budget")
#: Outside the denominator, counted (§8): a provider outage after its one rerun, and a run no check could decide.
OUTSIDE = ("error", "unverified")


def scores(runs: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """``s(i, a)`` per task over its counted runs (a task with none has no score)."""

    from row_bot.learning.evaluation import CaseResult, pass_fractions

    cases = [CaseResult(str(item["task"]), "pack", "", item["status"], "") for item in runs]
    return {task: fraction for task, (_kind, fraction) in pass_fractions(cases).items()}


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile(ordered: Sequence[float], share: float) -> float | None:
    if not ordered:
        return None
    position = share * (len(ordered) - 1)
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _resamples(clusters: Mapping[str, Sequence[str]], design: Design) -> list[list[str]]:
    """``design.resamples`` cluster resamples (the tasks of clusters drawn with replacement), seeded."""

    rng = random.Random(int(design.analysis_seed))
    keys = sorted(clusters)
    return [[task for key in rng.choices(keys, k=len(keys)) for task in clusters[key]]
            for _ in range(int(design.resamples))]


def _clusters(tasks: Sequence[str], cluster_of: Mapping[str, str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for task in sorted(tasks):
        found.setdefault(cluster_of.get(task, task), []).append(task)
    return found


def paired(first: Mapping[str, float], second: Mapping[str, float], cluster_of: Mapping[str, str],
           design: Design) -> dict[str, Any]:
    """``first`` against ``second`` on the tasks both counted: ``evaluation.paired_analysis`` and each arm's mean."""

    from row_bot.learning.evaluation import paired_analysis

    tasks = sorted(set(first) & set(second))
    found = paired_analysis([(cluster_of.get(task, task), first[task] - second[task]) for task in tasks],
                            resamples=design.resamples, permutations=design.permutations, seed=design.analysis_seed)
    found["first"] = _mean([first[task] for task in tasks])
    found["second"] = _mean([second[task] for task in tasks])
    return found


def confirmatory(arm_scores: Mapping[str, Mapping[str, float]], unavailable: Mapping[str, str],
                 cluster_of: Mapping[str, str], design: Design) -> dict[str, Any]:
    """S1-S3 with Holm (see the module's docstring)."""

    from row_bot.learning.evaluation import holm

    found: dict[str, Any] = {}
    for name, arm, against, sides in HYPOTHESES:
        missing = [item for item in (arm, against) if item in unavailable or item not in arm_scores]
        entry: dict[str, Any] = {"arm": arm, "against": against, "sides": sides}
        if missing:
            entry.update({"tested": False, "why": "; ".join(f"{item}: {unavailable.get(item, 'not run')}"
                                                            for item in missing)})
        else:
            entry["analysis"] = paired(arm_scores[arm], arm_scores[against], cluster_of, design)
            enough = entry["analysis"]["problems"] >= design.min_tasks
            entry["tested"] = enough
            if not enough:
                entry["why"] = (f"{entry['analysis']['problems']} counted paired tasks, fewer than {design.min_tasks}:"
                                " descriptive only")
        found[name] = entry
    adjusted = holm({name: found[name]["analysis"]["p_value"] if found[name]["tested"] else None for name in found},
                    family=[name for name, *_ in HYPOTHESES])
    for name, entry in found.items():
        entry["holm_p"] = adjusted[name] if entry["tested"] else None
        entry["verdict"] = _verdict(entry)
    return found


def _verdict(entry: Mapping[str, Any]) -> str:
    if not entry["tested"]:
        return "not tested"
    analysis, p = entry["analysis"], entry["holm_p"]
    low, high = analysis["low"], analysis["high"]
    if entry["sides"] == "superiority":
        return "holds" if p < 0.05 and low is not None and low > 0 else "does not hold"
    if p >= 0.05:
        return "no difference shown"
    if low is not None and low > 0:
        return f"differ: {entry['arm']} higher"
    if high is not None and high < 0:
        return f"differ: {entry['against']} higher"
    return "differ: no direction (the interval includes zero)"


def gap_closed(arm: Mapping[str, float], start: Mapping[str, float], teacher: Mapping[str, float],
               cluster_of: Mapping[str, str], design: Design) -> dict[str, Any]:
    """``(arm - start) / (teacher - start)`` on the tasks all three counted, with its cluster bootstrap interval
    (unbounded under A12's rule)."""

    tasks = sorted(set(arm) & set(start) & set(teacher))
    numerator = {task: arm[task] - start[task] for task in tasks}
    denominator = {task: teacher[task] - start[task] for task in tasks}
    total = sum(denominator.values())
    point = sum(numerator.values()) / total if total > 0 else None
    ratios, non_positive = [], 0
    for picked in _resamples(_clusters(tasks, cluster_of), design) if tasks else []:
        below = sum(denominator[task] for task in picked)
        if below <= 0:
            non_positive += 1
            continue
        ratios.append(sum(numerator[task] for task in picked) / below)
    ratios.sort()
    share = non_positive / design.resamples if tasks else None
    unbounded = share is None or share > design.unbounded_share
    return {"tasks": len(tasks), "gap_closed": round(point, 6) if point is not None else None,
            "low": None if unbounded else round(_percentile(ratios, 0.025), 6),
            "high": None if unbounded else round(_percentile(ratios, 0.975), 6),
            "unbounded": unbounded, "non_positive_share": None if share is None else round(share, 6)}


def slope(points: Sequence[tuple[float, float]]) -> float | None:
    """The least-squares slope of y on x (None for fewer than two distinct x)."""

    xs = [x for x, _ in points]
    if len(set(xs)) < 2:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(y for _, y in points) / len(points)
    spread = sum((x - mean_x) ** 2 for x in xs)
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / spread


def dose_response(arm_scores: Mapping[str, Mapping[str, float]], sizes: Mapping[str, int],
                  cluster_of: Mapping[str, str], design: Design,
                  trained: Mapping[str, int | None] | None = None) -> dict[str, Any]:
    """The slope of ``s`` on log10(N) over the ``sft`` arms that ran, on the tasks they all counted; ``sizes``: each
    arm's N in training targets, ``trained``: the targets each trained on after the split (reported beside it)."""

    arms = [arm for arm in SFT if arm in arm_scores and sizes.get(arm)]
    if len(arms) < 2:
        return {"arms": arms, "slope": None, "low": None, "high": None, "why": "fewer than two sft arms ran"}
    tasks = sorted(set.intersection(*(set(arm_scores[arm]) for arm in arms)))
    x = {arm: math.log10(sizes[arm]) for arm in arms}

    def fitted(picked: Sequence[str]) -> float | None:
        return slope([(x[arm], sum(arm_scores[arm][task] for task in picked) / len(picked)) for arm in arms]) \
            if picked else None

    boots = sorted(value for value in (fitted(picked) for picked in _resamples(_clusters(tasks, cluster_of), design))
                   if value is not None)
    point = fitted(tasks)
    return {"arms": arms, "n": {arm: sizes[arm] for arm in arms},
            "trained_after_split": {arm: (trained or {}).get(arm) for arm in arms}, "tasks": len(tasks),
            "slope": round(point, 6) if point is not None else None,
            "low": round(_percentile(boots, 0.025), 6) if boots else None,
            "high": round(_percentile(boots, 0.975), 6) if boots else None}


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        for index in order[start:end + 1]:
            ranks[index] = (start + end) / 2 + 1
        start = end + 1
    return ranks


def spearman(pairs: Sequence[tuple[float, float]]) -> float | None:
    """Spearman's rank correlation (average ranks for ties); None for fewer than three pairs or no spread."""

    if len(pairs) < 3:
        return None
    first, second = _ranks([a for a, _ in pairs]), _ranks([b for _, b in pairs])
    mean_a, mean_b = sum(first) / len(first), sum(second) / len(second)
    spread = math.sqrt(sum((a - mean_a) ** 2 for a in first) * sum((b - mean_b) ** 2 for b in second))
    return round(sum((a - mean_a) * (b - mean_b) for a, b in zip(first, second)) / spread, 6) if spread else None


def _runs_summary(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    from tests.e2e.learning.seeded.report import rate

    statuses = Counter(str(item["status"]) for item in runs)
    counted = sum(statuses[status] for status in COUNTED)
    return {"runs": len(runs), "statuses": dict(sorted(statuses.items())),
            "outside_denominator": {status: statuses[status] for status in OUTSIDE},
            "runs_passed": rate(statuses["passed"], counted)}


def analyse(arms: Mapping[str, Mapping[str, Any]], cluster_of: Mapping[str, str], design: Design) -> dict[str, Any]:
    """Everything §6-§7 asks for. ``arms``: per arm its ``state`` (``done``, or why it didn't run), ``runs`` (held-out
    runs: task, status, cause, tokens, local_tokens, seconds), ``replay`` (case, status), ``hidden`` (the vault's
    aggregates), ``n`` (rows it trained on or retrieved from), ``tokens`` and ``gpu_seconds``."""

    done = {name: arm for name, arm in arms.items() if arm.get("state") == "done"}
    unavailable = {name: str(arm.get("why") or arm.get("state")) for name, arm in arms.items() if name not in done}
    arm_scores = {name: scores(arm.get("runs") or ()) for name, arm in done.items()}
    rates = {}
    for name, arm in done.items():
        values = list(arm_scores[name].values())
        rates[name] = {"tasks": len(values), "rate": round(_mean(values), 6) if values else None,
                       **_runs_summary(arm.get("runs") or ())}
    replay = {}
    for name, arm in done.items():
        found = scores([{"task": item["case"], "status": item["status"]} for item in arm.get("replay") or ()])
        replay[name] = {"decisions": len(found), "agreement": round(_mean(list(found.values())), 6) if found else None}
    related = [(replay[name]["agreement"], rates[name]["rate"]) for name in done
               if replay[name]["agreement"] is not None and rates[name]["rate"] is not None]
    sizes = {name: int(arm.get("n") or 0) for name, arm in arms.items()}
    trained = {name: arm.get("trained") for name, arm in arms.items()}
    secondary: dict[str, Any] = {
        "label": "secondary: unadjusted, descriptive",
        "gap_closed": {name: gap_closed(arm_scores[name], arm_scores["start"], arm_scores["teacher"], cluster_of, design)
                       for name in done if name in TRAINED and {"start", "teacher"} <= set(done)},
        "transfer": {name: _transfer(arm.get("hidden") or {}, design) for name, arm in done.items() if name != "start"},
        "replay_agreement": replay,
        "replay_against_outcome": {"spearman": spearman(related), "arms": len(related)},
        "failure_causes": {name: dict(sorted(Counter(str(item.get("cause") or "") for item in arm.get("runs") or ()
                                                     if item["status"] in ("failed", "budget")).items()))
                           for name, arm in done.items()},
        "hidden_failure_causes": {name: dict((arm.get("hidden") or {}).get("causes") or {}) for name, arm in done.items()},
        "tokens_and_gpu": {name: {"tokens": dict(arm.get("tokens") or {}),
                                  "gpu_hours": round(float(arm.get("gpu_seconds") or 0.0) / 3600, 3)}
                           for name, arm in arms.items()},
        "training_seed": (paired(arm_scores["sft-1000"], arm_scores["sft-1000b"], cluster_of, design)
                          if {"sft-1000", "sft-1000b"} <= set(done) else {"why": "an arm didn't run"}),
        "dose_response": dose_response(arm_scores, sizes, cluster_of, design, trained),
    }
    return {"arms": {name: {"state": arm.get("state"), "why": arm.get("why"), "n": arm.get("n"),
                            "trained": arm.get("trained")}
                     for name, arm in arms.items()},
            "rates": rates, "confirmatory": confirmatory(arm_scores, unavailable, cluster_of, design),
            "secondary": secondary}


def _transfer(hidden: Mapping[str, Any], design: Design) -> dict[str, Any]:
    """The vault's paired analysis against ``start`` on the hidden pack tasks: non-inferior when its interval's lower
    end lies above -margin."""

    against = hidden.get("against_start") or {}
    low = against.get("low")
    return {"passed": hidden.get("passed"), "total": hidden.get("total"), "difference": against.get("difference"),
            "low": low, "high": against.get("high"), "problems": against.get("problems"),
            "non_inferior": None if low is None else low > -design.margin}


# ── report.md ────────────────────────────────────────────────────────────────


def _number(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return f"{value:,}" if isinstance(value, int) else str(value)


def _interval(entry: Mapping[str, Any]) -> str:
    if entry.get("unbounded"):
        return "unbounded"
    return f"[{_number(entry.get('low'))}, {_number(entry.get('high'))}]"


def _rate(found: Mapping[str, Any] | None) -> str:
    if not found or found.get("total") is None:
        return "—"
    return (f"{_number(found.get('passed'), 1)} of {found['total']} ({_number(found.get('rate'))}, "
            f"95% [{_number(found.get('low'))}, {_number(found.get('high'))}])")


def _table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return lines + [""]


def markdown(report: Mapping[str, Any]) -> str:
    """The report for people: every number with its denominator and interval; secondary measures labelled."""

    analysis = report["analysis"]
    build, pool = report.get("build") or {}, report.get("pool") or {}
    lines = ["# The controlled scaling study", "",
             f"Pre-registration `{report['prereg']['file']}` (SHA-256 `{report['prereg']['sha256'][:16]}…`); build "
             f"`{str(build.get('commit', ''))[:12]}`, code `{str(build.get('code_hash', ''))[:12]}`, pinned "
             f"{build.get('pinned_at', '')}; teacher `{(report.get('teacher') or {}).get('model', '—')}`.", "",
             "## The teacher pool (§3)", "",
             f"{_number(pool.get('rows'))} rows holding {_number(pool.get('targets'))} training targets from the "
             "training block (the 30-day past's imported rows kept out).", ""]
    lines += _table(["Person", "Requests", "Rows", "Targets", "Passed first try", "Passed in the end"],
                    [(person, item.get("requests"), (item.get("rows") or {}).get("rows"),
                      (item.get("rows") or {}).get("targets"),
                      _rate(item.get("pass_rate_first_try")), _rate(item.get("pass_rate_final")))
                     for person, item in sorted((pool.get("people") or {}).items())])
    subsets = (report.get("subsets") or {}).get("subsets") or {}
    lines += ["## Subsets (seed {})".format((report.get("subsets") or {}).get("seed", "—")), ""]
    lines += _table(["Arm", "N (targets)", "Targets", "Rows", "Last row trimmed to", "Trains on after the split",
                     "Whole pool", "By person (rows / targets)"],
                    [(arm, item.get("n"), item.get("targets"), item.get("rows"),
                      (item.get("trim") or {}).get("targets", "—"), item.get("trained_targets"),
                      "yes" if item.get("whole_pool") else "no",
                      ", ".join(f"{key} {value.get('rows')} / {value.get('targets')}"
                                for key, value in sorted((item.get("by_person") or {}).items())))
                     for arm, item in ((arm, subsets[arm]) for arm in SFT if arm in subsets)])
    lines += ["## Training (the certified recipe, up to three passes)", ""]
    lines += _table(["Arm", "State", "Rows", "Targets", "Targets after the split", "Validation rows", "Steps",
                     "Seed", "Pass kept"],
                    [(arm, item.get("state"), item.get("rows"), item.get("targets"), item.get("trained_targets"),
                      item.get("validation"),
                      item.get("steps"), item.get("seed"),
                      f"{(item.get('kept_pass') or {}).get('pass', '—')} of {(item.get('kept_pass') or {}).get('of', '—')}")
                     for arm, item in ((arm, (report.get("training") or {})[arm]) for arm in (*TRAINED, DAGGER_ROUND_1)
                                       if arm in (report.get("training") or {}))])
    dagger = report.get("dagger") or {}
    lines += ["## DAgger", "", f"Rounds: {json.dumps(dagger.get('rounds'), default=str)}; the relabelling's teacher "
              f"tokens {_number(dagger.get('teacher_tokens'))}; requests also in the 1,000-target subset "
              f"{_number(dagger.get('overlap_with_1000_subset'))} of {_number(dagger.get('requests'))}.", "",
              "## Outcome success on the held-out tasks (§6, primary)", ""]
    lines += _table(["Arm", "State", "Tasks", "Mean s(i, a)", "Runs passed", "Outages / undecided (outside)"],
                    [(arm, item.get("state"), (analysis["rates"].get(arm) or {}).get("tasks", "—"),
                      _number((analysis["rates"].get(arm) or {}).get("rate")),
                      _rate((analysis["rates"].get(arm) or {}).get("runs_passed")),
                      " / ".join(str(((analysis["rates"].get(arm) or {}).get("outside_denominator") or {}).get(key, "—"))
                                 for key in OUTSIDE))
                     for arm, item in analysis["arms"].items()])
    lines += ["## Confirmatory: S1-S3, Holm across the three (§5, §7)", ""]
    lines += _table(["", "Comparison", "Tasks", "Difference", "95% interval", "p", "Holm p", "Verdict"],
                    [(name, f"{item['arm']} − {item['against']} ({item['sides']})",
                      (item.get("analysis") or {}).get("problems", "—"), _number((item.get("analysis") or {}).get("difference")),
                      _interval(item.get("analysis") or {}), _number((item.get("analysis") or {}).get("p_value")),
                      _number(item.get("holm_p")), item["verdict"] + (f" ({item['why']})" if item.get("why") else ""))
                     for name, item in analysis["confirmatory"].items()])
    secondary = analysis["secondary"]
    lines += ["## Secondary (unadjusted, descriptive)", "", "### Gap closed, (arm − start) / (teacher − start)", ""]
    lines += _table(["Arm", "Tasks", "Gap closed", "95% interval", "Resamples with a non-positive denominator"],
                    [(arm, item["tasks"], _number(item["gap_closed"]), _interval(item),
                      _number(item["non_positive_share"])) for arm, item in secondary["gap_closed"].items()])
    lines += ["### Transfer to the packs' hidden tasks (non-inferiority at −10 points against start)", ""]
    lines += _table(["Arm", "Passed", "Difference", "95% interval", "Non-inferior"],
                    [(arm, f"{_number(item['passed'])} of {_number(item['total'])}", _number(item["difference"]),
                      _interval(item), {True: "yes", False: "no"}.get(item["non_inferior"], "—"))
                     for arm, item in secondary["transfer"].items()])
    related = secondary["replay_against_outcome"]
    lines += ["### Replay agreement (the field study's checker, METRIC_VERSION 3)", ""]
    lines += _table(["Arm", "Decisions", "Agreement"],
                    [(arm, item["decisions"], _number(item["agreement"]))
                     for arm, item in secondary["replay_agreement"].items()])
    dose = secondary["dose_response"]
    seed = secondary["training_seed"]
    lines += [f"Spearman's ρ between replay agreement and outcome success across {related['arms']} arms: "
              f"{_number(related['spearman'])}.", "",
              "**Dose-response**: slope of s on log10(N), N in training targets ("
              + ", ".join(f"{arm} {_number((dose.get('n') or {}).get(arm))}, "
                          f"{_number((dose.get('trained_after_split') or {}).get(arm))} after the split"
                          for arm in dose.get("arms") or [])
              + f"): {_number(dose.get('slope'))} {_interval(dose)} on {_number(dose.get('tasks'))} tasks.", "",
              f"**Training seed** (sft-1000 − sft-1000b): {_number(seed.get('difference'))} {_interval(seed)} on "
              f"{_number(seed.get('problems'))} tasks.", "", "### Failure causes (held-out runs that didn't pass)", ""]
    lines += _table(["Arm", "Causes"], [(arm, ", ".join(f"{key or 'unknown'} {value}" for key, value in causes.items())
                                         or "—") for arm, causes in secondary["failure_causes"].items()])
    lines += ["### Tokens and GPU-hours", ""]
    lines += _table(["Arm", "ChatGPT-lane tokens", "Local tokens", "of which the hidden tasks'", "GPU-hours"],
                    [(arm, _number(item["tokens"].get("cloud")), _number(item["tokens"].get("local")),
                      _number(item["tokens"].get("hidden")), _number(item["gpu_hours"]))
                     for arm, item in secondary["tokens_and_gpu"].items()])
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["COUNTED", "OUTSIDE", "analyse", "confirmatory", "dose_response", "gap_closed", "markdown", "paired",
           "scores", "slope", "spearman"]
