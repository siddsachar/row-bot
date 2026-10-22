"""Using a personal model for kinds of work: trial, keep, undo (D11, D24; designs/learning-ux.md §5).

The person decides, always (D24: new personal-model versions ask whatever the apply mode):

- **Use v1 for document lookups** starts a **trial**: requests of that kind go to the personal model,
  with fallback to the main model when it gets stuck (``routing.py``). A fallback counts as a cloud
  finish.
- After a few days *and* enough requests to say something (:data:`TRIAL_DAYS`,
  :data:`TRIAL_MIN_REQUESTS`), Overview asks **Keep v1 for document lookups?** with the trial's own
  numbers. **Keep** makes it in use; **Undo** (any time, trial or in use) goes back at once to the
  version used before for that kind of work, or to the main model.
- Deleting a version undoes it everywhere it is used.

The choices live in ``learning_models/use.json`` beside the versions (not in ``learning.db``), so
models in use stay in use when Learning is turned off or paused, and after "Delete learning data"
(UX §8: "Changes and models in use stay in use"). The file holds only counts and choices: kinds,
version numbers, the model each runs as, timestamps and tallies; never a request or an answer.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from row_bot.learning import kinds

logger = logging.getLogger(__name__)

USE_FILE = "use.json"
SCHEMA_VERSION = 1
#: A trial asks "Keep v1 …?" after this long *and* this many requests of the kind.
TRIAL_DAYS = 3
TRIAL_MIN_REQUESTS = 10
MAX_HISTORY = 200

State = Literal["trial", "in_use"]
Outcome = Literal["answered", "fallback"]
_LOCK = threading.RLock()


class UseError(ValueError):
    """A refused choice; ``code`` is stable (``unknown_kind``, ``not_ready``, ``not_in_use``, ``not_trial``)."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Tally:
    """Requests of a kind the personal model took: answered by it, or finished by the main model."""

    requests: int = 0
    answered: int = 0
    fallbacks: int = 0
    seconds: float = 0.0

    def add(self, outcome: str, seconds: float) -> Tally:
        return Tally(self.requests + 1, self.answered + (outcome == "answered"),
                     self.fallbacks + (outcome == "fallback"), round(self.seconds + max(0.0, float(seconds)), 3))

    @property
    def mean_seconds(self) -> float | None:
        return round(self.seconds / self.requests, 1) if self.requests else None


@dataclass(frozen=True)
class Assignment:
    """One kind of work given to one version."""

    kind: str
    version: int
    model_ref: str
    state: State
    since: float
    #: The version used for this kind before (Undo goes back to it); None: the main model.
    previous: int | None = None
    previous_ref: str | None = None
    #: The trial's own numbers (Keep …? shows them), and everything since it started.
    trial: Tally = field(default_factory=Tally)
    total: Tally = field(default_factory=Tally)
    kept_at: float | None = None
    #: "Not now" on Keep …? asks again after more requests.
    keep_asked_at: float | None = None
    keep_asked_requests: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Assignment | None:
        try:
            values = {key: data[key] for key in cls.__dataclass_fields__ if key in data}
            values["trial"] = Tally(**dict(values.get("trial") or {}))
            values["total"] = Tally(**dict(values.get("total") or {}))
            found = cls(**values)
        except (TypeError, KeyError, ValueError):
            return None
        if found.kind not in kinds.FAMILIES or found.state not in ("trial", "in_use") or found.version < 1:
            return None
        return found


@dataclass(frozen=True)
class UseState:
    assignments: Mapping[str, Assignment] = field(default_factory=dict)
    history: tuple[Mapping[str, Any], ...] = ()

    def for_version(self, version: int) -> list[Assignment]:
        return [item for item in self.assignments.values() if item.version == int(version)]

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION,
                "assignments": {kind: item.to_dict() for kind, item in sorted(self.assignments.items())},
                "history": [dict(item) for item in self.history[-MAX_HISTORY:]]}


def use_path(root: Path | None = None) -> Path:
    from row_bot.learning.training import jobs as jobs_module

    return jobs_module.models_dir(root) / USE_FILE


def exists(root: Path | None = None) -> bool:
    """A cheap check: whether any choice was ever saved (routing reads nothing without one)."""

    try:
        return use_path(root).is_file()
    except OSError:
        return False


