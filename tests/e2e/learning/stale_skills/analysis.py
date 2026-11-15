"""Phase 3's report (Design A, Lane B): every cell of the grid scored from what it saved, pooled per model and arm,
and the pre-registration's §6-§8 analysis.

    python -m tests.e2e.learning.stale_skills.analysis --root <phase 3 folder> --out <phase 3 folder>\\out

Run only once every cell is done (no interim outcome). It reads each cell's ``run/state.json`` and the product's
operation log (``data/stale_skills/operations.jsonl``), rebuilds each person's timeline (the only ground truth) and
rescores every run's first attempt and every probe with the scorer as it is.

**Choices fixed before any phase-3 outcome was computed** (26 Oct, written in ``wp-SA3-progress.md`` first):

- **Clusters** (§7 "clustered by person and change event"): a cluster is one change event of one person (48 per
  arm). Every interval resamples clusters with replacement (10,000 resamples, seed 20261026); a paired comparison
  resamples the same clusters in both arms. A two-stage bootstrap (people, then their change events) is reported
  beside it as a sensitivity view.
- **H-A1's ratios** are pooled rates (stale runs / window runs) of R over N and of R over F. Bootstrap p-value:
  twice the smaller tail of the ratio around 1. **Holm**: the comparison with the smaller p gets a 97.5% interval;
  the other a 95% interval if the first's upper bound is below 1, else 97.5% too. A resample whose denominator arm
  has no stale run gives an infinite ratio (kept: it counts against R).
- **Unchanged controls** (the six procedures that never change and are never edited): success = first acting step
  current. R passes if (R - N) >= the lower bound of the 95% interval of (A - R) (the A/A difference), resampling
  person x procedure clusters.
- **False patches**: applied R patches (from R's own proposals) whose procedure hadn't changed by the patch's day,
  over R's applied patches, all six people.
- **H-A3**: D vs N stale rate (paired, the same clusters); D's wrong-switch rate over every run of the newer-skill
  procedures on or after the edit, with Wilson's interval and the 14-day rate beside it.
- **The second result** (§7): every rate again over the changes that reached memory, each change's runs counted from
  the moment it reached memory (scorer.metrics' ``reached_memory``).
- **§8**: H-A1 holds -> paper around reconciliation; else N's 9B rate >= 15% -> measurement paper; N's 9B rate
  < 10% -> stop. Between 10% and 15% with H-A1 failing the pre-registration names no decision; reported as such.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.e2e.learning.stale_skills import grid, scorer, world

RESAMPLES = scorer.BOOTSTRAP_RESAMPLES
SEED = scorer.BOOTSTRAP_SEED
ALPHA = 0.05
MODEL_ORDER = ("q4b", "q9b", "q27b", "main")
ARM_ORDER = ("N", "R", "F", "D", "A")


@dataclass
class CellData:
    model: str
    arm: str
    person: str
    timeline: world.Timeline
    state: dict[str, Any]
    operations: list[dict[str, Any]]
    runs: list[dict[str, Any]] = field(default_factory=list)  # scored runs (scorer.metrics' input) + person
    probes: list[dict[str, Any]] = field(default_factory=list)
    stored: dict[str, Any] = field(default_factory=dict)
    patches: dict[str, Any] = field(default_factory=dict)
    folder: Path | None = None


_TIMELINES: dict[str, world.Timeline] = {}


def timeline_of(person: str) -> world.Timeline:
    if person not in _TIMELINES:
        from tests.e2e.learning.stale_skills import phrasing

        _TIMELINES[person] = world.build(person=person, days=world.DAYS, phrasing=phrasing.load(person, "main"))
    return _TIMELINES[person]


def load_cell(folder: Path, cell: grid.Cell) -> CellData:
    state = json.loads((folder / "run" / "state.json").read_text(encoding="utf-8"))
    path = folder / "data" / "stale_skills" / "operations.jsonl"
    operations = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] \
        if path.is_file() else []
    timeline = timeline_of(cell.person)
    found = CellData(cell.model, cell.arm, cell.person, timeline, state, operations, folder=folder)
    prompts = {item.id: item.prompt for item in timeline.runs}
    for item in state.get("runs") or ():
        if prompts.get(item["id"]) != item.get("prompt"):
            raise SystemExit(f"{folder}: a run's prompt differs from the timeline's (another world)")
        scored = scorer.score_run(timeline, item["id"], item["day"], item["attempts"][0]["actions"]).to_dict()
        found.runs.append({**scored, "person": cell.person, "skill_loaded": item.get("skill_loaded"),
                           "recalled_new": item.get("recalled_new"), "passed_first": item.get("passed_first"),
                           "at": item.get("at"), "date_note": item.get("date_note"), "tokens": item.get("tokens")})
    for item in state.get("probes") or ():
        found.probes.append({**item, "verdict": scorer.score_probe(item, item.get("answer") or "")})
    found.stored = scorer.stored_changes(timeline, operations)
    found.patches = scorer.patch_metrics(timeline, state.get("skills") or {}, operations)
    return found


# ── Clusters and the bootstrap ───────────────────────────────────────────────


def window_clusters(cell: CellData, *, reached: bool = False, edits: bool = False) -> dict[tuple[str, str], tuple[int, int]]:
    """(person, procedure) -> (stale runs, runs) over the change's window (``edits``: the newer-skill procedures' runs
    on or after the edit, wrong switches); ``reached``: only runs after the change reached memory."""

    out: dict[tuple[str, str], tuple[int, int]] = {}
    events = cell.timeline.edits if edits else cell.timeline.changes
    for change in events:
        if edits:
            mine = [run for run in cell.runs if run["procedure"] == change.procedure and run.get("after_edit")]
        else:
            mine = [run for run in cell.runs if run["procedure"] == change.procedure and run["in_window"]]
            if reached:
                mine = [run for run in mine if scorer._after_store(run, cell.stored.get(change.procedure))]  # noqa: SLF001
        out[(cell.person, change.procedure)] = (sum(1 for run in mine if run["verdict"] == scorer.OLD), len(mine))
    return out


def control_clusters(cell: CellData) -> dict[tuple[str, str], tuple[int, int]]:
    """(person, procedure) -> (first acting step current, runs) for the six unchanged controls."""

    out: dict[tuple[str, str], tuple[int, int]] = {}
    for procedure in cell.timeline.procedures:
        if cell.timeline.change_of(procedure.id) is not None or cell.timeline.edit_of(procedure.id) is not None:
            continue
        mine = [run for run in cell.runs if run["procedure"] == procedure.id]
        out[(cell.person, procedure.id)] = (sum(1 for run in mine if run["verdict"] == scorer.CURRENT), len(mine))
    return out


def pooled(clusters: Mapping[Any, tuple[int, int]]) -> float | None:
    n = sum(value[1] for value in clusters.values())
    return sum(value[0] for value in clusters.values()) / n if n else None


def _draws(keys: list[Any], *, two_stage: bool, rng: random.Random) -> list[Any]:
    if not two_stage:
        return [keys[rng.randrange(len(keys))] for _ in keys]
    people = sorted({key[0] for key in keys})
    by_person = {person: [key for key in keys if key[0] == person] for person in people}
    drawn = []
    for _ in people:
        mine = by_person[people[rng.randrange(len(people))]]
        drawn += [mine[rng.randrange(len(mine))] for _ in mine]
    return drawn


def bootstrap(arms: Mapping[str, Mapping[Any, tuple[int, int]]], statistic: Callable[[Mapping[str, float | None]], float | None],
              *, two_stage: bool = False, resamples: int = RESAMPLES, seed: int = SEED) -> list[float]:
    """``statistic`` over ``resamples`` draws of the clusters (the same draw for every arm: paired). Each arm's value
    in a draw is its pooled rate over the drawn clusters (None with no runs). Resamples whose statistic is None are
    dropped (counted by the caller via the length)."""

    keys = sorted(set().union(*(set(item) for item in arms.values())))
    rng = random.Random(seed)
    values = []
    for _ in range(resamples):
        drawn = _draws(keys, two_stage=two_stage, rng=rng)
        rates: dict[str, float | None] = {}
        for name, clusters in arms.items():
            k = sum(clusters.get(key, (0, 0))[0] for key in drawn)
            n = sum(clusters.get(key, (0, 0))[1] for key in drawn)
            rates[name] = k / n if n else None
        value = statistic(rates)
        if value is not None:
            values.append(value)
    return values


def interval(values: list[float], level: float = 0.95) -> list[float] | None:
    if not values:
        return None
    ordered = sorted(values)
    low = ordered[max(0, int(math.floor((1 - level) / 2 * len(ordered))))]
    high = ordered[min(len(ordered) - 1, int(math.ceil((1 + level) / 2 * len(ordered))) - 1)]
    return [low, high]


def ratio(numerator: str, denominator: str) -> Callable[[Mapping[str, float | None]], float | None]:
    def statistic(rates: Mapping[str, float | None]) -> float | None:
        top, bottom = rates.get(numerator), rates.get(denominator)
        if top is None or bottom is None:
            return None
        if bottom == 0:
            return math.inf if top > 0 else None
        return top / bottom

    return statistic


def difference(first: str, second: str) -> Callable[[Mapping[str, float | None]], float | None]:
    def statistic(rates: Mapping[str, float | None]) -> float | None:
        a, b = rates.get(first), rates.get(second)
        return None if a is None or b is None else a - b

    return statistic


def p_value(values: list[float], null: float) -> float | None:
    """Twice the smaller tail of the bootstrap distribution around ``null`` (at most 1)."""

    if not values:
        return None
    below = sum(1 for value in values if value >= null) / len(values)
    above = sum(1 for value in values if value <= null) / len(values)
    return min(1.0, 2 * min(below, above))


def holm(comparisons: dict[str, list[float]], null: float = 1.0) -> dict[str, dict[str, Any]]:
    """Holm-adjusted p-values and intervals for comparisons' bootstrap values (see the module's docstring)."""

    ps = {name: p_value(values, null) for name, values in comparisons.items()}
    order = sorted(comparisons, key=lambda name: (ps[name] if ps[name] is not None else 2.0, name))
    m = len(order)
    out: dict[str, dict[str, Any]] = {}
    rejected_so_far = True
    running = 0.0
    for rank, name in enumerate(order):
        p = ps[name]
        adjusted = None if p is None else min(1.0, max(running, (m - rank) * p))
        running = adjusted if adjusted is not None else running
        level = 1 - ALPHA / (m - rank) if rejected_so_far else 1 - ALPHA / m
        found = interval(comparisons[name], level)
        rejected = bool(found and found[1] < null)
        rejected_so_far = rejected_so_far and rejected
        out[name] = {"p": p, "p_holm": adjusted, "level": round(level, 4), "interval": found, "upper_below_1": rejected,
                     "resamples": len(comparisons[name]),
                     "infinite": sum(1 for value in comparisons[name] if math.isinf(value))}
    return out


# ── Pooling ──────────────────────────────────────────────────────────────────


def rate(k: int, n: int) -> dict[str, Any]:
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None, "wilson": scorer.wilson(k, n)}


def runs_rate(runs: Iterable[Mapping[str, Any]], verdict: str = scorer.OLD) -> dict[str, Any]:
    runs = list(runs)
    return rate(sum(1 for run in runs if run["verdict"] == verdict), len(runs))


def with_ci(clusters: Mapping[Any, tuple[int, int]]) -> dict[str, Any]:
    k = sum(value[0] for value in clusters.values())
    n = sum(value[1] for value in clusters.values())
    found = rate(k, n)
    found["cluster_ci"] = _pct(interval(bootstrap({"x": clusters}, lambda r: r["x"])))
    found["two_stage_ci"] = _pct(interval(bootstrap({"x": clusters}, lambda r: r["x"], two_stage=True)))
    found["clusters"] = sum(1 for value in clusters.values() if value[1])
    return found


def _pct(pair: list[float] | None) -> list[float] | None:
    return None if pair is None else [round(100 * pair[0], 1), round(100 * pair[1], 1)]


def _round(pair: list[float] | None, digits: int = 3) -> list[float] | None:
    return None if pair is None else [round(item, digits) if math.isfinite(item) else item for item in pair]


def arm_block(cells: list[CellData]) -> dict[str, Any]:
    """One model x arm, pooled over its people."""

    runs = [run for cell in cells for run in cell.runs]
    window = [run for run in runs if run["in_window"]]
    clusters = {key: value for cell in cells for key, value in window_clusters(cell).items()}
    reached = {key: value for cell in cells for key, value in window_clusters(cell, reached=True).items()}
    edits = {key: value for cell in cells for key, value in window_clusters(cell, edits=True).items()}
    controls = {key: value for cell in cells for key, value in control_clusters(cell).items()}
    kept_reached = [run for cell in cells for run in cell.runs if run["in_window"]
                    and scorer._after_store(run, cell.stored.get(run["procedure"]))]  # noqa: SLF001
    stored = sum(1 for cell in cells for change in cell.timeline.changes if cell.stored.get(change.procedure))
    patches = [item for cell in cells for item in (cell.patches.get("patches") or ())]
    candidates: dict[str, dict[str, int]] = {}
    for cell in cells:
        for arm_name, outcomes in (cell.patches.get("candidates") or {}).items():
            for outcome, count in outcomes.items():
                candidates.setdefault(arm_name, {}).setdefault(outcome, 0)
                candidates[arm_name][outcome] += count
    own = [item for item in patches if item["arm"] in ("R", "F")]
    false = [item for item in own if not item["true"]]
    skills = [value for cell in cells for value in (cell.state.get("skills") or {}).values()]
    probes = scorer.probe_metrics([probe for cell in cells for probe in cell.probes])
    fixes = []
    for cell in cells:
        for procedure, fix in scorer.metrics(cell.timeline, cell.runs)["time_to_fix"].items():
            fixes.append({"person": cell.person, "procedure": procedure, **fix})
    fixed = sorted(item["days_to_first_right"] for item in fixes if item["days_to_first_right"] is not None)
    days = sum(sum(1 for night in (cell.state.get("nights") or {}).values() if night.get("done")) for cell in cells)
    tokens = sum(grid.progress_from_state(cell.state, cell.folder / "data" if cell.folder else None)["tokens"] for cell in cells)
    gpu = sum(float((cell.state.get("gpu") or {}).get("seconds") or 0.0) for cell in cells)
    return {
        "people": sorted(cell.person for cell in cells),
        "stale_execution": with_ci(clusters),
        "stale_among_acted": runs_rate([run for run in window if run["verdict"] != scorer.NO_ACTION]),
        "window_verdicts": {verdict: sum(1 for run in window if run["verdict"] == verdict) for verdict in scorer.VERDICTS},
        "stale_skill_loaded": runs_rate([run for run in window if run.get("skill_loaded")]),
        "stale_skill_not_loaded": runs_rate([run for run in window if not run.get("skill_loaded")]),
        "stale_recall_surfaced_new": runs_rate([run for run in window if run.get("recalled_new")]),
        "stale_recall_did_not_surface_new": runs_rate([run for run in window if not run.get("recalled_new")]),
        "stale_all_after_change": runs_rate([run for run in runs if run["after_change"]]),
        "stale_by_kind": {kind: runs_rate([run for run in window if run["kind"] == kind]) for kind in world.KINDS},
        "stale_by_channel": {channel: runs_rate([run for cell in cells for run in cell.runs if run["in_window"]
                                                 and (cell.timeline.change_of(run["procedure"]).channel == channel)])
                             for channel in sorted({change.channel for cell in cells for change in cell.timeline.changes})},
        "reached_memory": {"changes_reached": stored, "changes": sum(len(cell.timeline.changes) for cell in cells),
                           "stale_execution": with_ci(reached),
                           "stale_skill_loaded": runs_rate([run for run in kept_reached if run.get("skill_loaded")]),
                           "stale_skill_not_loaded": runs_rate([run for run in kept_reached
                                                                if not run.get("skill_loaded")])},
        "wrong_switch_all_after_edit": rate(sum(v[0] for v in edits.values()), sum(v[1] for v in edits.values())),
        "wrong_switch_14_days": runs_rate([run for run in runs if run.get("edit_window")]),
        "newer_skill_verdicts": {verdict: sum(1 for run in runs if run.get("after_edit") and run["verdict"] == verdict)
                                 for verdict in scorer.VERDICTS},
        "date_notes": sum(1 for run in runs if run.get("date_note")),
        "date_notes_on_newer_skill_runs": sum(1 for run in runs if run.get("date_note") and run.get("after_edit")),
        "unchanged_controls_success": rate(sum(v[0] for v in controls.values()), sum(v[1] for v in controls.values())),
        "before_change_success": runs_rate([run for run in runs if run["changed"] and not run["after_change"]],
                                           scorer.CURRENT),
        "patches": {"applied": len(patches), "own_applied": len(own), "false": len(false),
                    "false_rate": round(len(false) / len(own), 4) if own else None,
                    "wilson_false": scorer.wilson(len(false), len(own)), "candidates": candidates},
        "skills": {"total": len(skills), "consent": sum(1 for item in skills if item.get("source") != "seeded"),
                   "seeded": sum(1 for item in skills if item.get("source") == "seeded"),
                   "names_fact": sum(1 for item in skills if item.get("names_fact"))},
        "time_to_fix": {"fixed": len(fixed), "changes": len(fixes),
                        "median_days": fixed[len(fixed) // 2] if fixed else None, "by_change": fixes},
        "probes": probes,
        "days": days, "tokens": tokens, "gpu_seconds": round(gpu, 1),
        "tokens_per_day": round(tokens / days) if days else None,
        "gpu_seconds_per_day": round(gpu / days, 1) if days else None,
        "redone": sum(len(cell.state.get("redone") or ()) for cell in cells),
        "limit_errors": sum(len(cell.state.get("limit_errors") or ()) for cell in cells),
        "_clusters": clusters, "_reached": reached, "_controls": controls, "_edits": edits,
    }


def analyse(blocks: dict[tuple[str, str], dict[str, Any]], *, resamples: int = RESAMPLES) -> dict[str, Any]:
    """§7's tests over the pooled blocks (model, arm) (``resamples``: fewer only in the harness's own tests)."""

    out: dict[str, Any] = {}

    def clusters(model: str, arm: str, key: str = "_clusters") -> Mapping[Any, tuple[int, int]]:
        return (blocks.get((model, arm)) or {}).get(key) or {}

    # H-A1 on the 9B, end to end and over changes that reached memory.
    for label, key in (("end_to_end", "_clusters"), ("reached_memory", "_reached")):
        arms = {arm: clusters("q9b", arm, key) for arm in ("R", "N", "F")}
        if not all(arms.values()):
            continue
        comparisons = {f"R/{other}": bootstrap(arms, ratio("R", other), resamples=resamples) for other in ("N", "F")}
        tests = holm(comparisons)
        points = {f"R/{other}": ratio("R", other)({arm: pooled(value) for arm, value in arms.items()})
                  for other in ("N", "F")}
        for name in tests:
            tests[name]["point"] = points[name]
            tests[name]["interval"] = _round(tests[name]["interval"])
        out[f"h_a1_{label}"] = {"tests": tests, "points_at_most_half": all(
            value is not None and value <= 0.5 for value in points.values()),
            "uppers_below_1": all(item["upper_below_1"] for item in tests.values())}
    r_block = blocks.get(("q9b", "R")) or {}
    false_rate = (r_block.get("patches") or {}).get("false_rate")
    # No applied patch at all: no false patch either (reported as such).
    out["false_patches_ok"] = bool(r_block) and (false_rate is None or false_rate <= 0.05)
    controls = {arm: clusters("q9b", arm, "_controls") for arm in ("R", "N", "A")}
    if all(controls.values()):
        aa = interval(bootstrap(controls, difference("A", "R"), resamples=resamples))
        rn = difference("R", "N")({arm: pooled(value) for arm, value in controls.items()})
        out["controls"] = {"r_minus_n": rn, "aa_difference_ci": _round(aa, 4),
                           "aa_point": difference("A", "R")({arm: pooled(v) for arm, v in controls.items()}),
                           "r_minus_n_ci": _round(interval(bootstrap(controls, difference("R", "N"), resamples=resamples)), 4),
                           "ok": bool(aa is not None and rn is not None and rn >= aa[0])}
    else:
        out["controls"] = {"ok": False, "why": "an arm is missing"}
    h_a1 = out.get("h_a1_end_to_end") or {}
    out["h_a1_holds"] = bool(h_a1.get("points_at_most_half") and h_a1.get("uppers_below_1")
                             and out["false_patches_ok"] and out["controls"].get("ok"))
    # H-A2: N by model.
    out["h_a2"] = {model: (blocks.get((model, "N")) or {}).get("stale_execution") for model in MODEL_ORDER}
    # H-A3: D vs N (paired), on the 9B and the main model.
    out["h_a3"] = {}
    for model in ("q9b", "main"):
        for label, key in (("end_to_end", "_clusters"), ("reached_memory", "_reached")):
            arms = {arm: clusters(model, arm, key) for arm in ("D", "N")}
            if not all(arms.values()):
                continue
            values = bootstrap(arms, difference("D", "N"), resamples=resamples)
            ratios = bootstrap(arms, ratio("D", "N"), resamples=resamples)
            rates = {arm: pooled(value) for arm, value in arms.items()}
            out["h_a3"][f"{model}_{label}"] = {
                "d_minus_n": difference("D", "N")(rates), "d_minus_n_ci": _round(interval(values), 4),
                "p": p_value(values, 0.0), "d_over_n": ratio("D", "N")(rates),
                "d_over_n_ci": _round(interval(ratios))}
        block = blocks.get((model, "D"))
        if block:
            wrong = block["wrong_switch_all_after_edit"]
            out["h_a3"][f"{model}_wrong_switch"] = {**wrong, "at_most_5pct": wrong["rate"] is not None
                                                     and wrong["rate"] <= 0.05,
                                                     "fourteen_days": block["wrong_switch_14_days"]}
    n9 = ((blocks.get(("q9b", "N")) or {}).get("stale_execution") or {}).get("rate")
    if out["h_a1_holds"]:
        decision = "H-A1 holds: write the paper around reconciliation, with H-A2 and H-A3 as secondary results."
    elif n9 is not None and n9 < 0.10:
        decision = "N's 9B rate is under 10%: stop and keep the notes."
    elif n9 is not None and n9 >= 0.15:
        decision = "H-A1 fails and N's 9B rate is at least 15%: a measurement paper (stale execution by model size)."
    else:
        decision = ("H-A1 fails and N's 9B rate is between 10% and 15%: the pre-registration names no decision "
                    "for this case; for the orchestrator.")
    out["decision"] = decision
    out["n_9b_rate"] = n9
    return out


# ── Output ───────────────────────────────────────────────────────────────────


def _p(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"< {1 / RESAMPLES:g}" if value == 0 else f"{value:.4f}"


def _f(item: Mapping[str, Any] | None, ci: str = "cluster_ci") -> str:
    if not item or not item.get("n"):
        return "0/0"
    text = f"{item['k']}/{item['n']} = {100 * item['k'] / item['n']:.1f}%"
    if item.get(ci):
        text += f" [{item[ci][0]}, {item[ci][1]}]"
    elif item.get("wilson"):
        text += f" (Wilson [{item['wilson'][0]}, {item['wilson'][1]}])"
    return text


def markdown(blocks: dict[tuple[str, str], dict[str, Any]], tests: dict[str, Any], meta: dict[str, Any]) -> str:
    label = {key: value["label"] for key, value in grid.MODELS.items()}
    order = [(model, arm) for model in MODEL_ORDER for arm in ARM_ORDER if (model, arm) in blocks]
    lines = ["# Stale skills, Design A: phase 3 report", "",
             f"Pre-registration `PREREG-DESIGN-A.md` (SHA-256 {meta['prereg_sha']}). Build {meta['builds']}. Six people x "
             "120 simulated days, paired. Generated " + meta["generated"] + ".", "",
             "Intervals: `[a, b]` = cluster bootstrap 95% (clusters = one person's change event; 10,000 resamples, "
             f"seed {SEED}); Wilson 95% where marked.", "",
             "## Decision (§8)", "", f"**{tests['decision']}**", "",
             "## H-A1 (primary): reconciliation on the 9B", ""]
    for label_key in ("h_a1_end_to_end", "h_a1_reached_memory"):
        h = tests.get(label_key)
        if not h:
            continue
        lines += [f"**{label_key.removeprefix('h_a1_').replace('_', ' ')}**", "",
                  "| Ratio | Point | Holm level | Interval | p | p (Holm) | Resamples (infinite) |", "|---|---|---|---|---|---|---|"]
        for name, item in h["tests"].items():
            point = "n/a" if item["point"] is None else f"{item['point']:.3f}"
            lines.append(f"| {name} | {point} | {item['level']} | {item['interval']} | {_p(item['p'])} | {_p(item['p_holm'])} | "
                         f"{item['resamples']} ({item['infinite']}) |")
        lines += ["", f"Both points at most 0.5: {h['points_at_most_half']}; both Holm upper bounds below 1: "
                  f"{h['uppers_below_1']}.", ""]
    r = blocks.get(("q9b", "R")) or {}
    patches = r.get("patches") or {}
    controls = tests.get("controls") or {}
    lines += [f"- False patches (R, 9B): {patches.get('false')}/{patches.get('own_applied')} = "
              f"{patches.get('false_rate')} (Wilson {patches.get('wilson_false')}); at most 5%: {tests['false_patches_ok']}.",
              f"- Unchanged controls: R - N = {controls.get('r_minus_n')} (95% {controls.get('r_minus_n_ci')}); A/A "
              f"(A - R) = {controls.get('aa_point')} (95% {controls.get('aa_difference_ci')}); R no worse than N beyond "
              f"the A/A interval: {controls.get('ok')}.",
              f"- **H-A1 holds: {tests['h_a1_holds']}**", "", "## H-A2: stale-execution rate under N by model", "",
              "| Model | Stale (window) | two-stage | Changes |", "|---|---|---|---|"]
    for model in MODEL_ORDER:
        item = tests["h_a2"].get(model)
        if item:
            lines.append(f"| {label[model]} | {_f(item)} | {item.get('two_stage_ci')} | {item.get('clusters')} |")
    lines += ["", "## H-A3: the date-aware note", ""]
    for key, item in tests["h_a3"].items():
        if key.endswith("wrong_switch"):
            lines.append(f"- {key}: wrong switches {item['k']}/{item['n']} = {item['rate']} (Wilson {item['wilson']}); "
                         f"at most 5%: {item['at_most_5pct']}; edit's day and 13 after: {_f(item['fourteen_days'])}")
        else:
            lines.append(f"- {key}: D - N = {item['d_minus_n']} (95% {item['d_minus_n_ci']}, p {item['p']}); D/N = "
                         f"{item['d_over_n']} (95% {item['d_over_n_ci']})")
    lines += ["", "## Every cell, pooled over six people", "",
              "| | " + " | ".join(f"{label[m]} {a}" for m, a in order) + " |", "|---|" + "---|" * len(order)]

    def row(name: str, pick: Callable[[dict[str, Any]], str]) -> None:
        lines.append(f"| {name} | " + " | ".join(pick(blocks[key]) for key in order) + " |")

    row("**Stale-execution rate** (window)", lambda b: _f(b["stale_execution"]))
    row("two-stage bootstrap", lambda b: str(b["stale_execution"]["two_stage_ci"]))
    row("among runs that acted", lambda b: _f(b["stale_among_acted"]))
    row("window verdicts", lambda b: ", ".join(f"{k} {v}" for k, v in b["window_verdicts"].items()))
    row("skill loaded", lambda b: _f(b["stale_skill_loaded"]))
    row("skill not loaded", lambda b: _f(b["stale_skill_not_loaded"]))
    row("recall surfaced the new fact", lambda b: _f(b["stale_recall_surfaced_new"]))
    row("recall didn't", lambda b: _f(b["stale_recall_did_not_surface_new"]))
    row("told / in passing", lambda b: " / ".join(_f(v) for v in b["stale_by_channel"].values()))
    row("all runs after a change", lambda b: _f(b["stale_all_after_change"]))
    row("**Changes that reached memory**", lambda b: f"{b['reached_memory']['changes_reached']}/{b['reached_memory']['changes']}")
    row("**Stale rate, reached memory**", lambda b: _f(b["reached_memory"]["stale_execution"]))
    row("that, skill loaded / not", lambda b: f"{_f(b['reached_memory']['stale_skill_loaded'])} / "
        f"{_f(b['reached_memory']['stale_skill_not_loaded'])}")
    row("**Wrong switches** (every run after the edit)", lambda b: _f(b["wrong_switch_all_after_edit"]))
    row("wrong switches, edit's day + 13", lambda b: _f(b["wrong_switch_14_days"]))
    row("date notes (on newer-skill runs: must be 0)", lambda b: f"{b['date_notes']} ({b['date_notes_on_newer_skill_runs']})")
    row("**Unchanged controls**: first acting step right", lambda b: _f(b["unchanged_controls_success"]))
    row("changed procedures before their change: right", lambda b: _f(b["before_change_success"]))
    row("Patches applied by the arm (false)", lambda b: f"{b['patches']['own_applied']} ({b['patches']['false']})")
    row("Skills: consent / seeded / naming the fact", lambda b: f"{b['skills']['consent']} / {b['skills']['seeded']} / "
        f"{b['skills']['names_fact']}")
    row("Time to fix: changes fixed, median days", lambda b: f"{b['time_to_fix']['fixed']}/{b['time_to_fix']['changes']}, "
        f"{b['time_to_fix']['median_days']}")
    for category in world.PROBE_CATEGORIES:
        row(f"Probes: {category.replace('_', ' ')}", lambda b, c=category: f"{b['probes'][c]['right']}/{b['probes'][c]['n']}")
    row("Simulated days", lambda b: str(b["days"]))
    row("ChatGPT-lane tokens (per simulated day)", lambda b: f"{b['tokens']:,} ({b['tokens_per_day']:,})")
    row("GPU-hours booked (seconds per simulated day)", lambda b: f"{b['gpu_seconds'] / 3600:.2f} ({b['gpu_seconds_per_day']})")
    row("Steps redone after a stop; limit errors", lambda b: f"{b['redone']}; {b['limit_errors']}")
    lines += ["", "## Stale rate by kind of fact (window)", "",
              "| Kind | " + " | ".join(f"{label[m]} {a}" for m, a in order) + " |", "|---|" + "---|" * len(order)]
    for kind in world.KINDS:
        row(kind, lambda b, k=kind: _f(b["stale_by_kind"][k]))
    lines.append("")
    return "\n".join(lines)


def tables(blocks: dict[tuple[str, str], dict[str, Any]], tests: dict[str, Any]) -> str:
    """The paper's tables (compact; the same numbers as the report)."""

    label = {key: value["label"] for key, value in grid.MODELS.items()}
    lines = ["# Paper tables (Design A)", "", "Table 1. Stale-execution rate on the change day and the 13 days after, "
             "by model and arm (stale runs / window runs; cluster bootstrap 95%).", "",
             "| Model | Arm | Stale | 95% CI | Acted-only | Reached memory: stale | Changes reached |", "|---|---|---|---|---|---|---|"]
    for model in MODEL_ORDER:
        for arm in ARM_ORDER:
            b = blocks.get((model, arm))
            if not b:
                continue
            s = b["stale_execution"]
            lines.append(f"| {label[model]} | {arm} | {s['k']}/{s['n']} ({100 * (s['rate'] or 0):.1f}%) | {s['cluster_ci']} | "
                         f"{_f(b['stale_among_acted'])} | {_f(b['reached_memory']['stale_execution'])} | "
                         f"{b['reached_memory']['changes_reached']}/{b['reached_memory']['changes']} |")
    lines += ["", "Table 2. H-A1 on the 9B (Holm-adjusted intervals of rate ratios).", "",
              "| Comparison | Ratio | Interval (level) | p (Holm) |", "|---|---|---|---|"]
    for key in ("h_a1_end_to_end", "h_a1_reached_memory"):
        for name, item in ((tests.get(key) or {}).get("tests") or {}).items():
            point = "n/a" if item["point"] is None else f"{item['point']:.2f}"
            lines.append(f"| {name} ({key.removeprefix('h_a1_').replace('_', ' ')}) | {point} | {item['interval']} "
                         f"({item['level']}) | {_p(item['p_holm'])} |")
    lines += ["", "Table 3. Safety: false patches, unchanged controls, wrong switches.", "",
              "| Model | Arm | False patches | Controls right | Wrong switches (all after edit) | Wrong switches (14 days) |",
              "|---|---|---|---|---|---|"]
    for model in MODEL_ORDER:
        for arm in ARM_ORDER:
            b = blocks.get((model, arm))
            if not b:
                continue
            lines.append(f"| {label[model]} | {arm} | {b['patches']['false']}/{b['patches']['own_applied']} | "
                         f"{_f(b['unchanged_controls_success'])} | {_f(b['wrong_switch_all_after_edit'])} | "
                         f"{_f(b['wrong_switch_14_days'])} |")
    lines += ["", "Table 4. Probes (right / asked), by arm.", "",
              "| Model | Arm | " + " | ".join(c.replace("_", " ") for c in world.PROBE_CATEGORIES) + " |",
              "|---|---|" + "---|" * len(world.PROBE_CATEGORIES)]
    for model in MODEL_ORDER:
        for arm in ARM_ORDER:
            b = blocks.get((model, arm))
            if b:
                lines.append(f"| {label[model]} | {arm} | " + " | ".join(
                    f"{b['probes'][c]['right']}/{b['probes'][c]['n']}" for c in world.PROBE_CATEGORIES) + " |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 3's report (run once every cell is done)")
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--prereg-sha", default="ba01c257fe04ce75bd1c8f3c959b2dededd989806863ed981544a2a76aa34072")
    parser.add_argument("--allow-unfinished", action="store_true", help="for the harness's own test data only")
    args = parser.parse_args(argv)
    root = Path(args.root)
    loaded: dict[tuple[str, str], list[CellData]] = {}
    unfinished = []
    builds = set()
    for queue in ("main", "local"):
        for cell in grid.cells(queue):
            folder = root / cell.name
            if not grid.progress(folder)["finished"]:
                unfinished.append(cell.name)
                if not args.allow_unfinished or not (folder / "run" / "state.json").is_file():
                    continue
            data = load_cell(folder, cell)
            builds |= {item.get("build") for item in data.state.get("builds") or ()}
            loaded.setdefault((cell.model, cell.arm), []).append(data)
    if unfinished and not args.allow_unfinished:
        raise SystemExit(f"{len(unfinished)} cells aren't done ({', '.join(unfinished[:5])}...): no report before "
                         "every run is done")
    blocks = {key: arm_block(cells) for key, cells in loaded.items()}
    tests = analyse(blocks)
    import time

    meta = {"prereg_sha": args.prereg_sha[:8] + "…" + args.prereg_sha[-4:], "builds": ", ".join(sorted(str(b) for b in builds)),
            "generated": time.strftime("%Y-%m-%d %H:%M"), "unfinished": unfinished}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    public = {f"{model}/{arm}": {key: value for key, value in block.items() if not key.startswith("_")}
              for (model, arm), block in blocks.items()}
    (out / "report.json").write_text(json.dumps({"meta": meta, "tests": tests, "blocks": public}, indent=1,
                                                sort_keys=True, default=str), encoding="utf-8")
    (out / "report.md").write_text(markdown(blocks, tests, meta), encoding="utf-8")
    (out / "tables.md").write_text(tables(blocks, tests), encoding="utf-8")
    print(out / "report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
