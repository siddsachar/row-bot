"""The stale-skills ground-truth scorer: runs and probes, read only from the timeline (Design A phase 1, Lane B).

**A run** (one recurring request) is scored from its **first attempt's first acting step**: the first email it sent
(for a mail procedure) or the first event it made (for an hours procedure), read from the fake apps' state in the
order the actions happened. Reads before it (skills, memory, the inbox, the runbook) are allowed and logged by the
runner, never scored. The acting step's value is compared with :meth:`world.Timeline.value` for the run's day:

- ``current``: the value in effect that day (after a change, the new one);
- ``old``: the procedure's old value on or after its change day (**stale**);
- ``both``: the old and the new value in the same step (an email to both addresses, say);
- ``neither``: an acting step with neither value; ``no_action``: no acting step in the first attempt.

The verifier and the simulated person are never read. Before a change (and for a procedure whose fact never
changes) a run is ``current`` or ``neither``, never ``old``.

**The stale-execution rate** (the pre-registration's §6): of every run of a changed procedure on its
change day and the 13 days after (:data:`world.WINDOW_DAYS`), the share whose first acting step is
``old``; every run counts, whether or not the skill was loaded and whether or not it acted.

**Changes that reached memory** (the second result, decided 26 Oct): a change reached memory when the product's
operation log has a memory written or updated on or after the change's day, not by Dream's own tidying, whose text
states the new value (:func:`stored_changes`); every rate is reported again over the runs of those changes from that
moment on.

**The wrong-switch rate** (H-A3): of every run of a newer-skill event's procedure (the person edited its skill;
memory still holds the older value) on the edit's day and the 13 days after, the share whose first acting step is
``old``: the older memory's value instead of the newer skill's.

**A probe** is scored from Row-Bot's final reply against its expected values (``right``, ``old``, ``both``,
``partial``, ``abstained``, ``wrong``; see :func:`score_probe`).
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import sqrt
from typing import Any

from tests.e2e.learning.stale_skills import world

CURRENT, OLD, BOTH, NEITHER, NO_ACTION = "current", "old", "both", "neither", "no_action"
VERDICTS = (CURRENT, OLD, BOTH, NEITHER, NO_ACTION)
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20261026
#: Words a reply uses when it doesn't know (abstention).
ABSTAIN = re.compile(
    r"\b(?:don't|do not|doesn't|didn't|can't|cannot|couldn't|haven't|hasn't|isn't|aren't|wasn't)\b[^.?!\n]{0,60}"
    r"\b(?:know|have|find|see|recorded|saved|stored|record|mention|on file|anywhere|any)\b"
    r"|\bno (?:record|note|mention|information|details?|number|phone|reference|saved)\b"
    r"|\bnot (?:sure|recorded|saved|stored|on file|in (?:my|your|the) (?:memory|notes|records|files))\b"
    r"|\bunknown\b|\bnowhere\b",
    re.IGNORECASE)


@dataclass(frozen=True)
class RunScore:
    """One run's score."""

    run: str
    procedure: str
    kind: str
    day: int
    changed: bool  # the procedure's fact changes at some point
    after_change: bool
    in_window: bool
    days_since_change: int | None
    verdict: str
    acting: Mapping[str, Any] | None  # the first acting step (what it did), or None
    edited: bool = False  # a newer-skill event's procedure (the person edits its skill)
    after_edit: bool = False
    edit_window: bool = False  # on the edit's day or the 13 days after

    def to_dict(self) -> dict[str, Any]:
        return {"run": self.run, "procedure": self.procedure, "kind": self.kind, "day": self.day,
                "changed": self.changed, "after_change": self.after_change, "in_window": self.in_window,
                "days_since_change": self.days_since_change, "verdict": self.verdict,
                "acting": dict(self.acting) if self.acting else None, "edited": self.edited,
                "after_edit": self.after_edit, "edit_window": self.edit_window}


