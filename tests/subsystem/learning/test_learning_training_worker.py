"""Personal-model training with the fake trainer (L2a): the worker's whole life.

Train now starts an owned worker that reports progress and writes checkpoints (the newest
two kept); pause on use saves at the next step and exits; a chat on a local model stops it at
once and it continues from its newest checkpoint; a crash, running out of GPU memory, a
broken checkpoint and a refused resume each end in a plain state with a way out; Stop and
quitting end the worker's whole process tree; nothing it runs belongs to a conversation, and
it gets only its spec, its lease and an environment with telemetry off. The fake trainer is
driven step by step (no sleeps); every process here ends before its test does."""

from __future__ import annotations

import hashlib
import json
import sys
import time

import pytest

from row_bot.learning.store import LearningStore
from row_bot.learning.training import jobs as training_jobs
from row_bot.learning.training import supervisor as supervisor_module
from row_bot.learning.training.supervisor import TrainingError, TrainingSupervisor
from row_bot.learning.training.worker import TELEMETRY_ENV, EngineCommand, worker_environment
from tests.fixtures.learning import (
    FakeClock,
    FakeDataset,
    FakeEngine,
    FakeLease,
    process_alive,
    save_learning_settings,
    wait_for_file,
)

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

BASE_PYTHON = getattr(sys, "_base_executable", "") or sys.executable
CHECKPOINT_EVERY = 5


@pytest.fixture(autouse=True)
def _no_worker_outlives_its_test(monkeypatch):
    """Every training worker a test starts has ended (with its tree) when the test ends."""

    from row_bot.learning.training import worker as worker_module

    started: list[int] = []
    original = worker_module.TrainingWorker.start

    def start(self) -> None:
        original(self)
        started.append(self.pid)

    monkeypatch.setattr(worker_module.TrainingWorker, "start", start)
    yield
    left = [pid for pid in started if process_alive(pid)]
    assert left == [], f"training workers still running: {left}"

class Training:
    """A supervisor over the fake trainer, a fake lease, fake clocks and a planned job."""

    def __init__(self, root, clock, *, steps_total: int = 20, recipe: dict | None = None, validation: int = 0,
                 **engine_options) -> None:
        LearningStore(root).create()
        self.root = root
        self.clock = clock
        self.ticks = FakeClock(1000.0)
        self.lease = FakeLease()
        self.engine = FakeEngine(**engine_options)
        self.dataset = FakeDataset(validation=validation)
        self.supervisor = TrainingSupervisor(root=root, clock=clock, monotonic=self.ticks, engine=self.engine,
                                             dataset=self.dataset, lease=self.lease, start_threads=False)
        self.job = self.supervisor.jobs.plan(steps_total=steps_total, checkpoint_every=CHECKPOINT_EVERY, recipe=recipe)

    def current(self):
        return self.supervisor.jobs.get(self.job.id)

    def until(self, predicate, timeout: float = 15.0):
        return self.supervisor.wait_for(predicate, timeout=timeout, job_id=self.job.id)

    def advance(self, steps: int):
        """The worker takes ``steps`` more steps. A step that saves a checkpoint reports the step first and
        the checkpoint after it, so landing on one waits for the checkpoint too (and its pruning, recorded
        with it)."""

        worker = self.supervisor.live_worker()
        assert worker is not None, "no live worker"
        target = self.current().steps_done + steps
        saves = target % CHECKPOINT_EVERY == 0
        worker.advance(steps)
        return self.until(lambda job: job.state not in ("preparing", "running") or (
            job.steps_done >= target and (not saves or (job.checkpoint_step or 0) >= target)))

    def start(self):
        self.supervisor.train_now(self.job.id)
        return self.until(lambda job: job.state == "running")

    def checkpoints(self):
        return [item.step for item in training_jobs.checkpoints(self.job.id, self.root)]

    def stop(self):
        self.supervisor.shutdown()
        assert self.supervisor.wait_idle(15)


@pytest.fixture
def training(learning_on, clock):
    found = Training(learning_on, clock)
    yield found
    found.stop()


def _training(root, clock, **options) -> Training:
    return Training(root, clock, **options)


