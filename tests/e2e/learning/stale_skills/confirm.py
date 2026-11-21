"""The stale-skills confirmation (``PREREG-DESIGN-A-CONFIRM.md``): its plan and its one-hypothesis report.

    python -m tests.e2e.learning.stale_skills.confirm --plan
    python -m tests.e2e.learning.stale_skills.confirm --report --root <confirmation folder> --out <report folder>

``--plan`` reads no folder and runs no model: the 12 cells, each person's change events in the fresh worlds
(``grid.CONFIRM_SEED``) and the estimates. The cells themselves run as ``grid --queue confirm``.

``--report`` refuses until all 12 cells have finished their 120 days (no interim outcome, §6). Its statistics are
Design A's (``analysis.py`` at ``95eadfaab``, unchanged): pooled window rates, ``ratio`` and the cluster
``bootstrap`` H-A1 used, applied to the main model's N and R cells. H-C1 holds when R/N is at most 0.5 and the upper
bound of its 95% interval is below 1 (§4; one test, so ``holm`` over one comparison is the plain 95% interval).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tests.e2e.learning.stale_skills import analysis, grid, phrasing, world

#: Design A's main cells, for the estimate (§6): 21.0M-30.7M ChatGPT-lane tokens a cell over 120 days.
TOKENS_PER_CELL = (21_000_000, 31_000_000)
CAP = 450_000_000


def timeline(person: str) -> world.Timeline:
    """``person``'s fresh world, as each confirm cell's ``run.py --seed`` builds it."""

    world.use_people_dir(person)
    return world.build(person=person, seed=grid.CONFIRM_SEED, days=world.DAYS, phrasing=phrasing.load(person, "main"))


def plan() -> str:
    cells = grid.cells("confirm")
    lines = [f"Stale-skills confirmation: {len(cells)} cells at world seed {grid.CONFIRM_SEED} (Design A: {world.SEED})",
             "", "Cells, in the order they run: " + ", ".join(cell.name for cell in cells), ""]
    changes = 0
    for person in world.PEOPLE:
        found = timeline(person)
        changes += len(found.changes)
        events = "; ".join(f"day {item.day} {item.kind} ({item.channel})" for item in found.changes)
        edits = ", ".join(f"day {item.day}" for item in found.edits)
        lines.append(f"- {person}: {len(found.changes)} changes: {events}. Newer-skill edits: {edits or 'none'}.")
    low, high = (len(cells) * value for value in TOKENS_PER_CELL)
    lines += ["", f"Change events: {changes}. Estimate: {low / 1e6:.0f}M-{high / 1e6:.0f}M ChatGPT-lane tokens "
              f"(cap {CAP / 1e6:.0f}M), no GPU."]
    return "\n".join(lines)


def h_c1(r_clusters: Mapping[Any, tuple[int, int]], n_clusters: Mapping[Any, tuple[int, int]], *,
         resamples: int = analysis.RESAMPLES) -> dict[str, Any]:
    """H-C1 from the two arms' (person, procedure) -> (stale runs, window runs) clusters."""

    arms = {"R": r_clusters, "N": n_clusters}
    values = analysis.bootstrap(arms, analysis.ratio("R", "N"), resamples=resamples)
    test = analysis.holm({"R/N": values})["R/N"]
    point = analysis.ratio("R", "N")({arm: analysis.pooled(value) for arm, value in arms.items()})
    test["point"] = point
    test["interval"] = analysis._round(test["interval"])  # noqa: SLF001
    test["holds"] = bool(point is not None and point <= 0.5 and test["upper_below_1"])
    return test


def report(root: Path, *, resamples: int = analysis.RESAMPLES) -> dict[str, Any]:
    cells = grid.cells("confirm")
    unfinished = [cell.name for cell in cells if not grid.progress(root / cell.name)["finished"]]
    if unfinished:
        raise SystemExit(f"not every cell has finished (no interim outcome): {', '.join(unfinished)}")
    for person in world.PEOPLE:
        # analysis.load_cell reads its timeline from this cache; the fresh worlds go in before any cell is loaded.
        analysis._TIMELINES[person] = timeline(person)  # noqa: SLF001
    loaded = {cell: analysis.load_cell(root / cell.name, cell) for cell in cells}
    blocks = {arm: analysis.arm_block([data for cell, data in loaded.items() if cell.arm == arm]) for arm in ("N", "R")}
    controls = {arm: blocks[arm]["_controls"] for arm in ("N", "R")}
    rates = {arm: analysis.pooled(value) for arm, value in controls.items()}
    out = {
        "seed": grid.CONFIRM_SEED,
        "h_c1": h_c1(blocks["R"]["_clusters"], blocks["N"]["_clusters"], resamples=resamples),
        "h_c1_reached_memory": h_c1(blocks["R"]["_reached"], blocks["N"]["_reached"], resamples=resamples),
        "controls_r_minus_n": {
            "point": analysis.difference("R", "N")(rates),
            "interval": analysis._round(analysis.interval(  # noqa: SLF001
                analysis.bootstrap(controls, analysis.difference("R", "N"), resamples=resamples)), 4)},
        "arms": {arm: {key: value for key, value in block.items() if not key.startswith("_")}
                 for arm, block in blocks.items()},
    }
    out["decision"] = ("H-C1 holds: reconciliation cuts the main model's stale execution by at least half on fresh "
                       "worlds." if out["h_c1"]["holds"] else "H-C1 fails: the Design A contrast did not replicate.")
    return out


def markdown(found: Mapping[str, Any]) -> str:
    test = found["h_c1"]
    arms = found["arms"]
    lines = ["# Stale skills: the confirmation's report", "", f"World seed {found['seed']}. {found['decision']}", "",
             "| | Point | 95% interval | p | Resamples (infinite) |", "|---|---|---|---|---|"]
    for label, key in (("R/N, end to end (H-C1)", "h_c1"), ("R/N, reached memory", "h_c1_reached_memory")):
        item = found[key]
        lines.append(f"| {label} | {item['point']} | {item['interval']} | {item['p']} | "
                     f"{item['resamples']} ({item['infinite']}) |")
    lines += ["", f"H-C1 holds: {test['holds']}.", ""]
    for arm in ("N", "R"):
        rate = arms[arm]["stale_execution"]
        acted = arms[arm]["stale_among_acted"]
        lines.append(f"- {arm}: stale {rate['k']}/{rate['n']} = {rate['rate']} (cluster {rate['cluster_ci']}); among "
                     f"runs that acted {acted['k']}/{acted['n']}; patches {arms[arm]['patches']}; tokens "
                     f"{arms[arm]['tokens']:,}")
    controls = found["controls_r_minus_n"]
    lines.append(f"- Unchanged controls, R - N: {controls['point']} {controls['interval']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The stale-skills confirmation: plan or report")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--root", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    if args.plan:
        print(plan())
        return 0
    if not (args.report and args.root and args.out):
        parser.error("--plan, or --report with --root and --out")
    found = report(Path(args.root))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "confirm-report.json").write_text(json.dumps(found, indent=2, default=str) + "\n", encoding="utf-8")
    (out / "confirm-report.md").write_text(markdown(found), encoding="utf-8")
    print(found["decision"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
