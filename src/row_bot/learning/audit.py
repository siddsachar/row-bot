"""The audit vault (E10): hidden test tasks, sealed away from everything that could tune on them.

**What it holds**

- The packs' hidden splits (``packs.hidden``, sealed so only this module reads them).
- Replay tasks from the person's own recent work: held-out model-call decisions with
  their captured request and the action to compare against, copied in by
  :meth:`AuditVault.refresh`.
- Held-out corrections (O87): every 2nd confirmed correction (``holdout``), with its
  corrected action as the reference, copied in by :meth:`AuditVault.hold_corrections`.

**Where**: its own file, ``learning/audit.db`` in the data folder (never ``learning.db``),
created only while Learning is on. ``secure_delete`` is on, so purged tasks are
overwritten.

**Who may read it**: only ``learning.evaluation`` (``sealing``). Drafting, practice,
training and strategy code can't import this module or call its readers; any other code
may only use the deletion functions (:func:`forget_threads`, :func:`delete_vault`,
:func:`vault_threads`). Evaluation turns what it reads into aggregates and a novelty
yes/no; no task text leaves it.

**Every look is logged** (``looks``): who asked, the code that asked, when, and what
kind of answer it got, including refused attempts.

**Refreshed by time**: :meth:`AuditVault.refresh` walks new eligible decisions in the
order they were made and holds every :data:`HOLD_EVERY`-th one, nothing a strategy
can steer. Past :data:`MAX_REPLAY_TASKS` the oldest retire (their text is dropped; the
decision stays held out of training and drafting for good).

**Deletion reaches it**: a deleted or opted-out conversation's tasks leave the vault
(``store`` calls :func:`forget_threads`), and "Delete learning data" removes the file.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from row_bot import learning as gate
from row_bot.learning.sealing import Seal, require_reader, seal_module

logger = logging.getLogger(__name__)

VAULT_FILE = "audit.db"
# Fixed points (E4): how the vault is refreshed is not part of any strategy.
HOLD_EVERY = 4
MAX_REPLAY_TASKS = 400
# A proposed practice task closer than this to a hidden one (rarity-weighted word overlap) is not novel.
NOVELTY_OVERLAP = 0.5
_READER = "row_bot.learning.evaluation"
_LOCK = threading.RLock()
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in into is it its of on or that the then this to was what when "
    "which who with write read reply only your you my me i".split()
)


class AuditVaultError(RuntimeError):
    """A refused vault operation; ``code`` is stable."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def vault_path(root: Path | None = None) -> Path:
    return gate.folder_path(root) / VAULT_FILE


def vault_exists(root: Path | None = None) -> bool:
    try:
        return vault_path(root).is_file()
    except OSError:
        return False


def _schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE IF NOT EXISTS tasks ("
        " id TEXT PRIMARY KEY, origin TEXT NOT NULL, kind_of_work TEXT NOT NULL DEFAULT '',"
        " thread_id TEXT NOT NULL DEFAULT '', conversation_id TEXT NOT NULL DEFAULT '',"
        " source_at REAL NOT NULL, added_at REAL NOT NULL, retired_at REAL, content TEXT);"
        "CREATE INDEX IF NOT EXISTS tasks_thread ON tasks(thread_id);"
        "CREATE INDEX IF NOT EXISTS tasks_conversation ON tasks(conversation_id);"
        "CREATE TABLE IF NOT EXISTS looks ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, asked_by TEXT NOT NULL,"
        " caller TEXT NOT NULL, kind TEXT NOT NULL, answer TEXT NOT NULL, tasks INTEGER NOT NULL DEFAULT 0);"
    )


@contextmanager
def _connect(root: Path | None, *, create: bool) -> Iterator[sqlite3.Connection | None]:
    path = vault_path(root)
    with _LOCK:
        if not path.is_file():
            if not create:
                yield None
                return
            path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path.absolute().as_uri() + ("?mode=rwc" if create else "?mode=rw"), uri=True,
                                     timeout=30, isolation_level=None)
        try:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA secure_delete = ON")
            if create:
                try:
                    connection.execute("PRAGMA journal_mode = WAL")
                except sqlite3.OperationalError:
                    logger.debug("The audit vault could not switch to WAL", exc_info=True)
            _schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        finally:
            connection.close()