def test_train_now_runs_a_worker_that_reports_progress_and_keeps_the_newest_two_checkpoints(training):
    job = training.start()
    assert job.state == "running" and job.trigger == "train_now" and job.version == 1
    job = training.advance(7)
    assert (job.steps_done, job.checkpoint_step) == (7, 5)
    assert job.loss is not None and job.seconds_per_step == pytest.approx(3.6)
    training.advance(8)
    assert training.checkpoints() == [15, 10]  # the newest two
    training.supervisor.live_worker().advance(5)
    job = training.until(lambda job: job.state == "evaluating")
    assert job.steps_done == 20 and job.finished_at is not None
    assert [(item.state, item.reason) for item in job.transitions] == [
        ("planned", None), ("preparing", "train_now"), ("running", "started"), ("evaluating", "finished")]
    assert training.supervisor.wait_idle(10)
    assert (training_jobs.run_dir(job.id, training.root) / "checkpoints" / "adapter.bin").is_file()
    # It read its own prepared data, nothing else.
    assert training.dataset.prepared == [job.id]


# O119: three passes of 4 steps; each pass's adapter is saved and reported.
PASSES = {"name": "8gb-v1", "settings": {"report_to": "none", "passes": 3}}


@pytest.mark.parametrize("losses, validation, reported", [
    ([0.9, 0.5, 0.7], 2, [0.9, 0.5, 0.7]),  # the loss on the rows held back, for the model card
    ([], 0, [None, None, None]),  # no rows held back: none measured
])
def test_a_run_of_several_passes_saves_and_reports_each_passs_adapter(learning_on, clock, losses, validation,
                                                                       reported):
    training = _training(learning_on, clock, steps_total=12, recipe=PASSES, validation=validation, pace="free",
                         validation_losses=losses)
    try:
        training.supervisor.train_now(training.job.id)
        job = training.until(lambda job: job.state == "evaluating")
        assert (job.steps_done, job.steps_total) == (12, 12)  # every pass trains
        run = training_jobs.run_dir(job.id, training.root)
        record = json.loads((run / training_jobs.PASSES_FILE).read_text(encoding="utf-8"))
        assert record["losses"] == reported
        checkpoints = run / "checkpoints"
        saved = [(checkpoints / f"pass-{number}" / "adapter.bin").read_bytes() for number in (1, 2, 3)]
        assert len(set(saved)) == 3  # each pass's own adapter, as it was at that pass's end
        assert [[item["sha256"] for item in files] for files in record["files"]] == [
            [hashlib.sha256(item).hexdigest()] for item in saved]
        assert (checkpoints / "adapter.bin").read_bytes() == saved[2]  # the run's adapter is the last pass's
        assert training.supervisor.wait_idle(10)
    finally:
        training.stop()


def test_a_resumed_run_remembers_the_passes_before_it(learning_on, clock):
    training = _training(learning_on, clock, steps_total=12, recipe=PASSES, validation=2,
                         validation_losses=[0.5, 0.6, 0.4])
    try:
        training.start()
        training.advance(6)  # pass 1 measured at step 4
        training.supervisor.stop(training.job.id)  # continues from step 5's checkpoint
        training.supervisor.train_now(training.job.id)
        training.until(lambda job: job.state == "running")
        training.supervisor.live_worker().advance(7)
        job = training.until(lambda job: job.state == "evaluating")
        record = json.loads((training_jobs.run_dir(job.id, training.root) / training_jobs.PASSES_FILE).read_text(
            encoding="utf-8"))
        assert record["losses"] == [0.5, 0.6, 0.4] and len(record["files"]) == 3
        assert training.supervisor.wait_idle(10)
    finally:
        training.stop()


