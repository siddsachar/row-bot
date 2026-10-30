"""A workflow schedule's own timezone (N9).

A schedule keeps its wall-clock time in its IANA zone across daylight saving:
"09:00 · Europe/London" runs at 09:00 London time all year. A time that doesn't
exist that day (the clocks go forward over it) runs at the next valid minute; a
time that happens twice (the clocks go back) runs once, the first time.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.base import BaseTrigger

logger = logging.getLogger(__name__)

_ZONE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+){0,2}")


def is_zone(name: object) -> bool:
    """Whether *name* is an IANA zone this computer knows ("Europe/London", "UTC")."""
    if not isinstance(name, str) or not 1 <= len(name) <= 64 or not _ZONE_NAME.fullmatch(name):
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


def local_zone_name() -> str:
    """The computer's IANA zone; UTC when it can't be told."""
    try:
        from tzlocal import get_localzone_name

        name = get_localzone_name()
    except Exception:
        logger.debug("The computer's timezone can't be read", exc_info=True)
        name = None
    return name if is_zone(name) else "UTC"


def zone(name: object) -> ZoneInfo:
    """The zone a schedule keeps its times in: its own, else the computer's."""
    return ZoneInfo(name if is_zone(name) else local_zone_name())


def _exists(wall: datetime, tz: tzinfo) -> bool:
    aware = wall.replace(tzinfo=tz, fold=0)
    return aware.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None, fold=0) == wall


def resolve(wall: datetime, tz: tzinfo) -> datetime:
    """The instant a wall-clock time means in *tz*, by the DST rules above."""
    wall = wall.replace(tzinfo=None, fold=0)
    probe = wall
    for _ in range(24 * 60):  # No zone skips more than a day.
        if _exists(probe, tz):
            return probe.replace(tzinfo=tz, fold=0)
        probe = (probe + timedelta(minutes=1)).replace(second=0, microsecond=0)
    return wall.replace(tzinfo=tz)


def _as_wall(value: datetime, tz: tzinfo) -> datetime:
    """An instant as its wall-clock time in *tz*, carried in UTC so the cron search sees no DST."""
    return value.astimezone(tz).replace(tzinfo=timezone.utc, fold=0)


class WallClockTrigger(BaseTrigger):
    """Fires at a cron pattern's wall-clock times in one zone, keeping them across DST.

    The pattern is searched on wall-clock times (as if the zone never changed),
    then each match is placed in the zone by ``resolve``. Searching on from the
    last fire's wall-clock time is what makes a repeated hour run once.
    """

    __slots__ = ("cron", "timezone")

    def __init__(self, cron: BaseTrigger, tz: tzinfo) -> None:
        self.cron = cron
        self.timezone = tz

    def get_next_fire_time(self, previous_fire_time: datetime | None, now: datetime) -> datetime | None:
        tz = self.timezone
        wall_now = _as_wall(now, tz)
        candidate = self.cron.get_next_fire_time(
            _as_wall(previous_fire_time, tz) if previous_fire_time else None, wall_now
        )
        for _ in range(1000):
            if candidate is None:
                return None
            fire = resolve(candidate.replace(tzinfo=None), tz)
            # Past the last fire; and, when Row-Bot starts inside a repeated hour, not a first
            # occurrence that has already gone by.
            if (previous_fire_time is None or fire > previous_fire_time) and (
                previous_fire_time is not None or fire >= now - timedelta(seconds=1)
            ):
                return fire
            candidate = self.cron.get_next_fire_time(candidate, candidate + timedelta(microseconds=1))
        return None

    def __str__(self) -> str:
        return f"{self.cron} in {self.timezone}"

    def __repr__(self) -> str:
        return f"<WallClockTrigger ({self.cron!r}, timezone={self.timezone!s})>"


def cron_trigger(**fields: object) -> BaseTrigger:
    """A cron pattern over wall-clock times (see ``WallClockTrigger``)."""
    from apscheduler.triggers.cron import CronTrigger

    return CronTrigger(timezone=timezone.utc, **fields)


_CRON_DAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat", "sun")
_NUMERIC_DAYS = re.compile(r"([0-7])(?:-([0-7]))?(?:/([1-7]))?")


def _standard_days(field: str) -> str:
    """A day-of-week field read as standard cron (0 or 7 Sunday, 1 Monday), in day names.

    APScheduler numbers days from Monday = 0, so "1-5" would run Tuesday to Saturday; people and
    models mean Monday to Friday. Numbers become names ("1-5" → "mon,tue,wed,thu,fri"); names,
    "*" and steps over "*" pass through.
    """
    parts = []
    for part in field.split(","):
        found = _NUMERIC_DAYS.fullmatch(part)
        if not found:
            parts.append(part)
            continue
        start = int(found[1])
        stop = int(found[2]) if found[2] else start
        step = int(found[3]) if found[3] else 1
        if stop < start:
            raise ValueError(f"day range {part} runs backwards")
        parts.extend(dict.fromkeys(_CRON_DAYS[day] for day in range(start, stop + 1, step)))
    return ",".join(dict.fromkeys(parts))


def crontab_trigger(expression: str) -> BaseTrigger:
    from apscheduler.triggers.cron import CronTrigger

    fields = expression.split()
    if len(fields) == 5:
        fields[4] = _standard_days(fields[4])
    return CronTrigger.from_crontab(" ".join(fields), timezone=timezone.utc)


def latest_fire_time(trigger: BaseTrigger, after: datetime, until: datetime) -> datetime | None:
    """The last time *trigger* fires in (after, until], or None.

    Searched back from *until* in growing windows, so a long time closed costs
    no more than the window that holds the last fire.
    """
    for span in (timedelta(hours=2), timedelta(days=2), timedelta(days=9), timedelta(days=40),
                 timedelta(days=400), until - after):
        start = max(after, until - span)
        latest = None
        fire = trigger.get_next_fire_time(None, start + timedelta(microseconds=1))
        for _ in range(50_000):
            if fire is None or fire > until:
                break
            if fire > after:
                latest = fire
            fire = trigger.get_next_fire_time(fire, fire + timedelta(microseconds=1))
        if latest is not None or start == after:
            return latest
    return None
