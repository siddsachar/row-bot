"""Personal-model training jobs: a persisted state machine (L2a).

A training job is a :class:`~row_bot.learning.contracts.Job` (``kind="training"``) in
``learning.db``. Its states::

    planned → waiting → preparing → running → pausing → paused → evaluating → ready → trial → in use

plus ``not used``, ``failed``, ``stopped`` and ``unknown remote`` (a Hugging Face run
whose outcome isn't known, L2d). Every move is checked against :data:`TRANSITIONS`,
timestamped (``updated_at`` and an entry in ``transitions``) and saved before anything
acts on it, so it survives a restart; an illegal move raises :class:`JobStateError` and
changes nothing.

**Files.** A job's bulk lives in its run folder, ``learning_models/runs/<job id>/``
in the data folder (:func:`run_dir`): ``data/`` (the prepared examples the worker is
leased), ``checkpoints/checkpoint-<step>/`` (each with its manifest, the newest two
kept), ``passes.json`` (each pass's validation loss and saved adapter, for a run of more than one
pass, O119), ``work/`` (the worker's own folder) and ``worker.log`` (the tail of what it wrote). ``learning_models/`` is re-downloadable or re-trainable bulk: profile backups
leave it out (O39) and a restore leaves it where it is.

**Recovery.** After a crash or a restart a job left preparing, running or pausing has
no worker any more: :func:`recover` pauses it at its newest good checkpoint (the
Personal model view then offers Continue or Discard). A checkpoint whose files don't
match its manifest is skipped for the one before it, or the job starts over, and the
job says which (``recovered``).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from row_bot import learning as gate
from row_bot.learning.contracts import Job, JobTransition
from row_bot.learning.training import protocol

logger = logging.getLogger(__name__)

KIND = "training"
MODELS_FOLDER = "learning_models"
RUNS_FOLDER = "runs"
KEEP_CHECKPOINTS = 2


def _check_plan(spec: dict[str, Any], steps_total: int) -> None:
    checkpoint_every = int(spec["checkpoint_every"])
    if not 1 <= checkpoint_every <= int(steps_total) <= protocol.MAX_STEPS:
        raise ValueError("A training plan needs 1 ≤ checkpoint interval ≤ steps.")
    somewhere = str(Path(__file__).resolve().parent)  # the shape check needs absolute paths
    protocol.check_spec_shape({
        "job_id": "job_check", "version": 1, "steps_total": int(steps_total), "checkpoint_every": checkpoint_every,
        "resume_from": None, "lease": {"dataset": somewhere, "examples": 0}, "output": somewhere,
        "recipe": spec["recipe"],
    })
PASSES_FILE = "passes.json"
MAX_TRANSITIONS = 200

TRANSITIONS: dict[str, frozenset[str]] = {
    "planned": frozenset({"waiting", "preparing", "stopped"}),
    # Waiting for the computer after Train now; Stop sends it back to where it was.
    "waiting": frozenset({"preparing", "planned", "paused", "stopped", "failed"}),
    "preparing": frozenset({"running", "waiting", "paused", "failed", "stopped"}),
    "running": frozenset({"pausing", "paused", "evaluating", "failed", "stopped", "unknown_remote"}),
    "pausing": frozenset({"paused", "evaluating", "failed", "stopped"}),
    "paused": frozenset({"waiting", "preparing", "failed", "stopped"}),
    "evaluating": frozenset({"ready", "not_used", "failed", "stopped"}),
    "ready": frozenset({"trial", "not_used"}),
    "trial": frozenset({"in_use", "not_used"}),
    "in_use": frozenset({"not_used"}),
    "unknown_remote": frozenset({"running", "evaluating", "failed", "stopped"}),
    "not_used": frozenset(),
    "failed": frozenset(),
    "stopped": frozenset(),
}
TERMINAL = frozenset(state for state, moves in TRANSITIONS.items() if not moves)
# A worker may be live in these (or was, before a crash).
LIVE = frozenset({"preparing", "running", "pausing"})
# The job the Personal model view follows (newer ones first): anything not settled.
CURRENT = frozenset({"planned", "waiting", "preparing", "running", "pausing", "paused", "evaluating", "failed"})
# Pauses that wait for the person (Continue or Discard), not for a free computer.
HELD = frozenset({"you", "app_closed", "worker_stopped"})

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_LOCK = threading.RLock()


class JobStateError(ValueError):
    """A refused move; ``code`` is stable (``illegal_transition``, ``job_missing``, ``job_changed``)."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def can_move(state: str, to: str) -> bool:
    return to in TRANSITIONS.get(state, frozenset())


