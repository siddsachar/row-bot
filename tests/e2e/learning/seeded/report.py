"""The release check's report: per person and overall, with denominators and intervals (S1).

Built from each person's ``result.json`` (counts, rates, times and tokens only). Every rate is shown
with its denominator and a 95% interval:

- **pass rates** (the seeded work as asked live, the visible packs, the hidden pack split and the
  held-out seeded work) carry a Wilson score interval on their counts. In a comparison a repeated
  problem counts as its pass fraction (rounded per kind of work, the product's own table), so the
  denominator is problems, not runs;
- **paired differences** (each model against the untouched starting model) are the product's own
  pre-registered analysis from ``evaluation`` (mean paired difference, 95% cluster-bootstrap interval,
  permutation p), per person: per-problem results never leave the vault, so nothing is re-derived here;
- **overall** rows pool counts over people (the packs' tasks recur once per person; the held-out
  seeded work is each person's own).

Stages that aren't built yet are listed plainly as such, never as zeros.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

FORMAT = "row_bot.learning.release_check_report"
VERSION = 1
Z95 = 1.959963984540054
FAMILIES = ("document_lookups", "structured_transforms", "small_coding", "tool_workflows")
LABELS = {"document_lookups": "Document lookups", "structured_transforms": "Structured transforms",
          "small_coding": "Small coding tasks", "tool_workflows": "Tool workflows", "other": "Other",
          "own_work": "Held-out seeded work"}
#: Stages of the Learning loop that later work packages build; the report names them, never zeros.
NOT_BUILT = (
    {"stage": "Issues, lessons, skills and ways of working (diagnosis, drafting, testing, use/undo)",
     "work_package": "L3a, L3b"},
    {"stage": "The harder pack split where the main model fails (O31)", "work_package": "L3b"},
    {"stage": "Held-out corrections stored and tested in the vault (O87)", "work_package": "L3a"},
    {"stage": "Improver strategies: three inherited generations at equal budget, frozen control (D15, H3)",
     "work_package": "L4a"},
    {"stage": "Practice tasks from failure patterns, the quick question (D17)", "work_package": "L4b"},
    {"stage": "The personal model taking over learning jobs (D21)", "work_package": "L4c"},
    {"stage": "Learn from corrections (self-distillation) against SFT", "work_package": "L2e"},
)
SURVIVAL = ("crash_mid_training", "pause_on_use", "negative_result", "source_deletion", "version_skew",
            "trial_use_undo")


def wilson(passed: float, total: float, z: float = Z95) -> tuple[float | None, float | None]:
    """The Wilson score interval of ``passed`` of ``total`` (None, None for no total)."""

    if total <= 0:
        return None, None
    rate = passed / total
    denominator = 1 + z * z / total
    centre = (rate + z * z / (2 * total)) / denominator
    half = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / denominator
    low = 0.0 if passed <= 0 else max(0.0, centre - half)
    high = 1.0 if passed >= total else min(1.0, centre + half)
    return low, high


def rate(passed: float, total: float) -> dict[str, Any]:
    """A pass rate with its denominator and 95% Wilson interval."""

    low, high = wilson(passed, total)
    return {"passed": round(passed, 3), "total": int(total), "rate": round(passed / total, 4) if total else None,
            "low": round(low, 4) if low is not None else None, "high": round(high, 4) if high is not None else None}


def _add(table: dict[str, list[float]], key: str, passed: float, total: float) -> None:
    row = table.setdefault(key, [0.0, 0.0])
    row[0] += float(passed)
    row[1] += float(total)


# ── One person ───────────────────────────────────────────────────────────────


def live_work(requests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The seeded stream as asked: first-try and final pass rates, overall and per kind (as classified),
    corrections, Teach, stops, approvals and routed turns."""

    asked = [item for item in requests if item.get("label", "stream") == "stream" and item.get("attempts")]
    first: dict[str, list[float]] = {}
    final: dict[str, list[float]] = {}
    for item in asked:
        for key in ("overall", str(item.get("intended") or "other")):
            _add(first, key, float(bool(item.get("passed_first"))), 1)
            _add(final, key, float(bool(item.get("passed_final"))), 1)
    reactions = [reaction for item in asked for reaction in item.get("reaction") or ()]
    attempts = [attempt for item in asked for attempt in item.get("attempts") or ()]
    approvals = [approval for attempt in attempts for approval in attempt.get("approvals") or ()]
    classified = [item for item in asked if item.get("kind") == item.get("intended")]
    by_intent: dict[str, list[float]] = {}
    for item in asked:
        _add(by_intent, str(item.get("intended") or "other"), float(item.get("kind") == item.get("intended")), 1)
    routed = [item for item in requests if (item.get("attempts") or [{}])[0].get("routed")]
    return {
        "requests": len(asked),
        "first_try": {key: rate(*value) for key, value in sorted(first.items())},
        "final": {key: rate(*value) for key, value in sorted(final.items())},
        "corrections": sum(1 for reaction in reactions if reaction.get("action") == "correct"),
        "teach": sum(1 for reaction in reactions if reaction.get("action") == "teach"),
        "teach_linked": sum(1 for reaction in reactions if reaction.get("action") == "teach" and reaction.get("linked")),
        "gave_up": sum(1 for reaction in reactions if reaction.get("action") == "give_up"),
        "stops": sum(1 for attempt in attempts if attempt.get("stopped")),
        "approvals": {"asked": len(approvals), "approved": sum(1 for item in approvals if item.get("approved"))},
        "words_from_template": sum(1 for reaction in reactions if reaction.get("source") == "template"),
        "kinds_agree": rate(len(classified), len(asked)),
        # Row-Bot's kind of work for each request against the kind it was meant as (routing goes by the former).
        "kinds_by_intent": {kind: rate(*value) for kind, value in sorted(by_intent.items())},
        "routed": {"turns": len(routed),
                   "answered_by_personal_model": sum(1 for item in routed
                                                     if not item["attempts"][0]["routed"].get("fallback")),
                   "passed": sum(1 for item in routed if item.get("passed_first"))},
        "seconds_per_request": round(sum(attempt.get("seconds") or 0 for attempt in attempts) / len(asked), 1)
        if asked else None,
    }