def _log(connection: sqlite3.Connection, at: float, asked_by: str, caller: str, kind: str, answer: str,
         tasks: int = 0) -> None:
    connection.execute(
        "INSERT INTO looks (at, asked_by, caller, kind, answer, tasks) VALUES (?, ?, ?, ?, ?, ?)",
        (float(at), str(asked_by or "")[:256], str(caller or "")[:256], kind, str(answer)[:256], int(tasks)),
    )


def _refused(caller: str, attribute: str) -> None:
    """A sealed-module refusal, logged as a look when the vault exists (never creates it)."""

    try:
        if vault_exists():
            with _connect(None, create=False) as connection:
                if connection is not None:
                    _log(connection, time.time(), "", caller, "refused", attribute)
    except Exception:  # noqa: BLE001 - logging a refusal never hides the refusal itself
        logger.debug("The audit vault could not log a refused look", exc_info=True)


# ── Deletion (anyone may call these; they return counts only) ───────────────


def forget_threads(thread_ids: Iterable[str], *, root: Path | None = None) -> int:
    """Remove the tasks a deleted or opted-out conversation gave the vault. Returns how many."""

    wanted = sorted({str(item) for item in thread_ids if item})
    if not wanted or not vault_exists(root):
        return 0
    removed = 0
    with _connect(root, create=False) as connection:
        if connection is None:
            return 0
        for start in range(0, len(wanted), 400):
            chunk = wanted[start:start + 400]
            marks = ", ".join("?" for _ in chunk)
            removed += connection.execute(
                f"DELETE FROM tasks WHERE thread_id IN ({marks}) OR conversation_id IN ({marks})", (*chunk, *chunk)
            ).rowcount
    if removed:
        try:
            with _LOCK:
                connection = sqlite3.connect(vault_path(root).absolute().as_uri() + "?mode=rw", uri=True, timeout=30)
                try:
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                finally:
                    connection.close()
        except sqlite3.Error:
            logger.debug("The audit vault checkpoint was deferred", exc_info=True)
    return removed


def vault_threads(*, root: Path | None = None) -> list[tuple[str, float]]:
    """The conversations the vault holds tasks from, with the newest task's time (the orphan sweep)."""

    if not vault_exists(root):
        return []
    with _connect(root, create=False) as connection:
        if connection is None:
            return []
        rows = connection.execute(
            "SELECT conversation_id, MAX(added_at) FROM tasks WHERE conversation_id != '' GROUP BY conversation_id"
        ).fetchall()
    return [(str(row[0]), float(row[1] or 0)) for row in rows]


def delete_vault(*, root: Path | None = None) -> bool:
    """Remove the vault file and its sidecars ("Delete learning data")."""

    path = vault_path(root)
    removed = False
    with _LOCK:
        for suffix in ("", "-wal", "-shm", "-journal"):
            target = Path(str(path) + suffix)
            try:
                if target.is_file():
                    target.unlink()
                    removed = True
            except OSError as error:
                raise AuditVaultError("audit_vault_busy") from error
    return removed


# ── The reader (evaluation only) ────────────────────────────────────────────


@dataclass(frozen=True)
class ReplayCandidate:
    """An eligible decision evaluation offers the vault: when it was made, whose it is, and
    its replay content (request and expected action). The vault decides what it holds."""

    decision_id: str
    created_at: float
    thread_id: str
    conversation_id: str
    kind_of_work: str
    content: Mapping[str, Any]


@dataclass(frozen=True)
class RefreshReport:
    considered: int
    added: tuple[str, ...]
    retired: int
    held: int


@dataclass(frozen=True)
class Look:
    at: float
    asked_by: str
    caller: str
    kind: str
    answer: str
    tasks: int