def transition(job: Job, to: str, *, at: float, reason: str | None = None, **changes: Any) -> Job:
    """The job moved to ``to`` at ``at``: validated, timestamped, with its reason code.

    ``changes`` sets other fields in the same move. Leaving pausing/paused clears the
    pause reason unless the move sets one.
    """

    if not can_move(job.state, to):
        raise JobStateError("illegal_transition", f"{job.state} → {to}")
    moment = max(float(at), job.updated_at)
    entries = (*job.transitions, JobTransition(state=to, at=moment, reason=reason))
    if len(entries) > MAX_TRANSITIONS:
        # Keep the first (when it was planned) and the newest.
        entries = (entries[0], *entries[-(MAX_TRANSITIONS - 1):])
    if to not in ("pausing", "paused") and "pause_reason" not in changes:
        changes["pause_reason"] = None
    return replace(job, state=to, updated_at=moment, transitions=entries, **changes)


# ── Files ───────────────────────────────────────────────────────────────────


def models_dir(root: Path | None = None) -> Path:
    """``learning_models/`` in the data folder: personal-model bulk, never in a backup (O39)."""

    return (root if root is not None else gate.data_dir()) / MODELS_FOLDER


def runs_dir(root: Path | None = None) -> Path:
    return models_dir(root) / RUNS_FOLDER


def run_dir(job_id: str, root: Path | None = None) -> Path:
    if not _ID.fullmatch(str(job_id or "")) or job_id in {".", ".."}:
        raise ValueError("Job id is not valid.")
    return runs_dir(root) / job_id


def _plain_folder(path: Path, parent: Path) -> bool:
    """A real folder directly inside ``parent`` (never a link out of it)."""

    try:
        return not path.is_symlink() and path.is_dir() and path.resolve().parent == parent.resolve()
    except OSError:
        return False


def remove_run(job_id: str, root: Path | None = None) -> bool:
    """Delete a job's run folder (its leased data, checkpoints and work folder)."""

    folder = run_dir(job_id, root)
    if not folder.exists():
        return False
    if not _plain_folder(folder, runs_dir(root)):
        logger.warning("Learning left a training folder it doesn't own")
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return not folder.exists()


def remove_all_runs(root: Path | None = None) -> int:
    """Delete every run folder ("Delete learning data": the leased data is learning data)."""

    folder = runs_dir(root)
    if not folder.is_dir() or folder.is_symlink():
        return 0
    removed = 0
    for entry in sorted(folder.iterdir()):
        if _ID.fullmatch(entry.name) and _plain_folder(entry, folder):
            shutil.rmtree(entry, ignore_errors=True)
            removed += not entry.exists()
    return removed


@dataclass(frozen=True)
class Checkpoint:
    step: int
    path: Path


def checkpoints(job_id: str, root: Path | None = None) -> list[Checkpoint]:
    """A job's checkpoint folders, newest first (not checked)."""

    folder = run_dir(job_id, root) / "checkpoints"
    if not folder.is_dir():
        return []
    found = []
    for entry in folder.iterdir():
        step = protocol.checkpoint_step(entry.name)
        if step is not None and _plain_folder(entry, folder):
            found.append(Checkpoint(step, entry))
    return sorted(found, key=lambda item: item.step, reverse=True)


def good_checkpoint(checkpoint: Checkpoint) -> bool:
    try:
        protocol.read_checkpoint_manifest(checkpoint.path)
        return True
    except protocol.ProtocolError:
        return False


def newest_good(job_id: str, root: Path | None = None, *, at_most: int | None = None) -> tuple[Checkpoint | None, int]:
    """The newest checkpoint whose files match its manifest, and how many newer ones didn't.

    ``at_most`` skips checkpoints after that step (one the worker refused, say).
    """

    skipped = 0
    for checkpoint in checkpoints(job_id, root):
        if at_most is not None and checkpoint.step > at_most:
            continue
        if good_checkpoint(checkpoint):
            return checkpoint, skipped
        skipped += 1
    return None, skipped


def discard_checkpoint(checkpoint: Checkpoint) -> None:
    shutil.rmtree(checkpoint.path, ignore_errors=True)


def prune_checkpoints(job_id: str, root: Path | None = None, *, keep: int = KEEP_CHECKPOINTS) -> int:
    """Keep the newest ``keep`` good checkpoints; delete older and broken ones behind them."""

    kept = 0
    removed = 0
    for checkpoint in checkpoints(job_id, root):
        if kept < keep and good_checkpoint(checkpoint):
            kept += 1
            continue
        if kept == 0:
            continue  # nothing good is newer: leave it for recovery to judge
        discard_checkpoint(checkpoint)
        removed += 1
    return removed


