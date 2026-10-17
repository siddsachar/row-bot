"""Learning's quiet-hours runner (L2a; L3a adds diagnosis to the same slot).

Quiet hours are Dream Cycle's hours (D12). Once a minute while Learning is on the runner
looks at what is due, after Dream:

- a personal-model training run starts or continues when **Train during quiet hours**
  is on, training happens on this computer, the training engine and its weights are
  installed (L2b), there is a job to run (L2c plans them from a dataset), it is quiet
  hours, Dream has run, and nothing heavy is running and nobody used Row-Bot for a few
  minutes;
- a run started with **Train now** and paused because the person used Row-Bot
  continues as soon as the computer is free again, whatever the hour;
- a run that stopped for the person (their Stop, Row-Bot closing, the engine stopping)
  waits for Continue or Discard.

Until the training engine and starting model are downloaded (L2b) and L2c gives it a dataset
it finds nothing to do, and its :meth:`LearningRunner.status` says why (``no_engine``,
``no_data``). It never runs while
Learning is off or paused, or in a build without the preview flag; it starts no thread
and imports nothing until Learning is on.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from row_bot import learning as gate
from row_bot.learning.training import jobs as jobs_module
from row_bot.learning.training.jobs import HELD
from row_bot.learning.training.supervisor import TrainingError, TrainingSupervisor

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 60.0
# Dream Cycle checks every 30 minutes; when it hasn't run this long into quiet hours
# (no model chosen, nothing to do) training doesn't wait for it any longer.
DREAM_GRACE_SECONDS = 45 * 60.0


@dataclass(frozen=True)
class RunnerStatus:
    """What the runner would do now, and why not (a reason code the client words)."""

    # off · paused · training · waiting (for the computer) · scheduled (quiet hours)
    # · held (Continue or Discard) · failed · trained · nothing_to_do
    state: str
    reason: str | None = None


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class QuietHours:
    """Dream Cycle's hours and whether Dream has run tonight, read from its own files
    (``dream_config.json``, ``dream_journal.json``) without importing it."""

    def __init__(self, root: Path | None = None, *, now: Callable[[], datetime] = datetime.now) -> None:
        self._root = root
        self.now = now

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else gate.data_dir()

    def window(self) -> tuple[int, int, bool]:
        config = _read_json(self.root / "dream_config.json")
        config = config if isinstance(config, dict) else {}
        try:
            start = max(0, min(23, int(config.get("window_start", 1))))
            end = max(0, min(23, int(config.get("window_end", 5))))
        except (TypeError, ValueError):
            start, end = 1, 5
        return start, end, config.get("enabled", True) is not False

    def started_at(self) -> datetime | None:
        """When tonight's quiet hours began, or None outside them."""

        start, end, _ = self.window()
        now = self.now()
        hour = now.hour
        inside = start <= hour < end if start <= end else (hour >= start or hour < end)
        if not inside:
            return None
        began = now.replace(hour=start, minute=0, second=0, microsecond=0)
        return began if began <= now else began - timedelta(days=1)

    def inside(self) -> bool:
        return self.started_at() is not None

    def dream_done(self) -> bool:
        """Training starts after Dream: Dream is off, has run tonight, or is overdue."""

        import sys

        dream = sys.modules.get("row_bot.dream_cycle")
        try:
            if dream is not None and dream.is_dreaming():
                return False
        except Exception:
            pass
        began = self.started_at()
        _, _, enabled = self.window()
        if not enabled or began is None:
            return True
        journal = _read_json(self.root / "dream_journal.json")
        if isinstance(journal, list) and journal and isinstance(journal[-1], dict):
            try:
                last = datetime.fromisoformat(str(journal[-1].get("timestamp")))
                if last.tzinfo is not None:
                    last = last.astimezone().replace(tzinfo=None)
                if last >= began:
                    return True
            except ValueError:
                pass
        return (self.now() - began).total_seconds() >= DREAM_GRACE_SECONDS


