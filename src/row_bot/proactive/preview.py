"""What each level would have brought up today, from today's real moments (Settings › Proactive's preview).

Today's moments are run again through the levels' budget, without a model call: the brief and the imminent
ones come up at Quiet (at most 3), more at natural breaks at Balanced and Eager within their daily number and
each kind's cooldown; everything else stays on Home. A kind that is off or went quiet stays on Home at every
level. Nothing checked yet today: ``example`` is true and the client shows an example day instead.

It is a preview, not a replay: breaks, calls and quiet hours aren't simulated, so it says what the budget
lets through, at the times the moments came. Where it says what really happened: a moment that reached a surface
names that surface, and one the person's level let through that never reached any (they were away, or on a
call) stayed on Home at every level, never "Buddy's bubble" (R5). Any other names the surface it would use now.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from row_bot.proactive import store
from row_bot.proactive import settings as proactive_settings
from row_bot.proactive.model import OPEN_STATES
from row_bot.proactive.timing import day_start

LEVELS = ("off", "quiet", "balanced", "eager")


# Each surface a moment really went to, as the preview names it.
_SURFACES = {"buddy": "buddy", "desktop": "notification", "notice": "notification", "push": "phone",
             "channel": "chat_app"}


def _when(moment: dict[str, Any]) -> float:
    return float(moment["shown_at"] or moment["created_at"])


def _today(now: float) -> list[dict[str, Any]]:
    start = day_start(datetime.fromtimestamp(now))
    found = [moment for moment in store.list_moments(since=start - 86400, limit=500)
             if moment["state"] != "dropped_stale" and (moment["created_at"] >= start or moment["shown_at"] >= start)]
    return sorted(found, key=_when)


def _budget(level: str, moments: list[dict[str, Any]], settings: dict[str, Any], quieted: set[str]) -> set[str]:
    """The moments *level*'s budget lets through to a surface."""
    if level == "off":
        return set()
    cap, cooldown = proactive_settings.DAILY_CAP[level], proactive_settings.COOLDOWN_SECONDS[level]
    used = 0
    last: dict[str, float] = {}
    through = set()
    for moment in moments:
        kind, at = moment["kind"], _when(moment)
        if not settings["kinds"].get(kind, False) or kind in quieted or moment["surface"] == "brief":
            continue
        if kind == "brief" or moment["urgency"] == "urgent":
            if not (used < cap or (moment["urgency"] == "urgent" and level != "quiet")):
                continue
        elif level != "quiet" and used < cap and at - last.get(kind, -cooldown) >= cooldown:
            last[kind] = at
        else:
            continue
        used += 1
        through.add(moment["id"])
    return through


def _level(level: str, moments: list[dict[str, Any]], through: set[str], held: set[str],
           now_surface: str) -> list[dict[str, Any]]:
    if level == "off":
        return []
    items = []
    for moment in moments:
        where = "home"
        if moment["id"] in through and moment["id"] not in held:
            where = _SURFACES.get(moment["surface"]) or now_surface
        items.append({"moment_id": moment["id"], "kind": moment["kind"], "title": moment["title"][:200],
                      "at": datetime.fromtimestamp(_when(moment)).astimezone().isoformat(timespec="seconds")
                      if where != "home" else "", "where": where, "in_brief": moment["surface"] == "brief"})
    return items


def preview(now: float) -> dict[str, Any]:
    """{example, levels: [{level, items}]}: what each level would have brought up today."""
    from row_bot.proactive import deliver

    settings = proactive_settings.load()
    moments = _today(now)
    quieted = {kind for kind in settings["kinds"] if store.kind_stats(kind)["quieted_at"]}
    through = {level: _budget(level, moments, settings, quieted) for level in LEVELS}
    # Let through at the person's own level, settled, and never on a surface: what held it (away, a call, quiet
    # hours) would have held it at any level.
    held = {moment["id"] for moment in moments if moment["id"] in through.get(settings["level"], set())
            and moment["state"] not in OPEN_STATES and not moment["surface"]}
    now_surface = "buddy" if deliver.buddy_on() else "notification"
    return {"example": not moments,
            "levels": [{"level": level, "items": _level(level, moments, through[level], held, now_surface)}
                       for level in LEVELS]}
