"""The long-running app's heap and log (P2, P11).

On the owner's build the event loop stalled 2-3 s about 400 times a day: full garbage collections over a 5 GB
heap. Start-up's objects now leave the collector's walk, the scheduler's per-run INFO lines (two thirds of
row_bot.log) are gone, and an hourly line says what the heap holds.
"""
from __future__ import annotations

import gc
import logging
import threading

import pytest

pytestmark = pytest.mark.subsystem


@pytest.fixture
def scheduler(monkeypatch):
    from row_bot import tasks

    monkeypatch.setattr(tasks, "_scheduler", None)
    started = tasks._get_scheduler()
    yield started
    started.shutdown(wait=True)


def test_a_scheduled_job_runs_without_writing_info_lines(scheduler, caplog) -> None:
    from apscheduler.events import EVENT_JOB_EXECUTED

    caplog.set_level(logging.INFO)
    finished = threading.Event()
    scheduler.add_listener(lambda _event: finished.set(), EVENT_JOB_EXECUTED)
    scheduler.add_job(lambda: None, id="quiet-job")

    assert finished.wait(10), "the job ran"
    scheduler_lines = [record for record in caplog.records if record.name.startswith("apscheduler")]
    assert not [record for record in scheduler_lines if record.levelno < logging.WARNING]


def test_a_failing_scheduled_job_still_reaches_the_log(scheduler, caplog) -> None:
    from apscheduler.events import EVENT_JOB_ERROR

    caplog.set_level(logging.INFO)
    failed = threading.Event()
    scheduler.add_listener(lambda _event: failed.set(), EVENT_JOB_ERROR)

    def broken() -> None:
        raise RuntimeError("the job broke")

    scheduler.add_job(broken, id="broken-job")

    assert failed.wait(10)
    assert any(record.name.startswith("apscheduler") and record.levelno >= logging.ERROR
               for record in caplog.records)


class _Leaf:
    __slots__ = ("box",)

    def __init__(self) -> None:
        self.box: list = []  # tracked by the collector: a list inside


def test_start_up_objects_are_frozen_out_of_later_collections(caplog) -> None:
    from row_bot.stability import freeze_start_up_heap

    caplog.set_level(logging.INFO, logger="row_bot.stability")
    made_at_start = _Leaf()
    try:
        freeze_start_up_heap()
        assert not any(item is made_at_start for item in gc.get_objects()), "a full collection no longer walks it"
        made_later = _Leaf()
        assert any(item is made_later for item in gc.get_objects())
    finally:
        gc.unfreeze()
    assert any(record.getMessage().startswith("perf: start-up heap frozen objects=") for record in caplog.records)


def test_the_object_line_names_the_commonest_types_and_the_kept_agent_graphs(caplog) -> None:
    import row_bot.agent as agent
    from row_bot.stability import log_object_counts

    caplog.set_level(logging.INFO, logger="row_bot.stability")
    many = [_Leaf() for _ in range(300_000)]
    log_object_counts()
    del many

    line = next(record.getMessage() for record in caplog.records if record.getMessage().startswith("perf: objects"))
    fields = dict(part.split("=", 1) for part in line.removeprefix("perf: objects ").split(" "))
    assert int(fields["tracked"]) > 300_000
    assert int(fields["agent_graphs"]) == agent.agent_graph_cache_size()
    top = [entry.rsplit(":", 1) for entry in fields["top"].split(",")]
    assert len(top) == 8
    assert ["_Leaf", "300000"] in [[name.rsplit(".", 1)[-1], count] for name, count in top]