class LearningRunner:
    """Decides what is due and asks the supervisor to start it. With a ``planner`` (L2c) it plans
    runs from the dataset, and with ``releases`` it makes each finished run a version."""

    def __init__(self, supervisor: TrainingSupervisor, *, root: Path | None = None,
                 hours: QuietHours | None = None, planner: Any = None, releases: Any = None,
                 comparisons: Any = None, unloader: Any = None) -> None:
        self.supervisor = supervisor
        self._root = root
        self.hours = hours or QuietHours(root)
        self.planner = planner
        self.releases = releases
        # L2c2: a ready version's comparison, and O51's unloading of Row-Bot's own resident model.
        self.comparisons = comparisons
        self.unloader = unloader
        self._last = RunnerStatus("off")
        self._lock = threading.Lock()

    def _unplanned(self, settings: Any, engine_ready: bool, act: bool) -> tuple[RunnerStatus, Any]:
        """No run is current: plan one from the dataset when there is enough (new) work (L2c)."""

        if settings.train_where != "this_computer":
            return RunnerStatus("nothing_to_do", "hugging_face"), None
        if not engine_ready:
            return RunnerStatus("nothing_to_do", "no_engine"), None
        if self.planner is None:
            if not settings.train_in_quiet_hours:
                return RunnerStatus("nothing_to_do", "train_off"), None
            return RunnerStatus("nothing_to_do", "no_data"), None
        if act and self.planner.due() and not self.supervisor.lease.read().busy:
            outcome = self.planner.plan()
            if outcome.job is not None:
                return RunnerStatus("scheduled", "planned"), outcome.job
        summary = self.planner.summary(compute=True)
        if summary is None or not summary.enough:
            return RunnerStatus("nothing_to_do", "no_data"), None
        from row_bot.learning.training import releases

        if releases.latest_state(self._root) is not None and summary.new_examples < self.planner.recipe().min_new_examples:
            return RunnerStatus("trained", "up_to_date"), None
        return RunnerStatus("nothing_to_do", "planning"), None

    def _decide(self, act: bool) -> RunnerStatus:
        settings = gate.read_settings(self._root)
        if not settings.enabled:
            return RunnerStatus("off")
        if settings.paused:
            return RunnerStatus("paused")
        supervisor = self.supervisor
        job = supervisor.jobs.current() if supervisor.jobs.store.exists() else None
        if supervisor.training() and (job is None or job.state in jobs_module.LIVE):
            return RunnerStatus("training")
        engine_ready = supervisor.engine.ready()
        if job is None:
            if self.planner is None and not settings.train_in_quiet_hours:
                return RunnerStatus("nothing_to_do", "train_off")
            status, job = self._unplanned(settings, engine_ready, act)
            if job is None:
                return status
        if job.state == "failed":
            return RunnerStatus("failed", job.error_code)
        if job.state in ("evaluating", "ready", "trial", "in_use"):
            return RunnerStatus("trained")
        if job.state == "paused" and job.pause_reason in HELD:
            return RunnerStatus("held", job.pause_reason)
        if job.state in jobs_module.LIVE:
            return RunnerStatus("held", "app_closed")  # left from before a restart
        if not engine_ready:
            return RunnerStatus("nothing_to_do", "no_engine")
        if job.state == "waiting":
            # Train now while something heavy ran: it starts once that ends (also after a restart).
            holder = supervisor.lease.read().holder
            return RunnerStatus("waiting", holder) if holder else self._start(job, "train_now", act)
        if job.trigger == "train_now" and job.state == "paused":
            # Paused while the person used Row-Bot after Train now: continue once it's free.
            if not supervisor.lease.read().free:
                return RunnerStatus("waiting", "in_use")
            return self._start(job, "train_now", act)
        if not settings.train_in_quiet_hours:
            return RunnerStatus("nothing_to_do", "train_off")
        if settings.train_where != "this_computer":
            return RunnerStatus("nothing_to_do", "hugging_face")
        if not self.hours.inside():
            return RunnerStatus("scheduled", "quiet_hours")
        if not self.hours.dream_done():
            return RunnerStatus("scheduled", "after_dream")
        if not supervisor.lease.read().free:
            return RunnerStatus("waiting", "in_use")
        return self._start(job, "quiet_hours", act)

    def _start(self, job, trigger: str, act: bool) -> RunnerStatus:
        if not act:
            if not self.supervisor.gpu_free():
                return RunnerStatus("waiting", "gpu_busy")  # another app holds the card's memory
            return RunnerStatus("scheduled", "now")
        if self.comparisons is not None and self.comparisons.running():
            return RunnerStatus("waiting", "comparing")  # a version's comparison has the card
        if (trigger == "quiet_hours" and self.unloader is not None and not self.supervisor.gpu_free()
                and self.hours.inside()):
            # O51: in quiet hours, with nobody using Row-Bot for 30 minutes, Row-Bot's own resident
            # local model is unloaded so training can have the card (never another app's model).
            try:
                if self.unloader.free_card(idle_seconds=self.supervisor.lease.read().since_activity):
                    from row_bot.learning.training import hardware

                    hardware.forget_memory_reading()
            except Exception:
                logger.debug("Learning couldn't free the graphics card", exc_info=True)
        try:
            self.supervisor.start(job, trigger)
        except TrainingError as error:
            if error.code == "gpu_busy":
                return RunnerStatus("waiting", "gpu_busy")
            return RunnerStatus("nothing_to_do", "no_data" if error.code == "nothing_to_train" else error.code)
        return RunnerStatus("training")

    def status(self) -> RunnerStatus:
        """What is due now, without acting (the snapshot reads this)."""

        try:
            return self._decide(act=False)
        except Exception:
            logger.debug("Learning's runner status could not be read", exc_info=True)
            return self._last

    def check(self) -> RunnerStatus:
        """One pass: start or continue what is due, and make a finished run a version."""

        with self._lock:
            try:
                self._last = self._decide(act=True)
            except Exception:
                logger.exception("Learning's quiet-hours runner failed")
            if self.releases is not None:
                try:
                    self.releases.tick()
                except Exception:
                    logger.exception("Learning couldn't make its personal model's version")
            if self.comparisons is not None and not self.supervisor.training() and (
                    self.releases is None or not self.releases.busy()):
                try:
                    self.comparisons.tick()
                except Exception:
                    logger.exception("Learning couldn't compare its personal model's version")
            return self._last


