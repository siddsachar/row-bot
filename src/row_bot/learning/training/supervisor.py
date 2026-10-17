"""Runs personal-model training: one worker at a time, under the resource lease (L2a).

The supervisor owns the live worker (``worker.TrainingWorker``) and moves its job
through the state machine (``jobs.py``) as the worker reports. It applies the lease's
rules while a run is live (:meth:`TrainingSupervisor.watch`, about once a second on its
own thread, plus a wake-up whenever something changes):

- **Pause on use.** A chat on a cloud model, a workflow, reading documents, or the
  person sending something after training started: the worker is asked to pause, saves
  at its next optimizer step and exits ("Pausing after this step…" until it has).
- **A chat on a local model** needs the same graphics card: the worker is stopped at
  once and the job continues later from its newest checkpoint.
- **Learning paused** pauses it at the next step; **Learning off** stops it at once.
- **The person's Stop** stops it at once; the job keeps its newest checkpoint and waits
  for Continue or Discard. So does a job whose worker stopped unexpectedly, and one
  that was running when Row-Bot closed (``jobs.recover`` at the next start).

Starting is the runner's call (quiet hours, after Dream) or the person's (**Train now**,
which starts at once when nothing heavy is running and otherwise waits for it). Neither starts
while another app holds the graphics card's memory (L2c): training would spill out of it and crawl.
Nothing here runs while Learning is off or paused, or in a build without the preview flag.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from row_bot import learning as gate
from row_bot.learning import lease as lease_module
from row_bot.learning.contracts import Job
from row_bot.learning.lease import ResourceLease
from row_bot.learning.training import jobs as jobs_module
from row_bot.learning.training import protocol
from row_bot.learning.training.jobs import HELD, LIVE, JobStateError, TrainingJobs
from row_bot.learning.training.worker import Engine, NoEngine, TrainingWorker, WorkerError

logger = logging.getLogger(__name__)

WATCH_SECONDS = 1.0
# A pause waits for the step in progress (27–43 s at 16K on an 8 GB card, L0d); a worker
# that hasn't paused after this long is stopped (its newest checkpoint is kept).
PAUSE_DEADLINE_SECONDS = 15 * 60.0
LEASE_FILE = "lease.json"
RECOMMENDED_RECIPE: dict[str, Any] = {"name": "recommended", "settings": {"report_to": "none"}}


class TrainingError(RuntimeError):
    """A refused training action; ``code`` is stable (``learning_off``, ``learning_paused``,
    ``nothing_to_train``, ``training_changed``, ``gpu_busy``)."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _gpu_free() -> bool:
    from row_bot.learning.training import hardware

    return hardware.gpu_free_for_training()


class Dataset(Protocol):
    """Prepares a job's own data folder (its lease) and says how many examples it holds (L2c)."""

    def prepare(self, job: Job, folder: Path) -> int: ...


class NoDataset:
    """No dataset compiler yet (L2c): nothing can be prepared."""

    def prepare(self, job: Job, folder: Path) -> int:
        raise TrainingError("nothing_to_train")


@dataclass
class _Run:
    job_id: str
    worker: TrainingWorker
    token: int
    started: float  # monotonic
    resumed_from: int = 0
    pause_reason: str | None = None
    pause_requested_at: float | None = None
    refused_from: int | None = None  # the checkpoint step the worker couldn't resume from
    trigger: str = "quiet_hours"
    messages: list[str] = field(default_factory=list)


