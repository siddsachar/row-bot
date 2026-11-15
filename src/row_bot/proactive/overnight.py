"""Overnight preparation ("sleep-time compute"): Dream Cycle's last phase gets tomorrow ready.

While the person is away at night (Dream Cycle runs only in its window, when Row-Bot is idle, nothing is
generating and a model is chosen), Row-Bot prepares for the day they wake up to:

- a **brief for each meeting** with other people or files;
- a **drafted reply for each follow-up** that comes due that day.

Each goes through the same read-only preparation as by day (``prepare.ahead``: a hidden thread on the
runtime surface "proactive", where only reads run), saved as a draft that waits for its moment. Nothing is
shown now: when the watcher makes the moment at its time, after triage and the person's rules, the draft is
attached to it, and the morning brief lists what was prepared. Nothing at Off, nothing for a source or kind
that is off, nothing the person's rules or triage already kept out, at most ``PER_NIGHT`` a night, and it
stops as soon as the person is back or Dream Cycle is asked to stop.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from row_bot.proactive import store, timing

logger = logging.getLogger(__name__)

PER_NIGHT = 6


def target_day(at: datetime) -> datetime:
    """Midnight of the day the person wakes up to: today in the small hours, tomorrow late in the evening."""
    return (at + timedelta(hours=12)).replace(hour=0, minute=0, second=0, microsecond=0)


def _jobs(settings: dict[str, Any], now: float, start: float, end: float) -> list[tuple[Any, str, str]]:
    """(candidate, what to prepare, title) for each thing worth preparing for that day."""
    from row_bot.proactive import sources as proactive_sources
    from row_bot.proactive.follow_ups import MailWatcher
    from row_bot.proactive.meetings import CalendarWatcher

    jobs: list[tuple[Any, str, str]] = []
    if settings["sources"]["calendar"] and settings["kinds"]["meeting_prep"]:
        calendar = CalendarWatcher(proactive_sources.calendar_sources())
        jobs.extend((candidate, "brief", title) for candidate, title in calendar.upcoming(start, end))
    if settings["sources"]["mail"] and settings["kinds"]["follow_up"]:
        mail = MailWatcher(proactive_sources.mail_sources())
        jobs.extend((candidate, "reply", candidate.title) for candidate in mail.upcoming(now, end))
    return jobs


def prepare_tomorrow(now: float | None = None, *, should_stop: Callable[[], bool] = lambda: False,
                     agent: Callable[[str, list[str], dict], Any] | None = None) -> int:
    """Prepare for the day ahead; how many drafts were saved."""
    from row_bot.proactive import feedback, prepare
    from row_bot.proactive import settings as proactive_settings

    if not store.exists():
        return 0  # Proactive Row-Bot hasn't started on this computer yet (its first tick makes the file).
    settings = proactive_settings.load()
    if settings["level"] == "off":
        return 0
    now = time.time() if now is None else now
    day = target_day(datetime.fromtimestamp(now))
    start, end = max(now, day.timestamp()), (day + timedelta(days=1)).timestamp()
    try:
        jobs = _jobs(settings, now, start, end)
    except Exception:
        logger.warning("Overnight preparation couldn't read the calendar or mail", exc_info=True)
        return 0
    if not jobs:
        return 0
    rules = store.rules()
    silenced = set(store.get_state("silenced") or [])
    saved = skipped = 0
    for candidate, what, title in jobs:
        if saved >= PER_NIGHT or should_stop():
            break
        if (candidate.key in silenced or store.seen_key(candidate.key) or store.prepared_ahead(candidate.key)
                or feedback.blocked(candidate, rules, today_start=timing.day_start(datetime.fromtimestamp(now)))):
            skipped += 1
            continue  # Already a moment or prepared, kept silent by triage, or the person's rules say no.
        try:
            draft = prepare.ahead(candidate, what, title=title, now=now, agent=agent)
        except Exception:
            logger.warning("An overnight preparation failed", exc_info=True)
            draft = None
        saved += draft is not None
    store.log("pass", f"Overnight: prepared {saved} for {day:%A} (reads only; nothing was sent).", now=now,
              detail={"prepared": saved, "found": len(jobs), "skipped": skipped})
    return saved