_SEAL = Seal(
    name=__name__,
    readers=frozenset({_READER}),
    open_names=frozenset({"forget_threads", "vault_threads", "delete_vault", "vault_exists", "vault_path",
                          "VAULT_FILE", "AuditVaultError", "HOLD_EVERY", "MAX_REPLAY_TASKS"}),
    on_refused=_refused,
)


def _words(text: str) -> set[str]:
    import re

    return {word.strip(".-") for word in re.findall(r"[a-z0-9][a-z0-9@.\-]*", str(text).casefold())
            if word.strip(".-") and word.strip(".-") not in _STOPWORDS}


def _novel(candidate: str, known: Sequence[Sequence[str]]) -> bool:
    """Whether ``candidate`` is far from every known task (each a few texts: a prompt, its files).

    Words are weighted by how rare they are among the known tasks, so the phrasing every task
    shares ("write only that file") counts for little and what makes a task itself counts most.
    """

    words = _words(candidate)
    if not words:
        return True
    tasks = [[_words(text) for text in texts if text] for texts in known]
    frequency: dict[str, int] = {}
    for texts in tasks:
        for word in set().union(*texts) if texts else set():
            frequency[word] = frequency.get(word, 0) + 1
    count = len(tasks)

    def weight(word: str) -> float:
        return math.log((count + 1) / (frequency.get(word, 0) + 1)) + 1e-3

    for texts in tasks:
        for other in texts:
            if not other:
                continue
            if other == words:
                return False
            shared = sum(weight(word) for word in words & other)
            union = sum(weight(word) for word in words | other)
            if union and shared / union >= NOVELTY_OVERLAP:
                return False
    return True


