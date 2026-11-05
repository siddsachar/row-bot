"""Stale skills: keeping saved skills consistent with what memory knows (research, off by default).

One setting turns it on: ``stale_skills.json`` in the data folder, ``{"enabled": true, "arm": "N"}``. A missing,
unreadable or not exactly ``true`` value is off: then every hook in the product returns at its first check, nothing is
written, no prompt text is added and no Dream phase runs (``tests/subsystem/knowledge_graph/test_stale_skills_off.py``).
There is no UI: it exists for the "Stale skills" study's scratch profiles.

When on, every arm keeps the same records:

- **fact validity** (:mod:`.facts`): each memory's facts (its description, its own properties, its relations) with
  ``valid_from``, ``valid_to`` and ``superseded_by``, written when a fact is updated, and an "as of" recall filter;
- **skill–memory links** (:mod:`.links`): the memories a user skill mentions, kept with the skill's last revision;
- **the operation log** (``stale_skills/operations.jsonl``): write, merge, supersede, delete, conflict, skill and patch
  events with their ids;
- **where each write came from** (:func:`current_origin`): the conversation it was written in or read from, a
  document, or neither (the person's own edit), so R and D count only changes from trusted sources (:mod:`.sources`).

The arm (:mod:`.arms`) adds at most one intervention: **R** drafts a ``patch_skill`` proposal overnight for a skill whose
linked memory changed (a Dream Cycle phase), **D** adds one dated line where such a skill is shown, **F** drafts a repair
after a run that used a skill failed its check. **N** adds none.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

SETTINGS_FILE = "stale_skills.json"
FOLDER = "stale_skills"
OPERATIONS_FILE = "operations.jsonl"
ARMS = ("N", "R", "D", "F")
_MAX_SETTINGS_BYTES = 4096


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    arm: str = "N"


OFF = Settings()
_cache: tuple[tuple[str, int, int], Settings] | None = None
_clock: Callable[[], datetime] = datetime.now
_cause: ContextVar[str] = ContextVar("stale_skills_cause", default="")
_origin: ContextVar[str] = ContextVar("stale_skills_origin", default="")
_log_lock = threading.Lock()


def settings_path() -> Path:
    return get_row_bot_data_dir(create=False) / SETTINGS_FILE


def settings() -> Settings:
    """The saved setting, cached until its file changes. Never raises; anything unexpected reads as off."""

    global _cache
    try:
        path = settings_path()
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            return OFF
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        cached = _cache
        if cached is not None and cached[0] == key:
            return cached[1]
        found = OFF
        if stat.st_size <= _MAX_SETTINGS_BYTES:
            document = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(document, dict) and document.get("enabled") is True:
                arm = document.get("arm", "N")
                found = Settings(True, arm if arm in ARMS else "N")
        _cache = (key, found)
        return found
    except Exception:
        logger.debug("Stale-skills setting unreadable; off", exc_info=True)
        return OFF


def invalidate() -> None:
    """Forget the cached setting (read again on the next check)."""

    global _cache
    _cache = None


def enabled() -> bool:
    return settings().enabled


def arm_on(arm: str) -> bool:
    """Whether the setting is on with this arm."""

    current = settings()
    return current.enabled and current.arm == arm


# ── Clock and causes ─────────────────────────────────────────────────────────


def now() -> datetime:
    """Now, as the validity records and skill revisions see it (a simulation sets its own clock)."""

    return _clock()


def set_clock(clock: Callable[[], datetime] | None) -> None:
    """Use ``clock`` for every record from now on (None: the real clock)."""

    global _clock
    _clock = clock or datetime.now


def current_cause() -> str:
    return _cause.get()


@contextmanager
def cause(name: str) -> Iterator[None]:
    """Writes inside are recorded with this cause (``merge``, ``enrich``, ``infer``, ``proposal:<id>``)."""

    token = _cause.set(name)
    try:
        yield
    finally:
        _cause.reset(token)


def current_origin() -> str:
    """Where a write now comes from: ``thread:<id>`` (the conversation memory extraction reads, or the agent's turn),
    ``document:<id>`` (document knowledge), or "" (neither: the person's own edit, on the Memory page say)."""

    found = _origin.get()
    if found:
        return found
    agent = sys.modules.get("row_bot.agent")
    thread_id = str(agent.get_current_thread_id() or "") if agent is not None else ""
    return f"thread:{thread_id}" if thread_id else ""


def origin_for(source: str, source_context: dict[str, Any] | None = None) -> str:
    """The origin of memory extraction's writes: its document, or the conversation it read."""

    if str(source or "").startswith("document:"):
        return str(source)
    thread_id = str((source_context or {}).get("thread_id") or "")
    return f"thread:{thread_id}" if thread_id else ""


@contextmanager
def origin(name: str) -> Iterator[None]:
    """Writes inside come from ``name`` (see :func:`current_origin`); "" leaves the current origin as it is."""

    token = _origin.set(name) if name else None
    try:
        yield
    finally:
        if token is not None:
            _origin.reset(token)


# ── The operation log ────────────────────────────────────────────────────────


def folder(*, create: bool = False) -> Path:
    path = get_row_bot_data_dir(create=create) / FOLDER
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def log(event: str, **fields: Any) -> str:
    """Append one event to the operation log and return its id (only while the setting is on)."""

    if not enabled():
        return ""
    record = {"id": uuid.uuid4().hex[:12], "at": now().isoformat(timespec="seconds"), "event": event,
              "cause": current_cause(), **fields}
    with _log_lock:
        target = folder(create=True) / OPERATIONS_FILE
        with open(target, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return record["id"]


def operations() -> list[dict[str, Any]]:
    """Every logged event, in order."""

    path = folder() / OPERATIONS_FILE
    if not path.is_file():
        return []
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            found.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return found


def read_json(name: str) -> dict[str, Any]:
    path = folder() / name
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(name: str, data: dict[str, Any]) -> None:
    path = folder(create=True) / name
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(temporary, path)


def parse_time(value: object) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return moment.replace(tzinfo=None)


def say_date(value: object) -> str:
    """A date as people write it: "Saturday 21 November 2026"."""

    moment = parse_time(value)
    return f"{moment.strftime('%A')} {moment.day} {moment.strftime('%B %Y')}" if moment else "an unknown date"


# ── Hooks: the product calls these; each returns at once while the setting is off ──


def _guarded(what: str, action: Callable[[], Any]) -> Any:
    try:
        return action()
    except Exception:
        # Research bookkeeping never breaks the write it follows.
        logger.warning("Stale skills: %s not recorded", what, exc_info=True)
        return None


def entity_written(before: dict[str, Any] | None, after: dict[str, Any] | None) -> None:
    """A memory was saved or updated (``before``: as it was, None when new)."""

    if not after or not enabled():
        return
    from row_bot.stale_skills import facts, links

    _guarded("memory write", lambda: (facts.entity_written(before, after), links.entity_changed(after)))


def entity_deleted(entity: dict[str, Any] | None) -> None:
    if not entity or not enabled():
        return
    from row_bot.stale_skills import facts, links

    _guarded("memory delete", lambda: (facts.entity_deleted(entity), links.entity_removed(str(entity["id"]))))


def entity_superseded(old: dict[str, Any] | None, new: dict[str, Any] | None) -> None:
    if not old or not new or not enabled():
        return
    from row_bot.stale_skills import facts

    _guarded("memory supersede", lambda: facts.entity_superseded(old, new))


def relation_added(relation: dict[str, Any] | None) -> None:
    if not relation or not enabled():
        return
    from row_bot.stale_skills import facts

    _guarded("relation write", lambda: facts.relation_added(relation))


def relation_deleted(relation: dict[str, Any] | None) -> None:
    if not relation or not enabled():
        return
    from row_bot.stale_skills import facts

    _guarded("relation delete", lambda: facts.relation_deleted(relation))


def skill_saved(skill: Any, *, created: bool) -> None:
    """A user skill was created or updated (it is linked, and its revision kept)."""

    if skill is None or not enabled():
        return
    from row_bot.stale_skills import links

    _guarded("skill links", lambda: links.skill_saved(skill, created=created))


def recall_as_of(candidates: list[dict[str, Any]], when: datetime | None) -> list[dict[str, Any]]:
    """Recall candidates as they stood at ``when`` (unchanged when off or ``when`` is None)."""

    if when is None or not enabled():
        return candidates
    from row_bot.stale_skills import facts

    found = _guarded("as-of recall", lambda: facts.apply_as_of(candidates, when))
    return candidates if found is None else found


def date_note(skill_name: str) -> str:
    """Arm D's line for a shown skill, or ""."""

    if not arm_on("D"):
        return ""
    from row_bot.stale_skills import arms

    return _guarded("date note", lambda: arms.date_note(skill_name)) or ""


__all__ = ["ARMS", "OFF", "Settings", "arm_on", "cause", "current_cause", "current_origin", "date_note", "enabled",
           "entity_deleted", "invalidate", "origin", "origin_for",
           "entity_superseded", "entity_written", "folder", "log", "now", "operations", "parse_time", "read_json",
           "recall_as_of", "relation_added", "relation_deleted", "say_date", "set_clock", "settings",
           "settings_path", "skill_saved", "write_json"]