def acting_step(procedure: world.Procedure, actions: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The first action of the procedure's own kind (an email, or a calendar event)."""

    wanted = "event" if procedure.action == "event" else "mail"
    return next((action for action in actions if action.get("kind") == wanted), None)


def _text_of(procedure: world.Procedure, action: Mapping[str, Any]) -> str:
    if procedure.kind in ("address", "supplier", "role"):
        return str(action.get("to") or "")
    if procedure.kind == "hours":
        return str(action.get("start") or "")
    return f"{action.get('subject') or ''}\n{action.get('body') or ''}"


def classify(timeline: world.Timeline, procedure_id: str, day: int, action: Mapping[str, Any]) -> str:
    """One acting step's class against the timeline (see the module's docstring)."""

    procedure = timeline.procedure(procedure_id)
    now = timeline.value(procedure_id, day)
    change = timeline.shift_of(procedure_id)
    old = change.old if change is not None and day >= change.day else None
    text = _text_of(procedure, action)
    if procedure.kind == "hours":
        start = str(action.get("start") or "").strip()
        has_now, has_old = start == now["start"], old is not None and start == old["start"]
    else:
        has_now = world.states(procedure.kind, now, text)
        has_old = old is not None and world.states(procedure.kind, old, text)
    if has_now and has_old:
        return BOTH
    if has_old:
        return OLD
    return CURRENT if has_now else NEITHER


def score_run(timeline: world.Timeline, run_id: str, day: int, actions: Iterable[Mapping[str, Any]]) -> RunScore:
    """A run's verdict from its first attempt's actions (in order)."""

    procedure_id = timeline.procedure_of(run_id)
    procedure = timeline.procedure(procedure_id)
    change = timeline.change_of(procedure_id)
    acting = acting_step(procedure, list(actions))
    verdict = NO_ACTION if acting is None else classify(timeline, procedure_id, day, acting)
    after = change is not None and day >= change.day
    since = day - change.day if after else None
    edit = timeline.edit_of(procedure_id)
    after_edit = edit is not None and day >= edit.day
    return RunScore(run_id, procedure_id, procedure.kind, day, change is not None, after,
                    after and since is not None and since < world.WINDOW_DAYS, since, verdict, acting,
                    edit is not None, after_edit, after_edit and day - edit.day < world.WINDOW_DAYS)


def actions_between(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The emails sent and events made between two fake-app states (by id): the emails in the order they were sent,
    then the events in the order they were made (the apps number each kind in order; the scorer only compares
    actions of one kind)."""

    def ids(state: Mapping[str, Any], app: str, name: str) -> set[str]:
        return {str(item.get("id")) for item in ((state.get(app) or {}).get(name) or [])}

    seen = ids(before, "mail", "messages")
    mails = [{"kind": "mail", "id": item.get("id"), "to": item.get("to"), "subject": item.get("subject"),
              "body": item.get("body")}
             for item in (after.get("mail") or {}).get("messages") or []
             if str(item.get("id")) not in seen and item.get("folder") == "sent"]
    seen = ids(before, "calendar", "events")
    events = [{"kind": "event", "id": item.get("id"), "title": item.get("title"), "date": item.get("date"),
               "start": item.get("start"), "end": item.get("end")}
              for item in (after.get("calendar") or {}).get("events") or [] if str(item.get("id")) not in seen]
    return [*sorted(mails, key=lambda item: _number(item["id"])), *sorted(events, key=lambda item: _number(item["id"]))]


def _number(item_id: Any) -> int:
    tail = str(item_id or "").rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def surfaced(timeline: world.Timeline, procedure_id: str, day: int, texts: Iterable[str], *, which: str = "new") -> bool:
    """Whether any of ``texts`` (recalled memories, memory-tool results) states the procedure's new (or old) value;
    only on or after its change (before it there is no new value to surface)."""

    change = timeline.change_of(procedure_id)
    if change is None or day < change.day:
        return False
    value = change.new if which == "new" else change.old
    return any(world.states(change.kind, value, text) for text in texts)


# ── Probes ───────────────────────────────────────────────────────────────────

_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")


def _states_date(value: str, text: str) -> bool:
    day, month = value.split()
    number = _MONTHS.index(month.lower()) + 1
    short = month[:3]
    patterns = (rf"(?<!\d){int(day)}(?:st|nd|rd|th)?\s+(?:of\s+)?{short}\w*", rf"\b{short}\w*\s+{int(day)}(?:st|nd|rd|th)?(?!\d)",
                rf"\b\d{{4}}-{number:02d}-{int(day):02d}\b", rf"(?<!\d){int(day)}/0?{number}(?!\d)")
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def probe_states(kind: str, expected: str, text: str) -> bool:
    """Whether a reply states one of a probe's values (the probe's own kind of value)."""

    if kind in world.KINDS:
        return world.states(kind, json.loads(expected), text)
    if kind == "date":
        return _states_date(expected, text)
    return expected.lower() in str(text or "").lower()


def score_probe(probe: world.Probe | Mapping[str, Any], answer: str) -> str:
    """A probe's verdict from the reply (revised 26 Oct after the first probe day's hand check, before any report):

    - abstention: ``right`` if the reply says it doesn't know, else ``wrong`` (a guess);
    - update detection: ``right`` if it states both the old and the new value, ``partial`` if only the new;
    - as-of: ``right`` if it states the value then (the old one) and not the later one; ``both`` if it states both
      (it can't be told which it gives as the answer); ``current_instead`` if only the later one;
    - current, alias and homonym: ``right`` if it states the expected value (saying what it replaced is fine);
      ``old`` if it states only a value it must not (an older, misstated or the other Sam's);
      ``abstained`` if it says it doesn't know; else ``wrong``."""

    item = probe if isinstance(probe, Mapping) else probe.to_dict()
    text = str(answer or "").replace("\u2019", "'")
    category, kind = item["category"], item["kind"]
    if category == "abstention":
        return "right" if ABSTAIN.search(text) else "wrong"
    expected = [probe_states(kind, value, text) for value in item["expected"]]
    wrong = any(probe_states(kind, value, text) for value in item["wrong"])
    if category == "update":
        return "right" if all(expected) else "partial" if expected and expected[0] else "wrong"
    if category == "as_of":
        if all(expected):
            return "both" if wrong else "right"
        return "current_instead" if wrong else "abstained" if ABSTAIN.search(text) else "wrong"
    if all(expected):
        return "right"
    if wrong:
        return "old"
    return "abstained" if ABSTAIN.search(text) else "wrong"


# ── Statistics ───────────────────────────────────────────────────────────────


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if not n:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(100 * max(0.0, centre - half), 1), round(100 * min(1.0, centre + half), 1)]


def cluster_bootstrap(clusters: Mapping[str, tuple[int, int]], *, resamples: int = BOOTSTRAP_RESAMPLES,
                      seed: int = BOOTSTRAP_SEED) -> list[float] | None:
    """A 95% percentile interval of the pooled rate, resampling clusters (k stale, n runs each) with replacement."""

    items = [value for value in clusters.values() if value[1]]
    if not items:
        return None
    rng = random.Random(seed)
    rates = []
    for _ in range(resamples):
        drawn = [items[rng.randrange(len(items))] for _ in items]
        total = sum(n for _, n in drawn)
        rates.append(sum(k for k, _ in drawn) / total if total else 0.0)
    rates.sort()
    return [round(100 * rates[int(0.025 * resamples)], 1), round(100 * rates[int(0.975 * resamples) - 1], 1)]


def _rate(runs: list[Mapping[str, Any]], verdict: str = OLD) -> dict[str, Any]:
    hit = sum(1 for run in runs if run["verdict"] == verdict)
    return {"k": hit, "n": len(runs), "rate": round(hit / len(runs), 4) if runs else None,
            "wilson": wilson(hit, len(runs))}


def metrics(timeline: world.Timeline, runs: Iterable[Mapping[str, Any]],
            stored: Mapping[str, Mapping[str, Any] | None] | None = None) -> dict[str, Any]:
    """The pre-registration's numbers for one arm and person from scored runs (each a :meth:`RunScore.to_dict`
    plus ``skill_loaded``, ``recalled_new``, ``passed_first`` and the run's simulated time ``at``). With ``stored``
    (:func:`stored_changes`), ``reached_memory`` holds every number again over the runs of the changes that reached
    memory, from the moment each did."""

    runs = [dict(run) for run in runs]
    window = [run for run in runs if run["in_window"]]
    after = [run for run in runs if run["after_change"]]
    by_change: dict[str, tuple[int, int]] = {}
    for change in timeline.changes:
        mine = [run for run in window if run["procedure"] == change.procedure]
        by_change[change.procedure] = (sum(1 for run in mine if run["verdict"] == OLD), len(mine))
    primary = _rate(window)
    primary["cluster_bootstrap"] = cluster_bootstrap(by_change)
    primary["bootstrap"] = {"resamples": BOOTSTRAP_RESAMPLES, "seed": BOOTSTRAP_SEED, "clusters": "change event"}
    fixes = {}
    for change in timeline.changes:
        mine = sorted((run for run in after if run["procedure"] == change.procedure), key=lambda run: run["day"])
        first_right = next((run["day"] for run in mine if run["verdict"] == CURRENT), None)
        stale_days = [run["day"] for run in mine if run["verdict"] == OLD]
        fixes[change.procedure] = {
            "change_day": change.day, "kind": change.kind, "channel": change.channel, "runs_after": len(mine),
            "days_to_first_right": first_right - change.day if first_right is not None else None,
            "stale_runs": len(stale_days), "last_stale_day": max(stale_days) if stale_days else None,
            "window": {"stale": by_change[change.procedure][0], "runs": by_change[change.procedure][1]}}
    unchanged = [run for run in runs if not run["changed"] and not run.get("edited")]
    before = [run for run in runs if run["changed"] and not run["after_change"]]
    newer_skill = [run for run in runs if run.get("edit_window")]
    found = {
        "stale_execution": primary,
        "stale_execution_by_kind": {kind: _rate([run for run in window if run["kind"] == kind]) for kind in world.KINDS},
        "stale_skill_loaded": _rate([run for run in window if run.get("skill_loaded")]),
        "stale_skill_not_loaded": _rate([run for run in window if not run.get("skill_loaded")]),
        "stale_recall_surfaced_new": _rate([run for run in window if run.get("recalled_new")]),
        "stale_recall_did_not_surface_new": _rate([run for run in window if not run.get("recalled_new")]),
        "stale_all_after_change": _rate(after),
        # Not the pre-registered rate: the same window, only the runs whose first attempt acted at all.
        "stale_among_acted": _rate([run for run in window if run["verdict"] != NO_ACTION]),
        "window_verdicts": {verdict: sum(1 for run in window if run["verdict"] == verdict) for verdict in VERDICTS},
        "time_to_fix": fixes,
        "unchanged_success": _rate(unchanged, CURRENT),
        "unchanged_verifier_first": {"k": sum(1 for run in unchanged if run.get("passed_first")), "n": len(unchanged)},
        "before_change_success": _rate(before, CURRENT),
        # H-A3: the older memory's value instead of the newer skill's (the person edited the skill).
        "wrong_switch": _rate(newer_skill),
        "wrong_switch_all_after_edit": _rate([run for run in runs if run.get("after_edit")]),
        "newer_skill_verdicts": {verdict: sum(1 for run in newer_skill if run["verdict"] == verdict)
                                 for verdict in VERDICTS},
        "all_runs": len(runs),
    }
    if stored is not None:
        kept = [run for run in runs if not run["after_change"] or _after_store(run, stored.get(run["procedure"]))]
        found["reached_memory"] = {
            "changes": {change.procedure: stored.get(change.procedure) for change in timeline.changes},
            "stored": sum(1 for change in timeline.changes if stored.get(change.procedure)),
            **{key: value for key, value in metrics(timeline, kept).items() if key not in (
                "unchanged_success", "unchanged_verifier_first", "before_change_success", "wrong_switch",
                "wrong_switch_all_after_edit", "newer_skill_verdicts")}}
    return found


def _after_store(run: Mapping[str, Any], hit: Mapping[str, Any] | None) -> bool:
    """Whether a run came after its change reached memory (by simulated time; by day when the run has no time)."""

    if not hit:
        return False
    if run.get("at"):
        return str(run["at"]) >= str(hit["at"])
    return int(run["day"]) > int(hit["day"])


def sim_day(moment: str) -> int:
    """The simulated day of a timestamp in the operation log (day 1 is the first live day)."""

    from datetime import datetime

    from tests.e2e.learning.seeded import generator

    return (datetime.fromisoformat(str(moment)).date() - generator.day_date(1)).days + 1


#: Dream's own tidying in the operation log: not a change reaching memory.
HOUSEKEEPING = frozenset({"merge", "enrich", "infer", "decay", "migrate"})


def stored_changes(timeline: world.Timeline, operations: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any] | None]:
    """When each change's new value first reached memory, from the product's operation log: the first memory written
    (``write``) or updated (``supersede``) on or after the change's day, not by Dream's own tidying, whose text states
    the new value; None when it never did (a contradiction only sent to review doesn't count)."""

    operations = [item for item in operations if item.get("event") in ("write", "supersede")
                  and str(item.get("cause") or "") not in HOUSEKEEPING]
    found: dict[str, dict[str, Any] | None] = {}
    for change in timeline.changes:
        found[change.procedure] = next((
            {"at": item["at"], "day": sim_day(item["at"]), "event": item["event"], "subject": item.get("subject"),
             "entity_id": item.get("new_entity_id") or item.get("entity_id"), "origin": item.get("origin")}
            for item in operations if sim_day(item["at"]) >= change.day and world.states(
                change.kind, change.new, str(item.get("text") if item["event"] == "write" else item.get("new") or ""))),
            None)
    return found