class AuditVault:
    """The vault in one data folder. Every method checks that ``evaluation`` is calling."""

    def __init__(self, root: Path | None = None, *, clock: Callable[[], float] = time.time) -> None:
        require_reader(_SEAL, "AuditVault")
        self._root = Path(root) if root is not None else gate.data_dir()
        self._clock = clock

    def _writable(self) -> bool:
        return gate.is_enabled(self._root) or vault_exists(self._root)

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        if not self._writable():
            raise AuditVaultError("learning_off")
        with _connect(self._root, create=True) as connection:
            assert connection is not None
            yield connection

    def _meta(self, connection: sqlite3.Connection, key: str, default: str = "") -> str:
        row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else default

    def _set_meta(self, connection: sqlite3.Connection, key: str, value: object) -> None:
        connection.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET"
                           " value = excluded.value", (key, str(value)))

    def horizon(self) -> float:
        """The creation time of the newest decision a refresh has considered (0 before any)."""

        require_reader(_SEAL, "horizon")
        if not vault_exists(self._root):
            return 0.0
        with _connect(self._root, create=False) as connection:
            if connection is None:
                return 0.0
            return float(self._meta(connection, "horizon", "0") or 0)

    def refresh(self, candidates: Sequence[ReplayCandidate], *, asked_by: str) -> RefreshReport:
        """Hold every :data:`HOLD_EVERY`-th new eligible decision, in the order they were made."""

        caller = require_reader(_SEAL, "refresh")
        now = float(self._clock())
        with self._open() as connection:
            horizon = float(self._meta(connection, "horizon", "0") or 0)
            seen = int(self._meta(connection, "seen", "0") or 0)
            fresh = sorted((item for item in candidates if item.created_at > horizon),
                           key=lambda item: (item.created_at, item.decision_id))
            added: list[str] = []
            for item in fresh:
                seen += 1
                if seen % HOLD_EVERY:
                    continue
                connection.execute(
                    "INSERT OR IGNORE INTO tasks (id, origin, kind_of_work, thread_id, conversation_id, source_at,"
                    " added_at, content) VALUES (?, 'replay', ?, ?, ?, ?, ?, ?)",
                    (item.decision_id, item.kind_of_work, item.thread_id, item.conversation_id, item.created_at, now,
                     json.dumps(dict(item.content), sort_keys=True, ensure_ascii=False)),
                )
                added.append(item.decision_id)
            if fresh:
                self._set_meta(connection, "horizon", repr(fresh[-1].created_at))
            self._set_meta(connection, "seen", seen)
            # The cap is the uncorrected decisions' (every 4th); held-out corrections are kept apart (O87).
            active = [str(row[0]) for row in connection.execute(
                "SELECT id FROM tasks WHERE retired_at IS NULL AND origin = 'replay'"
                " ORDER BY source_at DESC, id DESC")]
            retire = active[MAX_REPLAY_TASKS:]
            for task_id in retire:
                connection.execute("UPDATE tasks SET retired_at = ?, content = NULL WHERE id = ?", (now, task_id))
            held = len(active) - len(retire)
            _log(connection, now, asked_by, caller, "refresh", f"added {len(added)}", len(added))
        return RefreshReport(len(fresh), tuple(added), len(retire), held)

    def hold_corrections(self, candidates: Sequence[ReplayCandidate], *, asked_by: str) -> tuple[str, ...]:
        """Hold corrected decisions (O87) with their corrected action as the reference. Each one
        ``holdout`` held out comes here once it has a corrected action (no every-4th draw: holdout
        already chose every 2nd). A decision the vault already took as uncorrected, and that the
        person corrected later, becomes a correction task. Returns the ids added or converted."""

        caller = require_reader(_SEAL, "hold_corrections")
        now = float(self._clock())
        added: list[str] = []
        with self._open() as connection:
            for item in sorted(candidates, key=lambda found: (found.created_at, found.decision_id)):
                row = connection.execute("SELECT origin FROM tasks WHERE id = ?", (item.decision_id,)).fetchone()
                content = json.dumps(dict(item.content), sort_keys=True, ensure_ascii=False)
                if row is None:
                    connection.execute(
                        "INSERT INTO tasks (id, origin, kind_of_work, thread_id, conversation_id, source_at,"
                        " added_at, content) VALUES (?, 'correction', ?, ?, ?, ?, ?, ?)",
                        (item.decision_id, item.kind_of_work, item.thread_id, item.conversation_id, item.created_at,
                         now, content))
                elif row[0] == "replay":
                    connection.execute(
                        "UPDATE tasks SET origin = 'correction', content = ?, retired_at = NULL, added_at = ?"
                        " WHERE id = ?", (content, now, item.decision_id))
                else:
                    continue
                added.append(item.decision_id)
            _log(connection, now, asked_by, caller, "refresh", f"added {len(added)} corrections", len(added))
        return tuple(added)

    def correction_ids(self) -> set[str]:
        """The corrected decisions the vault holds (ids only, for evaluation's refresh)."""

        require_reader(_SEAL, "correction_ids")
        if not vault_exists(self._root):
            return set()
        with _connect(self._root, create=False) as connection:
            if connection is None:
                return set()
            return {str(row[0]) for row in connection.execute("SELECT id FROM tasks WHERE origin = 'correction'")}

    def retire_corrected(self, decision_ids: Iterable[str], *, asked_by: str) -> int:
        """Retire uncorrected tasks whose decision the person has since corrected, stopped or retried:
        their captured action is no longer a reference (O19's eligibility: no signal). Returns how many."""

        caller = require_reader(_SEAL, "retire_corrected")
        wanted = sorted({str(item) for item in decision_ids if item})
        if not wanted or not vault_exists(self._root):
            return 0
        now = float(self._clock())
        retired = 0
        with self._open() as connection:
            for start in range(0, len(wanted), 400):
                chunk = wanted[start:start + 400]
                marks = ", ".join("?" for _ in chunk)
                retired += connection.execute(
                    f"UPDATE tasks SET retired_at = ?, content = NULL WHERE origin = 'replay' AND retired_at IS NULL"
                    f" AND id IN ({marks})", (now, *chunk)).rowcount
            if retired:
                _log(connection, now, asked_by, caller, "refresh", f"retired {retired} corrected later", retired)
        return retired

    def replay_tasks(self, *, asked_by: str, origin: str | None = None) -> list[dict[str, Any]]:
        """The active replay tasks' content, for evaluation to run (``origin``: ``replay`` for the
        uncorrected decisions, ``correction`` for the held-out corrections; None for both). Logged."""

        caller = require_reader(_SEAL, "replay_tasks")
        where = "" if origin is None else " AND origin = ?"
        with self._open() as connection:
            rows = connection.execute(
                "SELECT id, kind_of_work, thread_id, conversation_id, source_at, content FROM tasks"
                " WHERE retired_at IS NULL AND content IS NOT NULL" + where + " ORDER BY source_at, id",
                () if origin is None else (origin,)).fetchall()
            _log(connection, self._clock(), asked_by, caller, "run",
                 "replay tasks" if origin is None else f"{origin} tasks", len(rows))
        found = []
        for row in rows:
            try:
                content = json.loads(row[5])
            except ValueError:
                continue
            found.append({"id": row[0], "kind_of_work": row[1], "thread_id": row[2], "conversation_id": row[3],
                          "created_at": row[4], **content})
        return found

    def hidden_tasks(self, *, asked_by: str, packs: Iterable[str] | None = None) -> list[Any]:
        """The packs' hidden tasks, for evaluation to run. Logged."""

        caller = require_reader(_SEAL, "hidden_tasks")
        from row_bot.learning.packs import hidden, validate

        wanted = None if packs is None else set(packs)
        tasks = [validate(task) for task in hidden.TASKS if wanted is None or task.pack in wanted]
        with self._open() as connection:
            _log(connection, self._clock(), asked_by, caller, "run", "hidden pack tasks", len(tasks))
        return tasks

    def is_novel(self, text: str, *, asked_by: str) -> bool:
        """Whether a proposed practice task is far enough from every hidden task. Logged as yes/no."""

        caller = require_reader(_SEAL, "is_novel")
        from row_bot.learning.packs import hidden

        known: list[tuple[str, ...]] = [(task.prompt, task.text()) for task in hidden.TASKS]
        with self._open() as connection:
            for (content,) in connection.execute(
                    "SELECT content FROM tasks WHERE retired_at IS NULL AND content IS NOT NULL"):
                try:
                    known.append((str(json.loads(content).get("text") or ""),))
                except ValueError:
                    continue
            novel = _novel(text, known)
            _log(connection, self._clock(), asked_by, caller, "novelty", "yes" if novel else "no")
        return novel

    def summary(self, *, asked_by: str) -> dict[str, int]:
        """How many tasks the vault holds, by kind (no ids, no text). Logged."""

        caller = require_reader(_SEAL, "summary")
        from row_bot.learning.packs import hidden

        with self._open() as connection:
            active = int(connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE retired_at IS NULL AND content IS NOT NULL").fetchone()[0])
            corrected = int(connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE retired_at IS NULL AND content IS NOT NULL"
                " AND origin = 'correction'").fetchone()[0])
            retired = int(connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE retired_at IS NOT NULL").fetchone()[0])
            _log(connection, self._clock(), asked_by, caller, "summary", "counts")
        return {"pack_tasks": len(hidden.TASKS), "replay_tasks": active, "correction_tasks": corrected,
                "retired_replay_tasks": retired}

    def looks(self, *, limit: int = 200) -> list[Look]:
        """The newest looks first."""

        require_reader(_SEAL, "looks")
        if not vault_exists(self._root):
            return []
        with _connect(self._root, create=False) as connection:
            if connection is None:
                return []
            rows = connection.execute(
                "SELECT at, asked_by, caller, kind, answer, tasks FROM looks ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),)).fetchall()
        return [Look(float(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4]), int(r[5])) for r in rows]


__all__ = [
    "AuditVault",
    "AuditVaultError",
    "HOLD_EVERY",
    "Look",
    "MAX_REPLAY_TASKS",
    "RefreshReport",
    "ReplayCandidate",
    "VAULT_FILE",
    "delete_vault",
    "forget_threads",
    "vault_exists",
    "vault_path",
    "vault_threads",
]

seal_module(__name__, _SEAL)