def builds_by_day(requests: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Which build answered which simulated days' requests, in order (a request asked before builds were
    recorded counts as ``""``)."""

    spans: list[dict[str, Any]] = []
    for item in sorted(requests, key=lambda value: (int(value.get("day") or 0), str(value.get("id") or ""))):
        build, day = str(item.get("build") or ""), int(item.get("day") or 0)
        if spans and spans[-1]["build"] == build:
            spans[-1]["last_day"] = max(spans[-1]["last_day"], day)
        else:
            spans.append({"build": build, "first_day": day, "last_day": day})
    return spans


def _arm_roles(comparison: Mapping[str, Any]) -> dict[str, str]:
    """role (baseline / new / main) -> the arm's key."""

    return {str(arm.get("role")): str(arm.get("key")) for arm in comparison.get("arms") or ()}


def model_rates(comparison: Mapping[str, Any]) -> dict[str, Any]:
    """Per arm (start / version / main): pass rates on the visible packs, the hidden pack split and the
    held-out seeded work, overall and per kind, from the comparison's own tables."""

    from row_bot.learning.kinds import LABELS as PRODUCT_LABELS

    # The comparison's tables are keyed by what people see ("Document lookups"); a held-out problem of the
    # person's own work is under its episode's kind of work, or "Your work" when it has none.
    packs = {label: kind for kind, label in PRODUCT_LABELS.items()}
    details = comparison.get("details") or {}
    roles = _arm_roles(comparison)
    found: dict[str, Any] = {}
    for role, key in roles.items():
        tables: dict[str, dict[str, list[float]]] = {"visible": {}, "hidden_packs": {}, "own_work": {}}
        for label, (passed, total) in ((details.get("visible") or {}).get(key) or {}).items():
            _add(tables["visible"], packs.get(label, label), passed, total)
            _add(tables["visible"], "overall", passed, total)
        for label, (passed, total) in ((details.get("hidden") or {}).get(key) or {}).items():
            part = "hidden_packs" if label in packs else "own_work"
            _add(tables[part], packs.get(label, "own_work"), passed, total)
            _add(tables[part], "overall", passed, total)
        found[role] = {part: {kind: rate(*value) for kind, value in sorted(table.items())}
                       for part, table in tables.items()}
        found[role]["key"] = key
    return found