class TrainingSupervisor:
    """One live training worker at most, and the rules it runs under."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        store: Any = None,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        engine: Engine | None = None,
        dataset: Dataset | None = None,
        lease: ResourceLease | None = None,
        start_threads: bool = True,
        gpu_free: Callable[[], bool] | None = None,
    ) -> None:
        self._root = root
        self.jobs = TrainingJobs(root, store=store, clock=clock)
        self.clock = clock
        self.monotonic = monotonic
        self.engine: Engine = engine or NoEngine()
        self.dataset: Dataset = dataset or NoDataset()
        self.lease = lease or ResourceLease()
        # Whether the graphics card's memory is free to train now (another app may hold it, L2c).
        self.gpu_free: Callable[[], bool] = gpu_free or _gpu_free
        self._start_threads = start_threads
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._run: _Run | None = None
        self._token = 0
        self._wake = threading.Event()
        self._watch: threading.Thread | None = None
        self._closed = False

    # ── Reads ───────────────────────────────────────────────────────────

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else gate.data_dir()

    def live_job_id(self) -> str | None:
        with self._lock:
            return self._run.job_id if self._run is not None else None

    def live_worker(self) -> TrainingWorker | None:
        with self._lock:
            return self._run.worker if self._run is not None else None

    def training(self) -> bool:
        """A worker is running now (Buddy's line, the lease's holder)."""

        with self._lock:
            return self._run is not None

    def wait_for(self, predicate: Callable[[Job | None], bool], timeout: float = 10.0,
                 job_id: str | None = None) -> Job | None:
        """Wait until the job (the current one by default) satisfies ``predicate`` (tests, the fixture)."""

        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                job = self.jobs.get(job_id) if job_id else self.jobs.current()
                if predicate(job):
                    return job
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError("the training job didn't get there in time")
                self._changed.wait(min(left, 0.25))

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """Wait until no worker is live."""

        deadline = time.monotonic() + timeout
        with self._changed:
            while self._run is not None:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._changed.wait(min(left, 0.25))
        return True

    # ── The person's actions ────────────────────────────────────────────

    def _require_on(self) -> None:
        settings = gate.read_settings(self._root)
        if not settings.enabled:
            raise TrainingError("learning_off")
        if settings.paused:
            raise TrainingError("learning_paused")

    def _current(self, job_id: str | None) -> Job | None:
        job = self.jobs.current() if self.jobs.store.exists() else None
        if job_id and (job is None or job.id != job_id):
            raise TrainingError("training_changed")
        return job

    def train_now(self, job_id: str | None = None) -> Job:
        """Start or continue the current job now; when something heavy runs, as soon as it ends.

        Also the recovery prompt's Continue.
        """

        with self._lock:
            self._require_on()
            job = self._current(job_id)
            if job is not None and job.state in LIVE and self.live_job_id() != job.id:
                # Left running when Row-Bot closed: pause it at its newest checkpoint first.
                jobs_module.recover(self.jobs, live=lambda job_id: False)
                job = self.jobs.get(job.id)
            if job is not None and (job.state in LIVE or (job.state == "waiting" and job.trigger == "train_now")):
                return job
            if job is None or job.state not in ("planned", "waiting", "paused") or not self.engine.ready():
                raise TrainingError("nothing_to_train")
            if self.lease.read().busy or not self.gpu_free():
                moved = self.jobs.move(job.id, "waiting", reason="train_now", trigger="train_now")
                self._ensure_watch()
                self._notify()
                return moved
            return self._launch(job, "train_now")

    def stop(self, job_id: str | None = None) -> Job:
        """The person's Stop: training ends now and keeps its newest checkpoint (Continue or Discard)."""

        with self._lock:
            job = self._current(job_id)
            if job is None:
                raise TrainingError("training_changed")
            if self._run is not None and self._run.job_id == job.id:
                return self._end_run("you") or job
            if job.state == "waiting":
                back = "paused" if job.checkpoint_step is not None or job.steps_done else "planned"
                moved = self.jobs.move(job.id, back, reason="you",
                                       **({"pause_reason": "you"} if back == "paused" else {}))
                self._notify()
                return moved
            if job.state in LIVE:  # left from before a restart
                jobs_module.recover(self.jobs)
                return self.jobs.get(job.id) or job
            raise TrainingError("training_changed")

    def discard(self, job_id: str | None = None) -> Job:
        """Discard a stopped or paused run: its checkpoints and leased data are deleted."""

        with self._lock:
            job = self._current(job_id)
            if job is None or job.state not in ("paused", "waiting", "planned", *LIVE):
                raise TrainingError("training_changed")
            if self._run is not None and self._run.job_id == job.id:
                self._end_run("you")
            moved = self.jobs.move(job.id, "stopped", reason="discarded")
            jobs_module.remove_run(job.id, self.jobs.root)
            self._notify()
            return moved

    def use_recommended(self, job_id: str | None = None) -> Job:
        """A run that failed is planned again with the recommended settings (the next quiet hours,
        or Train now)."""

        with self._lock:
            self._require_on()
            job = self._current(job_id)
            if job is None or job.state != "failed":
                raise TrainingError("training_changed")
            planned = self.jobs.plan(
                steps_total=int(job.steps_total or 1),
                checkpoint_every=int(self.jobs.plan_of(job)["checkpoint_every"]),
                recipe=dict(RECOMMENDED_RECIPE),
                version=job.version,
            )
            jobs_module.remove_run(job.id, self.jobs.root)
            self._notify()
            return planned

    # ── Starting a run ──────────────────────────────────────────────────

    def start(self, job: Job, trigger: str) -> Job:
        """Start or resume ``job`` now (the runner's call, after its own checks)."""

        with self._lock:
            if self._run is not None or self._closed:
                raise TrainingError("training_changed")
            self._require_on()
            if not self.gpu_free():
                raise TrainingError("gpu_busy")
            return self._launch(job, trigger)

    def _lease_folder(self, job: Job) -> tuple[Path, int]:
        """The job's own data folder, prepared once (the dataset lease)."""

        run = jobs_module.run_dir(job.id, self.jobs.root)
        data = run / "data"
        record = run / LEASE_FILE
        try:
            return data, int(json.loads(record.read_text(encoding="utf-8"))["examples"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        data.mkdir(parents=True, exist_ok=True)
        examples = int(self.dataset.prepare(job, data))
        record.write_text(json.dumps({"examples": examples}), encoding="utf-8")
        return data, examples

    def _launch(self, job: Job, trigger: str, *, at_most: int | None = None) -> Job:
        run = jobs_module.run_dir(job.id, self.jobs.root)
        try:
            data, examples = self._lease_folder(job)
        except TrainingError:
            raise
        except Exception:
            logger.warning("Learning couldn't prepare a training run's data", exc_info=True)
            return self._fail(job, "data_unreadable")
        checkpoint, how = (None, None)
        if job.checkpoint_step is not None or job.steps_done or at_most is not None:
            checkpoint, how = jobs_module.resume_point(job.id, self.jobs.root, at_most=at_most)
        step = checkpoint.step if checkpoint is not None else 0
        plan = self.jobs.plan_of(job)
        spec = {
            "job_id": job.id,
            "version": int(job.version or 1),
            "steps_total": int(job.steps_total or 1),
            "checkpoint_every": max(1, min(int(plan["checkpoint_every"]), int(job.steps_total or 1))),
            "resume_from": protocol.checkpoint_name(step) if checkpoint is not None else None,
            "lease": {"dataset": str(data.resolve()), "examples": examples},
            "output": str((run / "checkpoints").resolve()),
            "recipe": plan["recipe"],
        }
        moved = self.jobs.move(job.id, "preparing", reason=trigger, trigger=trigger, steps_done=step,
                               checkpoint_step=step if checkpoint is not None else None, recovered=how)
        self._token += 1
        token = self._token
        try:
            command = self.engine.command(moved)
            worker = TrainingWorker(command, run, spec, on_message=partial(self._message, token),
                                    on_exit=partial(self._exit, token))
        except (WorkerError, protocol.ProtocolError) as error:
            logger.warning("Learning couldn't start training (%s)", getattr(error, "code", error))
            return self._fail(moved, "engine_error")
        self._run = _Run(job.id, worker, token, self.monotonic(), resumed_from=step, trigger=trigger)
        try:
            worker.start()
        except WorkerError as error:
            self._run = None
            logger.warning("Learning couldn't start the training worker (%s)", error.code)
            return self._fail(moved, "engine_error")
        lease_module.claim_training()
        self._ensure_watch()
        self._notify()
        return moved

    def _fail(self, job: Job, reason: str) -> Job:
        moved = self.jobs.move(job.id, "failed", reason=reason, error_code=reason)
        self._notify()
        return moved

    # ── Ending a run ────────────────────────────────────────────────────

    def _end_run(self, reason: str) -> Job | None:
        """Stop the live worker now; its job pauses at its newest good checkpoint."""

        run = self._run
        if run is None:
            return None
        self._run = None  # its messages and exit are now ignored
        run.worker.stop()
        lease_module.release_training()
        job = self.jobs.get(run.job_id)
        moved = job
        if job is not None and job.state in LIVE:
            checkpoint, how = jobs_module.resume_point(job.id, self.jobs.root)
            step = checkpoint.step if checkpoint is not None else 0
            moved = self.jobs.move(job.id, "paused", reason=reason, pause_reason=reason, steps_done=step,
                                   checkpoint_step=checkpoint.step if checkpoint is not None else None,
                                   recovered=how)
            logger.info("Learning stopped training at once (%s); it continues from step %d", reason, step)
        self._notify()
        return moved

    def request_pause(self, reason: str) -> Job | None:
        """Ask the live worker to pause at its next step (pause on use, Learning paused)."""

        with self._lock:
            run = self._run
            if run is None:
                return None
            job = self.jobs.get(run.job_id)
            if job is None:
                return None
            if job.state == "preparing":
                return self._end_run(reason)  # nothing to save yet
            if job.state != "running":
                return job
            run.pause_reason = reason
            run.pause_requested_at = self.clock()
            moved = self.jobs.move(job.id, "pausing", reason=reason, pause_reason=reason)
            run.worker.request_pause()
            self._notify()
            return moved

    def stop_now(self, reason: str) -> Job | None:
        """Stop the live worker at once (a local-model chat, Learning off, quitting)."""

        with self._lock:
            return self._end_run(reason)

    def shutdown(self) -> None:
        """Row-Bot is quitting: the worker and everything it started end now; the job will
        say "Training stopped when Row-Bot closed"."""

        with self._lock:
            self._closed = True
            self._end_run("app_closed")
            self._wake.set()

    # ── What the worker says ────────────────────────────────────────────

    def _notify(self, *, wake: bool = True) -> None:
        self._changed.notify_all()
        if wake:
            self._wake.set()

    def _message(self, token: int, message: dict[str, Any]) -> None:
        with self._lock:
            run = self._run
            if run is None or run.token != token:
                return
            kind = message["type"]
            run.messages.append(kind)
            job = self.jobs.get(run.job_id)
            if job is None:
                return
            now = float(self.clock())
            if kind == "started" and job.state == "preparing":
                job = self.jobs.move(job.id, "running", reason="started", started_at=job.started_at or now,
                                     progress_at=now)
            elif kind == "progress":
                # One write per step (a step takes tens of seconds on a real card).
                self.jobs.save(replace(job, steps_done=message["step"], loss=message["loss"],
                                       seconds_per_step=message["seconds_per_step"], progress_at=now,
                                       updated_at=max(now, job.updated_at)))
                self._notify(wake=False)
                return
            elif kind == "checkpoint":
                checkpoint = jobs_module.Checkpoint(message["step"], jobs_module.run_dir(job.id, self.jobs.root)
                                                    / "checkpoints" / message["name"])
                if jobs_module.good_checkpoint(checkpoint):
                    self.jobs.save(replace(job, checkpoint_step=message["step"], updated_at=max(now, job.updated_at)))
                    jobs_module.prune_checkpoints(job.id, self.jobs.root)
                else:
                    logger.warning("Learning skipped a checkpoint whose files don't match it (step %d)",
                                   message["step"])
                self._notify(wake=False)
                return
            elif kind == "artifacts":
                # What the finished run produced (names, sizes, SHA-256): the version's adapter is checked
                # against it before it is exported (L2c).
                try:
                    (jobs_module.run_dir(job.id, self.jobs.root) / "artifacts.json").write_text(
                        json.dumps({"files": message["files"]}, sort_keys=True), encoding="utf-8")
                except OSError:
                    logger.warning("Learning couldn't record what a training run produced")
                return
            elif kind == "paused" and job.state == "pausing":
                self.jobs.move(job.id, "paused", reason=run.pause_reason, pause_reason=run.pause_reason,
                               steps_done=message["step"], checkpoint_step=message["step"])
            elif kind == "finished" and job.state in ("running", "pausing"):
                self.jobs.move(job.id, "evaluating", reason="finished", finished_at=now,
                               steps_done=message["step"])
            elif kind == "error":
                if message["reason"] == "cannot_resume" and run.resumed_from:
                    run.refused_from = run.resumed_from  # handled once it has exited
                else:
                    self.jobs.move(job.id, "failed", reason=message["reason"], error_code=message["reason"])
            self._notify()

    def _exit(self, token: int, code: int | None, violation: str | None) -> None:
        with self._lock:
            run = self._run
            if run is None or run.token != token:
                return
            self._run = None
            lease_module.release_training()
            job = self.jobs.get(run.job_id)
            if job is None:
                self._notify()
                return
            try:
                if run.refused_from is not None and job.state in LIVE and not self._closed:
                    # The worker couldn't continue from that checkpoint: the one before it, or start over.
                    logger.warning("Learning's training couldn't continue from step %d; using an earlier one",
                                   run.refused_from)
                    back = self.jobs.move(job.id, "waiting", reason="cannot_resume")
                    self._launch(back, run.trigger, at_most=run.refused_from - 1)
                elif violation is not None and job.state in LIVE:
                    self._fail(job, "engine_error")
                elif job.state in LIVE:
                    # Exited without saying why: a crash. Keep what was saved; ask before continuing.
                    checkpoint, how = jobs_module.resume_point(job.id, self.jobs.root)
                    step = checkpoint.step if checkpoint is not None else 0
                    self.jobs.move(job.id, "paused", reason="worker_stopped", pause_reason="worker_stopped",
                                   steps_done=step, checkpoint_step=checkpoint.step if checkpoint else None,
                                   recovered=how)
                    logger.warning("Learning's training worker stopped unexpectedly (exit %s)", code)
            except JobStateError:
                logger.debug("Learning's training job moved meanwhile", exc_info=True)
            self._notify()

    # ── The lease, while a run is live ──────────────────────────────────

    def watch(self) -> bool:
        """Apply the lease's rules to the live run (and start a waiting Train now when free).

        Returns whether there is still something to watch.
        """

        with self._lock:
            if self._closed:
                return False
            settings = gate.read_settings(self._root)
            run = self._run
            if run is not None:
                job = self.jobs.get(run.job_id)
                if job is None:
                    self._end_run("learning_off")
                    return False
                if not settings.enabled or not gate.preview_enabled():
                    self._end_run("learning_off")
                    return False
                if settings.paused:
                    self.request_pause("learning_paused")
                    return True
                state = self.lease.read()
                if state.chat_local:
                    self._end_run("local_gpu")
                    return False
                used = state.busy or state.used_since(self.monotonic() - run.started)
                if used and job.state in ("running", "preparing"):
                    self.request_pause("in_use")
                elif (job.state == "pausing" and run.pause_requested_at is not None
                        and self.clock() - run.pause_requested_at > PAUSE_DEADLINE_SECONDS):
                    self._end_run(run.pause_reason or "in_use")
                    return False
                return self._run is not None
            if not self.jobs.store.exists():
                return False
            job = self.jobs.current()
            if job is None or job.state != "waiting" or job.trigger != "train_now":
                return False
            if not settings.enabled or settings.paused or not self.engine.ready():
                return False  # the runner takes it up again when Learning resumes
            if not self.lease.read().busy and self.gpu_free():
                self._launch(job, "train_now")
            return True

    def _ensure_watch(self) -> None:
        if not self._start_threads or self._closed:
            return
        if self._watch is not None and self._watch.is_alive():
            self._wake.set()
            return

        def loop() -> None:
            while True:
                self._wake.wait(WATCH_SECONDS)
                self._wake.clear()
                try:
                    keep = self.watch()
                except Exception:
                    logger.exception("Learning's training watch failed")
                    keep = True
                with self._lock:
                    if not keep or self._closed:
                        if self._watch is threading.current_thread():
                            self._watch = None
                        return

        self._watch = threading.Thread(target=loop, name="learning-training-watch", daemon=True)
        self._watch.start()


def held(job: Job | None) -> bool:
    """A paused job that waits for the person's Continue or Discard."""

    return job is not None and job.state == "paused" and job.pause_reason in HELD


__all__ = [
    "Dataset",
    "NoDataset",
    "PAUSE_DEADLINE_SECONDS",
    "RECOMMENDED_RECIPE",
    "TrainingError",
    "TrainingSupervisor",
    "WATCH_SECONDS",
    "held",
]
