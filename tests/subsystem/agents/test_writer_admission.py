from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from row_bot.cancellation import CancellationScope, use_cancellation_scope

pytestmark = pytest.mark.subsystem


@pytest.fixture
def dispatcher(monkeypatch):
    from row_bot import agent_runner as runner, agent_runs, agent_settings

    monkeypatch.setattr(
        runner, "_DISPATCH_CONDITION", threading.Condition(threading.RLock())
    )
    monkeypatch.setattr(runner, "_DISPATCH_QUEUE", [])
    monkeypatch.setattr(runner, "_DISPATCH_ACTIVE", {})
    monkeypatch.setattr(runner, "_DISPATCH_WRITER_KEYS", {})
    settings = SimpleNamespace(max_concurrent_children=8, max_active_children_global=2)
    locks, statuses, released = {}, {}, []
    monkeypatch.setattr(agent_settings, "load_agent_runtime_settings", lambda: settings)
    monkeypatch.setattr(agent_runs, "get_agent_write_lock", lambda key: locks.get(key))

    def acquire(key, run, **kwargs):
        if key in locks:
            return False
        locks[key] = {"run_id": run}
        return True

    def release(*, run_id):
        released.append(run_id)
        for key in [key for key, value in locks.items() if value["run_id"] == run_id]:
            del locks[key]

    monkeypatch.setattr(agent_runs, "acquire_agent_write_lock", acquire)
    monkeypatch.setattr(agent_runs, "release_agent_write_lock", release)
    monkeypatch.setattr(
        agent_runs,
        "update_agent_status",
        lambda run, *args: statuses.setdefault(run, threading.Event()).set(),
    )
    return SimpleNamespace(
        runner=runner,
        runs=agent_runs,
        settings=settings,
        locks=locks,
        statuses=statuses,
        released=released,
        acquire=acquire,
        release=release,
    )


@pytest.mark.parametrize("failure", ["settings", "status", "read", "acquire"])
def test_failed_admission_removes_reservation_and_only_its_committed_lock(
    dispatcher, monkeypatch, failure
):
    d = dispatcher

    class Failure(Exception):
        pass

    def fail(*args, **kwargs):
        raise Failure()

    if failure == "settings":
        monkeypatch.setattr("row_bot.agent_settings.load_agent_runtime_settings", fail)
    elif failure == "status":
        d.settings.max_active_children_global = 0
        monkeypatch.setattr(d.runs, "update_agent_status", fail)
    elif failure == "read":
        monkeypatch.setattr(d.runs, "get_agent_write_lock", fail)
    else:

        def commit_then_fail(key, run, **kwargs):
            d.acquire(key, run, **kwargs)
            raise Failure()

        monkeypatch.setattr(d.runs, "acquire_agent_write_lock", commit_then_fail)
    with pytest.raises(Failure):
        d.runner._acquire_child_capacity(
            "failed", "parent", threading.Event(), write_lock_key="A"
        )
    assert d.runner._DISPATCH_QUEUE == []
    assert d.runner._DISPATCH_WRITER_KEYS == {}
    assert d.runner._DISPATCH_ACTIVE == {}
    assert d.locks == {}


def test_stop_during_lock_acquisition_never_commits_capacity(dispatcher, monkeypatch):
    d = dispatcher
    stop = threading.Event()

    def acquire(key, run, **kwargs):
        assert d.acquire(key, run, **kwargs)
        stop.set()
        return True

    monkeypatch.setattr(d.runs, "acquire_agent_write_lock", acquire)
    assert not d.runner._acquire_child_capacity(
        "stopped", "parent", stop, write_lock_key="A"
    )
    assert d.runner._DISPATCH_QUEUE == []
    assert d.runner._DISPATCH_ACTIVE == {}
    assert d.runner._DISPATCH_WRITER_KEYS == {}
    assert d.locks == {}
    assert d.released == ["stopped"]