def test_the_worker_belongs_to_no_conversation_and_gets_only_its_spec_lease_and_a_clean_environment(
        training, monkeypatch):
    from row_bot import background_processes
    from row_bot.runtime.executions import generation_registry

    training.start()
    worker = training.supervisor.live_worker()
    run = training_jobs.run_dir(training.job.id, training.root)
    assert worker.spec["lease"]["dataset"] == str((run / "data").resolve())
    assert worker.spec["output"] == str((run / "checkpoints").resolve())
    # Not a generation, not a conversation's background process: no chat's Stop or delete reaches it,
    # and it never counts as the person using Row-Bot.
    assert generation_registry.active() == ()
    assert all(record.process_id != str(worker.pid) for record in background_processes.list_processes(None))
    argv = training.engine.commands[-1].argv
    assert argv[0] == BASE_PYTHON and argv[1] == "-I"  # isolated: no user site, no PYTHON* variables

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(training.root))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-synthetic-not-for-the-worker")
    monkeypatch.setenv("HF_TOKEN", "hf_synthetic")
    environment = worker_environment(run, {"ROW_BOT_DATA_DIR": "x", "HF_HUB_OFFLINE": "0", "CUDA_VISIBLE_DEVICES": "0"})
    assert "ROW_BOT_DATA_DIR" not in environment and "OPENAI_API_KEY" not in environment
    assert "HF_TOKEN" not in environment
    assert environment["CUDA_VISIBLE_DEVICES"] == "0"  # the engine's own variables pass
    for key, value in TELEMETRY_ENV.items():  # O27: telemetry off, never overridden by the engine
        assert environment[key] == value
    for key in ("HOME", "USERPROFILE", "TEMP", "TMP"):
        assert environment[key].startswith(str(run))


def test_pause_on_use_saves_at_the_next_step_and_the_worker_exits(training):
    training.start()
    training.advance(7)
    training.lease.set(chat_cloud=True)
    assert training.supervisor.watch() is True
    job = training.current()
    assert job.state == "pausing" and job.pause_reason == "in_use"  # "Pausing after this step…"
    # The step in progress finishes, then it saves and exits.
    training.supervisor.live_worker().advance(1)
    job = training.until(lambda job: job.state == "paused")
    assert (job.steps_done, job.checkpoint_step, job.pause_reason) == (8, 8, "in_use")
    assert training.supervisor.wait_idle(10)
    assert 8 in training.checkpoints()
    assert [item.state for item in job.transitions][-2:] == ["pausing", "paused"]


def test_a_pause_before_the_first_step_ends_the_worker_at_once_and_it_continues_from_the_same_checkpoint(training):
    # A worker loads the model before its first step (minutes on a real card): using Row-Bot then doesn't wait
    # for that step. Nothing is lost: it continues from the checkpoint it started from.
    training.start()
    training.lease.set(chat_cloud=True)
    assert training.supervisor.watch() is False  # nothing left to watch: it ended now
    job = training.current()
    assert (job.state, job.pause_reason, job.steps_done, job.checkpoint_step) == ("paused", "in_use", 0, None)
    assert training.supervisor.wait_idle(5)

    training.lease.set()
    training.supervisor.train_now(job.id)
    training.until(lambda job: job.state == "running")
    training.advance(7)
    training.supervisor.stop(training.job.id)  # paused by the person at step 5's checkpoint
    training.supervisor.train_now(training.job.id)
    job = training.until(lambda job: job.state == "running")
    assert job.steps_done == 5  # resumed, still loading the model

    training.lease.set(chat_cloud=True)
    assert training.supervisor.watch() is False
    job = training.current()
    assert (job.state, job.pause_reason, job.steps_done, job.checkpoint_step) == ("paused", "in_use", 5, 5)
    assert [item.state for item in job.transitions][-2:] == ["running", "paused"]  # never "Pausing after this step"
    assert training.supervisor.wait_idle(5)

    training.lease.set()
    training.supervisor.train_now(job.id)
    assert training.until(lambda job: job.state == "running").steps_done == 5
    assert training.advance(2).steps_done == 7


def test_only_a_run_that_hasnt_trained_a_step_takes_the_recipe_as_it_is_now(training):
    training.start()
    assert training.dataset.started == [training.job.id]
    training.advance(7)
    training.supervisor.stop(training.job.id)
    training.supervisor.train_now(training.job.id)
    training.until(lambda job: job.state == "running")
    assert training.dataset.started == [training.job.id]  # it trained steps: it keeps its own spec


