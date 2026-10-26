"""The stale-skills pilot's scorer: a run is stale only against the timeline (Lane B, MEMORY-SKILLS §4).

A **run** is one recurring request. What it *did* is read from its **first attempt** (before any word from
the person): the emails it sent and the calendar events it made in that attempt. Each action that carries the
task's fact is classed by the value it used, compared with :meth:`Timeline.value` for the run's day:

- ``stale``: the fact's **old** value on a day after the change (the old address; the old start time);
- ``current``: the value in effect that day;
- ``other``: an action of the task with neither value; ``no_action``: nothing done in the first attempt.

A run is stale when any of its actions is. Before a change the old value *is* the current one, so a run there
is never stale. The verifier and the simulated person are never read: the scorer sees only the actions and
the timeline.

**The rate** (§4): stale runs / runs after the change, per model, split by whether recall surfaced the new
fact in that run (auto-recall's memories, or a memory tool's result, stating the new value).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from tests.e2e.learning.seeded.pilot_timeline import TASK_FACT, Timeline, mentions

STALE, CURRENT, OTHER, NO_ACTION = "stale", "current", "other", "no_action"


@dataclass(frozen=True)
class Scored:
    """One run's score."""

    run: str
    task: str
    day: int
    after_change: bool
    verdict: str
    actions: tuple[str, ...]  # each action's class, in order

    def to_dict(self) -> dict[str, Any]:
        return {"run": self.run, "task": self.task, "day": self.day, "after_change": self.after_change,
                "verdict": self.verdict, "actions": list(self.actions)}


def task_of(request_id: str) -> str:
    return "bread_order" if request_id.endswith("-bread") else "weekend_shift"


def _relevant(task: str, action: Mapping[str, Any]) -> bool:
    """Whether an action belongs to the task (an email for the bread order, a weekend-shift event)."""

    if task == "bread_order":
        return action.get("kind") == "mail"
    return action.get("kind") == "event"


def _value_used(task: str, action: Mapping[str, Any]) -> Any:
    if task == "bread_order":
        return str(action.get("to") or "").strip().lower()
    return {"start": str(action.get("start") or ""), "end": str(action.get("end") or "")}


def classify(timeline: Timeline, task: str, day: int, action: Mapping[str, Any]) -> str:
    """One action's class against the timeline (see the module's docstring)."""

    fact = TASK_FACT[task]
    change = timeline.change(fact)
    used = _value_used(task, action)
    now = timeline.value(fact, day)
    if task == "bread_order":
        if used == str(now).lower():
            return CURRENT
        if timeline.after_change(fact, day) and used == str(change.old).lower():
            return STALE
        return OTHER
    if used["start"] == now["start"]:
        return CURRENT
    if timeline.after_change(fact, day) and used["start"] == change.old["start"]:
        return STALE
    return OTHER


def score(timeline: Timeline, request_id: str, day: int, actions: Iterable[Mapping[str, Any]]) -> Scored:
    """The run's verdict from its first attempt's actions."""

    task = task_of(request_id)
    classes = tuple(classify(timeline, task, day, action) for action in actions if _relevant(task, action))
    if not classes:
        verdict = NO_ACTION
    elif STALE in classes:
        verdict = STALE
    elif CURRENT in classes:
        verdict = CURRENT
    else:
        verdict = OTHER
    return Scored(request_id, task, day, timeline.after_change(TASK_FACT[task], day), verdict, classes)


def actions_between(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The emails sent and events made between two fake-app states (by id), as scorer actions."""

    def ids(state: Mapping[str, Any], app: str, name: str) -> set[str]:
        return {str(item.get("id")) for item in ((state.get(app) or {}).get(name) or [])}

    found: list[dict[str, Any]] = []
    seen = ids(before, "mail", "messages")
    for item in (after.get("mail") or {}).get("messages") or []:
        if str(item.get("id")) not in seen and item.get("folder") == "sent":
            found.append({"kind": "mail", "to": item.get("to"), "subject": item.get("subject"),
                          "body": item.get("body")})
    seen = ids(before, "calendar", "events")
    for item in (after.get("calendar") or {}).get("events") or []:
        if str(item.get("id")) not in seen:
            found.append({"kind": "event", "title": item.get("title"), "date": item.get("date"),
                          "start": item.get("start"), "end": item.get("end")})
    return found


def surfaced(timeline: Timeline, task: str, day: int, texts: Iterable[str], *, which: str = "new") -> bool:
    """Whether any of ``texts`` (recalled memories, memory-tool results) states the fact's ``new`` (or ``old``)
    value; only after the change (before it there is no new value to surface)."""

    change = timeline.change(TASK_FACT[task])
    if not timeline.after_change(change.fact, day):
        return False
    value = change.new if which == "new" else change.old
    return any(mentions(change.fact, value, text) for text in texts)


def _rate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    stale = sum(1 for run in runs if run["verdict"] == STALE)
    return {"stale": stale, "runs": len(runs), "rate": round(stale / len(runs), 4) if runs else None}


def rates(runs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """§4's numbers from scored runs (each a :meth:`Scored.to_dict` plus ``recalled_new``, ``skill_loaded``):
    the stale-execution rate after the changes, overall and per task, split by whether recall surfaced the
    new fact, and every verdict's count."""

    after = [dict(run) for run in runs if run.get("after_change")]
    out: dict[str, Any] = {"overall": _rate(after)}
    for task in sorted(TASK_FACT):
        out[task] = _rate([run for run in after if run["task"] == task])
    out["recall_surfaced_new"] = _rate([run for run in after if run.get("recalled_new")])
    out["recall_did_not_surface_new"] = _rate([run for run in after if not run.get("recalled_new")])
    out["skill_loaded"] = _rate([run for run in after if run.get("skill_loaded")])
    out["skill_not_loaded"] = _rate([run for run in after if not run.get("skill_loaded")])
    out["verdicts"] = {verdict: sum(1 for run in after if run["verdict"] == verdict)
                       for verdict in (STALE, CURRENT, OTHER, NO_ACTION)}
    return out


def decision(rate_by_model: Mapping[str, float | None]) -> str:
    """§4's fixed rule: ``go`` if the rate is >= 20% on either model, ``no-go`` if < 5% on both, otherwise
    ``in between`` (report with the precedence-study option). A model with no runs after a change can't
    decide: ``undecided``."""

    values = list(rate_by_model.values())
    if any(value is not None and value >= 0.20 for value in values):
        return "go"
    if values and all(value is not None and value < 0.05 for value in values):
        return "no-go"
    if any(value is None for value in values):
        return "undecided"
    return "in between"


__all__ = ["CURRENT", "NO_ACTION", "OTHER", "STALE", "Scored", "actions_between", "classify", "decision", "rates",
           "score", "surfaced", "task_of"]
