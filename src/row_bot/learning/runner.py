"""Learning's quiet-hours runner (L2a; L3a adds the nightly look for things to improve).

Quiet hours are Dream Cycle's hours (D12). Once a minute while Learning is on the runner
looks at what is due, after Dream, in this order:

- **the night's look for things to improve** (L3a, :class:`NightLook`): once a night, when nobody
  is using Row-Bot, the vault takes its share of new work and held-out corrections first, then
  ``diagnosis`` groups issues and drafts changes, all by the learning method in use (L4a,
  ``improver.archive``). It stops before its next model call when the person comes back or Learning
  is paused, and goes on later that night. In a build with them on (L4b), the day's **quick question**
  (``question``) and the night's **practice** (``practice``) come next. Its tests follow (L3b), then a
  **strategy trial** when enough fresh hidden problems have built up (L4a, ``improver.meta``);

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
import sys
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


# O112: quiet-hours nights training waited for room on the graphics card (newest last, a week at most); after
# ROOM_NIGHTS of them the Personal model view says so and offers the next smaller model. Cleared when a run starts.
ROOM_FILE = "card_room.json"
ROOM_NIGHTS = 2


def _room_path(root: Path | None = None) -> Path:
    from row_bot.learning.training import jobs as jobs_module

    return jobs_module.models_dir(root) / ROOM_FILE


def card_room(root: Path | None = None) -> dict | None:
    """The nights training waited for room on the card: ``nights`` (dates), the card's ``free_mib`` and the
    run's ``need_mib`` when last seen, and the ``model`` it waited to train. None when it never waited."""

    data = _read_json(_room_path(root))
    if not isinstance(data, dict) or not isinstance(data.get("nights"), list):
        return None
    nights = [night for night in data["nights"] if isinstance(night, str)][-7:]
    return {"nights": nights, "free_mib": int(data.get("free_mib") or 0), "need_mib": int(data.get("need_mib") or 0),
            "model": str(data.get("model") or "")} if nights else None


def forget_card_room(root: Path | None = None) -> None:
    try:
        _room_path(root).unlink(missing_ok=True)
    except OSError:
        logger.debug("Learning couldn't forget the nights it waited for the card", exc_info=True)


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


MORNING_KEY = "changes.morning"


