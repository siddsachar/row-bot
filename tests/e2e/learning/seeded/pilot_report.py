"""The stale-skills pilot's result: both arms' ``pilot.json`` in, §4's numbers and the fixed decision out (Lane B).

``python -m tests.e2e.learning.seeded.pilot_report <main run dir> <local run dir> --out <folder>`` writes
``pilot_report.json`` and ``pilot_report.md``. Counts, rates and synthetic values only.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from tests.e2e.learning.seeded import pilot_scorer

LABELS = {"main": "Main model (ChatGPT lane, eval profile)", "local": "Local Qwen3.5 9B (Ollama)"}


def _fraction(item: dict[str, Any]) -> str:
    if not item.get("runs"):
        return "0/0"
    return f"{item['stale']}/{item['runs']} = {100 * item['rate']:.0f}%"


def summarise(arms: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rates = {arm: data["rates"]["overall"]["rate"] for arm, data in arms.items()}
    tokens = {arm: data["tokens"]["cloud_total"] for arm, data in arms.items()}
    gpu_hours = {arm: round(float(data["gpu"].get("seconds") or 0) / 3600, 2) for arm, data in arms.items()}
    return {"decision": pilot_scorer.decision(rates), "rates": rates, "arms": arms,
            "chatgpt_lane_tokens": {**tokens, "total": sum(tokens.values())}, "gpu_hours": gpu_hours}


def markdown(summary: dict[str, Any]) -> str:
    lines = ["# Stale-skills pilot: result", "",
             f"**Decision by §4's fixed rule: {summary['decision']}** "
             "(go if >= 20% on either model; no-go if < 5% on both; otherwise in between).", "",
             "| | Stale runs after a change | Bread order (day 10) | Weekend shift (day 19) | Recall surfaced the new "
             "fact | Recall didn't | Skill loaded | Skill not loaded |",
             "|---|---|---|---|---|---|---|---|"]
    for arm, data in summary["arms"].items():
        rate = data["rates"]
        lines.append(f"| {LABELS.get(arm, arm)} | {_fraction(rate['overall'])} | {_fraction(rate['bread_order'])} | "
                     f"{_fraction(rate['weekend_shift'])} | {_fraction(rate['recall_surfaced_new'])} | "
                     f"{_fraction(rate['recall_did_not_surface_new'])} | {_fraction(rate['skill_loaded'])} | "
                     f"{_fraction(rate['skill_not_loaded'])} |")
    lines += ["", "Verdicts after the changes (first attempt): " + "; ".join(
        f"{arm} {data['rates']['verdicts']}" for arm, data in summary["arms"].items()), "", "## The skills", ""]
    for arm, data in summary["arms"].items():
        for task, skill in sorted((data.get("skills") or {}).items()):
            lines.append(f"- {arm}, {task}: `{skill.get('name')}`, {skill.get('source')} on day {skill.get('day')}"
                         + (f" ({skill['why']})" if skill.get("why") else ""))
    lines += ["", "## Spend", "",
              f"- ChatGPT-lane tokens: {summary['chatgpt_lane_tokens']}",
              f"- GPU-hours (the local arm's bookings): {summary['gpu_hours']}", ""]
    return "\n".join(lines)


def wilson(stale: int, runs: int, z: float = 1.96) -> list[float] | None:
    """The 95% Wilson interval of ``stale``/``runs``, in percent."""

    if not runs:
        return None
    share = stale / runs
    middle = (share + z * z / (2 * runs)) / (1 + z * z / runs)
    half = z * math.sqrt(share * (1 - share) / runs + z * z / (4 * runs * runs)) / (1 + z * z / runs)
    return [round(100 * max(0.0, middle - half), 1), round(100 * min(1.0, middle + half), 1)]


def pooled(runs_by_seed: dict[int, list[dict[str, Any]]]) -> dict[str, Any]:
    """Per seed and pooled: §4's rates (stale / runs after a change) with Wilson intervals."""

    def with_intervals(rates: dict[str, Any]) -> dict[str, Any]:
        return {key: ({**value, "wilson95": wilson(value["stale"], value["runs"])}
                      if isinstance(value, dict) and "runs" in value else value) for key, value in rates.items()}

    found = {str(seed): with_intervals(pilot_scorer.rates(runs)) for seed, runs in sorted(runs_by_seed.items())}
    found["pooled"] = with_intervals(pilot_scorer.rates([run for runs in runs_by_seed.values() for run in runs]))
    return found


def pool_markdown(label: str, table: dict[str, Any]) -> str:
    def cell(item: dict[str, Any]) -> str:
        if not item.get("runs"):
            return "0/0"
        low, high = item["wilson95"]
        return f"{item['stale']}/{item['runs']} = {100 * item['rate']:.0f}% [{low}, {high}]"

    lines = [f"## {label}: per seed and pooled", "",
             "| Seed | Stale after a change | Bread order | Weekend shift | Recall surfaced new | Recall didn't | "
             "Skill loaded | Skill not loaded |", "|---|---|---|---|---|---|---|---|"]
    for seed, rate in table.items():
        lines.append(f"| {seed} | {cell(rate['overall'])} | {cell(rate['bread_order'])} | {cell(rate['weekend_shift'])} | "
                     f"{cell(rate['recall_surfaced_new'])} | {cell(rate['recall_did_not_surface_new'])} | "
                     f"{cell(rate['skill_loaded'])} | {cell(rate['skill_not_loaded'])} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The stale-skills pilot's report")
    parser.add_argument("main_run")
    parser.add_argument("local_run")
    parser.add_argument("--out", required=True)
    parser.add_argument("--local-extra", nargs="*", default=[], help="more local-arm run dirs (other seeds)")
    args = parser.parse_args(argv)
    arms = {arm: json.loads((Path(folder) / "pilot.json").read_text(encoding="utf-8"))
            for arm, folder in (("main", args.main_run), ("local", args.local_run))}
    summary = summarise(arms)
    text = markdown(summary)
    if args.local_extra:
        locals_ = [arms["local"], *(json.loads((Path(folder) / "pilot.json").read_text(encoding="utf-8"))
                                    for folder in args.local_extra)]
        runs = {int(item["seed"]): [{**run["score"], **run} if "score" in run else run for run in item["runs"]]
                for item in locals_}
        table = pooled(runs)
        rates = {"main": summary["rates"]["main"], "local (pooled)": table["pooled"]["overall"]["rate"]}
        summary["local_seeds"] = table
        summary["decision_pooled"] = pilot_scorer.decision(rates)
        summary["chatgpt_lane_tokens_extra_seeds"] = {str(item["seed"]): item["tokens"]["cloud_total"]
                                                      for item in locals_}
        summary["gpu_hours_extra_seeds"] = {str(item["seed"]): round(float(item["gpu"].get("seconds") or 0) / 3600, 3)
                                            for item in locals_}
        text += ("\n" + pool_markdown("Local Qwen3.5 9B", table)
                 + f"\n**Decision with the local arm pooled over seeds: {summary['decision_pooled']}**\n")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "pilot_report.json").write_text(json.dumps(summary, indent=1, sort_keys=True, default=str), encoding="utf-8")
    (out / "pilot_report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
