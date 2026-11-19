"""PawBench scores from a run's ``result/<run>/<model>/<harness>/T*/output/metrics.json``, the way PawBench's
own aggregator (``site/scripts/aggregate_results.py``) computes them, on any task subset.

- ``overall``: the mean ``task_score`` over the chosen tasks; an errored or missing task counts 0.
- ``automated`` / ``judge``: a hybrid task contributes the mean of its ``automated.*`` and ``llm_judge.*``
  breakdown entries; an automated-only task contributes its score to ``automated`` only, a judge-only
  task to ``judge`` only.

Standard library only.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any, Iterable


def task_id_of(folder: Path) -> str:
    return folder.name.split("_", 1)[0]


def read_metrics(harness_dir: Path) -> dict[str, dict[str, Any]]:
    found = {}
    for path in sorted(Path(harness_dir).glob("T*/output/metrics.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if "task_score" in doc:
            found[task_id_of(path.parent.parent)] = doc
    return found


def _partition(doc: dict[str, Any]) -> tuple[float | None, float | None]:
    kind, score, breakdown = doc.get("grading_type"), float(doc.get("task_score") or 0.0), doc.get("breakdown") or {}
    if kind == "hybrid":
        auto = [value for key, value in breakdown.items() if key.startswith("automated.")]
        judge = [value for key, value in breakdown.items() if key.startswith("llm_judge.")]
        return (mean(auto) if auto else None), (mean(judge) if judge else None)
    if kind == "automated":
        return score, None
    if kind == "llm_judge":
        return None, score
    return None, None


def summarize(metrics: dict[str, dict[str, Any]], tasks: Iterable[str]) -> dict[str, Any]:
    chosen = list(tasks)
    scores, auto, judge, errored, missing = [], [], [], [], []
    for task in chosen:
        doc = metrics.get(task)
        if doc is None:
            missing.append(task)
            scores.append(0.0)
            continue
        if doc.get("grading_type") in ("error", "missing") or doc.get("status") in ("error", "missing"):
            errored.append(task)
            scores.append(0.0)
            continue
        scores.append(float(doc.get("task_score") or 0.0))
        a, j = _partition(doc)
        if a is not None:
            auto.append(a)
        if j is not None:
            judge.append(j)
    rounded = lambda values: round(mean(values), 4) if values else None  # noqa: E731
    return {"tasks": len(chosen), "overall": rounded(scores), "automated": rounded(auto), "judge": rounded(judge),
            "tasks_errored": errored, "tasks_missing": missing}