@pytest.fixture
def waiters(dispatcher):
    workers = []

    def start(run_id, *, parent="parent", key="A"):
        scope = CancellationScope()
        queued = dispatcher.statuses.setdefault(run_id, threading.Event())
        finished = threading.Event()
        result, errors = [], []

        def work():
            try:
                with use_cancellation_scope(scope):
                    result.append(
                        dispatcher.runner._acquire_child_capacity(
                            run_id,
                            parent,
                            scope.stop_event,
                            write_lock_key=key,
                        )
                    )
            except BaseException as exc:
                errors.append(exc)
            finally:
                finished.set()

        worker = threading.Thread(target=work, daemon=True)
        state = SimpleNamespace(
            scope=scope,
            queued=queued,
            finished=finished,
            result=result,
            errors=errors,
            worker=worker,
        )
        workers.append(state)
        worker.start()
        return state

    yield start
    for state in workers:
        state.scope.cancel()
    for state in workers:
        state.worker.join(2)
        assert not state.worker.is_alive(), "Admission waiter did not drain"
        assert state.errors == []


def _finish(dispatcher, run_id):
    dispatcher.release(run_id=run_id)
    dispatcher.runner._release_child_capacity(run_id)


def test_contested_writer_waves_preserve_fifo_and_independent_capacity(
    dispatcher, waiters
):
    d = dispatcher
    assert d.runner._acquire_child_capacity(
        "A0", "parent", threading.Event(), write_lock_key="A"
    )
    queued = []
    for index in range(24):
        state = waiters(f"A{index + 1}")
        assert state.queued.wait(2)
        queued.append(state)
    assert d.runner.child_dispatch_state()["active"] == 1
    assert d.runner.child_dispatch_state()["queued"] == 24

    for key in ("B", "C", ""):
        assert d.runner._acquire_child_capacity(
            "independent", "other", threading.Event(), write_lock_key=key
        )
        assert len(d.runner._DISPATCH_ACTIVE) == 2
        _finish(d, "independent")

    # Remove both a head and a middle reservation without releasing A0's lock.
    for index in (0, 12):
        queued[index].scope.cancel()
        assert queued[index].finished.wait(2)
        assert queued[index].result == [False]
    assert d.locks == {"A": {"run_id": "A0"}}
    _finish(d, "A0")
    for index, state in enumerate(queued):
        if index in (0, 12):
            continue
        assert state.finished.wait(2)
        assert state.result == [True]
        assert d.locks == {"A": {"run_id": f"A{index + 1}"}}
        assert d.runner._DISPATCH_ACTIVE == {f"A{index + 1}": "parent"}
        assert all(
            not later.finished.is_set()
            for later_index, later in enumerate(queued)
            if later_index > index and later_index not in (0, 12)
        )
        _finish(d, f"A{index + 1}")
    assert d.runner._DISPATCH_QUEUE == []
    assert d.runner._DISPATCH_WRITER_KEYS == {}


def test_saturated_parent_does_not_block_other_parent(dispatcher, waiters):
    d = dispatcher
    d.settings.max_concurrent_children = 1
    assert d.runner._acquire_child_capacity("parent-owner", "parent", threading.Event())
    earlier = waiters("earlier", key="")
    assert earlier.queued.wait(2)
    later = waiters("later", key="")
    assert later.queued.wait(2)
    assert d.runner._acquire_child_capacity("other-owner", "other", threading.Event())
    _finish(d, "other-owner")
    _finish(d, "parent-owner")
    assert earlier.finished.wait(2)
    assert earlier.result == [True]
    assert not later.finished.is_set()
    _finish(d, "earlier")
    assert later.finished.wait(2)
    assert later.result == [True]
    _finish(d, "later")


def test_scope_cancellation_wakes_without_poll_and_unregisters(
    dispatcher, waiters, monkeypatch
):
    d = dispatcher
    d.locks["A"] = {"run_id": "external"}
    condition = d.runner._DISPATCH_CONDITION
    waiting = threading.Event()
    original_wait = condition.wait

    def wait_without_timeout(timeout=None):
        waiting.set()
        return original_wait()

    monkeypatch.setattr(condition, "wait", wait_without_timeout)
    waiter = waiters("cancelled")
    try:
        assert waiting.wait(2)
        waiter.scope.cancel()
        assert waiter.finished.wait(2)
        assert waiter.result == [False]
        assert waiter.scope._callbacks == []
        assert d.runner._DISPATCH_QUEUE == []
        assert d.runner._DISPATCH_ACTIVE == {}
        assert d.locks == {"A": {"run_id": "external"}}
    finally:
        d.runner.notify_agent_runtime_settings_changed()


