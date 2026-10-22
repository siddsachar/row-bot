"""Simulated time for the seeded runs: 30 days and their quiet hours in hours (S1).

Row-Bot's own code already takes its clocks and probes from outside: the quiet hours
(``runner.QuietHours(now=…)``), the runner (``LearningRunner(hours=…)``), the planner and supervisor
(``clock=``), the resource lease (``ResourceLease(since_activity=…)``) and use and undo (``clock=``).
The simulation hands them one :class:`SimClock`:

- the **day** moves only when the simulation says so: a request is asked at its simulated time, and
  the person's last message is remembered on that clock, so "nobody used Row-Bot for five minutes"
  is five *simulated* minutes;
- each **night** (:class:`Night`) runs the quiet-hours work **on demand, in order**: Dream Cycle first,
  then the audit vault's chronological refresh (fresh hidden problems arrive as the days pass), then
  Learning's runner, which trains, makes a version ready and compares it the way it would at night;
- :class:`SimQuietHours` is Row-Bot's own :class:`~row_bot.learning.runner.QuietHours` (the person's
  Dream window, read from their data folder) on the simulated clock; "Dream has run tonight" is this
  night's Dream step, not a journal entry stamped with the real time.

Nothing here imports Row-Bot until a :class:`SimQuietHours` is made.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

#: When the person's day starts and how far apart their requests are (simulated).
DAY_START = (8, 30)
REQUEST_GAP_MINUTES = 95
#: Nights start at Dream's default window (01:00).
NIGHT_HOUR = 1
# L3a: the night's look for things to improve (issues, a diagnosis each, drafts) comes after the vault's
# refresh and before training, as in the product's runner.
NIGHT_STEPS = ("dream", "vault", "look", "learning")


class SimClock:
    """A simulated wall clock: ``now()`` moves only when told to."""

    def __init__(self, first_day: date, *, day: int = 1, hour: int = DAY_START[0], minute: int = DAY_START[1]) -> None:
        self.first_day = first_day
        self._now = datetime.combine(first_day, datetime.min.time()) + timedelta(days=day - 1, hours=hour,
                                                                                 minutes=minute)
        self.last_use: datetime | None = None
        # The night whose Dream step finished (the date the night began on).
        self.dreamed: date | None = None

    # ── Reading ─────────────────────────────────────────────────────────

    def now(self) -> datetime:
        return self._now

    def time(self) -> float:
        """Epoch seconds of the simulated time (for collaborators that take ``clock=``)."""

        return self._now.timestamp()

    @property
    def day(self) -> int:
        """The simulated day number of the date now (1 is the first live day)."""

        return (self._now.date() - self.first_day).days + 1

    def idle_seconds(self) -> float | None:
        """Simulated seconds since the person's last message (None before the first)."""

        if self.last_use is None:
            return None
        return max(0.0, (self._now - self.last_use).total_seconds())

    # ── Moving ──────────────────────────────────────────────────────────

    def set(self, day: int, hour: int, minute: int = 0) -> datetime:
        """Move to ``day`` at ``hour:minute``; never backwards."""

        target = datetime.combine(self.first_day, datetime.min.time()) + timedelta(days=day - 1, hours=hour,
                                                                                   minutes=minute)
        if target < self._now:
            raise ValueError(f"the simulated clock doesn't go back ({target} < {self._now})")
        self._now = target
        return self._now

    def advance(self, *, minutes: float = 0.0, seconds: float = 0.0) -> datetime:
        if minutes < 0 or seconds < 0:
            raise ValueError("the simulated clock doesn't go back")
        self._now += timedelta(minutes=minutes, seconds=seconds)
        return self._now

    def request_time(self, day: int, order: int) -> datetime:
        """When the ``order``-th request of ``day`` is asked (later if the clock is past it)."""

        hour, minute = DAY_START
        target = datetime.combine(self.first_day, datetime.min.time()) + timedelta(
            days=day - 1, hours=hour, minutes=minute + order * REQUEST_GAP_MINUTES)
        self._now = max(self._now, target)
        return self._now

    def used(self) -> None:
        """The person sent something now."""

        self.last_use = self._now

    def to_dict(self) -> dict[str, Any]:
        return {"now": self._now.isoformat(), "last_use": self.last_use.isoformat() if self.last_use else None,
                "dreamed": self.dreamed.isoformat() if self.dreamed else None}

    def restore(self, saved: dict[str, Any] | None) -> None:
        """Back to a saved state (a resumed run): never earlier than now."""

        if not saved:
            return
        moment = datetime.fromisoformat(str(saved["now"]))
        self._now = max(self._now, moment)
        if saved.get("last_use"):
            self.last_use = datetime.fromisoformat(str(saved["last_use"]))
        if saved.get("dreamed"):
            self.dreamed = date.fromisoformat(str(saved["dreamed"]))


def sim_quiet_hours(clock: SimClock, root: Path | None) -> Any:
    """Row-Bot's quiet hours (the person's Dream window) on the simulated clock."""

    from row_bot.learning.runner import QuietHours

    class SimQuietHours(QuietHours):
        """``dream_done``: tonight's Dream step finished (or Dream is off for this person)."""

        def dream_done(self) -> bool:
            began = self.started_at()
            _, _, enabled = self.window()
            if not enabled or began is None:
                return True
            return clock.dreamed is not None and clock.dreamed == began.date()

    return SimQuietHours(root, now=clock.now)


@dataclass
class NightStep:
    """One step of a night: its name, what it said, and when (simulated and real)."""

    name: str
    at: str
    seconds: float
    outcome: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "at": self.at, "seconds": round(self.seconds, 1), "outcome": self.outcome,
                "error": self.error}


@dataclass
class Night:
    """The quiet-hours work of one night, on demand and in order (:data:`NIGHT_STEPS`)."""

    clock: SimClock
    steps: dict[str, Callable[[], Any]]
    done: list[NightStep] = field(default_factory=list)
    monotonic: Callable[[], float] = time.monotonic

    def run(self, day: int, *, only: tuple[str, ...] = NIGHT_STEPS) -> list[NightStep]:
        """The night after ``day``: the clock moves to that night's start, then each step runs in order.

        A step that fails is recorded and the next one still runs (Row-Bot's own runner doesn't wait
        on Dream for ever either). Dream's step marks the night dreamed even when Dream had nothing to do.
        """

        unknown = [name for name in self.steps if name not in NIGHT_STEPS]
        if unknown:
            raise ValueError(f"unknown night steps {unknown}")
        self.clock.set(day + 1, NIGHT_HOUR)
        night_of = self.clock.now().date()
        found: list[NightStep] = []
        for name in NIGHT_STEPS:
            if name not in only or name not in self.steps:
                continue
            started = self.monotonic()
            at = self.clock.now().isoformat()
            try:
                outcome, error = self.steps[name](), None
            except Exception as exc:  # noqa: BLE001 - recorded; the night goes on
                outcome, error = None, f"{type(exc).__name__}: {str(exc)[:200]}"
            if name == "dream":
                self.clock.dreamed = night_of
            found.append(NightStep(name, at, self.monotonic() - started, outcome, error))
            self.clock.advance(minutes=5)
        self.done.extend(found)
        return found


__all__ = ["DAY_START", "NIGHT_HOUR", "NIGHT_STEPS", "Night", "NightStep", "REQUEST_GAP_MINUTES", "SimClock",
           "sim_quiet_hours"]