# ── The app's runner ────────────────────────────────────────────────────────

_lock = threading.RLock()
_runner: LearningRunner | None = None
_thread: threading.Thread | None = None
_stop = threading.Event()


def get() -> LearningRunner:
    """The app's runner and supervisor (created on first use; no thread). Its engine is the
    installed training engine and starting model (L2b); tests plug in fakes with ``configure``."""

    global _runner
    with _lock:
        if _runner is None:
            from row_bot.learning.training.engine import LocalEngine
            from row_bot.learning.training.planner import LearningDataset, Planner
            from row_bot.learning.training.releases import ReleaseMaker

            from row_bot.learning.model_compare import Comparisons
            from row_bot.learning.training.freeing import OwnModelUnloader

            supervisor = TrainingSupervisor(engine=LocalEngine())
            planner = Planner(jobs=supervisor.jobs, engine=supervisor.engine)
            supervisor.dataset = LearningDataset(planner)
            comparisons = Comparisons(lease=supervisor.lease, training=supervisor.training)
            _runner = LearningRunner(supervisor, planner=planner, releases=ReleaseMaker(jobs=supervisor.jobs),
                                     comparisons=comparisons, unloader=OwnModelUnloader())
        return _runner


def loaded() -> LearningRunner | None:
    return _runner


