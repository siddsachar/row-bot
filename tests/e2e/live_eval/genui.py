"""The live eval's ``genui`` tier (generative UI, G8): does a model draw interfaces Row-Bot can show?

``ROW_BOT_LIVE_EVAL_TIER=genui`` runs ten one- or two-turn tasks through the real Agent runtime with
no tools: an interface where one helps (plan, compare, adjust, fill in, tick off, progress), private
bindings where the person's data is named, the same surface redrawn, and a plain answer where none
helps. Each reply is read the way the app reads it (``answer_components.component_spans``, so the
same healing applies) and checked on the drawn surface. Every check also records whether the fence
was strict JSON, so healing's share shows in the report.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Sequence

from .tasks import Check, Context, Outcome, Task, Turn, _done, _write

TIER = "genui"
_FENCE = re.compile(r"```rowbot-ui[^\n]*\n(.*?)```", re.S)
SPEND_ROWS = (("2026-10-02", "Groceries", "61.90"), ("2026-10-05", "Rent", "1200.00"),
              ("2026-10-11", "Train", "487.23"), ("2026-10-19", "Dinner out", "73.15"))


def drawn(answer: str) -> list[dict[str, Any]]:
    """The surfaces a reply drew, as the app reads them (healed and checked)."""
    from row_bot.application.answer_components import component_spans

    return [json.loads(fields["surface_json"]) for _start, _end, kind, fields in component_spans(answer)
            if kind == "surface"]


def _metrics(answer: str) -> dict[str, Any]:
    bodies = _FENCE.findall(answer)
    strict = 0
    for body in bodies:
        try:
            json.loads(body)
            strict += 1
        except ValueError:
            pass
    surfaces = drawn(answer)
    return {"fences": len(bodies), "strict_json": strict, "surfaces": len(surfaces),
            "components": sum(len(surface["components"]) for surface in surfaces)}


def kinds(surface: dict[str, Any]) -> list[str]:
    return [component["component"] for component in surface["components"]]


def bound(value: Any) -> bool:
    """Whether a component (or any value in it) reads the data model."""
    return '"@path"' in json.dumps(value)


def words(surface: dict[str, Any]) -> str:
    from row_bot.application.surfaces import surface_text

    return surface_text(surface).lower()


def _source_calls(surface: dict[str, Any]) -> list[dict[str, Any]]:
    return list((surface.get("sources") or {}).values())


def _on_surface(rule: Callable[[dict[str, Any], Outcome], str]) -> Callable[[Outcome, Context], Check]:
    """A check on the last turn's surface: *rule* returns "" when it passes, else why not."""

    def check(outcome: Outcome, _context: Context) -> Check:
        if (failure := _done(outcome)) is not None:
            return failure
        metrics = _metrics(outcome.answer)
        surfaces = drawn(outcome.answer)
        if not surfaces:
            return Check(False, "no interface Row-Bot can show" + (" (a fence that didn't parse)"
                                                                     if metrics["fences"] else ""), metrics)
        why = rule(surfaces[-1], outcome)
        return Check(not why, why, metrics)

    return check


def _trip(surface: dict[str, Any], _outcome: Outcome) -> str:
    if "Slider" not in kinds(surface):
        return "no budget slider"
    if not any(bound(component) for component in surface["components"] if component["component"] == "Slider"):
        return "the slider isn't bound to the data model"
    return ""


def _compare(surface: dict[str, Any], _outcome: Outcome) -> str:
    from row_bot.application.surfaces import declared_actions

    text = words(surface)
    missing = [name for name in ("air", "xps", "thinkpad") if name not in text]
    if missing:
        return f"doesn't show {missing}"
    if not declared_actions(surface) and "ChoicePicker" not in kinds(surface):
        return "nothing to pick with"
    return ""


def _budget(surface: dict[str, Any], _outcome: Outcome) -> str:
    inputs = [component for component in surface["components"]
              if component["component"] in {"Slider", "TextField"} and bound(component)]
    if "Breakdown" not in kinds(surface) and len(inputs) < 2:
        return "nothing to adjust the parts with"
    text = words(surface)
    return "" if "rent" in text and "food" in text else "the parts aren't shown"


def _form(surface: dict[str, Any], _outcome: Outcome) -> str:
    found = kinds(surface)
    if found.count("TextField") < 2 or "DateTimeInput" not in found:
        return f"not a form with fields and a date ({sorted(set(found))})"
    buttons = [component for component in surface["components"]
               if component["component"] == "Button" and component.get("action", {}).get("event")]
    if not any(sum(bound(value) for value in (button["action"]["event"].get("context") or {}).values()) >= 2
               for button in buttons):
        return "no Send button that sends what was entered"
    return ""


