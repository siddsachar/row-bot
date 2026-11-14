"""What each level would have brought up today, from today's real moments (Settings › Proactive's preview).

Today's moments are run again through the levels' budget, without a model call: the brief and the imminent
ones come up at Quiet (at most 3), more at natural breaks at Balanced and Eager within their daily number and
each kind's cooldown; everything else stays on Home. A kind that is off or went quiet stays on Home at every
level. Nothing checked yet today: ``example`` is true and the client shows an example day instead.

It is a preview, not a replay: breaks, calls and quiet hours aren't simulated, so it says what the budget
lets through, at the times the moments came.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from row_bot.proactive import store
from row_bot.proactive import settings as proactive_settings
from row_bot.proactive.timing import day_start

LEVELS = ("off", "quiet", "balanced", "eager")


def _when(moment: dict[str, Any]) -> float:
    return float(moment["shown_at"] or moment["created_at"])


def _today(now: float) -> list[dict[str, Any]]:
    start = day_start(datetime.fromtimestamp(now))
    found = [moment for moment in store.list_moments(since=start - 86400, limit=500)
             if moment["state"] != "dropped_stale" and (moment["created_at"] >= start or moment["shown_at"] >= start)]
    return sorted(found, key=_when)


def _level(level: str, moments: list[dict[str, Any]], settings: dict[str, Any],
           quieted: set[str]) -> list[dict[str, Any]]:
    if level == "off":
        return []
    cap, cooldown = proactive_settings.DAILY_CAP[level], proactive_settings.COOLDOWN_SECONDS[level]
    used = 0
    last: dict[str, float] = {}
    items = []
    for moment in moments:
        kind, at = moment["kind"], _when(moment)
        carried = moment["surface"] == "brief"
        where = "home"
        if not settings["kinds"].get(kind, False) or kind in quieted or carried:
            pass
        elif kind == "brief" or moment["urgency"] == "urgent":
            if used < cap or (moment["urgency"] == "urgent" and level != "quiet"):
                where = "buddy"
        elif level != "quiet" and used < cap and at - last.get(kind, -cooldown) >= cooldown:
            where = "buddy"
            last[kind] = at
        if where == "buddy":
            used += 1
        items.append({"moment_id": moment["id"], "kind": kind, "title": moment["title"][:200],
                      "at": datetime.fromtimestamp(at).astimezone().isoformat(timespec="seconds")
                      if where == "buddy" else "", "where": where, "in_brief": carried})
    return items


def preview(now: float) -> dict[str, Any]:
    """{example, levels: [{level, items}]}: what each level would have brought up today."""
    settings = proactive_settings.load()
    moments = _today(now)
    quieted = {kind for kind in settings["kinds"] if store.kind_stats(kind)["quieted_at"]}
    return {"example": not moments,
            "levels": [{"level": level, "items": _level(level, moments, settings, quieted)} for level in LEVELS]}
