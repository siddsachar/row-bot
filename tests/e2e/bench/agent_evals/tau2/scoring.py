"""tau2-bench scores: pass^k from per-task trial rewards, ours beside a published run's on the same tasks.

Imports nothing from Row-Bot or tau2. Pass^k is tau2's own estimator (``tau2.metrics.agent_metrics``):
for a task with *n* trials and *c* successes, ``C(c, k) / C(n, k)``, averaged over the tasks with at
least *k* trials. A trial succeeds when its reward is 1 (to within 1e-6), as tau2 counts it.

Both kinds of file are read: a run's ``results.json`` (``simulations``, each with ``reward_info``) and
a published run's index (``simulation_index``, gzip or plain, as Sierra publishes them).
"""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from math import comb
from pathlib import Path
from typing import Any, Iterable, Mapping

SUCCESS = 1.0 - 1e-6
#: Termination reasons tau2 leaves out of its metrics (``metrics.agent_metrics``): the run, not the agent, failed.
NOT_SCORED = ("infrastructure_error",)


def load_results(path: Path) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def trial_rewards(results: Mapping[str, Any], *, exclude: Iterable[str] = ()) -> dict[str, list[float]]:
    """Task id -> each trial's reward, from either file kind. Simulations whose termination reason is in
    *exclude* (a run stopped by a usage limit, say) are left out rather than counted as failures."""
    skipped = {str(item) for item in exclude}
    rows: list[tuple[str, float | None, str]] = []
    for item in results.get("simulation_index") or []:
        rows.append((str(item["task_id"]), item.get("reward"), str(item.get("termination_reason") or "")))
    for item in results.get("simulations") or []:
        info = item.get("reward_info") or {}
        rows.append((str(item["task_id"]), info.get("reward"), str(item.get("termination_reason") or "")))
    found: dict[str, list[float]] = {}
    for task_id, reward, reason in rows:
        if reason in skipped:
            continue
        found.setdefault(task_id, []).append(float(reward or 0.0))
    return found


def successes(rewards: list[float]) -> int:
    return sum(1 for value in rewards if value >= SUCCESS)


def pass_hat_k(by_task: Mapping[str, list[float]], k: int, tasks: Iterable[str] | None = None) -> float | None:
    """tau2's pass^k over *tasks* (default: every task), or None when no task has *k* trials."""
    chosen = list(by_task) if tasks is None else [task for task in tasks if task in by_task]
    values = [comb(successes(by_task[task]), k) / comb(len(by_task[task]), k)
              for task in chosen if len(by_task[task]) >= k]
    return sum(values) / len(values) if values else None


@dataclass
class Comparison:
    tasks: list[str]
    ours: dict[str, list[float]]
    theirs: dict[str, list[float]]
    k_max: int = 4
    rows: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        for task in self.tasks:
            mine, other = self.ours.get(task, []), self.theirs.get(task, [])
            rate = lambda values: successes(values) / len(values) if values else None  # noqa: E731
            self.rows.append({"task_id": task, "ours": f"{successes(mine)}/{len(mine)}",
                              "theirs": f"{successes(other)}/{len(other)}", "ours_rate": rate(mine),
                              "theirs_rate": rate(other)})

    def summary(self) -> dict[str, Any]:
        both = [row for row in self.rows if row["ours_rate"] is not None and row["theirs_rate"] is not None]
        return {
            "tasks": len(self.tasks), "tasks_run": sum(row["ours_rate"] is not None for row in self.rows),
            "ours": {f"pass^{k}": pass_hat_k(self.ours, k, self.tasks) for k in range(1, self.k_max + 1)},
            "theirs": {f"pass^{k}": pass_hat_k(self.theirs, k, self.tasks) for k in range(1, self.k_max + 1)},
            # On the tasks both ran: their pass^1 there, so a subset run is compared on its own subset.
            "theirs_on_ours": {f"pass^{k}": pass_hat_k(self.theirs, k, [row["task_id"] for row in both])
                               for k in range(1, self.k_max + 1)},
            "worse": sorted((row["task_id"] for row in both if row["ours_rate"] < row["theirs_rate"]), key=_key),
            "better": sorted((row["task_id"] for row in both if row["ours_rate"] > row["theirs_rate"]), key=_key),
        }


def _key(task: str) -> tuple[int, str]:
    return (int(task), "") if task.isdigit() else (1 << 30, task)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}"


def render(comparison: Comparison, *, title: str, ours_label: str, theirs_label: str) -> str:
    data = comparison.summary()
    lines = [f"# {title}", "", f"{data['tasks_run']} of {data['tasks']} tasks run.", "",
             "| | " + " | ".join(f"pass^{k}" for k in range(1, comparison.k_max + 1)) + " |",
             "|---|" + "---|" * comparison.k_max]
    for label, key in ((ours_label, "ours"), (f"{theirs_label}, same tasks as ours", "theirs_on_ours"),
                       (f"{theirs_label}, whole split", "theirs")):
        lines.append(f"| {label} | " + " | ".join(_pct(data[key][f'pass^{k}'])
                                                for k in range(1, comparison.k_max + 1)) + " |")
    lines += ["", f"Worse than theirs on {len(data['worse'])} task(s): {', '.join(data['worse']) or '-'}",
              f"Better on {len(data['better'])}: {', '.join(data['better']) or '-'}", "",
              "| task | ours | theirs |", "|---|---|---|"]
    lines += [f"| {row['task_id']} | {row['ours']} | {row['theirs']} |" for row in comparison.rows
              if row["ours_rate"] is not None]
    return "\n".join(lines) + "\n"