def _private_file(surface: dict[str, Any], outcome: Outcome) -> str:
    calls = _source_calls(surface)
    if not any(call.get("@call") == "rowbot.file" and str((call.get("args") or {}).get("path", "")).endswith("oct.csv")
               for call in calls):
        return f"doesn't read bank/oct.csv on this computer (sources: {calls})"
    copied = [amount for _day, _what, amount in SPEND_ROWS if amount in outcome.answer]
    if copied:
        return f"copied the values {copied} into the conversation"
    return "" if {"Chart", "Table"} & set(kinds(surface)) else "no chart or table"


def _savings(surface: dict[str, Any], _outcome: Outcome) -> str:
    if not {"ProgressBar", "Metric"} & set(kinds(surface)):
        return "no progress or figure"
    text = words(surface).replace(",", "")
    return "" if "1100" in text and "3000" in text else "the saved and target amounts aren't shown"


def _tasks_source(surface: dict[str, Any], _outcome: Outcome) -> str:
    calls = _source_calls(surface)
    return "" if any(call.get("@call") == "rowbot.tasks" for call in calls) else f"doesn't read the tasks ({calls})"


def _check_checklist(outcome: Outcome, _context: Context) -> Check:
    from row_bot.application.answer_components import component_spans

    if (failure := _done(outcome)) is not None:
        return failure
    metrics = _metrics(outcome.answer)
    if any(kind == "checklist" for _start, _end, kind, _fields in component_spans(outcome.answer)):
        return Check(True, "", {**metrics, "as": "rowbot-checklist"})
    surfaces = drawn(outcome.answer)
    ticks = kinds(surfaces[-1]).count("CheckBox") if surfaces else 0
    return Check(ticks >= 3, "" if ticks >= 3 else "nothing to tick off", {**metrics, "as": "surface"})


def _check_plain(outcome: Outcome, _context: Context) -> Check:
    if (failure := _done(outcome)) is not None:
        return failure
    metrics = _metrics(outcome.answer)
    if metrics["fences"] or metrics["surfaces"]:
        return Check(False, "drew an interface for a one-line answer", metrics)
    return Check("lisbon" in outcome.answer.lower(), "", metrics)


def _check_redraw(outcome: Outcome, _context: Context) -> Check:
    if (failure := _done(outcome)) is not None:
        return failure
    first, second = (drawn(turn.answer) for turn in outcome.turns[:2])
    metrics = _metrics(outcome.answer)
    if not first or not second:
        return Check(False, "a turn drew no interface", metrics)
    if first[-1]["surfaceId"] != second[-1]["surfaceId"]:
        return Check(False, "redrawn under another surfaceId", metrics)
    return Check("4" in words(second[-1]), "" if "4" in words(second[-1]) else "the fourth day isn't there", metrics)


def _seed_spend(workspace: Path, _context: Context) -> None:
    _write(workspace, "bank/oct.csv", "date,what,amount\n" + "\n".join(",".join(row) for row in SPEND_ROWS) + "\n")


def _turns(*prompts: str) -> Callable[[Context], Sequence[Turn]]:
    return lambda _context: [Turn(prompt) for prompt in prompts]


TRIP = "Plan three days in Lisbon in March with a budget of £1,500. Make it something I can adjust, with the budget as a slider."


def _task(task_id: str, prompt: str | tuple[str, ...], check: Callable[[Outcome, Context], Check], **extra: Any) -> Task:
    prompts = prompt if isinstance(prompt, tuple) else (prompt,)
    return Task(f"genui_{task_id}", "genui", _turns(*prompts), check, est_calls=len(prompts), tier=TIER,
                **{"est_output_tokens": 2_500, **extra})


TASKS: tuple[Task, ...] = (
    _task("trip", TRIP, _on_surface(_trip)),
    _task("compare", "Compare the MacBook Air 13, the Dell XPS 13 and the ThinkPad X1 Carbon on price, weight and "
                     "battery life, and let me pick one.", _on_surface(_compare)),
    _task("budget", "My monthly budget: rent 1200, food 350, transport 120, fun 200. Show it so I can adjust each part "
                    "and see the total.", _on_surface(_budget)),
    _task("form", "I need a dentist appointment. Give me a form for my name, phone, preferred date and the reason, "
                  "with a Send button.", _on_surface(_form)),
    _task("private_file", "Show my October spending from bank/oct.csv as a chart and a table. Don't copy the numbers "
                          "into the chat: have them read on my computer.", _on_surface(_private_file),
          seed=_seed_spend),
    _task("plain", "What's the capital of Portugal? One sentence.", _check_plain, est_output_tokens=300),
    _task("checklist", "Make me a packing checklist for a weekend hike that I can tick off.", _check_checklist),
    _task("savings", "I'm saving for a £3,000 bike and have saved £1,100 so far. Show my progress.",
          _on_surface(_savings)),
    _task("redraw", (TRIP, "Make it four days instead, in the same interface."), _check_redraw),
    _task("tasks_source", "Show my open tasks as a live list I could pin to Home.", _on_surface(_tasks_source)),
)


def tasks() -> list[Task]:
    return list(TASKS)