def patch_metrics(timeline: world.Timeline, skills: Mapping[str, Mapping[str, Any]],
                  operations: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The arms' skill patches from the product's operation log, scored against the timeline: a patch is **true**
    when the skill's procedure changed on or before the day it was patched, else **false** (a patch to a skill whose
    fact hadn't changed: the pre-registration's false patch). Candidates the product considered are counted by
    outcome."""

    by_skill = {str(value.get("name")): procedure for procedure, value in skills.items() if value.get("name")}
    operations = list(operations)
    proposals: dict[str, str] = {}
    candidates: dict[str, dict[str, int]] = {}
    for item in operations:
        if item.get("event") != "patch_candidate":
            continue
        arm = str(item.get("arm") or "")
        outcome = str(item.get("outcome") or "")
        candidates.setdefault(arm, {}).setdefault(outcome, 0)
        candidates[arm][outcome] += 1
        if item.get("proposal_id"):
            proposals[str(item["proposal_id"])] = arm
    patches = []
    for item in operations:
        cause = str(item.get("cause") or "")
        if item.get("event") != "patch" or not cause.startswith("proposal:"):
            continue
        procedure = by_skill.get(str(item.get("skill") or ""))
        day = sim_day(item["at"])
        change = timeline.change_of(procedure) if procedure else None
        patches.append({"skill": item.get("skill"), "procedure": procedure, "day": day,
                        "arm": proposals.get(cause.removeprefix("proposal:"), "other"),
                        "true": bool(change is not None and change.day <= day),
                        "days_after_change": (day - change.day) if change is not None and change.day <= day else None})
    false = [item for item in patches if not item["true"]]
    return {"applied": len(patches), "false": len(false), "false_rate": round(len(false) / len(patches), 4)
            if patches else None, "wilson_false": wilson(len(false), len(patches)), "patches": patches,
            "candidates": candidates}


def probe_metrics(probes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Probe results by category and by probe day (right / asked)."""

    probes = list(probes)
    out: dict[str, Any] = {}
    for category in world.PROBE_CATEGORIES:
        mine = [item for item in probes if item["category"] == category]
        right = sum(1 for item in mine if item["verdict"] == "right")
        out[category] = {"right": right, "n": len(mine), "wilson": wilson(right, len(mine)),
                         "verdicts": {verdict: sum(1 for item in mine if item["verdict"] == verdict)
                                      for verdict in sorted({item["verdict"] for item in mine})}}
    out["by_day"] = {str(day): {"right": sum(1 for item in probes if item["day"] == day and item["verdict"] == "right"),
                                "n": sum(1 for item in probes if item["day"] == day)}
                     for day in sorted({item["day"] for item in probes})}
    return out


__all__ = ["BOTH", "CURRENT", "HOUSEKEEPING", "NEITHER", "NO_ACTION", "OLD", "RunScore", "VERDICTS", "acting_step",
           "actions_between", "patch_metrics", "sim_day", "stored_changes",
           "classify", "cluster_bootstrap", "metrics", "probe_metrics", "probe_states", "score_probe", "score_run",
           "surfaced", "wilson"]