def paired(comparison: Mapping[str, Any]) -> dict[str, Any]:
    """The product's paired analyses against the baseline arm (the starting model, or the version in use):
    per arm and part (visible, hidden, combined), overall and per kind."""

    roles = _arm_roles(comparison)
    analysis = comparison.get("analysis") or {}
    found: dict[str, Any] = {}
    for role in ("new", "main"):
        key = roles.get(role)
        if not key:
            continue
        found[role] = {}
        for part in ("visible", "hidden", "combined"):
            entry = (analysis.get(part) or {}).get(key)
            if not entry:
                continue
            keep = ("difference", "low", "high", "p_value", "problems", "clusters", "baseline", "arm", "enough",
                    "interval_verdict", "not_worse")
            found[role][part] = {"overall": {name: (entry.get("overall") or {}).get(name) for name in keep},
                                 "by_kind": {kind: {name: value.get(name) for name in keep}
                                             for kind, value in sorted((entry.get("by_kind") or {}).items())}}
    found["baseline"] = next((arm.get("label") for arm in comparison.get("arms") or () if arm.get("role") == "baseline"),
                             None)
    found["seed"] = analysis.get("seed")
    return found


def person_report(result: Mapping[str, Any]) -> dict[str, Any]:
    versions = sorted((value for value in (result.get("versions") or {}).values() if isinstance(value, dict)),
                      key=lambda item: int(item.get("version") or 0))
    compared = [item for item in versions if item.get("comparison")]
    newest = compared[-1] if compared else None
    # The run's own numbers: the newest version compared after the last day (Compare now), else its
    # last comparison by the product's runner.
    benchmark = result.get("benchmark") or {}
    comparison = benchmark.get("comparison") or (newest or {}).get("comparison") or {}
    arms = {str(arm.get("role")): arm for arm in comparison.get("arms") or ()}
    tokens = result.get("tokens") or {}
    ledger = tokens.get("ledger") or {}

    def ledger_sum(prefixes: Iterable[str]) -> int:
        return sum(int(item.get("input", 0)) + int(item.get("output", 0)) for key, item in ledger.items()
                   if key.split(":", 1)[0] in set(prefixes) and key.split(":", 1)[1] == "codex")

    gpu = result.get("gpu") or {}
    phases: dict[str, float] = {}
    for item in gpu.get("phases") or ():
        phases[item.get("phase", "?")] = phases.get(item.get("phase", "?"), 0.0) + float(item.get("seconds") or 0)
    return {
        "person": result.get("person"), "name": result.get("name"), "tier": result.get("tier"),
        "days": result.get("days"), "history": result.get("history"), "model": result.get("model"),
        "stages": result.get("stages"), "past": result.get("past"), "live": live_work(result.get("requests") or ()),
        "container": (result.get("profile") or {}).get("container"),
        "builds": list(result.get("builds") or []),
        "builds_by_day": builds_by_day(result.get("requests") or ()),
        "versions": [{key: item.get(key) for key in ("version", "trained_after_day", "state", "steps", "loss",
                                                     "training_seconds", "phase", "failure", "gguf_bytes",
                                                     "compared_on_day", "kept_pass")}
                     | {"verdict": ((item.get("comparison") or {}).get("overall") or {}).get("verdict"),
                        "line": ((item.get("comparison") or {}).get("overall") or {}).get("line")}
                     for item in versions],
        "newest_compared": newest.get("version") if newest else None,
        "benchmark": {key: benchmark.get(key) for key in ("state", "version", "day", "fresh", "why")} if benchmark
        else {"state": "not_run"},
        "models": model_rates(comparison) if comparison else {},
        "paired": paired(comparison) if comparison else {},
        "time_per_task": {role: arm.get("seconds_per_task") for role, arm in arms.items()},
        "cloud_tokens_per_task": {role: arm.get("cloud_tokens_per_task") for role, arm in arms.items()},
        "uses": result.get("uses") or [],
        "survival": {name: (result.get("survival") or {}).get(name) or {"state": "not_run"} for name in SURVIVAL},
        "tokens": {
            "main_model_turns": ledger_sum(("main", "chat_only", "finalization", "synthesis", "child", "title",
                                            "compaction")),
            "learning": ledger_sum(("learning_test", "learning_diagnosis", "learning_teacher", "learning_meta")),
            "dream_and_memory": ledger_sum(("dream", "extraction", "contradiction")),
            "other": ledger_sum(("other", "verifier", "condition", "approval_review", "document")),
            "simulated_person": int((tokens.get("sim_person") or {}).get("total") or 0),
            "total_cloud": int(tokens.get("cloud_total") or 0),
        },
        "gpu_hours": round(float(gpu.get("seconds") or 0) / 3600, 3),
        "gpu_hours_by_phase": {name: round(value / 3600, 3) for name, value in sorted(phases.items())},
        "refused_tool_calls": result.get("refused_tool_calls", 0),
    }


