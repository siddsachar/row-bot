"""The stale-skills report: each run's ``stale.json`` in, each arm's numbers out (Design A, Lane B).

    python -m tests.e2e.learning.stale_skills.report <run dir> [<run dir> ...] --out <folder> [--hand-check 20]

writes ``report.json`` and ``report.md`` (counts, rates with denominators, Wilson and change-clustered bootstrap
intervals, synthetic values only) and, with ``--hand-check N``, ``hand_check.md``: N runs drawn across the verdicts
(seeded), each with its first attempt's tool calls and acting step and the timeline's values, for a person to check
the scorer by hand.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

from tests.e2e.learning.stale_skills import scorer, world

LABELS = {"main": "Main model (ChatGPT lane, eval profile)", "local": "Local Qwen3.5 9B (Ollama)"}


def _frac(item: dict[str, Any] | None) -> str:
    if not item or not item.get("n"):
        return "0/0"
    text = f"{item['k']}/{item['n']} = {100 * item['k'] / item['n']:.1f}%"
    if item.get("wilson"):
        text += f" [{item['wilson'][0]}, {item['wilson'][1]}]"
    return text


def rule_check(timeline: world.Timeline) -> dict[str, Any]:
    """Whether the changes landed where the pre-registration's rule says (read back from the timeline)."""

    days = [change.day for change in timeline.changes]
    return {"count": len(days), "in_range": all(world.CHANGE_FIRST <= day <= world.CHANGE_LAST for day in days),
            "min_gap": min(later - earlier for earlier, later in zip(days, days[1:])),
            "kinds": sorted({change.kind for change in timeline.changes}),
            "unchanged": sorted(item.id for item in timeline.procedures if timeline.change_of(item.id) is None),
            "weekly": all(len(timeline.schedule[item.id]) >= 1 for item in timeline.procedures)}


