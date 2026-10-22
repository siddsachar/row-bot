"""Simulated time for the seeded runs (S1): days move only when told, nights run in order on demand.

Row-Bot's own quiet hours, runner and lease take their clocks from outside; the simulation gives them
one simulated clock, so "after Dream", "in quiet hours" and "nobody used Row-Bot for five minutes" are
simulated, and a 30-day run takes hours, not a month.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from row_bot.learning.lease import ResourceLease
from row_bot.learning.runner import LearningRunner
from row_bot.learning.store import LearningStore
from row_bot.learning.training.supervisor import TrainingSupervisor
from tests.e2e.learning.seeded.clock import NIGHT_STEPS, Night, SimClock, sim_quiet_hours
from tests.fixtures.learning import FakeDataset, FakeEngine

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

FIRST = date(2026, 11, 2)


def test_the_clock_moves_only_forward_and_only_when_told():
    clock = SimClock(FIRST)
    assert clock.now() == datetime(2026, 11, 2, 8, 30) and clock.day == 1
    assert clock.request_time(1, 2) == datetime(2026, 11, 2, 11, 40)
    clock.advance(minutes=10)
    assert clock.request_time(1, 1) == datetime(2026, 11, 2, 11, 50)  # later than its slot: now
    with pytest.raises(ValueError):
        clock.set(1, 9)
    with pytest.raises(ValueError):
        clock.advance(minutes=-1)
    clock.set(3, 1)
    assert clock.day == 3 and clock.time() == datetime(2026, 11, 4, 1, 0).timestamp()


def test_idle_time_is_simulated_and_the_lease_reads_it():
    clock = SimClock(FIRST)
    lease = ResourceLease(generations=lambda: (), workflows=lambda: False, indexing=lambda: False,
                          dreaming=lambda: False, extracting=lambda: False, since_activity=clock.idle_seconds)
    assert clock.idle_seconds() is None and lease.read().free
    clock.used()
    assert lease.read().recently_used and not lease.read().free
    clock.advance(minutes=4)
    assert not lease.read().free
    clock.advance(minutes=2)  # six simulated minutes, no real time
    assert lease.read().free and clock.idle_seconds() == 360.0


def test_a_saved_clock_comes_back_never_earlier():
    clock = SimClock(FIRST)
    clock.set(5, 14)
    clock.used()
    clock.dreamed = date(2026, 11, 6)
    again = SimClock(FIRST)
    again.restore(clock.to_dict())
    assert again.to_dict() == clock.to_dict()


def test_quiet_hours_follow_the_persons_dream_window_on_the_simulated_clock(learning_on):
    clock = SimClock(FIRST)
    hours = sim_quiet_hours(clock, learning_on)
    assert not hours.inside()  # 08:30
    clock.set(2, 1, 30)
    assert hours.inside()
    (learning_on / "dream_config.json").write_text(json.dumps({"window_start": 2, "window_end": 4}), encoding="utf-8")
    assert not hours.inside()
    clock.set(2, 2, 15)
    assert hours.inside()


def test_a_night_runs_dream_then_the_vault_then_learning_and_goes_on_after_a_failure():
    clock = SimClock(FIRST)
    order: list[str] = []

    def dream():
        order.append("dream")
        return {"merged": 0}

    def vault():
        order.append("vault")
        raise RuntimeError("no store")

    def learning():
        order.append("learning")
        return {"trained": False}

    night = Night(clock, {"learning": learning, "vault": vault, "dream": dream})
    done = night.run(1)
    assert order == list(NIGHT_STEPS) == ["dream", "vault", "learning"]
    assert [step.name for step in done] == order
    assert done[1].error == "RuntimeError: no store" and done[2].outcome == {"trained": False}
    assert done[0].at.startswith("2026-11-03T01:00") and clock.dreamed == date(2026, 11, 3)
    with pytest.raises(ValueError):
        Night(clock, {"cleanup": lambda: None}).run(2)


def test_training_waits_for_the_nights_dream_step_and_for_nobody_using_row_bot(learning_on, clock):
    LearningStore(learning_on).create()
    sim = SimClock(FIRST)
    lease = ResourceLease(generations=lambda: (), workflows=lambda: False, indexing=lambda: False,
                          dreaming=lambda: False, extracting=lambda: False, since_activity=sim.idle_seconds)
    supervisor = TrainingSupervisor(root=learning_on, clock=clock, engine=FakeEngine(), dataset=FakeDataset(),
                                    lease=lease, start_threads=False)
    runner = LearningRunner(supervisor, root=learning_on, hours=sim_quiet_hours(sim, learning_on))
    try:
        supervisor.jobs.plan(steps_total=4, checkpoint_every=2)
        sim.used()  # the day's last request
        assert (runner.status().state, runner.status().reason) == ("scheduled", "quiet_hours")
        seen: list[tuple[str, str | None]] = []
        night = Night(sim, {"dream": lambda: seen.append(("dream", runner.status().reason)),
                            "learning": lambda: seen.append(("learning", runner.status().reason))})
        night.run(1)
        # During Dream, training waits for it; after it (and hours without use) it is due.
        assert seen == [("dream", "after_dream"), ("learning", "now")]
        sim.used()  # someone wrote at night
        assert (runner.status().state, runner.status().reason) == ("waiting", "in_use")
        sim.advance(minutes=6)
        assert runner.status().reason == "now"
    finally:
        supervisor.shutdown()