class NightLook:
    """The night's look for things to improve (L3a) and its tests (L3b): after Dream, while nobody uses
    Row-Bot, before training. ``client`` and ``check_model`` are the diagnosis call's, ``tester`` makes the
    changes' tester (tests pass fakes)."""

    def __init__(self, *, lease: Any, root: Path | None = None, hours: QuietHours | None = None,
                 client: Any = None, check_model: Callable[[], None] | None = None,
                 clock: Callable[[], float] | None = None, tester: Callable[..., Any] | None = None) -> None:
        self.lease = lease
        self._root = root
        self.hours = hours or QuietHours(root)
        self.client = client
        self.check_model = check_model
        self.clock = clock
        # ``tester(method=None, purpose=...)`` makes the changes' tester (tests pass fakes).
        self.tester = tester

    def _store(self) -> Any:
        from row_bot.learning.store import LearningStore

        return LearningStore(self._root, clock=self.clock) if self.clock is not None else LearningStore(self._root)

    def tonight(self) -> str | None:
        began = self.hours.started_at()
        return began.date().isoformat() if began is not None else None

    def looked_tonight(self) -> bool:
        from row_bot.learning import diagnosis

        night = self.tonight()
        found = diagnosis.last_night(self._store()) if gate.store_exists(self._root) else None
        return bool(night and found and found.get("night") == night and found.get("state") != "stopped")

    def tested_tonight(self) -> bool:
        """Tonight's tests finished, or nothing waits to be tested (L3b)."""

        from row_bot.learning import changes

        night = self.tonight()
        found = changes.last_tests(self._store()) if gate.store_exists(self._root) else None
        if found and found.get("night") == night and found.get("state") not in ("stopped",):
            return True
        try:
            return not changes.due()
        except Exception:  # noqa: BLE001 - the changes can't be read: nothing to test tonight
            return True

    def practised_tonight(self) -> bool:
        """Tonight's practice finished, or this build doesn't practise (L4b)."""

        from row_bot.learning import practice

        if not practice.enabled():
            return True
        night = self.tonight()
        found = practice.last_night(self._store()) if gate.store_exists(self._root) else None
        return bool(night and found and found.get("night") == night and found.get("state") != "stopped")

    def tried_tonight(self) -> bool:
        """Tonight's strategy trial finished, or none could run (L4a)."""

        from row_bot.learning.improver import meta

        night = self.tonight()
        found = meta.last_night(self._store()) if gate.store_exists(self._root) else None
        return bool(night and found and found.get("night") == night and found.get("state") != "stopped")

    def jobs_tested_tonight(self) -> bool:
        """Tonight's tests of the personal model for Learning's jobs finished, or this build has none (L4c)."""

        from row_bot.learning.improver import jobs

        night = self.tonight()
        return not night or not gate.store_exists(self._root) or jobs.tested_tonight(self._store(), night)

    def done_tonight(self) -> bool:
        return (self.looked_tonight() and self.practised_tonight() and self.tested_tonight() and self.tried_tonight()
                and self.jobs_tested_tonight())

    def due(self) -> bool:
        """Learning is on, it is quiet hours after Dream, and tonight's look hasn't finished."""

        if not gate.is_enabled(self._root) or not gate.store_exists(self._root):
            return False
        if not self.hours.inside() or not self.hours.dream_done():
            return False
        return not self.done_tonight()

    def tick(self) -> Any:
        """Run tonight's look when it is due and the computer is free; returns its report, else None."""

        if not self.due() or not self.lease.read().free:
            return None
        night = self.tonight()
        if night is None:
            return None
        report = self._look(night) if not self.looked_tonight() else None
        if self.looked_tonight() and not self._stop():
            self._ask(night)
        if self.looked_tonight() and not self.practised_tonight() and not self._stop():
            self._practise(night)
        if self.looked_tonight() and self.practised_tonight() and not self.tested_tonight():
            self._test(night)
        if self.looked_tonight() and self.tested_tonight() and not self.tried_tonight() and not self._stop():
            self._trial(night)
        if self.tried_tonight() and not self.jobs_tested_tonight() and not self._stop():
            self._jobs(night)
        return report

    def _stop(self) -> bool:
        return not gate.is_enabled(self._root) or not self.lease.read().free

    def morning(self, notify: Callable[[str], None] | None = None) -> str | None:
        """The morning summary (UX "Notifications: at most once a morning"): once quiet hours are over, last
        night's changes ready to review and those put into use by themselves, in one quiet notice. Nothing
        when there is nothing to say; never twice for one night. Returns what it said."""

        if self.hours.inside() or not gate.is_enabled(self._root) or not gate.store_exists(self._root):
            return None
        from row_bot.learning import changes

        store = self._store()
        tests = changes.last_tests(store) or {}
        night = str(tests.get("night") or "")
        if not night or store.meta_value(MORNING_KEY, "") == night:
            return None
        from datetime import date

        try:
            if (self.hours.now().date() - date.fromisoformat(night)).days > 1:
                return None
        except ValueError:
            return None
        ready, used = int(tests.get("ready") or 0) - int(tests.get("used") or 0), int(tests.get("used") or 0)
        parts = []
        if ready > 0:
            parts.append(f"{ready} change{'s' if ready != 1 else ''} ready to review")
        if used > 0:
            parts.append(f"{used} change{'s' if used != 1 else ''} put into use by "
                         f"{'themselves' if used != 1 else 'itself'} (Undo in Learning)")
        from row_bot.learning.improver import meta

        trial = meta.last_night(store) or {}
        switched = trial.get("switched") if trial.get("night") == night else None
        if isinstance(switched, dict) and switched.get("to"):
            parts.append(f"changed how it learns (method v{switched['to']}): {switched.get('words') or 'a new method'}"
                         " (Undo in Learning)")
        from row_bot.learning import question

        if question.current(store) is not None:
            parts.append("a quick question for you (in Learning)")
        store.set_meta_values({MORNING_KEY: night})
        if not parts:
            return None
        text = "Overnight: " + " · ".join(parts)
        if notify is not None:
            notify(text)
        else:
            from row_bot import notifications

            notifications.notify("Learning", text, sound="none", toast_type="info", source="learning",
                                 requested=True)
        return text

    def _look(self, night: str) -> Any:
        from row_bot.learning import diagnosis, holdout

        store = self._store()
        try:
            holdout.mark_corrections(store)  # first: drafting never sees a correction held out tonight
        except Exception:
            logger.exception("Learning couldn't hold out tonight's corrections; it looks again tomorrow night")
            diagnosis.note_night(store, night, "failed")
            return None
        try:
            from row_bot.learning import evaluation

            evaluation.refresh_audit(store=store, asked_by="quiet hours")
        except Exception:
            logger.warning("Learning couldn't refresh its hidden tests tonight", exc_info=True)
        client = self.client
        if client is None:
            from row_bot.learning.calls import LedgerModelClient

            client = LedgerModelClient()
        from row_bot.learning.improver import archive

        method = archive.current(store)
        from row_bot.learning.improver import jobs

        client = jobs.client_for(store, jobs.DRAFTING, client)  # the personal model, once it qualified (L4c)
        try:
            return diagnosis.run(store, client=client, night=night, should_stop=self._stop,
                                 check_model=self.check_model or diagnosis.check_default_model,
                                 method=method.strategy, strategy_release_id=method.id)
        except Exception:
            logger.exception("Learning's look for things to improve failed")
            diagnosis.note_night(store, night, "failed")
            return None

    def _client(self) -> Any:
        if self.client is not None:
            return self.client
        from row_bot.learning.calls import LedgerModelClient

        return LedgerModelClient()

    def _ask(self, night: str) -> Any:
        """Today's quick question, when the night left real uncertainty (L4b; at most one a day)."""

        from row_bot.learning import question

        try:
            return question.maybe_ask(self._store(), client=self._client(), night=night,
                                      now=self.clock() if self.clock else None)
        except Exception:
            logger.exception("Learning couldn't ask its quick question")
            return None

    def _practise(self, night: str) -> Any:
        """Tonight's practice (L4b): tasks made up from the issues, run once, kept; under the same stop rule."""

        from row_bot.learning import practice
        from row_bot.learning.improver import archive, jobs

        store = self._store()
        try:
            method = archive.current(store).strategy
            client = jobs.client_for(store, jobs.PRACTICE, self._client())  # the personal model, once it qualified
            return practice.practise(store, night=night, client=client, tester=self._tester(method),
                                     method=method, should_stop=self._stop, now=self.clock() if self.clock else None)
        except Exception:
            logger.exception("Learning's practice failed")
            practice.note_failed(store, night)
            return None

    def _jobs(self, night: str) -> Any:
        """Tonight's tests of the personal model for Learning's jobs, last (L4c): takes a job over or hands it back."""

        from row_bot.learning.improver import jobs

        try:
            return jobs.run_due(self._store(), client=self._client(), night=night,
                                now=self.clock() if self.clock else None, should_stop=self._stop)
        except Exception:
            logger.exception("Learning couldn't test its personal model for its jobs")
            return None

    def _test(self, night: str) -> Any:
        """Tonight's drafts tested (L3b), under the same stop rule as the look; training waits for them."""

        from row_bot.learning import changes
        from row_bot.learning.improver import archive

        try:
            method = archive.current(self._store()).strategy
            tester = self._tester(method)
            return changes.test_waiting(tester, night=night, should_stop=self._stop, limit=method.budget.tests)
        except Exception:
            logger.exception("Learning couldn't test tonight's changes")
            return None

    def _tester(self, method: Any = None, purpose: str = "learning_test") -> Any:
        from row_bot.learning import changes, diagnosis

        if self.tester is not None:
            return self.tester(method=method, purpose=purpose)
        kwargs = {"clock": self.clock} if self.clock is not None else {}
        return changes.Tester(self._root, store=self._store(), client=self.client, plan=changes.TestPlan.of(method),
                              check_model=self.check_model or diagnosis.check_default_model, purpose=purpose,
                              **kwargs)

    def _trial(self, night: str) -> Any:
        """Tonight's strategy trial (L4a) when enough fresh hidden problems have built up and the trials' share
        allows it; under the same stop rule; training waits for it."""

        from row_bot.learning import changes
        from row_bot.learning.improver import meta

        store = self._store()
        client = self.client
        if client is None:
            from row_bot.learning.calls import LedgerModelClient

            client = LedgerModelClient()
        try:
            probe = self._tester(None, meta.PURPOSE)
            local = changes._local(probe._main())  # noqa: SLF001 - the main model runs on this computer
            return meta.run_trial(store, night=night, client=client, should_stop=self._stop, local=local,
                                  tester_factory=lambda method: self._tester(method, meta.PURPOSE),
                                  check_model=self.check_model, now=self.clock() if self.clock else None)
        except Exception:
            logger.exception("Learning's strategy trial failed")
            try:
                meta._note(store, meta.TrialReport(night, "failed"))  # noqa: SLF001 - recorded once a night
            except Exception:  # noqa: BLE001
                logger.debug("Learning couldn't note tonight's trial", exc_info=True)
            return None


