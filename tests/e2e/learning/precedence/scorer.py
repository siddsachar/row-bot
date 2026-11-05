"""The precedence study's scorer: the first action, matched to X, Y, both or neither (Lane B, ``PRECEDENCE.md``).

The **first action** is the model's first reply that acts or answers. Calls that only read (:data:`READS`: loading a
skill, listing mail, reading a ticket, a sum, a search) take no side: they are answered and the turn goes on, up to
:data:`MAX_MODEL_CALLS` model calls (revised after the probes, 25 Oct 14:55: one-call scoring left 39% of the main
model's and nearly all of the 27B's first replies as reads). Of the first reply that isn't only reads:

- if it calls the item's tool (directly, or through ``tool_invoke``), the value is read from that call's
  argument (the recipient, the start time, the email body, the assignee, the labels);
- if it asks the person (``ask_person``), the value is read from the question it asks, as from a text reply;
- if it calls another acting tool, it took no side: ``neither``;
- if it answers in text, the value is read from the text (the reasoning, if any, is never read).

The value is matched by the item's own checker against X and Y: ``x``, ``y``, ``both`` (states both) or
``neither``. The **stale choice** is ``x`` in condition 3 (and 4); the **false switch** is ``x`` in condition 6.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from math import sqrt
from typing import Any

from tests.e2e.learning.precedence.items import Item

X, Y, BOTH, NEITHER = "x", "y", "both", "neither"
#: Tools that only read: the turn goes on past them.
READS = frozenset({"skill_load", "skill_search", "tool_search", "read_tool_output", "calculate", "mail_list",
                   "mail_read", "calendar_list_events", "issues_list", "issues_get", "search_memory",
                   "workspace_read_file", "workspace_list_files", "workspace_search_files", "conversation_search"})
ASK = "ask_person"
MAX_MODEL_CALLS = 4


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().replace(",", "").split())


def _hour_pattern(value: str) -> str:
    hour, minute = (int(part) for part in value.split(":"))
    twelve = hour % 12 or 12
    suffix = "pm" if hour >= 12 else "am"
    options = [rf"{hour:02d}[:.]{minute:02d}", rf"(?<!\d){hour}[:.]{minute:02d}"]
    if minute == 0:
        options.append(rf"(?<!\d){twelve}\s?{suffix}\b")
    else:
        options.append(rf"(?<!\d){twelve}[:.]{minute:02d}\s?{suffix}\b")
    return "|".join(f"(?:{option})" for option in options)


def states(item: Item, value: str, text: str) -> bool:
    """Whether ``text`` states ``value`` of ``item``'s fact (the item kind's own rule)."""

    text = str(text or "")
    if item.kind == "hours":
        return re.search(rf"(?<![\d:])(?:{_hour_pattern(value)})(?![\d])", text, re.IGNORECASE) is not None
    if item.kind == "amount":
        number = value.split()[-1]
        return re.search(rf"(?<![\d.]){re.escape(number)}(?:\.00)?(?![\d])", text.replace(",", "")) is not None
    if item.kind in ("role",):
        full = _norm(value)
        return full in _norm(text) or re.search(rf"\b{re.escape(full.split()[0])}\b", _norm(text)) is not None
    if item.kind == "policy":
        return re.search(rf"(?<![\w-]){re.escape(value.lower())}(?![\w-])", text.lower()) is not None
    return value.lower() in text.lower()


def match(item: Item, text: str) -> str:
    has_x, has_y = states(item, item.x, text), states(item, item.y, text)
    return BOTH if has_x and has_y else X if has_x else Y if has_y else NEITHER


def _call_name_args(call: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    name = str(call.get("name") or "")
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    if name == "tool_invoke":
        inner = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
        return str(args.get("name") or args.get("tool_name") or ""), dict(inner)
    return name, dict(args)


def _arg_text(item: Item, args: Mapping[str, Any]) -> str:
    if item.arg == "body":
        return f"{args.get('subject') or ''}\n{args.get('body') or ''}"
    value = args.get(item.arg)
    if isinstance(value, (list, tuple)):
        return " ".join(str(part) for part in value)
    return str(value or "")


def first_action(item: Item, tool_calls: Iterable[Mapping[str, Any]], text: str) -> dict[str, Any]:
    """The first action's class: how it acted (``tool``, ``other_tool``, ``text``) and the value it chose."""

    calls = [_call_name_args(call) for call in tool_calls or ()]
    for name, args in calls:
        if name == item.tool:
            return {"via": "tool", "tool": name, "choice": match(item, _arg_text(item, args)),
                    "value": _arg_text(item, args)[:300]}
    for name, args in calls:
        if name == ASK:
            asked = json.dumps(args, ensure_ascii=False)
            return {"via": "ask", "tool": name, "choice": match(item, asked), "value": asked[:300]}
    if calls:
        return {"via": "other_tool", "tool": calls[0][0], "choice": NEITHER, "value": json.dumps(calls[0][1])[:300]}
    return {"via": "text", "tool": None, "choice": match(item, text), "value": str(text or "")[:300]}


def only_reads(tool_calls: Iterable[Mapping[str, Any]]) -> bool:
    """Whether a reply's calls all only read (so the turn goes on to the next model call)."""

    calls = [_call_name_args(call) for call in tool_calls or ()]
    return bool(calls) and all(name in READS for name, _args in calls)


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if not n:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(100 * max(0.0, centre - half), 1), round(100 * min(1.0, centre + half), 1)]


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p (``b``: stale in 3 only; ``c``: stale in 4 only)."""

    from math import comb

    n = b + c
    if n == 0:
        return 1.0
    tail = sum(comb(n, k) for k in range(0, min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def holm(pvalues: Mapping[str, float]) -> dict[str, float]:
    """Holm-adjusted p-values."""

    ordered = sorted(pvalues.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    running = 0.0
    total = len(ordered)
    for rank, (name, value) in enumerate(ordered):
        running = max(running, min(1.0, (total - rank) * value))
        adjusted[name] = round(running, 6)
    return adjusted


def item_clustered_interval(per_item: Mapping[str, list[int]], *, draws: int = 2000, seed: int = 7) -> list[float] | None:
    """A 95% bootstrap interval of the rate, resampling items (each item's runs stay together)."""

    import random

    keys = sorted(per_item)
    if not keys:
        return None
    rng = random.Random(seed)
    rates = []
    for _ in range(draws):
        sample = [per_item[rng.choice(keys)] for _ in keys]
        hits = sum(sum(runs) for runs in sample)
        total = sum(len(runs) for runs in sample)
        rates.append(hits / total if total else 0.0)
    rates.sort()
    return [round(100 * rates[int(0.025 * draws)], 1), round(100 * rates[int(0.975 * draws) - 1], 1)]


def decision(stale_9b_c3: float | None, cut_by_half: bool, note_p_holm: float | None,
             false_switch_9b: float | None) -> str:
    """PRECEDENCE.md's fixed rule."""

    if stale_9b_c3 is None:
        return "undecided"
    if stale_9b_c3 < 0.10:
        return "precedence does not replicate"
    if stale_9b_c3 < 0.15:
        return "in between"
    works = bool(cut_by_half and note_p_holm is not None and note_p_holm < 0.05 and false_switch_9b is not None
                 and false_switch_9b <= 0.05)
    return "both hold" if works else "precedence replicates, the note does not work"


__all__ = ["ASK", "BOTH", "MAX_MODEL_CALLS", "NEITHER", "READS", "X", "Y", "only_reads", "decision", "first_action", "holm", "item_clustered_interval", "match",
           "mcnemar_exact", "states", "wilson"]