def configure(*, engine=None, dataset=None, lease=None, planner=None, releases=None,
              comparisons=None) -> TrainingSupervisor:
    """Plug in the training engine, the dataset compiler, the lease, the planner, the release maker or
    the comparisons (L2b, L2c, L2c2; the fake ones in tests and the browser fixture)."""

    runner = get()
    supervisor = runner.supervisor
    if engine is not None:
        supervisor.engine = engine
        if runner.planner is not None:
            runner.planner.engine = engine
    if dataset is not None:
        supervisor.dataset = dataset
    if lease is not None:
        supervisor.lease = lease
    if planner is not None:
        runner.planner = planner
    if releases is not None:
        runner.releases = releases
    if comparisons is not None:
        runner.comparisons = comparisons
    return supervisor


def recover() -> list:
    """Training left running when Row-Bot closed is paused at its newest checkpoint; run
    folders of jobs that no longer exist are removed."""

    supervisor = get().supervisor
    if not gate.store_exists():
        return []
    with supervisor._lock:  # noqa: SLF001 - one owner of the live worker
        found = jobs_module.recover(supervisor.jobs, live=lambda job_id: supervisor.live_job_id() == job_id)
        jobs_module.sweep_runs(supervisor.jobs)
    return found


def start_background() -> bool:
    """Start the runner's loop while Learning is on (start-up, turning on, resuming)."""

    global _thread
    if not gate.is_enabled():
        return False
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False
        try:
            recover()
        except Exception:
            logger.warning("Learning could not check for interrupted training", exc_info=True)
        _stop.clear()
        runner = get()

        def loop() -> None:
            while not _stop.is_set():
                if not gate.is_enabled():
                    break
                runner.check()
                _stop.wait(INTERVAL_SECONDS)

        _thread = threading.Thread(target=loop, name="learning-runner", daemon=True)
        _thread.start()
        return True


def stop_background(timeout: float = 5.0) -> None:
    _stop.set()
    thread = _thread
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout)


def shutdown(timeout: float = 10.0) -> None:
    """Row-Bot is quitting: the runner stops, and so does any training (with its whole tree)."""

    stop_background(min(timeout, 5.0))
    runner = _runner
    if runner is not None:
        runner.supervisor.shutdown()
        if runner.releases is not None:
            runner.releases.shutdown()
        if runner.comparisons is not None:
            runner.comparisons.stop()


def learning_turned_off() -> None:
    """Learning was turned off: training and any comparison stop at once; nothing new starts."""

    runner = _runner
    if runner is not None:
        runner.supervisor.stop_now("learning_off")
        if runner.releases is not None:
            runner.releases.shutdown()
        if runner.comparisons is not None:
            runner.comparisons.stop()
    _stop.set()


def learning_paused() -> None:
    """Learning was paused: training pauses at its next step; a comparison stops (it runs again)."""

    runner = _runner
    if runner is not None:
        runner.supervisor.request_pause("learning_paused")
        if runner.comparisons is not None:
            runner.comparisons.stop()


def forget_training() -> None:
    """"Delete learning data": any training stops at once (its run folders go next)."""

    runner = _runner
    if runner is not None:
        runner.supervisor.stop_now("learning_off")
        if runner.releases is not None:
            runner.releases.shutdown()
        if runner.comparisons is not None:
            runner.comparisons.stop()
        if runner.planner is not None:
            runner.planner.forget()


def training_now() -> bool:
    """A training worker is running (Buddy's quiet line). Never creates anything."""

    runner = _runner
    return runner is not None and runner.supervisor.training()


__all__ = [
    "DREAM_GRACE_SECONDS",
    "INTERVAL_SECONDS",
    "LearningRunner",
    "QuietHours",
    "RunnerStatus",
    "configure",
    "forget_training",
    "get",
    "learning_paused",
    "learning_turned_off",
    "loaded",
    "recover",
    "shutdown",
    "start_background",
    "stop_background",
    "training_now",
]
