"""Catalog updates: explicit, one source at a time, in the background; and a schedule
the user may turn on (off by default).

Nothing here contacts a catalog unless the user asked for an update or turned the
schedule on. A failed update keeps what the source had before.
"""
from __future__ import annotations

from collections.abc import Callable
import json
import logging
import threading
import time

from row_bot.integrations import index, sources
from row_bot.integrations.safe import write_atomic

logger = logging.getLogger(__name__)

INTERVALS = (1, 7, 30)
JOB = "integration_catalog_updates"
_LOCK = threading.RLock()
_RUNNING: dict[str, threading.Thread] = {}


def updatable() -> dict[str, sources.Source]:
    return {key: source for key, source in sources.SOURCES.items()
            if source.network == "explicit" and source.eligibility == "eligible"}


def _read() -> dict:
    try:
        value = json.loads((index.folder() / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(change: Callable[[dict], None]) -> dict:
    with _LOCK:
        value = _read()
        change(value)
        write_atomic(index.folder(create=True) / "state.json", json.dumps(value, indent=2, sort_keys=True))
        return value


def states() -> dict[str, dict]:
    """The last update of every updatable source, read once."""
    saved = _read().get("sources") or {}
    return {key: _view(key, saved.get(key) or {}) for key in updatable()}


def state(source_id: str) -> dict | None:
    """The last update of an updatable source: ``{state, updated_at, checked_at, error, entries}``."""
    if source_id not in updatable():
        return None
    return _view(source_id, (_read().get("sources") or {}).get(source_id) or {})


def _view(source_id: str, saved: dict) -> dict:
    running = source_id in _RUNNING
    return {"state": "updating" if running else saved.get("state", "never"), "updated_at": saved.get("updated_at"),
            "checked_at": saved.get("checked_at"), "error": "" if running else saved.get("error", ""),
            "entries": saved.get("entries")}


def update(source_id: str, *, wait: bool = False, cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Start one explicit update of a source; a running update is returned as it is."""
    source = updatable().get(source_id)
    if source is None:
        raise ValueError("not_updatable")
    with _LOCK:
        thread = _RUNNING.get(source_id)
        if thread is None:
            thread = threading.Thread(target=_run, args=(source, cancelled), daemon=True, name="catalog-update-" + source_id)
            _RUNNING[source_id] = thread
            thread.start()
    if wait:
        thread.join()
    return state(source_id)


def _run(source: sources.Source, cancelled: Callable[[], bool]) -> None:
    started = time.time()
    try:
        summary = source.update(cancelled)
        outcome = {"state": "done", "updated_at": started, "checked_at": started, "error": "",
                   "entries": summary.get("entries")}
    except Exception as exc:  # The source keeps what it had; the reason is a short public code.
        code = str(exc) if isinstance(exc, ValueError) and str(exc).replace("_", "").isalpha() else "source_unavailable"
        logger.warning("Catalog update of %s failed: %s", source.id, type(exc).__name__)
        outcome = {"state": "failed", "checked_at": started, "error": code[:64]}
    try:
        _write(lambda value: value.setdefault("sources", {}).setdefault(source.id, {}).update(outcome))
    except OSError:
        logger.warning("Catalog update state of %s was not saved", source.id)
    finally:
        with _LOCK:
            _RUNNING.pop(source.id, None)


def schedule() -> dict:
    """The user's choice: on or off (off by default), how often, and which catalogs (None: all)."""
    saved = _read().get("schedule") or {}
    interval, chosen = saved.get("interval_days"), saved.get("sources")
    return {"enabled": saved.get("enabled") is True, "interval_days": interval if interval in INTERVALS else 7,
            "sources": [key for key in chosen if key in updatable()] if isinstance(chosen, list) else None}


def set_schedule(*, enabled: bool, interval_days: int, sources: list[str] | None = None) -> dict:
    """The user's choice; the scheduler job exists only while it is on."""
    if interval_days not in INTERVALS or (sources is not None and set(sources) - set(updatable())):
        raise ValueError("invalid_catalog_schedule")
    _write(lambda value: value.update(schedule={"enabled": bool(enabled), "interval_days": interval_days,
                                                "sources": sorted(set(sources)) if sources is not None else None}))
    _register()
    return schedule()


def _register() -> None:
    from row_bot import tasks
    if schedule()["enabled"]:
        tasks._get_scheduler().add_job(run_due, trigger="interval", hours=6, id=JOB, replace_existing=True, coalesce=True,
                                       max_instances=1)
    elif tasks._scheduler is not None and tasks._scheduler.get_job(JOB) is not None:
        tasks._scheduler.remove_job(JOB)


def run_due(now: Callable[[], float] = time.time) -> list[str]:
    """Scheduled: update each source whose last update is older than the chosen interval."""
    chosen = schedule()
    if not chosen["enabled"]:
        return []
    chosen_sources = chosen["sources"] if chosen["sources"] is not None else list(updatable())
    due = [key for key in chosen_sources
           if now() - ((state(key) or {}).get("checked_at") or 0) >= chosen["interval_days"] * 86400]
    for key in due:
        update(key, wait=True)
    return due


def start() -> None:
    """At start-up: build the local Registry mirror if needed; schedule updates only if the user chose to."""
    try:
        if schedule()["enabled"]:
            _register()
    finally:
        index.ensure()