def test_the_person_using_row_bot_after_training_started_counts_but_earlier_use_does_not(training):
    training.lease.set(since_activity=30.0)  # they pressed Train now half a minute after chatting
    training.start()
    training.advance(1)
    assert training.supervisor.watch() is True
    assert training.current().state == "running"
    training.ticks.advance(60)  # a minute later they send a message
    training.lease.set(since_activity=5.0)
    training.supervisor.watch()
    assert training.current().state == "pausing"


def test_a_chat_on_a_local_model_stops_training_at_once_and_it_continues_from_the_newest_checkpoint(training):
    training.start()
    training.advance(7)
    training.lease.set(chat_local=True)
    assert training.supervisor.watch() is False  # nothing left to watch: it stopped now
    job = training.current()
    assert (job.state, job.pause_reason, job.checkpoint_step, job.steps_done) == ("paused", "local_gpu", 5, 5)
    assert training.supervisor.training() is False

    training.lease.set()
    job = training.supervisor.train_now(job.id)
    job = training.until(lambda job: job.state == "running")
    assert job.steps_done == 5  # continued from step 5
    job = training.advance(3)
    assert job.steps_done == 8


def test_learning_off_stops_training_at_once_and_learning_paused_pauses_it_at_the_next_step(training):
    training.start()
    training.advance(3)
    save_learning_settings(training.root, paused=True)
    training.supervisor.watch()
    assert training.current().state == "pausing"
    training.supervisor.live_worker().advance(1)
    job = training.until(lambda job: job.state == "paused")
    assert job.pause_reason == "learning_paused" and job.checkpoint_step == 4

    save_learning_settings(training.root, paused=False)
    training.supervisor.train_now()
    training.until(lambda job: job.state == "running")
    save_learning_settings(training.root, enabled=False)
    assert training.supervisor.watch() is False
    job = training.current()
    assert job.state == "paused" and job.pause_reason == "learning_off" and not training.supervisor.training()


def test_a_crash_mid_step_keeps_the_newest_checkpoint_and_waits_for_continue(learning_on, clock):
    training = _training(learning_on, clock, crash_at_step=8)
    try:
        training.start()
        training.supervisor.live_worker().advance(10)
        job = training.until(lambda job: job.state == "paused")
        assert (job.pause_reason, job.checkpoint_step, job.steps_done) == ("worker_stopped", 5, 5)
        assert supervisor_module.held(job)  # Continue or Discard
        assert training.supervisor.wait_idle(10)
    finally:
        training.stop()


def test_out_of_gpu_memory_fails_with_its_reason_and_recommended_settings_plan_it_again(learning_on, clock):
    training = _training(learning_on, clock, oom_at_step=3)
    try:
        training.start()
        training.supervisor.live_worker().advance(5)
        job = training.until(lambda job: job.state == "failed")
        assert job.error_code == "out_of_gpu_memory"
        assert training.supervisor.wait_idle(10)
        again = training.supervisor.use_recommended(job.id)
        assert again.state == "planned" and again.version == job.version and again.id != job.id
        assert training.supervisor.jobs.current().id == again.id
        assert not training_jobs.run_dir(job.id, training.root).exists()
    finally:
        training.stop()


def test_a_broken_newest_checkpoint_falls_back_to_the_one_before_or_to_starting_over(training):
    training.start()
    training.advance(10)
    training.supervisor.stop(training.job.id)
    assert training.checkpoints() == [10, 5]
    newest = training_jobs.run_dir(training.job.id, training.root) / "checkpoints" / "checkpoint-10" / "weights.bin"
    newest.write_bytes(b"not what the manifest says")

    training.supervisor.train_now(training.job.id)
    job = training.until(lambda job: job.state == "running")
    assert (job.steps_done, job.recovered) == (5, "previous_checkpoint")
    assert training.checkpoints() == [5]
    training.supervisor.stop(job.id)
    for item in training_jobs.checkpoints(job.id, training.root):
        (item.path / "state.json").unlink()  # every checkpoint broken
    training.supervisor.train_now(job.id)
    job = training.until(lambda job: job.state == "running")
    assert (job.steps_done, job.recovered) == (0, "start_over")