def rescore(timeline: world.Timeline, state: dict[str, Any],
            stored: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Every run and probe scored again from what the run saved (its first attempt's actions; the probe's reply), by
    the scorer as it is now: the timeline is the only ground truth. ``stored``: when each change reached memory (the
    run's ``stale.json``, from the product's operation log)."""

    runs = []
    for item in state.get("runs") or ():
        scored = scorer.score_run(timeline, item["id"], item["day"], item["attempts"][0]["actions"]).to_dict()
        item["score"] = scored
        runs.append({**scored, "skill_loaded": item.get("skill_loaded"), "recalled_new": item.get("recalled_new"),
                     "passed_first": item.get("passed_first"), "at": item.get("at")})
    probes = []
    for item in state.get("probes") or ():
        item["verdict"] = scorer.score_probe(item, item.get("answer") or "")
        probes.append(item)
    return scorer.metrics(timeline, runs, stored), scorer.probe_metrics(probes)


def arm_summary(data: dict[str, Any], state: dict[str, Any], timeline: world.Timeline) -> dict[str, Any]:
    gpu = round(float((data.get("gpu") or {}).get("seconds") or 0) / 3600, 2)
    skills = data.get("skills") or {}
    metrics, probes = rescore(timeline, state, data.get("stored"))
    asked = sum(1 for item in state.get("runs") or () if item["score"]["in_window"] and item.get("asked_first", any(
        call["name"] == "ask_person" for call in item["attempts"][0]["calls"])))
    arm = data.get("arm") or {}
    return {"model": f"{arm.get('arm', 'N')}/{arm.get('lane', arm.get('arm'))}", "study_arm": arm.get("arm"),
            "lane": arm.get("lane"), "main_ref": arm.get("main_ref"), "patches": data.get("patches"),
            "approvals": data.get("approvals"), "repairs": data.get("repairs"), "operations": data.get("operations"),
            "date_notes": sum(1 for item in state.get("runs") or () if item.get("date_note")),
            "date_notes_newer_skill": sum(1 for item in state.get("runs") or () if item.get("date_note")
                                          and timeline.edit_of(item["procedure"]) is not None
                                          and item["day"] >= timeline.edit_of(item["procedure"]).day),
            "edits": data.get("edits") or [],
            "build": (data.get("arm") or {}).get("build"), "days_done": data.get("days_done"),
            "metrics": metrics, "probes": probes, "asked_first_window": asked,
            "simulated_date": bool((data.get("arm") or {}).get("simulated_date")),
            "skills": {"made_by_consent": sorted(key for key, value in skills.items() if value.get("source") != "seeded"),
                       "seeded": sorted(key for key, value in skills.items() if value.get("source") == "seeded"),
                       "names_fact": sorted(key for key, value in skills.items() if value.get("names_fact")),
                       "by_procedure": skills},
            "unprompted_skills": data.get("unprompted_skills"), "redone": data.get("redone"),
            "tokens": data["tokens"], "gpu_hours": gpu, "limit_errors": data.get("limit_errors"),
            "write_backs": data.get("write_backs"), "refused_tool_calls": data.get("refused_tool_calls")}


def markdown(summary: dict[str, Any]) -> str:
    rule = summary["rule"]
    lines = ["# Stale skills, Design A: arms, one person", "",
             f"Person {summary['person']}, seed {summary['seed']}, timeline `{summary['timeline'][:12]}`, phrasing "
             f"{summary['phrasing']}.", "",
             f"Changes: {rule['count']} on days {', '.join(str(c['day']) for c in summary['changes'])} "
             f"(in 20-110: {rule['in_range']}; smallest gap {rule['min_gap']} days; kinds {', '.join(rule['kinds'])}); "
             f"{len(rule['unchanged'])} procedures never change.", "",
             "| Change | Day | Kind | Told | Old | New |", "|---|---|---|---|---|---|"]
    for change in summary["changes"]:
        lines.append(f"| {change['procedure']} | {change['day']} | {change['kind']} | {change['channel']} | "
                     f"{world.say(change['kind'], change['old'])} | {world.say(change['kind'], change['new'])} |")
    lines += ["", "## Arms", "",
              "| | " + " | ".join(LABELS.get(arm["model"], arm["model"]) + f" ({arm['days_done']} days)"
                                 for arm in summary["arms"]) + " |",
              "|---|" + "---|" * len(summary["arms"])]

    def row(label: str, pick: Any) -> None:
        lines.append(f"| {label} | " + " | ".join(pick(arm) for arm in summary["arms"]) + " |")

    row("**Stale-execution rate** (runs within 14 days of a change)", lambda a: _frac(a["metrics"]["stale_execution"]))
    row("change-clustered bootstrap 95%", lambda a: str(a["metrics"]["stale_execution"].get("cluster_bootstrap")))
    row("with the procedure's skill loaded", lambda a: _frac(a["metrics"]["stale_skill_loaded"]))
    row("without it", lambda a: _frac(a["metrics"]["stale_skill_not_loaded"]))
    row("recall surfaced the new fact", lambda a: _frac(a["metrics"]["stale_recall_surfaced_new"]))
    row("recall didn't", lambda a: _frac(a["metrics"]["stale_recall_did_not_surface_new"]))
    row("all runs after a change", lambda a: _frac(a["metrics"]["stale_all_after_change"]))
    row("only window runs that acted (not pre-registered)", lambda a: _frac(a["metrics"]["stale_among_acted"]))
    row("window runs that asked the person before acting", lambda a: str(a["asked_first_window"]))
    row("window verdicts", lambda a: ", ".join(f"{k} {v}" for k, v in a["metrics"]["window_verdicts"].items()))
    row("**Changes that reached memory** (op log)", lambda a: f"{a['metrics'].get('reached_memory', {}).get('stored', '-')}"
        f" of {len(summary['changes'])}")
    row("**Stale-execution rate, changes that reached memory** (their runs from then on)",
        lambda a: _frac(a["metrics"].get("reached_memory", {}).get("stale_execution")))
    row("that, with the procedure's skill loaded",
        lambda a: _frac(a["metrics"].get("reached_memory", {}).get("stale_skill_loaded")))
    row("that, without it", lambda a: _frac(a["metrics"].get("reached_memory", {}).get("stale_skill_not_loaded")))
    row("**Wrong switches** (newer skill, older memory; 14 days)", lambda a: _frac(a["metrics"].get("wrong_switch")))
    row("wrong switches, all runs after the edit", lambda a: _frac(a["metrics"].get("wrong_switch_all_after_edit")))
    row("newer-skill runs' verdicts", lambda a: ", ".join(f"{k} {v}" for k, v in
                                                       (a["metrics"].get("newer_skill_verdicts") or {}).items()))
    row("date notes on newer-skill runs (must be 0)", lambda a: str(a.get("date_notes_newer_skill") or 0))
    row("**Unchanged procedures**: first acting step right", lambda a: _frac(a["metrics"]["unchanged_success"]))
    row("unchanged: first attempt passed (verifier and value)",
        lambda a: f"{a['metrics']['unchanged_verifier_first']['k']}/{a['metrics']['unchanged_verifier_first']['n']}")
    row("changed procedures before their change: right", lambda a: _frac(a["metrics"]["before_change_success"]))
    row("Skills made through consent", lambda a: f"{len(a['skills']['made_by_consent'])} of "
        f"{len(a['skills']['made_by_consent']) + len(a['skills']['seeded'])}")
    row("Skills seeded (Row-Bot saved none)", lambda a: str(len(a["skills"]["seeded"])))
    row("Skills naming their fact", lambda a: str(len(a["skills"]["names_fact"])))
    row("Runs with arm D's date note", lambda a: str(a.get("date_notes") or 0))
    row("Skill patches applied (false)", lambda a: f"{(a.get('patches') or {}).get('applied', 0)} "
        f"({(a.get('patches') or {}).get('false', 0)})")
    row("Patch candidates by outcome", lambda a: json.dumps((a.get("patches") or {}).get("candidates") or {}))
    for category in world.PROBE_CATEGORIES:
        row(f"Probes: {category.replace('_', ' ')}", lambda a, c=category: f"{a['probes'][c]['right']}/{a['probes'][c]['n']}")
    row("ChatGPT-lane tokens", lambda a: f"{a['tokens']['cloud_total']:,}")
    row("GPU-hours booked", lambda a: str(a["gpu_hours"]))
    lines += ["", "95% Wilson intervals in brackets. The bootstrap resamples change events (10,000 resamples, seed "
              f"{scorer.BOOTSTRAP_SEED}).", "", "## Stale rate by kind of fact (window)", "",
              "| Kind | " + " | ".join(LABELS.get(a["model"], a["model"]) for a in summary["arms"]) + " |",
              "|---|" + "---|" * len(summary["arms"])]
    for kind in world.KINDS:
        row(kind, lambda a, k=kind: _frac(a["metrics"]["stale_execution_by_kind"][k]))
    lines += ["", "## Time to fix", ""]
    for arm in summary["arms"]:
        lines += [f"**{LABELS.get(arm['model'], arm['model'])}**", "",
                  "| Change | Day | Runs after | Days to first right run | Stale runs (all after) | Window stale/runs |",
                  "|---|---|---|---|---|---|"]
        for procedure, fix in arm["metrics"]["time_to_fix"].items():
            lines.append(f"| {procedure} | {fix['change_day']} | {fix['runs_after']} | {fix['days_to_first_right']} | "
                         f"{fix['stale_runs']} | {fix['window']['stale']}/{fix['window']['runs']} |")
        lines.append("")
    lines += ["## Changes reaching memory", ""]
    for arm in summary["arms"]:
        stored = (arm["metrics"].get("reached_memory") or {}).get("changes") or {}
        lines.append(f"- {arm['model']}: " + "; ".join(
            f"{procedure} {'day ' + str(hit['day']) + ' (' + str(hit.get('event')) + ')' if hit else 'never'}"
            for procedure, hit in stored.items()))
    lines += ["", "## Skills", ""]
    for arm in summary["arms"]:
        for procedure, skill in sorted(arm["skills"]["by_procedure"].items()):
            lines.append(f"- {arm['model']}, {procedure}: `{skill.get('name')}`, {skill.get('source')} on day "
                         f"{skill.get('day')}; names the fact {skill.get('names_fact')}; consent turns "
                         f"{skill.get('consent_turns')}")
    lines += ["", "## Probes by day", ""]
    for arm in summary["arms"]:
        lines.append(f"- {arm['model']}: " + "; ".join(f"day {day} {v['right']}/{v['n']}"
                                                        for day, v in arm["probes"]["by_day"].items()))
    lines.append("")
    return "\n".join(lines)


def hand_check(runs: list[tuple[str, dict[str, Any]]], timeline: world.Timeline, count: int, seed: int) -> str:
    """``count`` runs across the verdicts (window runs first), with what a person needs to check each by hand."""

    rng = random.Random(seed)
    by_verdict: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for model, record in runs:
        key = record["score"]["verdict"] + ("/window" if record["score"]["in_window"] else "")
        by_verdict.setdefault(key, []).append((model, record))
    picked: list[tuple[str, dict[str, Any]]] = []
    keys = sorted(by_verdict, key=lambda key: ("/window" not in key, key))
    while len(picked) < count and any(by_verdict.values()):
        for key in keys:
            if by_verdict[key] and len(picked) < count:
                picked.append(by_verdict[key].pop(rng.randrange(len(by_verdict[key]))))
    lines = ["# Hand check of the scorer", "", f"{len(picked)} runs drawn across the verdicts (seed {seed}). For each: "
             "the timeline's values on the run's day, the first attempt's tool calls in order and its first acting "
             "step. Check by eye whether the step used the old value, the new one or neither.", ""]
    for number, (model, record) in enumerate(picked, 1):
        procedure = timeline.procedure(record["procedure"])
        change = timeline.change_of(procedure.id)
        first = record["attempts"][0]
        lines += [f"## {number}. {record['id']} ({model})", "",
                  f"- Procedure: {procedure.id} ({procedure.kind}); asked: {record['prompt']}",
                  f"- Timeline on day {record['day']}: in effect **{world.say(procedure.kind, timeline.value(procedure.id, record['day']))}**"
                  + (f"; change on day {change.day}: {world.say(change.kind, change.old)} -> "
                     f"{world.say(change.kind, change.new)}" if change else "; never changes"),
                  "- Calls: " + "; ".join(f"{call['name']}({json.dumps(call['args'], ensure_ascii=False)[:220]})"
                                          for call in first["calls"][:8]),
                  f"- Actions (app state): {json.dumps(first['actions'], ensure_ascii=False)[:600]}",
                  f"- Scorer: **{record['score']['verdict']}** (in window {record['score']['in_window']})",
                  "- By hand: ", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The stale-skills phase-1 report")
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--out", required=True)
    parser.add_argument("--hand-check", type=int, default=0)
    parser.add_argument("--hand-seed", type=int, default=20261026)
    args = parser.parse_args(argv)
    arms, records, loaded = [], [], []
    for folder in args.runs:
        data = json.loads((Path(folder) / "stale.json").read_text(encoding="utf-8"))
        state = json.loads((Path(folder) / "state.json").read_text(encoding="utf-8"))
        loaded.append((data, state))
    from tests.e2e.learning.stale_skills import phrasing

    label = loaded[0][0].get("phrasing") or "templates"
    # The longest run's timeline: a shorter run's days are the same days (the change rule doesn't depend on length).
    person = str(loaded[0][0].get("person") or world.PERSON)
    timeline = world.build(person=person, seed=int(loaded[0][0]["seed"]),
                           days=max(int(data["days_asked"]) for data, _ in loaded),
                           phrasing=None if label == "templates" else phrasing.load(person, label),
                           smoke=bool(loaded[0][0].get("smoke")))
    for data, state in loaded:
        if [item["day"] for item in data["changes"]] != [item.day for item in timeline.changes]:
            raise SystemExit("the runs come from different timelines")
        arms.append(arm_summary(data, state, timeline))
        records += [((data.get("arm") or {}).get("arm"), item) for item in state.get("runs") or ()]
    summary = {"person": person, "seed": timeline.seed, "timeline": timeline.fingerprint(),
               "phrasing": timeline.phrasing, "changes": [item.to_dict() for item in timeline.changes],
               "rule": rule_check(timeline), "arms": arms}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(summary, indent=1, sort_keys=True, default=str), encoding="utf-8")
    (out / "report.md").write_text(markdown(summary), encoding="utf-8")
    if args.hand_check:
        (out / "hand_check.md").write_text(hand_check(records, timeline, args.hand_check, args.hand_seed),
                                           encoding="utf-8")
    print(out / "report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
