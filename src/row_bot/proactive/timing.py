"""The attention budget: when a moment may reach the person, and when it waits.

One budget across every surface. A moment waits for a natural break (a *boundary*: unlock, back from being
away, the end of a call, the start or end of the day, opening the app) and never arrives during quiet
hours, a call, Focus / Do Not Disturb or while the person is typing. Each level has a daily cap and a
per-kind cooldown; an urgent moment (a meeting in 10 minutes) skips the wait for a break, the cap and the
cooldown, but not quiet hours, a call or Focus.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from row_bot.proactive.model import AWAY_IDLE_SECONDS, KIND_WORDS, TYPING_IDLE_SECONDS, Desktop
from row_bot.proactive.settings import COOLDOWN_SECONDS, DAILY_CAP, LEVEL_WORDS, wrap_on

BOUNDARY_WINDOW_SECONDS = 120  # A break stays open this long: moments batched for it go out in it.
RETURNED_IDLE_SECONDS = 60  # Input this recent after being away means the person is back.
BOUNDARY_WORDS = {"unlock": "you unlocked the computer", "idle_return": "you came back",
                  "call_end": "your call ended", "day_start": "the start of your day",
                  "day_end": "the end of your day", "app_open": "you opened Row-Bot"}
# The first of these each day brings the morning brief.
DAY_OPENERS = frozenset({"unlock", "idle_return", "day_start", "app_open"})


def _minutes(text: str) -> int:
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def in_quiet_hours(at: datetime, settings: dict[str, Any]) -> bool:
    now, start, end = at.hour * 60 + at.minute, _minutes(settings["quiet_start"]), _minutes(settings["quiet_end"])
    return start <= now < end if start < end else now >= start or now < end


def day_start(at: datetime) -> float:
    """Local midnight: the daily cap counts from here."""
    return at.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def detect_boundaries(previous: dict[str, Any], desktop: Desktop, at: datetime,
                      settings: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """The breaks that happened since the last tick, and what to remember for the next one."""
    found: list[str] = []
    today = at.date().isoformat()
    if previous.get("locked") is True and desktop.locked is False:
        found.append("unlock")
    if ((previous.get("idle") or 0) >= AWAY_IDLE_SECONDS and desktop.idle_seconds is not None
            and desktop.idle_seconds < RETURNED_IDLE_SECONDS):
        found.append("idle_return")
    if previous.get("on_call") is True and desktop.known and not desktop.on_call:
        found.append("call_end")
    quiet = in_quiet_hours(at, settings)
    if not quiet and previous.get("day_started") != today:
        found.append("day_start")
    day_end = _minutes(settings["day_end"])
    if not quiet and at.hour * 60 + at.minute >= day_end and previous.get("day_ended") != today:
        found.append("day_end")
    remembered = {
        "locked": desktop.locked, "idle": desktop.idle_seconds, "on_call": desktop.on_call if desktop.known else None,
        "day_started": today if "day_start" in found else previous.get("day_started"),
        "day_ended": today if "day_end" in found else previous.get("day_ended"),
    }
    return found, remembered


@dataclass(frozen=True)
class Decision:
    action: Literal["deliver", "hold", "expire"]
    reason: str
    why: tuple[str, ...] = ()


def decide(moment: dict[str, Any], *, at: datetime, desktop: Desktop, settings: dict[str, Any],
           boundary: str | None, shown_today: int, kind_shown_today: int, kind_last_shown: float,
           quieted: bool, rules: list[dict[str, Any]]) -> Decision:
    """Whether one pending moment goes out now, waits, or has run out."""
    now = at.timestamp()
    kind, level = moment["kind"], settings["level"]
    if now >= moment["expires_at"]:
        return Decision("expire", "It ran out before a good moment came.")
    if level == "off":
        return Decision("hold", "Proactive is off.")
    if moment["watcher"] == "wrap" and not wrap_on(settings):
        return Decision("hold", "The end-of-day wrap is off.")
    if moment["watcher"] != "wrap" and not settings["kinds"].get(kind, False):
        return Decision("hold", f"{KIND_WORDS[kind]} are off.")
    if quieted:
        return Decision("hold", f"{KIND_WORDS[kind]} went quiet after being ignored.")
    if in_quiet_hours(at, settings):
        return Decision("hold", "Quiet hours.")
    if desktop.on_call:
        return Decision("hold", "You're on a call.")
    if desktop.focus:
        return Decision("hold", "Focus or Do Not Disturb is on.")
    if desktop.idle_seconds is not None and desktop.idle_seconds < TYPING_IDLE_SECONDS and not desktop.locked:
        return Decision("hold", "You're typing.")
    for rule in rules:
        if rule["type"] == "not_at_time" and rule["kind"] == kind and rule["hour"] == at.hour:
            return Decision("hold", f"Your rule: no {KIND_WORDS[kind].lower()} around {at.hour}:00.")
    if now < moment["due_at"]:  # Even an urgent one: a meeting brief prepared overnight waits for its meeting.
        return Decision("hold", "Not due yet.")
    if moment["urgency"] == "urgent":
        return Decision("deliver", "Urgent.", ("It's urgent, so it didn't wait for a break.",))
    if boundary is None:
        return Decision("hold", "Waiting for a natural break.")
    cap = DAILY_CAP[level]
    if shown_today >= cap:
        return Decision("hold", f"Today's {cap} are used ({LEVEL_WORDS[level]}).")
    if kind_shown_today and any(rule["type"] == "fewer" and rule["kind"] == kind for rule in rules):
        return Decision("hold", f"Your rule: at most one {KIND_WORDS[kind].lower()} a day.")
    if now - kind_last_shown < COOLDOWN_SECONDS[level]:
        return Decision("hold", f"Another {KIND_WORDS[kind].lower()} came recently.")
    return Decision("deliver", "At a natural break.", (
        f"{LEVEL_WORDS[level]}: {shown_today + 1} of {cap} today.",
        f"It waited for a break: {BOUNDARY_WORDS.get(boundary, boundary)}.",
    ))
