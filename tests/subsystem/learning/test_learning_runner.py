"""The quiet-hours runner and the resource lease (L2a).

Training starts only in quiet hours (Dream Cycle's), after Dream, with Train during quiet
hours on, on this computer, with an engine and a job, and only when nothing heavy runs and
nobody used Row-Bot for a few minutes; a run paused after Train now continues as soon as the
computer is free; stopped runs wait for the person. Until L2b and L2c there is no engine or
dataset, and the runner says so. The lease reads chats (local or cloud), workflows, document
reading, Dream and the person's activity, and Dream waits while training holds it."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from row_bot.learning import lease as lease_module
from row_bot.learning.lease import LeaseState, ResourceLease
from row_bot.learning.runner import DREAM_GRACE_SECONDS, LearningRunner, QuietHours
from row_bot.learning.store import LearningStore
from row_bot.learning.training.supervisor import TrainingSupervisor
from tests.fixtures.learning import FakeDataset, FakeEngine, FakeLease, save_learning_settings

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

NIGHT = datetime(2026, 10, 16, 2, 30)  # inside the default quiet hours, 1:00–5:00
DAY = datetime(2026, 10, 16, 14, 0)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def setup(learning_on, clock):
    LearningStore(learning_on).create()
    when = Clock(NIGHT)
    lease = FakeLease()
    engine = FakeEngine()
    supervisor = TrainingSupervisor(root=learning_on, clock=clock, engine=engine, dataset=FakeDataset(),
                                    lease=lease, start_threads=False)
    runner = LearningRunner(supervisor, root=learning_on, hours=QuietHours(learning_on, now=when))
    found = SimpleNamespace(root=learning_on, when=when, lease=lease, engine=engine, supervisor=supervisor,
                            runner=runner)
    yield found
    supervisor.shutdown()
    supervisor.wait_idle(15)


def _journal(root, moment: datetime) -> None:
    (root / "dream_journal.json").write_text(json.dumps([{"timestamp": moment.isoformat(), "summary": "ok"}]),
                                             encoding="utf-8")


def _plan(setup):
    return setup.supervisor.jobs.plan(steps_total=10, checkpoint_every=5)


def test_with_no_engine_or_no_data_the_runner_finds_nothing_to_do_and_says_why(setup):
    setup.engine.is_ready = False
    assert (setup.runner.status().state, setup.runner.status().reason) == ("nothing_to_do", "no_engine")
    setup.engine.is_ready = True
    assert setup.runner.status().reason == "no_data"
    save_learning_settings(setup.root, train_in_quiet_hours=False)
    assert setup.runner.status().reason == "train_off"
    save_learning_settings(setup.root, train_in_quiet_hours=True, train_where="hugging_face")
    assert setup.runner.status().reason == "hugging_face"
    assert setup.runner.check().state == "nothing_to_do"
    assert setup.engine.commands == []


def test_a_planned_run_waits_for_quiet_hours_and_dream_then_starts(setup):
    _plan(setup)
    setup.when.now = DAY
    assert (setup.runner.status().state, setup.runner.status().reason) == ("scheduled", "quiet_hours")
    setup.when.now = datetime(2026, 10, 16, 1, 20)  # 20 minutes in; Dream hasn't run tonight
    assert setup.runner.status().reason == "after_dream"
    _journal(setup.root, datetime(2026, 10, 15, 1, 40))  # last night's Dream doesn't count
    assert setup.runner.status().reason == "after_dream"
    _journal(setup.root, datetime(2026, 10, 16, 1, 10))
    assert (setup.runner.status().state, setup.runner.status().reason) == ("scheduled", "now")
    setup.lease.set(since_activity=60.0)  # someone used Row-Bot a minute ago
    assert (setup.runner.status().state, setup.runner.status().reason) == ("waiting", "in_use")
    setup.lease.set(since_activity=600.0)
    assert setup.runner.check().state == "training"
    job = setup.supervisor.wait_for(lambda job: job.state == "running")
    assert job.trigger == "quiet_hours"
    assert setup.runner.status().state == "training"


def test_dream_running_holds_training_back_and_an_overdue_dream_does_not(setup, monkeypatch):
    _plan(setup)
    dream = SimpleNamespace(is_dreaming=lambda: True)
    monkeypatch.setitem(sys.modules, "row_bot.dream_cycle", dream)
    _journal(setup.root, datetime(2026, 10, 16, 1, 10))
    assert setup.runner.status().reason == "after_dream"
    dream.is_dreaming = lambda: False
    (setup.root / "dream_journal.json").unlink()
    setup.when.now = datetime(2026, 10, 16, 1, 0) + timedelta(seconds=DREAM_GRACE_SECONDS)
    assert setup.runner.status().reason == "now"
    (setup.root / "dream_config.json").write_text(json.dumps({"enabled": False}), encoding="utf-8")
    setup.when.now = datetime(2026, 10, 16, 1, 5)
    assert setup.runner.status().reason == "now"  # Dream is off: nothing to wait for


def test_quiet_hours_that_wrap_midnight(setup):
    (setup.root / "dream_config.json").write_text(json.dumps({"window_start": 23, "window_end": 3}),
                                                  encoding="utf-8")
    hours = setup.runner.hours
    for moment, inside in ((datetime(2026, 10, 16, 23, 30), True), (datetime(2026, 10, 17, 2, 0), True),
                           (datetime(2026, 10, 17, 3, 0), False), (datetime(2026, 10, 16, 12, 0), False)):
        setup.when.now = moment
        assert hours.inside() is inside
    setup.when.now = datetime(2026, 10, 17, 0, 30)
    assert hours.started_at() == datetime(2026, 10, 16, 23, 0)


def test_a_run_paused_after_train_now_continues_once_the_computer_is_free_at_any_hour(setup):
    job = _plan(setup)
    setup.when.now = DAY
    setup.supervisor.train_now(job.id)
    setup.supervisor.wait_for(lambda job: job.state == "running")
    setup.supervisor.live_worker().advance(5)
    setup.supervisor.wait_for(lambda job: job.steps_done == 5)
    setup.lease.set(chat_cloud=True)
    setup.supervisor.watch()
    setup.supervisor.live_worker().advance(1)
    job = setup.supervisor.wait_for(lambda job: job.state == "paused")
    assert setup.supervisor.wait_idle(10)
    setup.lease.set(since_activity=30.0)
    assert (setup.runner.check().state, setup.runner.status().reason) == ("waiting", "in_use")
    setup.lease.set(since_activity=900.0)
    assert setup.runner.check().state == "training"
    job = setup.supervisor.wait_for(lambda job: job.state == "running")
    assert job.steps_done == 6


def test_training_waits_while_another_app_holds_the_graphics_cards_memory(setup):
    job = _plan(setup)
    _journal(setup.root, datetime(2026, 10, 16, 1, 10))
    setup.lease.set(since_activity=900.0)
    card = {"free": False}  # a local model still loaded in Ollama, say (L2c)
    setup.supervisor.gpu_free = lambda: card["free"]

    assert (setup.runner.status().state, setup.runner.status().reason) == ("waiting", "gpu_busy")
    assert (setup.runner.check().state, setup.runner.check().reason) == ("waiting", "gpu_busy")
    assert setup.engine.commands == [] and setup.supervisor.jobs.get(job.id).state == "planned"
    setup.when.now = DAY
    waiting = setup.supervisor.train_now(job.id)  # Train now waits for the card too
    assert waiting.state == "waiting" and setup.engine.commands == []

    card["free"] = True
    setup.supervisor.watch()
    assert setup.supervisor.wait_for(lambda job: job.state == "running").trigger == "train_now"


def test_train_now_waiting_for_a_chat_starts_once_it_ends_even_after_a_restart(setup):
    job = _plan(setup)
    setup.when.now = DAY
    setup.supervisor.jobs.move(job.id, "waiting", reason="train_now", trigger="train_now")  # then Row-Bot restarted
    setup.lease.set(chat_cloud=True)
    assert (setup.runner.check().state, setup.runner.status().reason) == ("waiting", "chat")
    assert setup.engine.commands == []
    setup.lease.set(since_activity=20.0)  # the chat ended; Train now doesn't wait for quiet
    assert setup.runner.check().state == "training"
    assert setup.supervisor.wait_for(lambda job: job.state == "running").trigger == "train_now"


def test_runs_stopped_for_the_person_wait_for_them_and_failures_show_their_reason(setup):
    job = _plan(setup)
    moved = setup.supervisor.jobs.move(job.id, "preparing")
    moved = setup.supervisor.jobs.move(moved.id, "paused", pause_reason="app_closed")
    assert (setup.runner.check().state, setup.runner.status().reason) == ("held", "app_closed")
    assert setup.engine.commands == []
    moved = setup.supervisor.jobs.move(moved.id, "failed", error_code="out_of_gpu_memory")
    assert (setup.runner.status().state, setup.runner.status().reason) == ("failed", "out_of_gpu_memory")


@pytest.mark.parametrize("state", ["off", "paused", "without_the_preview_flag"])
def test_nothing_starts_while_learning_is_off_paused_or_without_the_preview_flag(setup, state, monkeypatch):
    from tests.fixtures.learning import set_learning_preview

    _plan(setup)
    _journal(setup.root, datetime(2026, 10, 16, 1, 10))
    if state == "off":
        save_learning_settings(setup.root, enabled=False)
    elif state == "paused":
        save_learning_settings(setup.root, paused=True)
    else:
        set_learning_preview(monkeypatch, False)
    assert setup.runner.check().state in ("off", "paused")
    assert setup.engine.commands == [] and setup.supervisor.training() is False


# ── The lease ───────────────────────────────────────────────────────────────


def test_the_lease_reads_chats_by_where_their_model_runs_and_the_other_holders():
    handles = [SimpleNamespace(model_ref="model:ollama:qwen3.5:2b")]
    flags = {"workflows": False, "indexing": False, "dreaming": False}
    calls = {"indexing": 0}

    def indexing() -> bool:
        calls["indexing"] += 1
        return flags["indexing"]

    ticks = [0.0]
    lease = ResourceLease(
        generations=lambda: list(handles), is_local=lambda ref: ref.startswith("model:ollama:"),
        workflows=lambda: flags["workflows"], indexing=indexing, dreaming=lambda: flags["dreaming"],
        since_activity=lambda: 400.0, clock=lambda: ticks[0],
    )
    state = lease.read()
    assert state.chat_local and not state.chat_cloud and state.busy and state.holder == "chat"
    handles[0] = SimpleNamespace(model_ref="model:openai:gpt-5.6-sol")
    state = lease.read()
    assert state.chat_cloud and not state.chat_local
    handles.clear()
    flags.update(workflows=True)
    assert lease.read().holder == "workflow"
    flags.update(workflows=False, dreaming=True)
    assert lease.read().holder == "dream"
    flags.update(dreaming=False, indexing=True)
    assert lease.read().indexing is False  # read at most every few seconds
    ticks[0] = 6.0
    assert lease.read().holder == "indexing"
    assert calls["indexing"] == 2
    assert LeaseState(since_activity=400.0).free and not LeaseState(since_activity=60.0).free
    assert LeaseState(since_activity=None).free  # unknown activity doesn't block training


def test_a_chat_whose_model_is_unknown_counts_as_local_so_training_stops_for_it(monkeypatch):
    monkeypatch.setattr(lease_module, "_default_model_ref", lambda: "")
    assert lease_module.model_is_local("") is False  # no model chosen: no chat can run
    monkeypatch.setattr(lease_module, "_default_model_ref", lambda: "model:ollama:qwen3.5:2b")
    assert lease_module.model_is_local("") is True  # the default model is local
    assert lease_module.model_is_local("model:ollama:qwen3:8b") is True


def test_dream_waits_while_training_holds_the_lease(monkeypatch):
    import row_bot.dream_cycle as dream_cycle
    from row_bot.runtime import executions

    monkeypatch.setattr(dream_cycle, "is_enabled", lambda: True)
    monkeypatch.setattr(dream_cycle, "_already_ran_today", lambda: False)
    monkeypatch.setattr(dream_cycle, "_in_dream_window", lambda: True)
    monkeypatch.setattr(dream_cycle, "_is_idle", lambda: True)
    monkeypatch.setattr(dream_cycle, "_model_chosen", lambda: True)
    monkeypatch.setattr(executions.generation_registry, "active", lambda conversation_id="": ())
    assert dream_cycle._should_dream() is True
    lease_module.claim_training()
    try:
        assert dream_cycle._should_dream() is False
    finally:
        lease_module.release_training()
    assert dream_cycle._should_dream() is True
    assert dream_cycle.is_dreaming() is False
