"""The precedence study's report: every model's ``<model>.jsonl`` in, PRECEDENCE.md's numbers out (Lane B).

Fixed before any run (``PRECEDENCE.md`` "Metrics and analysis", "Decision"):

- **Kept items** (local models): an item is kept for a model when the model passes conditions 1, 2 and 5 on it
  (choice ``x``, ``y``, ``y``) in at least 2 of its 3 runs; dropped items are counted per model. The main model
  runs only conditions 3, 4 and 6, so its items can't be checked and all of them are kept (said so).
- **Primary**: the stale-choice rate in condition 3 (choice ``x``) over the kept items' calls, per model, with a
  Wilson interval and an item-clustered bootstrap interval. Calls with a provider error are left out (counted).
- **Note effect**: condition 3 vs 4, paired per item and run, exact McNemar per model, Holm across the models.
- **False switch**: the rate of ``x`` (acting on the older memory) in condition 6.
- Also: ``neither`` and ``both`` per condition, the rate by kind of fact, how the first action came (tool, text,
  another tool).
- **Decision** for the 9B (:func:`scorer.decision`).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tests.e2e.learning.precedence import scorer
from tests.e2e.learning.precedence.items import KINDS

ORDER = ("qwen3.5-4b", "qwen3.5-9b", "qwen3.8-27b", "main")
LABELS = {"qwen3.5-4b": "Qwen3.5 4B", "qwen3.5-9b": "Qwen3.5 9B", "qwen3.8-27b": "Qwen3.8 27B",
          "main": "Main model (ChatGPT lane)"}


def load(folder: Path) -> dict[str, list[dict[str, Any]]]:
    found = {}
    for model in ORDER:
        path = folder / f"{model}.jsonl"
        if path.is_file():
            found[model] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return found


def kept_items(rows: list[dict[str, Any]], model: str) -> tuple[set[str], set[str]]:
    items = {row["item"] for row in rows}
    if model == "main":
        return items, set()
    need = {1: "x", 2: "y", 5: "y"}
    passes: dict[tuple[str, int], int] = defaultdict(int)
    for row in rows:
        if row["condition"] in need and row.get("choice") == need[row["condition"]]:
            passes[(row["item"], row["condition"])] += 1
    kept = {item for item in items if all(passes[(item, condition)] >= 2 for condition in need)}
    return kept, items - kept


def _rate(rows: list[dict[str, Any]], choice: str = "x") -> dict[str, Any]:
    scored = [row for row in rows if row.get("choice") is not None]
    hits = sum(1 for row in scored if row["choice"] == choice)
    per_item: dict[str, list[int]] = defaultdict(list)
    for row in scored:
        per_item[row["item"]].append(int(row["choice"] == choice))
    return {"k": hits, "n": len(scored), "rate": round(hits / len(scored), 4) if scored else None,
            "wilson95": scorer.wilson(hits, len(scored)), "items": len(per_item),
            "item_bootstrap95": scorer.item_clustered_interval(per_item),
            "errors": sum(1 for row in rows if row.get("choice") is None)}


def model_summary(model: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    kept, dropped = kept_items(rows, model)
    mine = [row for row in rows if row["item"] in kept]
    by_condition = {condition: [row for row in mine if row["condition"] == condition] for condition in range(1, 7)}
    summary: dict[str, Any] = {
        "items": len({row["item"] for row in rows}), "kept": len(kept), "dropped": len(dropped),
        "runs": max((row.get("run", 1) for row in rows), default=0), "calls": len(rows),
        "errors": sum(1 for row in rows if row.get("choice") is None),
        "stale_c3": _rate(by_condition[3]), "stale_c4": _rate(by_condition[4]),
        "false_switch_c6": _rate(by_condition[6]),
        # Sensitivity (not the fixed metric): without the calls whose first acting reply was another tool (most
        # are reads the READS list doesn't name, such as workspace_list_directory).
        "stale_c3_without_other_tool": _rate([row for row in by_condition[3] if row.get("via") != "other_tool"]),
        "stale_c4_without_other_tool": _rate([row for row in by_condition[4] if row.get("via") != "other_tool"]),
        "false_switch_c6_without_other_tool": _rate([row for row in by_condition[6]
                                                      if row.get("via") != "other_tool"]),
        "other_tools": dict(sorted(Counter(str(row.get("tool")) for row in rows
                                           if row.get("via") == "other_tool").items())),
        "conditions": {
            str(condition): {str(choice or "error"): sum(1 for row in group if row.get("choice") == choice)
                             for choice in (scorer.X, scorer.Y, scorer.BOTH, scorer.NEITHER, None)}
            | {"via": {via: sum(1 for row in group if row.get("via") == via)
                       for via in ("tool", "ask", "text", "other_tool", "reads_only")}}
            for condition, group in by_condition.items() if group},
        "by_kind_c3": {kind: _rate([row for row in by_condition[3] if row["kind"] == kind]) for kind in KINDS},
        "tokens": sum(sum((row.get("tokens") or {}).values()) for row in rows),
        "seconds": round(sum(float(row.get("seconds") or 0) for row in rows), 1),
    }
    pairs3 = {(row["item"], row.get("run", 1)): row for row in by_condition[3] if row.get("choice") is not None}
    pairs4 = {(row["item"], row.get("run", 1)): row for row in by_condition[4] if row.get("choice") is not None}
    both = sorted(set(pairs3) & set(pairs4))
    only3 = sum(1 for key in both if pairs3[key]["choice"] == "x" and pairs4[key]["choice"] != "x")
    only4 = sum(1 for key in both if pairs4[key]["choice"] == "x" and pairs3[key]["choice"] != "x")
    summary["note_effect"] = {"pairs": len(both), "stale_in_3_only": only3, "stale_in_4_only": only4,
                              "p_mcnemar": round(scorer.mcnemar_exact(only3, only4), 6)}
    return summary


def build(folder: Path) -> dict[str, Any]:
    data = load(folder)
    models = {model: model_summary(model, rows) for model, rows in data.items()}
    adjusted = scorer.holm({model: summary["note_effect"]["p_mcnemar"] for model, summary in models.items()})
    for model, summary in models.items():
        summary["note_effect"]["p_holm"] = adjusted[model]
    nine = models.get("qwen3.5-9b")
    if nine:
        rate3, rate4 = nine["stale_c3"]["rate"], nine["stale_c4"]["rate"]
        cut = bool(rate3 and rate4 is not None and rate4 <= 0.5 * rate3)
        verdict = scorer.decision(rate3, cut, nine["note_effect"]["p_holm"], nine["false_switch_c6"]["rate"])
    else:
        cut, verdict = False, "undecided"
    return {"models": models, "decision": verdict, "note_cuts_9b_by_half": cut}


def _cell(rate: dict[str, Any]) -> str:
    if not rate.get("n"):
        return "-"
    low, high = rate["wilson95"]
    boot = rate.get("item_bootstrap95")
    extra = f"; items [{boot[0]}, {boot[1]}]" if boot else ""
    return f"{rate['k']}/{rate['n']} = {100 * rate['rate']:.1f}% [{low}, {high}{extra}]"


def markdown(report: dict[str, Any]) -> str:
    models = report["models"]
    lines = ["# Precedence study: result", "",
             f"**Decision by PRECEDENCE.md's fixed rule: {report['decision']}.**", "",
             "## Stale choice (condition 3), the note (4) and false switches (6)", "",
             "| Model | Items kept / dropped | Stale choice, cond. 3 | Stale choice, cond. 4 (note) | "
             "Note effect (3 only / 4 only, McNemar p, Holm p) | False switch, cond. 6 |",
             "|---|---|---|---|---|---|"]
    for model in ORDER:
        if model not in models:
            continue
        item = models[model]
        effect = item["note_effect"]
        lines.append(f"| {LABELS[model]} | {item['kept']} / {item['dropped']} | {_cell(item['stale_c3'])} | "
                     f"{_cell(item['stale_c4'])} | {effect['stale_in_3_only']} / {effect['stale_in_4_only']}, "
                     f"p {effect['p_mcnemar']}, Holm {effect['p_holm']} | {_cell(item['false_switch_c6'])} |")
    lines += ["", "Rates are over kept items' calls (3 runs a local model, 1 for the main model); [Wilson 95%; "
              "item-clustered bootstrap 95%].", "", "## Choices per condition (kept items)", "",
              "| Model | Cond. | x | y | both | neither | error | via tool / ask / text / other tool / reads only |",
              "|---|---|---|---|---|---|---|---|"]
    for model in ORDER:
        if model not in models:
            continue
        for condition, counts in models[model]["conditions"].items():
            via = counts["via"]
            lines.append(f"| {LABELS[model]} | {condition} | {counts['x']} | {counts['y']} | {counts['both']} | "
                         f"{counts['neither']} | {counts['error']} | {via['tool']} / {via['ask']} / {via['text']} / "
                         f"{via['other_tool']} / {via['reads_only']} |")
    lines += ["", "## Stale choice by kind of fact (condition 3)", "",
              "| Model | " + " | ".join(KINDS) + " |", "|---|" + "---|" * len(KINDS)]
    for model in ORDER:
        if model not in models:
            continue
        cells = [f"{rate['k']}/{rate['n']}" if rate["n"] else "-" for rate in
                 (models[model]["by_kind_c3"][kind] for kind in KINDS)]
        lines.append(f"| {LABELS[model]} | " + " | ".join(cells) + " |")
    lines += ["", "## Sensitivity: without first replies that called another tool (not the fixed metric)", "",
              "| Model | Cond. 3 | Cond. 4 | Cond. 6 | Those other tools |", "|---|---|---|---|---|"]
    for model in ORDER:
        if model in models:
            item = models[model]
            lines.append(f"| {LABELS[model]} | {_cell(item['stale_c3_without_other_tool'])} | "
                         f"{_cell(item['stale_c4_without_other_tool'])} | "
                         f"{_cell(item['false_switch_c6_without_other_tool'])} | {item['other_tools']} |")
    lines += ["", "## Spend", ""]
    for model in ORDER:
        if model in models:
            lines.append(f"- {LABELS[model]}: {models[model]['calls']} calls, {models[model]['tokens']:,} tokens, "
                         f"{models[model]['seconds'] / 3600:.2f} h of calls, {models[model]['errors']} errors")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The precedence study's report")
    parser.add_argument("folder")
    args = parser.parse_args(argv)
    folder = Path(args.folder)
    report = build(folder)
    (folder / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True, default=str), encoding="utf-8")
    text = markdown(report)
    (folder / "report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