def read(root: Path | None = None) -> UseState:
    """The saved choices. A file that can't be read reads as none (the main model answers)."""

    try:
        data = json.loads(use_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UseState()
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return UseState()
    found: dict[str, Assignment] = {}
    for kind, raw in (data.get("assignments") or {}).items():
        item = Assignment.from_dict(raw) if isinstance(raw, dict) else None
        if item is not None and item.kind == kind:
            found[kind] = item
    history = tuple(dict(item) for item in data.get("history") or () if isinstance(item, dict))[-MAX_HISTORY:]
    return UseState(found, history)


def _write(state: UseState, root: Path | None = None) -> UseState:
    from row_bot.learning import atomic

    atomic.write_text(use_path(root), json.dumps(state.to_dict(), indent=1, sort_keys=True))
    return state


def _event(at: float, event: str, kind: str, version: int | None, **extra: Any) -> dict[str, Any]:
    return {"at": round(float(at), 3), "event": event, "kind": kind, "version": version, **extra}


def _check_kind(kind: str) -> None:
    if kind not in kinds.FAMILIES:
        raise UseError("unknown_kind")


def start_trial(version: int, kind: str, model_ref: str, *, root: Path | None = None,
                clock: Callable[[], float] = time.time) -> Assignment:
    """"Use v1 for document lookups": a trial starts now (the person asked, D24)."""

    _check_kind(kind)
    if not model_ref or int(version) < 1:
        raise UseError("not_ready")
    with _LOCK:
        state = read(root)
        current = state.assignments.get(kind)
        now = float(clock())
        if current is not None and current.version == int(version):
            return current
        # Undo goes back to what was in use for this kind (a version kept before), else the main model.
        previous, previous_ref = None, None
        if current is not None:
            if current.state == "in_use":
                previous, previous_ref = current.version, current.model_ref
            else:
                previous, previous_ref = current.previous, current.previous_ref
        item = Assignment(kind=kind, version=int(version), model_ref=str(model_ref), state="trial", since=now,
                          previous=previous, previous_ref=previous_ref)
        assignments = {**state.assignments, kind: item}
        _write(UseState(assignments, (*state.history, _event(now, "trial", kind, int(version),
                                                              previous=previous))), root)
        return item


def keep(kind: str, *, root: Path | None = None, clock: Callable[[], float] = time.time) -> Assignment:
    """"Keep v1 for document lookups?" → Keep: in use from now on."""

    _check_kind(kind)
    with _LOCK:
        state = read(root)
        current = state.assignments.get(kind)
        if current is None:
            raise UseError("not_in_use")
        if current.state == "in_use":
            return current
        now = float(clock())
        kept = replace(current, state="in_use", kept_at=now)
        _write(UseState({**state.assignments, kind: kept},
                        (*state.history, _event(now, "kept", kind, current.version))), root)
        return kept


def not_now(kind: str, *, root: Path | None = None, clock: Callable[[], float] = time.time) -> Assignment:
    """"Keep …?" → Not now: the trial goes on; it asks again after more requests."""

    _check_kind(kind)
    with _LOCK:
        state = read(root)
        current = state.assignments.get(kind)
        if current is None or current.state != "trial":
            raise UseError("not_trial")
        later = replace(current, keep_asked_at=float(clock()), keep_asked_requests=current.trial.requests)
        _write(UseState({**state.assignments, kind: later}, state.history), root)
        return later


def undo(kind: str, *, root: Path | None = None, clock: Callable[[], float] = time.time,
         still_ready: Callable[[int], str | None] | None = None) -> Assignment | None:
    """Undo, at once: back to the version used before for this kind (when it is still ready; its
    model reference from ``still_ready``), else the main model. Returns what answers now (None: the
    main model)."""

    _check_kind(kind)
    with _LOCK:
        state = read(root)
        current = state.assignments.get(kind)
        if current is None:
            raise UseError("not_in_use")
        now = float(clock())
        assignments = dict(state.assignments)
        restored: Assignment | None = None
        if current.previous is not None:
            ref = still_ready(current.previous) if still_ready is not None else current.previous_ref
            if ref:
                restored = Assignment(kind=kind, version=current.previous, model_ref=ref, state="in_use",
                                      since=now, kept_at=now)
        if restored is None:
            assignments.pop(kind, None)
        else:
            assignments[kind] = restored
        _write(UseState(assignments, (*state.history, _event(now, "undone", kind, current.version,
                                                              back_to=restored.version if restored else None))),
               root)
        return restored


def forget_version(version: int, *, root: Path | None = None, clock: Callable[[], float] = time.time,
                   still_ready: Callable[[int], str | None] | None = None) -> list[str]:
    """A version is deleted: every kind it answers is undone (back to the previous version or the main
    model), and no kind keeps it as the one to go back to. Returns the kinds undone."""

    with _LOCK:
        state = read(root)
        undone = [item.kind for item in state.assignments.values() if item.version == int(version)]
        for kind in undone:
            undo(kind, root=root, clock=clock, still_ready=lambda found: None if found == int(version)
                 else (still_ready(found) if still_ready is not None else None))
        state = read(root)
        cleaned = {kind: (replace(item, previous=None, previous_ref=None) if item.previous == int(version) else item)
                   for kind, item in state.assignments.items()}
        if cleaned != dict(state.assignments) or undone:
            _write(UseState(cleaned, state.history), root)
        return undone


def record(kind: str, version: int, outcome: Outcome, seconds: float, *, root: Path | None = None) -> None:
    """One routed request ended: answered by the personal model, or finished by the main model."""

    if outcome not in ("answered", "fallback"):
        return
    with _LOCK:
        state = read(root)
        current = state.assignments.get(kind)
        if current is None or current.version != int(version):
            return
        updated = replace(current, total=current.total.add(outcome, seconds),
                          trial=current.trial.add(outcome, seconds) if current.state == "trial" else current.trial)
        _write(UseState({**state.assignments, kind: updated}, state.history), root)


def keep_due(item: Assignment, *, now: float | None = None) -> bool:
    """Whether a trial has run long enough, with enough requests, to ask "Keep …?"."""

    if item.state != "trial":
        return False
    moment = time.time() if now is None else float(now)
    if moment - item.since < TRIAL_DAYS * 86_400 or item.trial.requests < TRIAL_MIN_REQUESTS:
        return False
    if item.keep_asked_at is not None:
        # After "Not now", ask again once the trial has as many requests again.
        return item.trial.requests >= max(TRIAL_MIN_REQUESTS, 2 * item.keep_asked_requests)
    return True


def kinds_for(version: int, *, root: Path | None = None) -> list[Assignment]:
    return read(root).for_version(version)


__all__ = [
    "Assignment",
    "Outcome",
    "TRIAL_DAYS",
    "TRIAL_MIN_REQUESTS",
    "Tally",
    "USE_FILE",
    "UseError",
    "UseState",
    "exists",
    "forget_version",
    "keep",
    "keep_due",
    "kinds_for",
    "not_now",
    "read",
    "record",
    "start_trial",
    "undo",
    "use_path",
]