def test_stop_after_admission_keeps_capacity_until_producer_finishes(dispatcher):
    d = dispatcher
    scope = CancellationScope()
    with use_cancellation_scope(scope):
        assert d.runner._acquire_child_capacity(
            "active", "parent", scope.stop_event, write_lock_key="A"
        )
    assert scope._callbacks == []
    scope.cancel()
    assert d.runner._DISPATCH_ACTIVE == {"active": "parent"}
    assert d.locks == {"A": {"run_id": "active"}}
    _finish(d, "active")
    assert d.runner._DISPATCH_ACTIVE == {}
    assert d.locks == {}


@pytest.mark.parametrize("active", [False, True])
def test_duplicate_reservation_cannot_corrupt_original(dispatcher, waiters, active):
    d = dispatcher
    if active:
        assert d.runner._acquire_child_capacity(
            "original", "parent", threading.Event(), write_lock_key="A"
        )
    else:
        d.locks["A"] = {"run_id": "external"}
        waiter = waiters("original")
        assert waiter.queued.wait(2)
    before = (
        list(d.runner._DISPATCH_QUEUE),
        dict(d.runner._DISPATCH_ACTIVE),
        dict(d.locks),
    )
    with pytest.raises(
        d.runner.AgentRunnerError, match="already has a dispatch reservation"
    ):
        d.runner._acquire_child_capacity(
            "original", "different", threading.Event(), write_lock_key="B"
        )
    assert (d.runner._DISPATCH_QUEUE, d.runner._DISPATCH_ACTIVE, d.locks) == before
    assert d.runner._DISPATCH_WRITER_KEYS == {"original": "A"}


def test_acquisition_failure_cannot_release_competing_writer(dispatcher, monkeypatch):
    d = dispatcher

    def competitor_wins_then_failure(key, run, **kwargs):
        d.locks[key] = {"run_id": "competitor"}
        raise RuntimeError("Acquisition failed")

    monkeypatch.setattr(
        d.runs, "acquire_agent_write_lock", competitor_wins_then_failure
    )
    with pytest.raises(RuntimeError, match="Acquisition failed"):
        d.runner._acquire_child_capacity(
            "failed", "parent", threading.Event(), write_lock_key="A"
        )
    assert d.locks == {"A": {"run_id": "competitor"}}
    assert d.runner._DISPATCH_QUEUE == []
    assert d.runner._DISPATCH_ACTIVE == {}


def test_cleanup_failure_still_removes_reservation(dispatcher, monkeypatch):
    d = dispatcher
    stop = threading.Event()

    def acquire_then_stop(key, run, **kwargs):
        d.acquire(key, run, **kwargs)
        stop.set()
        return True

    def release_then_failure(*, run_id):
        d.release(run_id=run_id)
        raise RuntimeError("Release event failed")

    monkeypatch.setattr(d.runs, "acquire_agent_write_lock", acquire_then_stop)
    monkeypatch.setattr(d.runs, "release_agent_write_lock", release_then_failure)
    with pytest.raises(RuntimeError, match="Release event failed"):
        d.runner._acquire_child_capacity("stopped", "parent", stop, write_lock_key="A")
    assert d.runner._DISPATCH_QUEUE == []
    assert d.runner._DISPATCH_WRITER_KEYS == {}
    assert d.runner._DISPATCH_ACTIVE == {}
    assert d.locks == {}


def test_writer_race_does_not_reserve_capacity(dispatcher, waiters, monkeypatch):
    d = dispatcher
    lost_race = threading.Event()

    def competing_writer_wins(key, run, **kwargs):
        if not lost_race.is_set():
            d.locks[key] = {"run_id": "external"}
            lost_race.set()
            return False
        return d.acquire(key, run, **kwargs)

    monkeypatch.setattr(d.runs, "acquire_agent_write_lock", competing_writer_wins)
    waiting = waiters("waiting")
    assert lost_race.wait(2)
    assert d.runner._acquire_child_capacity(
        "independent", "other", threading.Event(), write_lock_key="B"
    )
    assert d.runner._DISPATCH_ACTIVE == {"independent": "other"}
    assert d.released == []
    _finish(d, "external")
    assert waiting.finished.wait(2)
    assert waiting.result == [True]
    assert d.locks == {"A": {"run_id": "waiting"}, "B": {"run_id": "independent"}}
    _finish(d, "waiting")
    _finish(d, "independent")