# ── Overall ──────────────────────────────────────────────────────────────────


def _pool(people: Sequence[Mapping[str, Any]], role: str, part: str) -> dict[str, Any]:
    table: dict[str, list[float]] = {}
    for person in people:
        rows = ((person.get("models") or {}).get(role) or {}).get(part) or {}
        for kind, value in rows.items():
            _add(table, kind, float(value["passed"]), float(value["total"]))
    return {kind: rate(*value) for kind, value in sorted(table.items())}


def build(results: Sequence[Mapping[str, Any]], *, run: Mapping[str, Any] | None = None,
          tiers: Mapping[str, str] | None = None, cap_tokens: int | None = None,
          archived: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """The whole report from every person's result (any order). ``archived``: earlier runs of a person kept
    under another name (``kept_as``): listed apart, never pooled; their tokens count in the spend."""

    people = [person_report(result) for result in sorted(results, key=lambda item: str(item.get("person")))]
    by_tier: dict[str, list[dict[str, Any]]] = {}
    for person in people:
        by_tier.setdefault(str(person.get("tier")), []).append(person)
    overall: dict[str, Any] = {}
    for tier, members in by_tier.items():
        first = {"passed": 0.0, "total": 0.0}
        final = {"passed": 0.0, "total": 0.0}
        for person in members:
            row = (person["live"].get("first_try") or {}).get("overall")
            if row:
                first["passed"] += row["passed"]
                first["total"] += row["total"]
            row = (person["live"].get("final") or {}).get("overall")
            if row:
                final["passed"] += row["passed"]
                final["total"] += row["total"]
        overall[tier] = {
            "people": len(members),
            "live_first_try": rate(first["passed"], first["total"]),
            "live_final": rate(final["passed"], final["total"]),
            "models": {role: {part: _pool(members, role, part) for part in ("visible", "hidden_packs", "own_work")}
                       for role in ("baseline", "new", "main")},
            "survival": {name: _tally(person["survival"][name] for person in members) for name in SURVIVAL},
            "tokens": {key: sum(int(person["tokens"][key]) for person in members) for key in members[0]["tokens"]}
            if members else {},
            "gpu_hours": round(sum(person["gpu_hours"] for person in members), 3),
            "kinds_by_intent": _pool_live(members, "kinds_by_intent"),
            "container": sorted({str(person.get("container")) for person in members}),
        }
    earlier = [person_report(result) | {"kept_as": result.get("kept_as")} for result in archived]
    total_tokens = sum(person["tokens"]["total_cloud"] for person in (*people, *earlier))
    return {
        "format": FORMAT, "version": VERSION, "run": dict(run or {}), "tiers": dict(tiers or {}),
        "people": people, "overall": overall, "earlier": earlier,
        "tokens": {"total_cloud": total_tokens, "cap": cap_tokens,
                   "within_cap": None if cap_tokens is None else total_tokens <= cap_tokens},
        "not_built": list(NOT_BUILT),
    }


def _pool_live(people: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    table: dict[str, list[float]] = {}
    for person in people:
        for kind, value in ((person.get("live") or {}).get(key) or {}).items():
            _add(table, kind, float(value["passed"]), float(value["total"]))
    return {kind: rate(*value) for kind, value in sorted(table.items())}


def _tally(checks: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    found: dict[str, int] = {}
    for check in checks:
        state = str((check or {}).get("state") or "not_run")
        found[state] = found.get(state, 0) + 1
    return found


# ── Markdown ─────────────────────────────────────────────────────────────────


def _pct(value: Mapping[str, Any] | None) -> str:
    if not value or value.get("rate") is None:
        return "–"
    passed = value["passed"]
    shown = f"{passed:g}" if isinstance(passed, (int, float)) else str(passed)
    return (f"{shown}/{value['total']} = {100 * value['rate']:.1f}% "
            f"[{100 * value['low']:.1f}, {100 * value['high']:.1f}]")


def _diff(entry: Mapping[str, Any] | None) -> str:
    if not entry or entry.get("difference") is None:
        return "–"
    p_value = entry.get("p_value")
    return (f"{100 * entry['difference']:+.1f} pt [{100 * entry['low']:+.1f}, {100 * entry['high']:+.1f}], "
            f"p {p_value:.2f}, n {entry.get('problems')}" if p_value is not None else
            f"{100 * entry['difference']:+.1f} pt, n {entry.get('problems')}")


def _kept_words(kept: Mapping[str, Any] | None) -> str:
    """Which pass a version of several passes kept (O119), in a few words."""

    if not kept or int(kept.get("of") or 0) < 2:
        return ""
    failed = [int(item.get("pass") or 0) for item in kept.get("failed") or () if isinstance(item, Mapping)]
    words = f", kept pass {kept.get('pass')} of {kept.get('of')}"
    if failed:
        words += " (pass " + ", ".join(str(item) for item in failed) + " couldn't finish a short task)"
    return words


def _survival_note(check: Mapping[str, Any]) -> str:
    """What a survival check measured, in a few words (empty when nothing to add)."""

    notes = []
    if check.get("resumes_from") is not None:
        notes.append(f"continued from checkpoint {check['resumes_from']}")
    if check.get("seconds_from_message_to_pause") is not None:
        notes.append(f"paused {check['seconds_from_message_to_pause']:g} s after the message")
    elif check.get("seconds_to_pause") is not None and check.get("state_after_use") == "paused":
        notes.append("paused before the person's turn ended" if not check["seconds_to_pause"] else
                     f"paused {check['seconds_to_pause']:g} s after the person's turn ended")
    if check.get("state_after_use") not in (None, "paused"):
        notes.append(f"still {check['state_after_use']} when the check stopped waiting")
    if check.get("kind") and check.get("routed") is not None:
        notes.append(f"{check['kind'].replace('_', ' ')}: {check['routed']} routed, {check.get('passed', 0)} passed")
    if check.get("why"):
        notes.append(str(check["why"]))
    return f" ({'; '.join(notes)})" if notes else ""


def markdown(report: Mapping[str, Any]) -> str:
    """The report as Markdown: headline tables first, details after."""

    run = report.get("run") or {}
    lines = ["# Learning release check", ""]
    lines.append(f"Build `{run.get('build', '?')}` · seed {run.get('seed', '?')} · {run.get('days', '?')} simulated days · "
                 f"main model {run.get('model', '?')} · started {run.get('started_at', '?')}")
    lines.append("")
    lines.append("Rates: passed/total = rate [95% Wilson interval]. Differences: the product's paired analysis "
                 "against the starting model (95% cluster-bootstrap interval, permutation p, n problems).")
    lines.append("")
    for tier, status in (report.get("tiers") or {}).items():
        if status != "ran":
            lines.append(f"- Tier **{tier}**: {status}.")
    for tier, overall in (report.get("overall") or {}).items():
        people = f"{overall['people']} {'person' if overall['people'] == 1 else 'people'}"
        lines += ["", f"## Headline: tier {tier} ({people})", ""]
        lines.append("| | Starting model | Personal model (newest) | Main model |")
        lines.append("|---|---|---|---|")
        models = overall["models"]
        for part, title in (("visible", "Visible packs"), ("hidden_packs", "Hidden pack split"),
                            ("own_work", "Held-out seeded work")):
            cells = [_pct((models.get(role, {}).get(part) or {}).get("overall")) for role in ("baseline", "new", "main")]
            lines.append(f"| {title} | " + " | ".join(cells) + " |")
        lines.append("| Harder split (O31) | not built yet (L3b) | not built yet (L3b) | not built yet (L3b) |")
        lines += ["", f"Seeded work as asked (main model, or a personal model in trial): first try "
                      f"{_pct(overall['live_first_try'])}; after the person's corrections {_pct(overall['live_final'])}.",
                  ""]
        kinds = overall.get("kinds_by_intent") or {}
        if kinds:
            lines.append("Row-Bot's kind of work (it routes by this) against the kind each request was meant as: "
                         + "; ".join(f"{LABELS.get(kind, kind).lower()} {_pct(value)}" for kind, value in kinds.items())
                         + ".")
            lines.append("")
        containers = overall.get("container") or []
        lines.append("Coding pack: " + ("ran with a container engine (Docker)." if containers == ["True"] else
                                        "left out: no container engine." if containers == ["False"] else
                                        f"container engine per person: {', '.join(containers)}."))
        lines.append("")
        lines.append("Survival checks: " + "; ".join(
            f"{name.replace('_', ' ')} " + ", ".join(f"{state} {count}" for state, count in sorted(tally.items()))
            for name, tally in overall["survival"].items()))
        lines.append("")
        tokens = overall.get("tokens") or {}
        lines.append("Cloud tokens: " + ", ".join(f"{key.replace('_', ' ')} {value:,}" for key, value in tokens.items())
                     + f". GPU: {overall['gpu_hours']} h.")
    lines += ["", "## Per person", ""]
    for person in report.get("people") or ():
        lines += [f"### {person['name']} ({person['person']}, tier {person['tier']})", ""]
        live = person["live"]
        lines.append(f"- Seeded work: {live['requests']} requests; first try {_pct(live['first_try'].get('overall'))}; "
                     f"final {_pct(live['final'].get('overall'))}; corrections {live['corrections']}, Teach "
                     f"{live['teach']} ({live['teach_linked']} linked), stops {live['stops']}, approvals "
                     f"{live['approvals']['approved']}/{live['approvals']['asked']} approved; routed turns "
                     f"{live['routed']['turns']} ({live['routed']['answered_by_personal_model']} answered by the personal "
                     f"model); the product's kind of work agreed with the request's {_pct(live['kinds_agree'])}.")
        if person.get("builds_by_day"):
            lines.append("- Builds that answered the requests: " + "; ".join(
                (f"`{item['build']}`" if item["build"] else "before builds were recorded")
                + f" days {item['first_day']}-{item['last_day']}" for item in person["builds_by_day"]) + ".")
        past = person.get("past") or {}
        lines.append(f"- Past: {past.get('conversations', '–')} conversations, {past.get('turns', '–')} turns imported "
                     f"as partial ({(past.get('import') or {}).get('state', '–')}).")
        for version in person["versions"]:
            lines.append(f"- v{version['version']}: trained after day {version.get('trained_after_day')}, "
                         f"{version.get('steps')} steps, {version.get('phase')}"
                         + (f" ({version['failure']})" if version.get("failure") else "")
                         + _kept_words(version.get("kept_pass"))
                         + f"; comparison: {version.get('line') or '–'}")
        models = person.get("models") or {}
        if models:
            lines += ["", "| | Starting model | Personal model | Main model |", "|---|---|---|---|"]
            for part, title in (("visible", "Visible packs"), ("hidden_packs", "Hidden pack split"),
                                ("own_work", "Held-out seeded work")):
                cells = [_pct(((models.get(role) or {}).get(part) or {}).get("overall")) for role in
                         ("baseline", "new", "main")]
                lines.append(f"| {title} | " + " | ".join(cells) + " |")
            pairs = person.get("paired") or {}
            for role, title in (("new", "Personal model − starting model"), ("main", "Main model − starting model")):
                entry = pairs.get(role) or {}
                lines.append(f"| {title} | visible {_diff((entry.get('visible') or {}).get('overall'))} | hidden "
                             f"{_diff((entry.get('hidden') or {}).get('overall'))} | all "
                             f"{_diff((entry.get('combined') or {}).get('overall'))} |")
            lines.append(f"| Seconds per task | {person['time_per_task'].get('baseline')} | "
                         f"{person['time_per_task'].get('new')} | {person['time_per_task'].get('main')} |")
        lines.append("")
        lines.append("- Survival: " + "; ".join(f"{name.replace('_', ' ')}: {check.get('state')}{_survival_note(check)}"
                                               for name, check in person["survival"].items()))
        lines.append("- Tokens: " + ", ".join(f"{key.replace('_', ' ')} {value:,}" for key, value in
                                               person["tokens"].items()) + f"; GPU {person['gpu_hours']} h "
                     f"({', '.join(f'{name} {hours}' for name, hours in person['gpu_hours_by_phase'].items())}).")
        lines.append("")
    tokens = report.get("tokens") or {}
    earlier = report.get("earlier") or []
    if earlier:
        lines += ["## Earlier runs, kept for the record", "",
                  "Not pooled above: each was run again fresh (its name says why).", ""]
        for person in earlier:
            live = person["live"]
            versions = "; ".join(f"v{item['version']} {item.get('phase')}"
                                 + (f" ({item['failure']})" if item.get("failure") else "") for item in person["versions"])
            lines.append(f"- **{person['kept_as']}** ({person['name']}, tier {person['tier']}): {live['requests']} requests, "
                         f"first try {_pct(live['first_try'].get('overall'))}; versions: {versions or 'none'}; survival: "
                         + ", ".join(f"{name.replace('_', ' ')} {check.get('state')}"
                                     for name, check in person["survival"].items())
                         + f"; cloud tokens {person['tokens']['total_cloud']:,}.")
        lines.append("")
    lines += ["## Spend", "", f"Cloud tokens on the ChatGPT lane: {tokens.get('total_cloud', 0):,}"
                              + (f" of a {tokens['cap']:,} cap." if tokens.get("cap") else ".")]
    lines += ["", "## Not built yet", ""]
    for item in report.get("not_built") or ():
        lines.append(f"- {item['stage']}: **not built yet** ({item['work_package']}).")
    return "\n".join(lines) + "\n"


__all__ = ["FORMAT", "NOT_BUILT", "SURVIVAL", "build", "live_work", "markdown", "model_rates", "paired",
           "person_report", "rate", "wilson"]