class LearningRunner:
    """Decides what is due and asks the supervisor to start it. With a ``planner`` (L2c) it plans
    runs from the dataset, and with ``releases`` it makes each finished run a version. With a
    ``look`` (L3a) the night's look for things to improve runs first."""

    def __init__(self, supervisor: TrainingSupervisor, *, root: Path | None = None,
                 hours: QuietHours | None = None, planner: Any = None, releases: Any = None,
                 comparisons: Any = None, unloader: Any = None, look: NightLook | None = None) -> None:
        self.supervisor = supervisor
        self._root = root
        self.hours = hours or QuietHours(root)
        self.planner = planner
        self.releases = releases
        # L2c2: a ready version's comparison, and O51's unloading of Row-Bot's own resident model.
        self.comparisons = comparisons
        self.unloader = unloader
        self.look = look
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

        if (releases.latest_state(self._root, started=True) is not None
                and summary.new_examples < self.planner.recipe().min_new_examples):
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
        if job is not None and job.state == "evaluating":
            from row_bot.learning.training import releases

            if not releases.holds_back(job, self._root):
                job = None  # its version couldn't start: the next one is planned (it stays listed)
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
            if not self.supervisor.memory_free():
                return RunnerStatus("waiting", "memory_busy")  # other apps hold the computer's memory (O105)
            return RunnerStatus("scheduled", "now")
        if self.comparisons is not None and self.comparisons.running():
            return RunnerStatus("waiting", "comparing")  # a version's comparison has the card
        # Row-Bot's own model server (O75) gives the card back first: it holds personal models only while
        # they answer, and nothing answers now (training starts only when no chat runs, the lease).
        server = sys.modules.get("row_bot.learning.training.llama_server")
        if server is not None:
            try:
                if server.release_card():
                    from row_bot.learning.training import hardware

                    hardware.forget_memory_reading()
            except Exception:
                logger.debug("Learning couldn't stop its model server for training", exc_info=True)
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
            if error.code in ("gpu_busy", "memory_busy"):
                if error.code == "gpu_busy" and trigger == "quiet_hours":
                    self._note_no_room()
                return RunnerStatus("waiting", error.code)
            return RunnerStatus("nothing_to_do", "no_data" if error.code == "nothing_to_train" else error.code)
        forget_card_room(self._root)
        return RunnerStatus("training")

    def _note_no_room(self) -> None:
        """Tonight training waited for room on the card (O112): noted once a night, with what was free."""

        began = self.hours.started_at()
        if began is None:
            return
        night = began.date().isoformat()
        found = card_room(self._root) or {"nights": []}
        if night in found["nights"]:
            return
        from row_bot import learning as gate
        from row_bot.learning import atomic
        from row_bot.learning.training import hardware

        reading = hardware.gpu_memory()
        need = hardware.training_need_mib(hardware.peek()) or 0
        record = {"nights": [*found["nights"], night][-7:],
                  "free_mib": max(0, reading[1] - reading[0]) if reading else 0, "need_mib": int(need),
                  "model": gate.read_settings(self._root).starting_model}
        try:
            path = _room_path(self._root)
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic.write_text(path, json.dumps(record))
        except OSError:
            logger.debug("Learning couldn't note a night without room on the card", exc_info=True)

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
            if self.look is not None:
                try:
                    self.look.tick()  # Dream → the night's look and its tests → training
                    self.look.morning()
                except Exception:
                    logger.exception("Learning's look for things to improve failed")
            try:
                if self.look is not None and self.look.due():
                    # Tonight's look hasn't finished (the person came back): training waits for it.
                    self._last = RunnerStatus("waiting", "in_use")
                else:
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
                                     comparisons=comparisons, unloader=OwnModelUnloader(),
                                     look=NightLook(lease=supervisor.lease))
        return _runner


def loaded() -> LearningRunner | None:
    return _runner


def configure(*, engine=None, dataset=None, lease=None, planner=None, releases=None,
              comparisons=None, look=None) -> TrainingSupervisor:
    """Plug in the training engine, the dataset compiler, the lease, the planner, the release maker,
    the comparisons or the night's look (L2b, L2c, L2c2, L3a; the fake ones in tests and the browser
    fixture)."""

    runner = get()
    supervisor = runner.supervisor
    if engine is not None:
        supervisor.engine = engine
        if runner.planner is not None:
            runner.planner.engine = engine
    if dataset is not None:
        supervisor.dataset = dataset
    if look is not None:
        runner.look = look
    if lease is not None:
        supervisor.lease = lease
        if runner.look is not None:
            runner.look.lease = lease
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
    "NightLook",
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