# ── Persistence ─────────────────────────────────────────────────────────────


def _store(root: Path | None = None) -> Any:
    from row_bot.learning.store import LearningStore

    return LearningStore(root)


class TrainingJobs:
    """Training jobs in ``learning.db``: read, plan and move them (under one lock).

    Never creates a store by reading; ``plan`` writes one only while Learning is on.
    """

    def __init__(self, root: Path | None = None, *, store: Any = None,
                 clock: Callable[[], float] = time.time) -> None:
        self._root = root
        self._store = store
        self.clock = clock

    @property
    def store(self) -> Any:
        if self._store is None:
            self._store = _store(self._root)
        return self._store

    @property
    def root(self) -> Path:
        return self.store.root

    def get(self, job_id: str) -> Job | None:
        found = self.store.get("job", job_id)
        return found if isinstance(found, Job) and found.kind == KIND else None

    def all(self) -> list[Job]:
        """Training jobs, newest first."""

        return [job for job in self.store.find("job", subkind=KIND, limit=10_000, newest_first=True)
                if isinstance(job, Job)]

    def current(self) -> Job | None:
        """The job the Personal model view follows: the newest one not yet settled."""

        jobs = self.all()
        newest = jobs[0] if jobs else None
        if newest is not None and newest.state in CURRENT:
            return newest
        live = [job for job in jobs if job.state in LIVE or job.state == "paused"]
        return live[0] if live else None

    def next_version(self) -> int:
        """The next personal-model version: runs that failed or were discarded made none."""

        versions = [job.version or 0 for job in self.all() if job.state not in ("failed", "stopped")]
        return max(versions, default=0) + 1

    def plan(self, *, steps_total: int, checkpoint_every: int, recipe: dict[str, Any] | None = None,
             version: int | None = None, trigger: str = "quiet_hours", dataset: dict[str, Any] | None = None) -> Job:
        """A new planned training job (L2c plans them from a dataset; tests and the fixture too).
        ``dataset`` names the compiled dataset its lease holds (its id and example count)."""

        spec: dict[str, Any] = {
            "checkpoint_every": int(checkpoint_every),
            "recipe": recipe or {"name": "recommended", "settings": {"report_to": "none"}},
        }
        if dataset:
            spec["dataset"] = {"id": str(dataset.get("id") or "")[:128], "examples": int(dataset.get("examples") or 0)}
        _check_plan(spec, steps_total)
        with _LOCK:
            newest = self.all()
            # Newer than every job before it, even on a clock that stood still.
            now = max(float(self.clock()), (newest[0].created_at + 0.001) if newest else 0.0)
            sha = self.store.put_blob(json.dumps(spec, sort_keys=True).encode("utf-8"))
            job = Job(
                id=f"job_{uuid.uuid4().hex}", kind=KIND, created_at=now, updated_at=now, state="planned",
                spec_blob=sha, steps_total=int(steps_total), version=version or self.next_version(),
                trigger=trigger,  # type: ignore[arg-type]
                transitions=(JobTransition(state="planned", at=now),),
            )
            self.store.put(job)
        return job

    def replan(self, job: Job, *, steps_total: int, checkpoint_every: int, recipe: dict[str, Any]) -> Job:
        """A job that hasn't trained a step takes another recipe and step plan; its rows stay (O117)."""

        spec: dict[str, Any] = {"checkpoint_every": int(checkpoint_every), "recipe": recipe}
        dataset = self.plan_of(job).get("dataset")
        if dataset:
            spec["dataset"] = dataset
        _check_plan(spec, steps_total)
        with _LOCK:
            current = self.get(job.id) or job
            sha = self.store.put_blob(json.dumps(spec, sort_keys=True).encode("utf-8"))
            updated = replace(current, spec_blob=sha, steps_total=int(steps_total),
                              updated_at=max(float(self.clock()), current.updated_at))
            self.store.put(updated)
        return updated

    def plan_of(self, job: Job) -> dict[str, Any]:
        """The job's planned checkpoint interval and recipe (from its spec blob)."""

        default = {"checkpoint_every": max(1, min(int(job.steps_total or 1), 100)),
                   "recipe": {"name": "recommended", "settings": {"report_to": "none"}}}
        data = self.store.read_blob(job.spec_blob or "") if job.spec_blob else None
        if data is None:
            return default
        try:
            value = json.loads(data.decode("utf-8"))
            found = {"checkpoint_every": int(value["checkpoint_every"]), "recipe": dict(value["recipe"])}
            if isinstance(value.get("dataset"), dict):
                found["dataset"] = dict(value["dataset"])
            return found
        except (ValueError, KeyError, TypeError):
            return default

    def save(self, job: Job) -> Job:
        with _LOCK:
            self.store.put(job)
        return job

    def move(self, job_id: str, to: str, *, reason: str | None = None, expect: Iterable[str] | None = None,
             **changes: Any) -> Job:
        """Read, check and save one move. ``expect``: the states it may move from."""

        with _LOCK:
            job = self.get(job_id)
            if job is None:
                raise JobStateError("job_missing", job_id)
            if expect is not None and job.state not in set(expect):
                raise JobStateError("job_changed", f"{job.state}")
            moved = transition(job, to, at=float(self.clock()), reason=reason, **changes)
            self.store.put(moved)
            return moved

    def update(self, job_id: str, **changes: Any) -> Job | None:
        """Change fields without a state move (progress, a checkpoint). None when it's gone."""

        with _LOCK:
            job = self.get(job_id)
            if job is None:
                return None
            moved = replace(job, updated_at=max(float(self.clock()), job.updated_at), **changes)
            self.store.put(moved)
            return moved