def test_capacity_change_wakes_earliest_eligible_without_poll(
    dispatcher, waiters, monkeypatch
):
    d = dispatcher
    d.settings.max_active_children_global = 0
    condition = d.runner._DISPATCH_CONDITION
    original_wait = condition.wait
    monkeypatch.setattr(condition, "wait", lambda timeout=None: original_wait())
    first = waiters("first", key="A")
    assert first.queued.wait(2)
    second = waiters("second", key="B")
    assert second.queued.wait(2)
    d.settings.max_active_children_global = 1
    d.runner.notify_agent_runtime_settings_changed()
    assert first.finished.wait(2)
    assert first.result == [True]
    assert not second.finished.is_set()
    _finish(d, "first")
    assert second.finished.wait(2)
    assert second.result == [True]
    _finish(d, "second")


@pytest.mark.parametrize("failure", ["event-publication", "stop"])
def test_real_writer_commit_is_released_before_failed_admission_returns(
    tmp_path, monkeypatch, failure
):
    from tests.fixtures.tasks import fresh_tasks_module

    fresh_tasks_module(tmp_path, monkeypatch)
    from row_bot import agent_runner as runner, agent_runs, agent_settings

    monkeypatch.setattr(
        runner, "_DISPATCH_CONDITION", threading.Condition(threading.RLock())
    )
    monkeypatch.setattr(runner, "_DISPATCH_QUEUE", [])
    monkeypatch.setattr(runner, "_DISPATCH_ACTIVE", {})
    monkeypatch.setattr(runner, "_DISPATCH_WRITER_KEYS", {})
    monkeypatch.setattr(
        agent_settings,
        "load_agent_runtime_settings",
        lambda: agent_settings.AgentRuntimeSettings(
            max_concurrent_children=2,
            max_active_children_global=2,
        ),
    )
    agent_runs.create_agent_run(run_id="failed", thread_id="synthetic-thread")
    agent_runs.create_agent_run(run_id="successor", thread_id="synthetic-thread")
    original_append = agent_runs.append_agent_event
    stop = threading.Event()
    observed = []

    def publish(run_id, event_type, *args, **kwargs):
        if run_id == "failed" and event_type == "write_lock.acquired":
            observed.append(
                agent_runs.get_agent_write_lock("synthetic-workspace")["run_id"]
            )
            if failure == "event-publication":
                raise RuntimeError("Synthetic event publication failure")
            stop.set()
        return original_append(run_id, event_type, *args, **kwargs)

    monkeypatch.setattr(agent_runs, "append_agent_event", publish)
    if failure == "event-publication":
        with pytest.raises(RuntimeError, match="Synthetic event publication failure"):
            runner._acquire_child_capacity(
                "failed", "parent", stop, write_lock_key="synthetic-workspace"
            )
    else:
        assert not runner._acquire_child_capacity(
            "failed", "parent", stop, write_lock_key="synthetic-workspace"
        )
    assert observed == ["failed"]
    assert agent_runs.get_agent_write_lock("synthetic-workspace") is None
    assert runner.child_dispatch_state()["active"] == 0
    assert runner.child_dispatch_state()["queued"] == 0
    assert runner._DISPATCH_WRITER_KEYS == {}
    assert runner._acquire_child_capacity(
        "successor",
        "parent",
        threading.Event(),
        write_lock_key="synthetic-workspace",
    )
    assert (
        agent_runs.get_agent_write_lock("synthetic-workspace")["run_id"] == "successor"
    )
    agent_runs.release_agent_write_lock(run_id="successor")
    runner._release_child_capacity("successor")


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("failure", ["release-event", "waiter-notification"])
def test_worker_teardown_drains_capacity_after_real_release_failure(
    tmp_path,
    monkeypatch,
    resume,
    failure,
):
    from tests.fixtures.tasks import fresh_tasks_module

    fresh_tasks_module(tmp_path, monkeypatch)
    from row_bot import agent_runner as runner, agent_runs

    monkeypatch.setattr(
        runner, "_DISPATCH_CONDITION", threading.Condition(threading.RLock())
    )
    monkeypatch.setattr(runner, "_DISPATCH_QUEUE", [])
    monkeypatch.setattr(runner, "_DISPATCH_ACTIVE", {})
    monkeypatch.setattr(runner, "_DISPATCH_WRITER_KEYS", {})
    monkeypatch.setattr(runner, "_ACTIVE_AGENT_RUNS", {"worker": {}})
    agent_runs.create_agent_run(
        run_id="worker",
        thread_id="synthetic-thread",
        write_lock_key="synthetic-workspace",
    )
    original_append = agent_runs.append_agent_event
    notifications, released = [], []

    def publish(run_id, event_type, *args, **kwargs):
        if event_type == "write_lock.released":
            assert agent_runs.get_agent_write_lock("synthetic-workspace") is None
            released.append(run_id)
            if failure == "release-event":
                raise RuntimeError("Synthetic teardown failure")
        return original_append(run_id, event_type, *args, **kwargs)

    def notify(run_id):
        notifications.append(run_id)
        if failure == "waiter-notification":
            raise RuntimeError("Synthetic teardown failure")

    def invoke(*args, **kwargs):
        assert runner._DISPATCH_ACTIVE == {"worker": "top-level"}
        assert (
            agent_runs.get_agent_write_lock("synthetic-workspace")["run_id"] == "worker"
        )
        return "Synthetic work complete"

    monkeypatch.setattr(agent_runs, "append_agent_event", publish)
    monkeypatch.setattr(runner, "_invoke_agent", invoke)
    monkeypatch.setattr(runner, "_resume_invoke_agent", invoke)
    monkeypatch.setattr(runner, "_notify_child_agent_waiters", notify)
    with pytest.raises(RuntimeError, match="Synthetic teardown failure"):
        if resume:
            runner._resume_agent_thread("worker", [], {}, [], threading.Event())
        else:
            runner._run_agent_thread(
                "worker",
                "Synthetic work",
                [],
                {},
                threading.Event(),
                requires_write_lock=True,
                write_lock_key="synthetic-workspace",
            )
    assert released == ["worker"]
    assert notifications == ["worker"]
    assert agent_runs.get_agent_run("worker")["status"] == "completed"
    assert agent_runs.get_agent_write_lock("synthetic-workspace") is None
    assert runner._DISPATCH_QUEUE == []
    assert runner._DISPATCH_WRITER_KEYS == {}
    assert runner._DISPATCH_ACTIVE == {}
    assert runner._ACTIVE_AGENT_RUNS == {}