def test_a_worker_that_refuses_to_resume_goes_back_a_checkpoint_then_starts_over(learning_on, clock):
    training = _training(learning_on, clock)
    try:
        training.start()
        training.advance(10)
        training.supervisor.stop(training.job.id)
        training.engine.options["refuse_resume"] = True
        training.supervisor.train_now(training.job.id)
        # Refused at 10, then at 5: it starts over (a fresh start isn't a resume).
        job = training.until(lambda job: job.state == "running" and job.recovered == "start_over")
        assert job.steps_done == 0 and training.checkpoints() == []
        assert [command.name for command in training.engine.commands] == ["fake"] * 4
    finally:
        training.stop()


def _ended(*pids: int, seconds: float = 5.0) -> bool:
    """Every process has ended. Windows ends a job's processes asynchronously once it is
    terminated, so a grandchild may take a moment after the worker itself is gone."""

    deadline = time.monotonic() + seconds
    while any(process_alive(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not any(process_alive(pid) for pid in pids)


def test_stop_and_quitting_end_the_workers_whole_process_tree(learning_on, clock):
    training = _training(learning_on, clock, spawn_child=True)
    try:
        training.start()
        work = training_jobs.run_dir(training.job.id, training.root) / "work"
        child = int(wait_for_file(work / "child.pid"))
        worker = training.supervisor.live_worker()
        assert process_alive(child) and process_alive(worker.pid)
        job = training.supervisor.stop(training.job.id)  # the person's Stop
        assert (job.state, job.pause_reason) == ("paused", "you")
        assert not process_alive(worker.pid) and _ended(child)

        (work / "child.pid").unlink()
        training.supervisor.train_now(job.id)
        training.until(lambda job: job.state == "running")
        child = int(wait_for_file(work / "child.pid"))
        worker = training.supervisor.live_worker()
        training.supervisor.shutdown()  # Row-Bot quits
        assert not process_alive(worker.pid) and _ended(child)
        assert training.current().pause_reason == "app_closed"
    finally:
        training.stop()


HOST = """
import json, os, sys, threading
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from row_bot.learning.training.worker import TrainingWorker
from tests.fixtures.learning import FakeEngine

run = Path(sys.argv[1])
(run / "data").mkdir(parents=True)
(run / "data" / "examples.jsonl").write_text("{}\\n", encoding="utf-8")
spec = {"job_id": "job_crash", "version": 1, "steps_total": 10, "checkpoint_every": 5, "resume_from": None,
        "lease": {"dataset": str((run / "data").resolve()), "examples": 1},
        "output": str((run / "checkpoints").resolve()), "recipe": {"name": "recommended", "settings": {}}}
started = threading.Event()
worker = TrainingWorker(FakeEngine(spawn_child=True).command(None), run, spec,
                        on_message=lambda message: message["type"] == "started" and started.set(),
                        on_exit=lambda *args: None)
worker.start()
started.wait(20)
child = run / "work" / "child.pid"
while not child.is_file() or not child.read_text().strip():
    threading.Event().wait(0.02)
print(json.dumps({"worker": worker.pid, "child": int(child.read_text())}), flush=True)
os._exit(9)  # Row-Bot crashes: no stop, no quit
"""


def test_when_row_bot_crashes_the_worker_and_everything_it_started_end_too(tmp_path):
    import json
    import subprocess
    import time
    from pathlib import Path

    host = tmp_path / "host.py"
    host.write_text(HOST, encoding="utf-8")
    root = Path(__file__).resolve().parents[3]
    environment = {**__import__("os").environ, "ROW_BOT_DATA_DIR": str(tmp_path / "data")}
    result = subprocess.run([sys.executable, str(host), str(tmp_path / "run"), str(root)], capture_output=True,
                            text=True, timeout=60, env=environment)
    assert result.returncode == 9, result.stderr  # it crashed after its worker and child were running
    pids = json.loads(result.stdout.strip().splitlines()[-1])
    assert pids["worker"] != pids["child"]
    deadline = time.monotonic() + 10
    while any(process_alive(pid) for pid in pids.values()) and time.monotonic() < deadline:
        time.sleep(0.05)  # the OS ends the tree as the crashed host's handles close
    assert {name: process_alive(pid) for name, pid in pids.items()} == {"worker": False, "child": False}


def test_a_worker_that_breaks_the_protocol_is_ended_and_its_run_fails(learning_on, clock):
    class Garbage(FakeEngine):
        def command(self, job):
            script = ("import sys, threading; sys.stdout.write('not the protocol\\n'); sys.stdout.flush(); "
                      "threading.Event().wait()")
            return EngineCommand(argv=(BASE_PYTHON, "-I", "-c", script), name="garbage")

    training = _training(learning_on, clock)
    training.supervisor.engine = Garbage()
    try:
        training.supervisor.train_now(training.job.id)
        job = training.until(lambda job: job.state == "failed")
        assert job.error_code == "engine_error"
        assert training.supervisor.wait_idle(10)
        assert [(item.state, item.reason) for item in job.transitions][-1] == ("failed", "engine_error")
    finally:
        training.stop()


def test_a_virtual_environments_launcher_is_never_used_as_the_workers_interpreter_on_windows(tmp_path):
    from row_bot.learning.training.worker import venv_launcher

    scripts = tmp_path / "engine" / ("Scripts" if sys.platform == "win32" else "bin")
    scripts.mkdir(parents=True)
    (tmp_path / "engine" / "pyvenv.cfg").write_text("home = C:\\Python313\n", encoding="utf-8")
    launcher = scripts / "python.exe"
    assert venv_launcher(str(launcher)) is (sys.platform == "win32")
    assert venv_launcher(BASE_PYTHON) is False


def test_a_worker_that_never_pauses_is_stopped_after_the_deadline(training):
    training.start()
    training.advance(6)
    training.lease.set(workflow=True)
    training.supervisor.watch()
    assert training.current().state == "pausing"
    training.clock.advance(supervisor_module.PAUSE_DEADLINE_SECONDS + 1)  # its step never ends
    assert training.supervisor.watch() is False
    job = training.current()
    assert (job.state, job.pause_reason, job.checkpoint_step) == ("paused", "in_use", 5)


def test_train_now_waits_while_something_heavy_runs_and_stop_cancels_the_wait(training):
    training.lease.set(indexing=True)
    job = training.supervisor.train_now(training.job.id)
    assert job.state == "waiting" and training.supervisor.training() is False
    assert training.supervisor.watch() is True  # still waiting
    training.lease.set()
    training.supervisor.watch()
    training.until(lambda job: job.state == "running")
    training.advance(5)
    training.supervisor.stop(training.job.id)
    training.lease.set(chat_cloud=True)
    job = training.supervisor.train_now(training.job.id)
    assert job.state == "waiting"
    job = training.supervisor.stop(job.id)
    assert job.state == "paused" and job.pause_reason == "you"


def test_discard_deletes_the_runs_checkpoints_and_leased_data(training):
    training.start()
    training.advance(6)
    job = training.supervisor.stop(training.job.id)
    run = training_jobs.run_dir(job.id, training.root)
    assert (run / "data" / "examples.jsonl").is_file()
    job = training.supervisor.discard(job.id)
    assert job.state == "stopped" and not run.exists()
    assert training.supervisor.jobs.current() is None
    with pytest.raises(TrainingError) as error:
        training.supervisor.train_now()
    assert error.value.code == "nothing_to_train"


def test_nothing_trains_without_an_engine_while_learning_is_paused_or_off(learning_on, clock):
    training = _training(learning_on, clock)
    try:
        training.engine.is_ready = False
        with pytest.raises(TrainingError) as error:
            training.supervisor.train_now()
        assert error.value.code == "nothing_to_train"
        training.engine.is_ready = True
        save_learning_settings(training.root, paused=True)
        with pytest.raises(TrainingError) as error:
            training.supervisor.train_now()
        assert error.value.code == "learning_paused"
        save_learning_settings(training.root, paused=False, enabled=False)
        with pytest.raises(TrainingError) as error:
            training.supervisor.train_now()
        assert error.value.code == "learning_off"
        assert training.engine.commands == [] and training.current().state == "planned"
    finally:
        training.stop()