# ── Recovery ────────────────────────────────────────────────────────────────


def resume_point(job_id: str, root: Path | None = None, *, at_most: int | None = None
                 ) -> tuple[Checkpoint | None, str | None]:
    """Where a job continues from: its newest good checkpoint, and how that was found.

    ``None`` as the second value when the newest was good; ``previous_checkpoint``
    when a newer one couldn't be used (the broken ones are deleted); ``start_over``
    when none could.
    """

    checkpoint, skipped = newest_good(job_id, root, at_most=at_most)
    if skipped or at_most is not None:
        for item in checkpoints(job_id, root):
            if checkpoint is None or item.step > checkpoint.step:
                discard_checkpoint(item)
    if checkpoint is None:
        return None, ("start_over" if skipped or at_most is not None else None)
    return checkpoint, ("previous_checkpoint" if skipped or at_most is not None else None)


def recover(jobs: TrainingJobs, *, live: Callable[[str], bool] = lambda job_id: False) -> list[Job]:
    """Jobs left preparing, running or pausing with no live worker are paused at their
    newest good checkpoint ("Training stopped when Row-Bot closed"). Returns them."""

    recovered: list[Job] = []
    if not jobs.store.exists():
        return recovered
    for job in jobs.all():
        if job.state not in LIVE or job.backend != "local" or live(job.id):
            continue
        checkpoint, how = resume_point(job.id, jobs.root)
        step = checkpoint.step if checkpoint is not None else 0
        try:
            # What's kept is the checkpoint: steps after it run again.
            moved = jobs.move(job.id, "paused", reason="app_closed", expect=LIVE, pause_reason="app_closed",
                              checkpoint_step=checkpoint.step if checkpoint else None, steps_done=step,
                              recovered=how)  # type: ignore[arg-type]
        except JobStateError:
            continue
        logger.info("Learning paused a training run that stopped when Row-Bot closed (step %d)", step)
        recovered.append(moved)
    return recovered


def sweep_runs(jobs: TrainingJobs) -> int:
    """Run folders of jobs that no longer exist (after Delete learning data, say) are removed."""

    folder = runs_dir(jobs.root)
    if not folder.is_dir() or not jobs.store.exists():
        return 0
    known = {job.id for job in jobs.all() if job.state not in ("stopped", "not_used")}
    removed = 0
    for entry in sorted(folder.iterdir()):
        if _ID.fullmatch(entry.name) and entry.name not in known and remove_run(entry.name, jobs.root):
            removed += 1
    return removed


__all__ = [
    "CURRENT",
    "Checkpoint",
    "HELD",
    "JobStateError",
    "KEEP_CHECKPOINTS",
    "PASSES_FILE",
    "KIND",
    "LIVE",
    "MODELS_FOLDER",
    "TERMINAL",
    "TRANSITIONS",
    "TrainingJobs",
    "can_move",
    "checkpoints",
    "good_checkpoint",
    "models_dir",
    "newest_good",
    "prune_checkpoints",
    "recover",
    "remove_all_runs",
    "remove_run",
    "resume_point",
    "run_dir",
    "runs_dir",
    "sweep_runs",
    "transition",
]