@pytest.mark.parametrize("failure", ["finish", "notification"])
def test_rejected_entry_cleans_reservation_even_if_domain_cleanup_fails(
    dispatcher,
    monkeypatch,
    failure,
):
    d = dispatcher
    d.runner._DISPATCH_QUEUE.append(("rejected", "parent"))
    d.runner._DISPATCH_WRITER_KEYS["rejected"] = "A"
    d.runner._DISPATCH_ACTIVE["other"] = "other-parent"
    monkeypatch.setattr(d.runner, "_ACTIVE_AGENT_RUNS", {"rejected": {}, "other": {}})
    calls = []

    def finish(*args, **kwargs):
        calls.append("finish")
        if failure == "finish":
            raise RuntimeError("Synthetic entry cleanup failure")

    def notify(*args, **kwargs):
        calls.append("notification")
        if failure == "notification":
            raise RuntimeError("Synthetic entry cleanup failure")

    monkeypatch.setattr(d.runs, "finish_agent_run", finish)
    monkeypatch.setattr(d.runner, "_notify_child_agent_waiters", notify)
    with pytest.raises(RuntimeError, match="Synthetic entry cleanup failure"):
        d.runner._agent_entry_failed("rejected", InterruptedError())
    assert calls == ["finish", "notification"]
    assert d.runner._DISPATCH_QUEUE == []
    assert d.runner._DISPATCH_WRITER_KEYS == {}
    assert d.runner._DISPATCH_ACTIVE == {"other": "other-parent"}
    assert d.runner._ACTIVE_AGENT_RUNS == {"other": {}}
