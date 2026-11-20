"""Learning's local store: ``learning.db`` and ``learning/blobs/`` in the data folder.

- **Created only when Learning is turned on** (the first enable, :meth:`LearningStore.create`)
  or by the first write while it is on. Never by a read, never while Learning is off, never
  at import. While Learning is off nothing new is recorded: episodes, decisions, signals and
  blobs are refused. Maintenance on data that is already there (deletion purges, lesson
  marks, delete-all) still works, because turning Learning off keeps the data.
- **One SQLite file**, WAL, ``secure_delete`` on so purged rows are overwritten. Every
  operation opens and closes its own connection under one process lock, so no handle
  stays open (Windows can then remove the file) and writes never interleave.
- **Every record** from ``contracts`` is kept as its canonical JSON with a few indexed
  columns, plus link tables: which conversations own a record (``owners``), which it was
  derived from (``derivations``), its parents (``lineage``, for the archive of attempts and
  for releases) and the blobs it names (``blob_refs``).
- **Blobs** are SHA-256 addressed files, written atomically (a temp file, then a replace),
  deduplicated, and reference counted by the records naming them. Purges free the ones
  nothing references; :meth:`LearningStore.gc_blobs` also sweeps leftovers.
- **Every blob belongs to its chat** (P2). A model call's request names its messages, tools
  and pictures only inside its manifest blob (and a past chat's request its conversation's
  blob), so writing the decision records those blobs as the conversation's (``blob_owners``,
  one row per blob per conversation). Deleting or opting out the conversation frees them
  once no other conversation or record holds them. A one-off sweep
  (:meth:`LearningStore.sweep_orphans`) records the owners of what earlier builds wrote and
  removes blobs nothing references any more (counts only).
- **Pictures** (``pictures``) are blobs of their own, named by the messages they were in. Past
  :data:`PICTURE_CAP_BYTES` the oldest go first, never one a vault task's decision used; the
  decision and its text stay, and the picture is marked dropped.
- **Deleting or excluding a conversation** purges what it owns (episodes, decisions,
  signals, replay fixtures and their unreferenced blobs) and *marks* the lessons, releases,
  attempts and test runs derived from it (``source_chat_deleted`` / ``source_chat_excluded``)
  instead of deleting them, so a later version can offer "Retrain without it". It also
  reaches the audit vault (``learning/audit.db``, E10): the conversation's hidden tasks
  leave it, and "Delete learning data" removes the vault.
- **Held-out decisions**: the audit vault marks the decisions it holds (``held_out``);
  training data and drafting leave them out (:meth:`LearningStore.held_out_ids`).
- **Schema migrations** are versioned with ``PRAGMA user_version``; a store from a newer app
  is refused rather than changed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

from row_bot import learning as gate
from row_bot.learning import contracts
from row_bot.learning.contracts import (
    Attempt,
    ContractError,
    LessonRecord,
    Record,
    Release,
    derived_from_threads,
    owner_threads,
)

logger = logging.getLogger(__name__)

BLOBS_FOLDER = "blobs"
MAX_BLOB_BYTES = 64 * 1024 * 1024
# Leftover blob files (a write cut short) and unreferenced blobs are swept only
# once they are this old, so a sweep never races a write in progress.
BLOB_GRACE_SECONDS = 3600.0
# A captured model call's request (capture writes it): its messages, tools and pictures by hash.
REQUEST_FORMAT = "row_bot.learning.model_request"
# How much space pictures may take; past it the oldest go first (never a vault task's).
PICTURE_CAP_BYTES = 10 * 1000**3
# The one-off orphan sweep's result (meta), so it runs once per store.
SWEEP_META_KEY = "blob_sweep_v1"
# What an orphan was, by its content (the sweep's counts): fixed words, never content.
ORPHAN_KINDS = ("message", "tool_list", "model_request", "conversation", "past_chat_plan", "tool_call",
                "approval", "test_results", "pack_task", "picture", "unfinished_write", "other")

# What "recording" means: refused while Learning is off.
RECORDING_KINDS = frozenset({"episode", "decision", "signal", "blob"})

MARK_CHAT_DELETED = "source_chat_deleted"
MARK_CHAT_EXCLUDED = "source_chat_excluded"
MARK_MEMORY_DELETED = "memory_deleted"
# A decision the audit vault holds as a hidden test: never training data, never drafting evidence.
MARK_HELD_OUT = "held_out"
# A corrected decision held out as a hidden problem (O87; L3a puts it in the vault). It is also
# ``held_out``, so everything that leaves held-out decisions out leaves it out.
MARK_HELD_OUT_CORRECTION = "held_out_correction"
VAULT_FILE = "audit.db"

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
# A blob nothing holds: no record names it, and no conversation owns it (through a request it named).
_UNREFERENCED = ("NOT EXISTS (SELECT 1 FROM blob_refs r WHERE r.sha256 = b.sha256)"
                 " AND NOT EXISTS (SELECT 1 FROM blob_owners o WHERE o.sha256 = b.sha256)")
_LINEAGE_KINDS = ("attempt", "release")
_CHUNK = 500

# One lock for every store in this process: operations are short, and a
# delete-all must never race a writer that would re-create the file.
_LOCK = threading.RLock()

Migration = Callable[[sqlite3.Connection], None]


class LearningStoreError(RuntimeError):
    """A refused store operation; ``code`` is stable."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _schema_v1(connection: sqlite3.Connection) -> None:
    statements = (
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        # Every record: its canonical JSON (body) and the columns queries need.
        "CREATE TABLE records ("
        " kind TEXT NOT NULL, id TEXT NOT NULL, schema_version INTEGER NOT NULL,"
        " thread_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '',"
        " subkind TEXT NOT NULL DEFAULT '', kept INTEGER, memory_id TEXT,"
        " created_at REAL NOT NULL, body TEXT NOT NULL, PRIMARY KEY (kind, id))",
        "CREATE INDEX records_thread ON records(thread_id, created_at)",
        "CREATE INDEX records_kind_status ON records(kind, status, created_at)",
        "CREATE INDEX records_memory ON records(memory_id) WHERE memory_id IS NOT NULL",
        # Conversations that own a record: deleting or excluding one purges it.
        "CREATE TABLE owners (kind TEXT NOT NULL, id TEXT NOT NULL, thread_id TEXT NOT NULL,"
        " PRIMARY KEY (kind, id, thread_id))",
        "CREATE INDEX owners_thread ON owners(thread_id)",
        # Conversations a derived record was built from: deleting one marks it.
        "CREATE TABLE derivations (kind TEXT NOT NULL, id TEXT NOT NULL, thread_id TEXT NOT NULL,"
        " PRIMARY KEY (kind, id, thread_id))",
        "CREATE INDEX derivations_thread ON derivations(thread_id)",
        "CREATE TABLE lineage (kind TEXT NOT NULL, child_id TEXT NOT NULL, parent_id TEXT NOT NULL,"
        " PRIMARY KEY (kind, child_id, parent_id))",
        "CREATE INDEX lineage_parent ON lineage(kind, parent_id)",
        "CREATE TABLE marks (kind TEXT NOT NULL, id TEXT NOT NULL, mark TEXT NOT NULL,"
        " thread_id TEXT NOT NULL DEFAULT '', marked_at REAL NOT NULL,"
        " PRIMARY KEY (kind, id, mark, thread_id))",
        "CREATE TABLE blobs (sha256 TEXT PRIMARY KEY, size INTEGER NOT NULL, created_at REAL NOT NULL)",
        "CREATE TABLE blob_refs (sha256 TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,"
        " PRIMARY KEY (sha256, kind, id))",
        "CREATE INDEX blob_refs_record ON blob_refs(kind, id)",
        # "Don't learn from this chat" (thread_meta has no such flag, by design).
        "CREATE TABLE chat_optouts (thread_id TEXT PRIMARY KEY, created_at REAL NOT NULL)",
    )
    for statement in statements:
        connection.execute(statement)


def _schema_v2(connection: sqlite3.Connection) -> None:
    statements = (
        # The conversation each blob belongs to when a record names it only inside another blob (a request's
        # messages, tools and pictures; a past chat's conversation): deleting or excluding it frees them.
        "CREATE TABLE blob_owners (sha256 TEXT NOT NULL, thread_id TEXT NOT NULL, PRIMARY KEY (sha256, thread_id))",
        "CREATE INDEX blob_owners_thread ON blob_owners(thread_id)",
        # Pictures moved out of captured messages: their size for the cap, and when one was dropped.
        "CREATE TABLE pictures (sha256 TEXT PRIMARY KEY, size INTEGER NOT NULL, created_at REAL NOT NULL,"
        " dropped_at REAL)",
        "CREATE INDEX pictures_age ON pictures(dropped_at, created_at)",
        # The model calls that sent each picture (a vault task's pictures are never dropped).
        "CREATE TABLE picture_uses (sha256 TEXT NOT NULL, decision_id TEXT NOT NULL,"
        " PRIMARY KEY (sha256, decision_id))",
        "CREATE INDEX picture_uses_decision ON picture_uses(decision_id)",
    )
    for statement in statements:
        connection.execute(statement)


def _schema_v3(connection: sqlite3.Connection) -> None:
    statements = (
        # The blobs holding another conversation's words (a past-conversation search's matches) and that
        # conversation: excluding or deleting it frees them, even while the quoting chat still owns them.
        "CREATE TABLE quote_blobs (sha256 TEXT NOT NULL, thread_id TEXT NOT NULL, PRIMARY KEY (sha256, thread_id))",
        "CREATE INDEX quote_blobs_thread ON quote_blobs(thread_id)",
    )
    for statement in statements:
        connection.execute(statement)


# MIGRATIONS[n] takes a store at user_version n to n + 1.
MIGRATIONS: tuple[Migration, ...] = (_schema_v1, _schema_v2, _schema_v3)
SCHEMA_VERSION = len(MIGRATIONS)


@dataclass(frozen=True)
class PurgeReport:
    """What a conversation's purge removed and marked."""

    thread_id: str
    removed: tuple[tuple[str, int], ...] = ()
    blobs_freed: int = 0
    marked: tuple[tuple[str, str], ...] = ()

    @property
    def total(self) -> int:
        return sum(count for _, count in self.removed)

    def removed_of(self, kind: str) -> int:
        return dict(self.removed).get(kind, 0)


@dataclass
class _Owning:
    """What one write already recorded as a conversation's: a turn's calls share their messages, a past
    chat's turns its conversation, so each (blob, conversation) is recorded once."""

    conversations: dict[str, list[str]] = field(default_factory=dict)
    owned: set[tuple[str, str]] = field(default_factory=set)


@dataclass(frozen=True)
class SweepReport:
    """What the orphan sweep removed (or, without removing, found): files and bytes, in all and by kind."""

    files: int = 0
    bytes: int = 0
    kinds: tuple[tuple[str, int, int], ...] = ()
    owners_recorded: int = 0

    def to_dict(self) -> dict[str, object]:
        return {"files": self.files, "bytes": self.bytes,
                "kinds": {kind: {"files": files, "bytes": size} for kind, files, size in self.kinds}}


@dataclass(frozen=True)
class PictureStats:
    """Pictures kept (count, bytes), dropped under the cap, and the cap."""

    stored: int = 0
    bytes: int = 0
    dropped: int = 0
    cap_bytes: int = PICTURE_CAP_BYTES


@dataclass(frozen=True)
class Mark:
    kind: str
    id: str
    mark: str
    thread_id: str
    marked_at: float


@dataclass(frozen=True)
class ReleaseSummary:
    id: str
    kind: str
    version: int
    label: str
    status: str


@dataclass(frozen=True)
class DeleteAllReport:
    """What "Delete learning data…" removed, and the personal-model versions that
    were trained on it (their files are kept until they are retrained)."""

    deleted: bool
    bytes_freed: int = 0
    trained_releases: tuple[ReleaseSummary, ...] = ()
    kept_opt_outs: int = 0


def _thread(thread_id: str) -> str:
    clean = str(thread_id or "")
    if not contracts.valid_thread_id(clean):
        raise ValueError("Conversation id is not valid.")
    return clean


def _columns(record: Record) -> tuple[str, str, str, int | None, str | None, float]:
    """(thread_id, status, subkind, kept, memory_id, created_at) for one record."""

    if isinstance(record, contracts.Episode):
        return record.thread_id, record.status, record.source, None, None, record.started_at
    if isinstance(record, contracts.Decision):
        return record.thread_id, record.status, record.kind, None, None, record.created_at
    if isinstance(record, contracts.Signal):
        status = "withdrawn" if record.withdrawn_at is not None else "active"
        return record.thread_id, status, record.kind, None, None, record.created_at
    if isinstance(record, LessonRecord):
        return "", record.status, record.origin, None, record.memory_id, record.created_at
    if isinstance(record, contracts.TaskFixture):
        return record.thread_id or "", "", record.origin, None, None, record.created_at
    if isinstance(record, contracts.TestRun):
        return "", record.status, record.subject_kind, None, None, record.started_at
    if isinstance(record, Attempt):
        return "", record.outcome, record.change_kind, int(record.kept), None, record.created_at
    if isinstance(record, Release):
        return "", record.status, record.kind, None, None, record.created_at
    if isinstance(record, contracts.Job):
        return "", record.state, record.kind, None, None, record.created_at
    raise TypeError(f"not a Learning record: {type(record).__name__}")


def _chunks(values: Sequence[str]) -> Iterator[Sequence[str]]:
    for start in range(0, len(values), _CHUNK):
        yield values[start : start + _CHUNK]


class LearningStore:
    """``learning.db`` and its blobs for one data folder (the current one by default).

    Constructing a store touches nothing. ``clock`` (epoch seconds) is injectable
    for tests; ``migrations`` too, to exercise a schema upgrade.
    """

    def __init__(
        self,
        data_dir: Path | str | None = None,
        *,
        clock: Callable[[], float] = time.time,
        migrations: Sequence[Migration] | None = None,
        picture_cap: int = PICTURE_CAP_BYTES,
    ) -> None:
        self._data_dir = Path(data_dir) if data_dir is not None else None
        self._clock = clock
        self._migrations = tuple(migrations) if migrations is not None else MIGRATIONS
        self.picture_cap = max(0, int(picture_cap))

    def now(self) -> float:
        """The store's clock (epoch seconds): what Learning's passes over it call now."""

        return float(self._clock())

    # ── Paths ────────────────────────────────────────────────────────────

    @property
    def root(self) -> Path:
        return self._data_dir if self._data_dir is not None else gate.data_dir()

    @property
    def path(self) -> Path:
        return gate.store_path(self.root)

    @property
    def blobs_dir(self) -> Path:
        return gate.folder_path(self.root) / BLOBS_FOLDER

    def exists(self) -> bool:
        return gate.store_exists(self.root)

    def _blob_path(self, sha: str) -> Path:
        return self.blobs_dir / sha[:2] / sha

    # ── Connections and schema ───────────────────────────────────────────

    def _open(self, *, create: bool = False) -> sqlite3.Connection:
        # mode=rw never creates a missing file, so a read can't bring the store into being.
        uri = self.path.absolute().as_uri() + ("?mode=rwc" if create else "?mode=rw")
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)
        except sqlite3.OperationalError as error:
            if not create and not self.exists():
                raise LearningStoreError("store_missing") from error
            raise
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("PRAGMA synchronous = NORMAL")
            self._ensure_schema(connection)
        except BaseException:
            connection.close()
            raise
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        target = len(self._migrations)
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > target:
            raise LearningStoreError("store_newer_than_app")
        if version == target:
            return
        connection.execute("BEGIN IMMEDIATE")
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > target:
                raise LearningStoreError("store_newer_than_app")
            for index in range(version, target):
                self._migrations[index](connection)
                connection.execute(f"PRAGMA user_version = {index + 1}")
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with _LOCK:
            if not self.exists():
                raise LearningStoreError("store_missing")
            connection = self._open()
            try:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                    connection.execute("COMMIT")
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise
            finally:
                connection.close()

    @contextmanager
    def _reader(self) -> Iterator[sqlite3.Connection | None]:
        """A read connection, or None when there is no store (nothing is created)."""

        with _LOCK:
            if not self.exists():
                yield None
                return
            connection = self._open()
            try:
                yield connection
            finally:
                connection.close()

    def _checkpoint(self) -> None:
        """Fold the WAL back so purged pages don't linger in it."""

        try:
            with _LOCK:
                connection = self._open()
                try:
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                finally:
                    connection.close()
        except (sqlite3.Error, LearningStoreError):
            logger.debug("Learning store checkpoint deferred", exc_info=True)

    def create(self) -> None:
        """Create ``learning.db`` and ``learning/blobs/`` (turning Learning on). Idempotent."""

        with _LOCK:
            self.root.mkdir(parents=True, exist_ok=True)
            connection = self._open(create=True)
            try:
                try:
                    connection.execute("PRAGMA journal_mode = WAL")
                except sqlite3.OperationalError:
                    logger.debug("Learning store could not switch to WAL", exc_info=True)
                connection.execute(
                    "INSERT OR IGNORE INTO meta (key, value) VALUES ('created_at', ?)",
                    (repr(float(self._clock())),),
                )
            finally:
                connection.close()
            self.blobs_dir.mkdir(parents=True, exist_ok=True)

    def schema_version(self) -> int | None:
        """The store's schema version, or None when there is no store."""

        if not self.exists():
            return None
        with _LOCK:
            uri = self.path.absolute().as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=30)
            try:
                return int(connection.execute("PRAGMA user_version").fetchone()[0])
            finally:
                connection.close()

    def _writable(self, kinds: Iterable[str]) -> None:
        """Refuse what may not be written now; create the store on the first write while on."""

        if gate.is_enabled(self.root):
            if not self.exists():
                self.create()
            return
        if RECORDING_KINDS.intersection(kinds) or not self.exists():
            raise LearningStoreError("learning_off")

    # ── Records ──────────────────────────────────────────────────────────

    def put(self, record: Record) -> bool:
        """Save (or replace) one record. Returns False when its chat is opted out."""

        return self.put_many([record]) == 1

    def put_many(self, records: Iterable[Record]) -> int:
        """Save records in one transaction; returns how many were saved.

        Records owned by a chat that is opted out are skipped: scope stops them
        first, and this is the last line.
        """

        items = list(records)
        if not items:
            return 0
        for item in items:
            if not isinstance(item, Record) or item.KIND not in contracts.RECORD_TYPES:
                raise TypeError("not a Learning record")
        self._writable({item.KIND for item in items})
        saved = 0
        moved = False
        owning = _Owning()
        with self._transaction() as connection:
            for item in items:
                threads = owner_threads(item)
                if threads and self._opted_out(connection, threads):
                    logger.debug("Learning skipped a %s from an opted-out chat", item.KIND)
                    continue
                moved = moved or self._state_moves(connection, item)
                self._write(connection, item, owning)
                saved += 1
        if moved:
            gate.changed()
        return saved

    @staticmethod
    def _state_moves(connection: sqlite3.Connection, record: Record) -> bool:
        """A record other than capture's that is new or changes state (a job planned, running or done; a
        release or a test run): what the Learning screens show moved on (O127). Progress alone doesn't."""

        if record.KIND in RECORDING_KINDS:
            return False
        row = connection.execute(
            "SELECT status FROM records WHERE kind = ? AND id = ?", (record.KIND, record.id)
        ).fetchone()
        return row is None or row[0] != _columns(record)[1]

    def _write(self, connection: sqlite3.Connection, record: Record, owning: _Owning | None = None) -> None:
        body = contracts.to_json(record)
        thread_id, status, subkind, kept, memory_id, created_at = _columns(record)
        key = (record.KIND, record.id)
        connection.execute(
            "INSERT INTO records (kind, id, schema_version, thread_id, status, subkind, kept, memory_id,"
            " created_at, body) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(kind, id) DO UPDATE SET schema_version = excluded.schema_version,"
            " thread_id = excluded.thread_id, status = excluded.status, subkind = excluded.subkind,"
            " kept = excluded.kept, memory_id = excluded.memory_id, created_at = excluded.created_at,"
            " body = excluded.body",
            (*key, record.SCHEMA_VERSION, thread_id, status, subkind, kept, memory_id, created_at, body),
        )
        for table in ("owners", "derivations", "blob_refs"):
            connection.execute(f"DELETE FROM {table} WHERE kind = ? AND id = ?", key)
        connection.execute("DELETE FROM lineage WHERE kind = ? AND child_id = ?", key)
        connection.executemany(
            "INSERT OR IGNORE INTO owners (kind, id, thread_id) VALUES (?, ?, ?)",
            [(*key, thread) for thread in owner_threads(record)],
        )
        connection.executemany(
            "INSERT OR IGNORE INTO derivations (kind, id, thread_id) VALUES (?, ?, ?)",
            [(*key, thread) for thread in derived_from_threads(record)],
        )
        connection.executemany(
            "INSERT OR IGNORE INTO blob_refs (sha256, kind, id) VALUES (?, ?, ?)",
            [(sha, *key) for sha in sorted(record.blob_refs())],
        )
        if record.KIND in _LINEAGE_KINDS:
            connection.executemany(
                "INSERT OR IGNORE INTO lineage (kind, child_id, parent_id) VALUES (?, ?, ?)",
                [(*key, parent) for parent in getattr(record, "parent_ids", ())],
            )
        if isinstance(record, contracts.Decision) and record.request_blob:
            self._own_request(connection, record, owning or _Owning())
        if isinstance(record, contracts.Decision) and record.quoted_thread_ids:
            self._own_quotes(connection, record, owning or _Owning())

    def _blob_json(self, sha: object) -> object:
        data = self.read_blob(sha) if isinstance(sha, str) else None
        if data is None:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None

    def _request_blobs(self, sha: str, conversations: dict[str, list[str]]) -> tuple[list[str], list[str]]:
        """The blobs a request manifest names (messages, tools, a past chat's conversation and its messages)
        and, apart, its pictures. Empty when the manifest is gone or isn't one. ``conversations`` keeps the
        past chats' conversations already read."""

        manifest = self._blob_json(sha)
        if not isinstance(manifest, dict) or manifest.get("format") != REQUEST_FORMAT:
            return [], []
        names = [str(item) for item in manifest.get("messages") or () if isinstance(item, str)]
        if isinstance(manifest.get("tools"), str):
            names.append(manifest["tools"])
        conversation = manifest.get("messages_of")
        if isinstance(conversation, str):
            if conversation not in conversations:
                body = self._blob_json(conversation)
                conversations[conversation] = [str(item) for item in (body.get("messages") or ())
                                               if isinstance(item, str)] if isinstance(body, dict) else []
            names += [conversation, *conversations[conversation]]
        found = [str(item) for item in manifest.get("pictures") or () if isinstance(item, str)]
        return [name for name in names if _SHA256.fullmatch(name)], [name for name in found if _SHA256.fullmatch(name)]

    def _own_quotes(self, connection: sqlite3.Connection, decision: contracts.Decision, owning: _Owning) -> None:
        """The blobs of a step that quotes other conversations (``quoted_thread_ids``) that hold their words:
        each quoted conversation has them too, so excluding or deleting it frees them (``_purge``). Every
        step naming such a blob quotes that conversation as well, so it is purged with it."""

        from row_bot.tool_outputs import QUOTED_CONVERSATIONS_KEY

        names: list[str] = []
        if decision.request_blob:
            names, _pictures = self._request_blobs(str(decision.request_blob), owning.conversations)
        if decision.result_blob:
            names.append(str(decision.result_blob))
        wanted = set(decision.quoted_thread_ids)
        rows: list[tuple[str, str]] = []
        for sha in dict.fromkeys(names):
            body = self._blob_json(sha)
            data = body.get("data") if isinstance(body, dict) else None
            extra = data.get("additional_kwargs") if isinstance(data, dict) else None
            quoted = extra.get(QUOTED_CONVERSATIONS_KEY) if isinstance(extra, dict) else None
            if isinstance(quoted, list):
                rows.extend((sha, str(thread)) for thread in quoted if str(thread) in wanted)
        connection.executemany("INSERT OR IGNORE INTO quote_blobs (sha256, thread_id) VALUES (?, ?)", rows)

    def _own_request(self, connection: sqlite3.Connection, decision: contracts.Decision, owning: _Owning) -> int:
        """The blobs a decision's request names become its conversation's; its pictures are counted for the
        cap and remember the call that sent them. Returns how many owner rows were new."""

        names, found = self._request_blobs(str(decision.request_blob), owning.conversations)
        owner = decision.conversation_id or decision.thread_id
        fresh = [(sha, owner) for sha in dict.fromkeys([*names, *found]) if (sha, owner) not in owning.owned]
        owning.owned.update(fresh)
        before = connection.total_changes
        connection.executemany("INSERT OR IGNORE INTO blob_owners (sha256, thread_id) VALUES (?, ?)", fresh)
        added = connection.total_changes - before
        now = float(self._clock())
        for sha in found:
            row = connection.execute("SELECT size FROM blobs WHERE sha256 = ?", (sha,)).fetchone()
            if row is None:
                continue  # dropped (or never written): nothing to count
            connection.execute(
                "INSERT INTO pictures (sha256, size, created_at) VALUES (?, ?, ?) ON CONFLICT(sha256) DO UPDATE"
                " SET dropped_at = NULL, created_at = CASE WHEN pictures.dropped_at IS NULL THEN pictures.created_at"
                " ELSE excluded.created_at END", (sha, int(row[0]), now))
            connection.execute("INSERT OR IGNORE INTO picture_uses (sha256, decision_id) VALUES (?, ?)",
                               (sha, decision.id))
        return added

    @staticmethod
    def _parse(rows: Iterable[sqlite3.Row]) -> list[Record]:
        found: list[Record] = []
        for row in rows:
            try:
                found.append(contracts.from_json(str(row["body"])))
            except ContractError:
                logger.warning("Learning store skipped an unreadable %s record", row["kind"])
        return found

    def get(self, kind: str, record_id: str) -> Record | None:
        with self._reader() as connection:
            if connection is None:
                return None
            rows = connection.execute(
                "SELECT kind, body FROM records WHERE kind = ? AND id = ?", (kind, record_id)
            ).fetchall()
        found = self._parse(rows)
        return found[0] if found else None

    def remove(self, kind: str, record_id: str) -> bool:
        """Delete one record, its links and the blobs only it named (Undo on a Teach).

        Never creates a store; False when there was nothing to remove.
        """

        if not self.exists():
            return False
        with _LOCK:
            with self._transaction() as connection:
                shas = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT sha256 FROM blob_refs WHERE kind = ? AND id = ?", (kind, record_id)
                    )
                }
                removed = connection.execute(
                    "DELETE FROM records WHERE kind = ? AND id = ?", (kind, record_id)
                ).rowcount
                for table in ("owners", "derivations", "blob_refs", "marks"):
                    connection.execute(f"DELETE FROM {table} WHERE kind = ? AND id = ?", (kind, record_id))
                connection.execute("DELETE FROM lineage WHERE kind = ? AND child_id = ?", (kind, record_id))
                if kind == "decision":
                    connection.execute("DELETE FROM picture_uses WHERE decision_id = ?", (record_id,))
            if removed:
                self._checkpoint()
            if shas:
                self.gc_blobs(shas)
        return bool(removed)

    def find(
        self,
        kind: str,
        *,
        thread_id: str | None = None,
        status: str | None = None,
        subkind: str | None = None,
        kept: bool | None = None,
        memory_id: str | None = None,
        limit: int = 1000,
        newest_first: bool = False,
    ) -> list[Record]:
        """Records of one kind, oldest first (or newest first), filtered on the indexed columns.

        ``status`` is an episode's or run's status, an attempt's outcome or a job's
        state; ``subkind`` is a decision's or signal's kind, a release's kind, an
        attempt's change kind, a lesson's origin or a fixture's origin.
        """

        clauses = ["kind = ?"]
        params: list[object] = [kind]
        for column, value in (
            ("thread_id", thread_id),
            ("status", status),
            ("subkind", subkind),
            ("memory_id", memory_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if kept is not None:
            clauses.append("kept = ?")
            params.append(int(kept))
        params.append(max(1, min(int(limit), 100_000)))
        order = " ORDER BY created_at DESC, id DESC" if newest_first else " ORDER BY created_at, id"
        with self._reader() as connection:
            if connection is None:
                return []
            rows = connection.execute(
                "SELECT kind, body FROM records WHERE " + " AND ".join(clauses) + order + " LIMIT ?",
                params,
            ).fetchall()
        return self._parse(rows)

    def owned_by(self, thread_id: str) -> list[Record]:
        """Everything a conversation owns (what deleting it would purge)."""

        with self._reader() as connection:
            if connection is None:
                return []
            rows = connection.execute(
                "SELECT r.kind, r.body FROM records r JOIN owners o ON o.kind = r.kind AND o.id = r.id"
                " WHERE o.thread_id = ? ORDER BY r.created_at, r.id",
                (thread_id,),
            ).fetchall()
        return self._parse(rows)

    # ── The archive ──────────────────────────────────────────────────────

    def attempts(
        self,
        *,
        outcome: str | None = None,
        kept: bool | None = None,
        change_kind: str | None = None,
        strategy_release_id: str | None = None,
        kind_of_work: str | None = None,
        limit: int = 1000,
    ) -> list[Attempt]:
        """Attempts, kept or not, filtered by outcome, change kind, strategy or kind of work."""

        found = self.find(
            "attempt", status=outcome, subkind=change_kind, kept=kept, limit=100_000
        )
        attempts = [
            item
            for item in found
            if isinstance(item, Attempt)
            and (strategy_release_id is None or item.strategy_release_id == strategy_release_id)
            and (kind_of_work is None or item.kind_of_work == kind_of_work)
        ]
        return attempts[: max(0, int(limit))]

    def _walk(self, kind: str, record_id: str, *, up: bool) -> list[Record]:
        if kind not in _LINEAGE_KINDS:
            raise ValueError("Only attempts and releases have lineage.")
        with self._reader() as connection:
            if connection is None:
                return []
            seen: set[str] = {record_id}
            order: list[str] = []
            frontier = [record_id]
            while frontier:
                column, other = ("child_id", "parent_id") if up else ("parent_id", "child_id")
                next_frontier: list[str] = []
                for chunk in _chunks(frontier):
                    marks = ", ".join("?" for _ in chunk)
                    for row in connection.execute(
                        f"SELECT {other} FROM lineage WHERE kind = ? AND {column} IN ({marks})"
                        f" ORDER BY {other}",
                        (kind, *chunk),
                    ):
                        value = str(row[0])
                        if value not in seen:
                            seen.add(value)
                            order.append(value)
                            next_frontier.append(value)
                frontier = next_frontier
            rows: list[sqlite3.Row] = []
            for chunk in _chunks(order):
                marks = ", ".join("?" for _ in chunk)
                rows.extend(
                    connection.execute(
                        f"SELECT kind, id, body FROM records WHERE kind = ? AND id IN ({marks})",
                        (kind, *chunk),
                    ).fetchall()
                )
        by_id = {str(row["id"]): row for row in rows}
        return self._parse(by_id[item] for item in order if item in by_id)

    def ancestors(self, kind: str, record_id: str) -> list[Record]:
        """An attempt's or release's ancestors, nearest generation first."""

        return self._walk(kind, record_id, up=True)

    def descendants(self, kind: str, record_id: str) -> list[Record]:
        """An attempt's or release's descendants, nearest generation first."""

        return self._walk(kind, record_id, up=False)

    def marks(self, kind: str | None = None, record_id: str | None = None) -> list[Mark]:
        with self._reader() as connection:
            if connection is None:
                return []
            clauses, params = [], []
            if kind is not None:
                clauses.append("kind = ?")
                params.append(kind)
            if record_id is not None:
                clauses.append("id = ?")
                params.append(record_id)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = connection.execute(
                "SELECT kind, id, mark, thread_id, marked_at FROM marks" + where
                + " ORDER BY marked_at, kind, id, mark, thread_id",
                params,
            ).fetchall()
        return [Mark(str(r[0]), str(r[1]), str(r[2]), str(r[3]), float(r[4])) for r in rows]

    # ── Blobs ────────────────────────────────────────────────────────────

    def put_blob(self, data: bytes) -> str:
        """Store content once, by its SHA-256; returns the hash. Refused while Learning is off."""

        return self.put_blobs([data])[0]

    def put_blobs(self, datas: Sequence[bytes]) -> list[str]:
        """Store several contents at once (one transaction); returns their hashes in order.

        Content already stored is not written again. Refused while Learning is off.
        """

        items = list(datas)
        for data in items:
            if type(data) is not bytes:
                raise TypeError("A blob is bytes.")
            if len(data) > MAX_BLOB_BYTES:
                raise LearningStoreError("blob_too_large")
        if not items:
            return []
        self._writable({"blob"})
        hashes = [hashlib.sha256(data).hexdigest() for data in items]
        unique = dict(zip(hashes, items, strict=True))
        with _LOCK:
            for sha, data in unique.items():
                self._write_blob_file(sha, data)
            now = float(self._clock())
            with self._transaction() as connection:
                connection.executemany(
                    "INSERT OR IGNORE INTO blobs (sha256, size, created_at) VALUES (?, ?, ?)",
                    [(sha, len(data), now) for sha, data in unique.items()],
                )
        return hashes

    def _write_blob_file(self, sha: str, data: bytes) -> None:
        path = self._blob_path(sha)
        if path.is_file() and path.stat().st_size == len(data):
            return
        staging = self.blobs_dir / "tmp"
        staging.mkdir(parents=True, exist_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = staging / f"{sha}.{uuid.uuid4().hex}.tmp"
        try:
            with open(temporary, "xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def read_blob(self, sha: str) -> bytes | None:
        """A blob's content, or None when it is missing or damaged."""

        if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
            return None
        with _LOCK:
            path = self._blob_path(sha)
            try:
                data = path.read_bytes()
            except OSError:
                return None
        return data if hashlib.sha256(data).hexdigest() == sha else None

    def gc_blobs(self, candidates: Iterable[str] | None = None) -> int:
        """Free blobs no record names; returns how many were freed.

        With ``candidates`` (a purge's blobs) they go at once. Without, every
        unreferenced blob older than the grace period goes, and blob files left
        by an interrupted write are swept too.
        """

        if not self.exists():
            return 0
        now = float(self._clock())
        cutoff = now - BLOB_GRACE_SECONDS
        with _LOCK:
            with self._transaction() as connection:
                if candidates is None:
                    dead = [
                        str(row[0])
                        for row in connection.execute(
                            "SELECT sha256 FROM blobs b WHERE created_at < ? AND " + _UNREFERENCED, (cutoff,))
                    ]
                else:
                    wanted = sorted({sha for sha in candidates if isinstance(sha, str)})
                    dead = []
                    for chunk in _chunks(wanted):
                        marks = ", ".join("?" for _ in chunk)
                        dead.extend(
                            str(row[0])
                            for row in connection.execute(
                                f"SELECT sha256 FROM blobs b WHERE sha256 IN ({marks}) AND " + _UNREFERENCED, chunk)
                        )
                        # A dropped picture of a purged chat: its file is already gone, its row goes too.
                        connection.execute(
                            f"DELETE FROM pictures WHERE dropped_at IS NOT NULL AND sha256 IN ({marks}) AND NOT EXISTS"
                            " (SELECT 1 FROM blob_owners o WHERE o.sha256 = pictures.sha256)", chunk)
                self._forget_blobs(connection, dead)
                known = (
                    {str(row[0]) for row in connection.execute("SELECT sha256 FROM blobs")}
                    if candidates is None
                    else set()
                )
            freed = 0
            for sha in dead:
                try:
                    self._blob_path(sha).unlink(missing_ok=True)
                    freed += 1
                except OSError:
                    logger.debug("Learning blob %s is busy; a later sweep removes it", sha[:12])
            if candidates is None:
                freed += self._sweep_leftover_files(known, cutoff)
        return freed

    @staticmethod
    def _forget_blobs(connection: sqlite3.Connection, shas: Sequence[str]) -> None:
        """Blobs about to be freed leave the tables (a picture's size and uses with them)."""

        rows = [(sha,) for sha in shas]
        for table in ("blobs", "pictures", "picture_uses", "blob_owners", "quote_blobs"):
            connection.executemany(f"DELETE FROM {table} WHERE sha256 = ?", rows)

    def _leftover_files(self, known: set[str], cutoff: float) -> list[Path]:
        """Blob files no table row knows (a write cut short, a blob freed while its file was busy) and
        staging files, older than ``cutoff``."""

        found: list[Path] = []
        if not self.blobs_dir.is_dir():
            return found
        for path in self.blobs_dir.rglob("*"):
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_mtime >= cutoff:
                    continue
                if path.parent.name == "tmp" or (_SHA256.fullmatch(path.name) is not None and path.name not in known):
                    found.append(path)
            except OSError:
                logger.debug("Learning blob leftover %s is busy", path.name, exc_info=True)
        return found

    def _sweep_leftover_files(self, known: set[str], cutoff: float) -> int:
        removed = 0
        for path in self._leftover_files(known, cutoff):
            try:
                path.unlink()
                removed += 1
            except OSError:
                logger.debug("Learning blob leftover %s is busy", path.name, exc_info=True)
        return removed

    # ── Pictures: the size cap ───────────────────────────────────────────

    def picture_dropped(self, sha: str) -> bool:
        """Whether a picture was dropped under the size cap (its message and caption stay)."""

        with self._reader() as connection:
            if connection is None:
                return False
            row = connection.execute("SELECT dropped_at FROM pictures WHERE sha256 = ?", (str(sha),)).fetchone()
        return row is not None and row[0] is not None

    def picture_stats(self) -> PictureStats:
        """Pictures kept and the space they take, and how many were dropped under the cap."""

        with self._reader() as connection:
            if connection is None:
                return PictureStats(cap_bytes=self.picture_cap)
            stored, size, dropped = connection.execute(
                "SELECT COALESCE(SUM(dropped_at IS NULL), 0), COALESCE(SUM(CASE WHEN dropped_at IS NULL THEN size"
                " ELSE 0 END), 0), COALESCE(SUM(dropped_at IS NOT NULL), 0) FROM pictures").fetchone()
        return PictureStats(int(stored), int(size), int(dropped), self.picture_cap)

    def keep_pictures_within(self, cap: int | None = None) -> tuple[int, int]:
        """Past the cap, drop the oldest pictures until they fit, never one a held-out decision (a vault
        task) sent. The decision and its message stay; the picture is marked dropped. Returns (pictures,
        bytes) dropped."""

        limit = self.picture_cap if cap is None else max(0, int(cap))
        if not self.exists():
            return 0, 0
        with _LOCK:
            with self._transaction() as connection:
                total = int(connection.execute(
                    "SELECT COALESCE(SUM(size), 0) FROM pictures WHERE dropped_at IS NULL").fetchone()[0])
                if total <= limit:
                    return 0, 0
                drop: list[tuple[str, int]] = []
                for sha, size in connection.execute(
                        "SELECT p.sha256, p.size FROM pictures p WHERE p.dropped_at IS NULL AND NOT EXISTS"
                        " (SELECT 1 FROM picture_uses u JOIN marks m ON m.kind = 'decision' AND m.id = u.decision_id"
                        " AND m.mark = ? WHERE u.sha256 = p.sha256) ORDER BY p.created_at, p.sha256",
                        (MARK_HELD_OUT,)):
                    if total <= limit:
                        break
                    drop.append((str(sha), int(size)))
                    total -= int(size)
                now = float(self._clock())
                connection.executemany("UPDATE pictures SET dropped_at = ? WHERE sha256 = ?",
                                       [(now, sha) for sha, _ in drop])
                connection.executemany("DELETE FROM blobs WHERE sha256 = ?", [(sha,) for sha, _ in drop])
            for sha, _ in drop:
                try:
                    self._blob_path(sha).unlink(missing_ok=True)
                except OSError:
                    logger.debug("Learning picture %s is busy; a later sweep removes it", sha[:12])
        if drop:
            logger.info("Learning dropped %d old pictures (%d bytes) to stay within its picture limit",
                        len(drop), sum(size for _, size in drop))
        return len(drop), sum(size for _, size in drop)

    # ── The one-off orphan sweep (P2) ────────────────────────────────────

    def _orphan_kind(self, path: Path) -> str:
        try:
            data = path.read_bytes()
        except OSError:
            return "other"
        if path.parent.name == "tmp":
            return "unfinished_write"
        if data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or data[:4] == b"RIFF":
            return "picture"
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return "other"
        if isinstance(value, list):
            return "tool_list" if all(isinstance(item, dict) and "function" in item for item in value) else "other"
        if not isinstance(value, dict):
            return "other"
        kind = str(value.get("format") or "").rsplit(".", 1)[-1]
        if kind in ORPHAN_KINDS:
            return kind
        if isinstance(value.get("data"), dict) and isinstance(value.get("type"), str):
            return "message"  # a LangChain message: a request's, an answer, a tool result
        if "outcome" in value and "decided_by" in value:
            return "approval"
        return "other"

    def record_blob_owners(self) -> int:
        """Record, for every model call already stored, the blobs its request names as its conversation's
        (what earlier builds wrote without owners). Idempotent; returns how many owner rows were new."""

        if not self.exists():
            return 0
        added = 0
        owning = _Owning()
        decisions = [item for item in self.find("decision", subkind="model_call", limit=100_000_000)
                     if isinstance(item, contracts.Decision) and item.request_blob]
        for start in range(0, len(decisions), _CHUNK):
            with _LOCK:
                with self._transaction() as connection:
                    for decision in decisions[start:start + _CHUNK]:
                        added += self._own_request(connection, decision, owning)
        return added

    def sweep_orphans(self, *, remove: bool = True, grace_seconds: float = BLOB_GRACE_SECONDS) -> SweepReport:
        """Find (and with ``remove``, delete) every blob no Learning record references any more: named by
        no record and by no stored request, older than ``grace_seconds`` so a write in progress is never
        touched. The owners of what earlier builds wrote are recorded first. Counts only: files and bytes,
        by kind."""

        if not self.exists():
            return SweepReport()
        owners = self.record_blob_owners()
        cutoff = float(self._clock()) - max(0.0, float(grace_seconds))
        counts: dict[str, list[int]] = {}
        files = size = 0

        def count(path: Path) -> None:
            nonlocal files, size
            try:
                length = path.stat().st_size
            except OSError:
                return
            entry = counts.setdefault(self._orphan_kind(path), [0, 0])
            entry[0] += 1
            entry[1] += length
            files += 1
            size += length

        with _LOCK:
            with self._transaction() as connection:
                dead = [str(row[0]) for row in connection.execute(
                    "SELECT sha256 FROM blobs b WHERE created_at < ? AND " + _UNREFERENCED, (cutoff,))]
                for sha in dead:
                    count(self._blob_path(sha))
                if remove:
                    self._forget_blobs(connection, dead)
                # The rows just found count once, as orphans, never again as leftover files.
                known = {str(row[0]) for row in connection.execute("SELECT sha256 FROM blobs")} | set(dead)
            leftovers = self._leftover_files(known, cutoff)
            for path in leftovers:
                count(path)
            if remove:
                for path in [*(self._blob_path(sha) for sha in dead), *leftovers]:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        logger.debug("Learning blob %s is busy; a later sweep removes it", path.name[:12])
        kinds = tuple((kind, found[0], found[1]) for kind, found in sorted(counts.items()))
        return SweepReport(files, size, kinds, owners)

    def sweep_once(self) -> SweepReport | None:
        """The one-off sweep on this build's first start: runs once per store (its result kept in ``meta``),
        logging counts only. None when it already ran or there is no store."""

        if not self.exists() or self.meta_value(SWEEP_META_KEY):
            return None
        report = self.sweep_orphans()
        self.set_meta_values({SWEEP_META_KEY: json.dumps(
            {"at": float(self._clock()), **report.to_dict()}, sort_keys=True)})
        logger.info("Learning removed %d files (%d bytes) no Learning record referenced any more",
                    report.files, report.bytes)
        return report

    def sweep_result(self) -> dict[str, object] | None:
        """What the one-off sweep removed ({at, files, bytes, kinds}), or None before it ran."""

        text = self.meta_value(SWEEP_META_KEY)
        try:
            found = json.loads(text) if text else None
        except ValueError:
            return None
        return found if isinstance(found, dict) else None

    # ── Deletion: conversations ──────────────────────────────────────────

    @staticmethod
    def _opted_out(connection: sqlite3.Connection, threads: Iterable[str]) -> bool:
        values = [thread for thread in threads if thread]
        if not values:
            return False
        marks = ", ".join("?" for _ in values)
        row = connection.execute(
            f"SELECT 1 FROM chat_optouts WHERE thread_id IN ({marks}) LIMIT 1", values
        ).fetchone()
        return row is not None

    def _purge(self, connection: sqlite3.Connection, thread_id: str, mark: str) -> tuple[
        dict[str, int], set[str], list[tuple[str, str]]
    ]:
        owned = [
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT DISTINCT kind, id FROM owners WHERE thread_id = ?", (thread_id,)
            )
        ]
        # The blobs its records name only inside other blobs (messages, tools, pictures, a past chat).
        shas: set[str] = {str(row[0]) for row in connection.execute(
            "SELECT sha256 FROM blob_owners WHERE thread_id = ?", (thread_id,))}
        connection.execute("DELETE FROM blob_owners WHERE thread_id = ?", (thread_id,))
        # Its words that other chats quoted: they go however many chats hold them (each step naming one
        # quotes this chat, so it is among the records purged below).
        quoted = sorted({str(row[0]) for row in connection.execute(
            "SELECT sha256 FROM quote_blobs WHERE thread_id = ?", (thread_id,))})
        for chunk in _chunks(quoted):
            marks = ", ".join("?" for _ in chunk)
            connection.execute(f"DELETE FROM blob_owners WHERE sha256 IN ({marks})", chunk)
            connection.execute(f"DELETE FROM quote_blobs WHERE sha256 IN ({marks})", chunk)
        shas.update(quoted)
        removed: dict[str, int] = {}
        for kind, record_id in owned:
            shas.update(
                str(row[0])
                for row in connection.execute(
                    "SELECT sha256 FROM blob_refs WHERE kind = ? AND id = ?", (kind, record_id)
                )
            )
            deleted = connection.execute(
                "DELETE FROM records WHERE kind = ? AND id = ?", (kind, record_id)
            ).rowcount
            for table in ("owners", "derivations", "blob_refs", "marks"):
                connection.execute(f"DELETE FROM {table} WHERE kind = ? AND id = ?", (kind, record_id))
            connection.execute("DELETE FROM lineage WHERE kind = ? AND child_id = ?", (kind, record_id))
            if kind == "decision":
                connection.execute("DELETE FROM picture_uses WHERE decision_id = ?", (record_id,))
            if deleted:
                removed[kind] = removed.get(kind, 0) + 1
        derived = [
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT DISTINCT kind, id FROM derivations WHERE thread_id = ? ORDER BY kind, id",
                (thread_id,),
            )
        ]
        now = float(self._clock())
        connection.executemany(
            "INSERT OR IGNORE INTO marks (kind, id, mark, thread_id, marked_at) VALUES (?, ?, ?, ?, ?)",
            [(kind, record_id, mark, thread_id, now) for kind, record_id in derived],
        )
        return removed, shas, derived

    def purge_thread(self, thread_id: str, *, excluded: bool = False) -> PurgeReport:
        """Purge what a deleted (or excluded) conversation owns; mark what was derived from it.

        Idempotent, and a no-op without a store: it never creates one.
        """

        clean = _thread(thread_id)
        if not self.exists():
            return PurgeReport(clean)
        mark = MARK_CHAT_EXCLUDED if excluded else MARK_CHAT_DELETED
        with _LOCK:
            with self._transaction() as connection:
                removed, shas, derived = self._purge(connection, clean, mark)
                if not excluded:
                    # A deleted chat's id may be used again by a new conversation.
                    connection.execute("DELETE FROM chat_optouts WHERE thread_id = ?", (clean,))
            if removed:
                self._checkpoint()
            freed = self.gc_blobs(shas) if shas else 0
            vault = self._forget_in_vault([clean])
        if vault:
            removed["audit_task"] = vault
        return PurgeReport(clean, tuple(sorted(removed.items())), freed, tuple(derived))

    def purge_missing_threads(self, existing: Iterable[str], *, older_than: float) -> int:
        """Purge records of conversations that no longer exist (a deletion whose Learning
        step failed), when their newest record is older than ``older_than``. Returns how
        many conversations were purged. Never creates a store."""

        if not self.exists():
            return 0
        keep = {str(thread) for thread in existing}
        with self._reader() as connection:
            if connection is None:
                return 0
            rows = connection.execute(
                "SELECT o.thread_id, MAX(r.created_at) FROM owners o"
                " JOIN records r ON r.kind = o.kind AND r.id = o.id GROUP BY o.thread_id"
            ).fetchall()
        purged = 0
        for thread_id, newest in rows:
            if str(thread_id) in keep or float(newest or 0) >= older_than:
                continue
            try:
                if self.purge_thread(str(thread_id)).total:
                    purged += 1
            except ValueError:
                continue
        if self._vault_exists():
            from row_bot.learning import audit

            for thread_id, newest in audit.vault_threads(root=self.root):
                if thread_id not in keep and newest < older_than and self._forget_in_vault([thread_id]):
                    purged += 1
        return purged

    # ── Per-chat opt-out ("Don't learn from this chat") ──────────────────

    def set_chat_opt_out(self, thread_id: str, opted_out: bool) -> PurgeReport | None:
        """Opt a chat out (purging what Learning recorded from it) or back in.

        Opting back in does not restore anything. Opting out needs Learning on or
        an existing store; it never creates a store while Learning is off.
        """

        clean = _thread(thread_id)
        if not opted_out:
            if self.exists():
                with self._transaction() as connection:
                    connection.execute("DELETE FROM chat_optouts WHERE thread_id = ?", (clean,))
            return None
        self._writable({"opt_out"})
        with _LOCK:
            with self._transaction() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO chat_optouts (thread_id, created_at) VALUES (?, ?)",
                    (clean, float(self._clock())),
                )
                removed, shas, derived = self._purge(connection, clean, MARK_CHAT_EXCLUDED)
            if removed:
                self._checkpoint()
            freed = self.gc_blobs(shas) if shas else 0
            vault = self._forget_in_vault([clean])
        if vault:
            removed["audit_task"] = vault
        return PurgeReport(clean, tuple(sorted(removed.items())), freed, tuple(derived))

    def is_chat_opted_out(self, thread_id: str) -> bool:
        with self._reader() as connection:
            if connection is None:
                return False
            return self._opted_out(connection, [str(thread_id or "")])

    def opted_out_chats(self) -> list[str]:
        with self._reader() as connection:
            if connection is None:
                return []
            return [str(row[0]) for row in connection.execute(
                "SELECT thread_id FROM chat_optouts ORDER BY created_at, thread_id"
            )]

    # ── Deletion: memories (E5) ──────────────────────────────────────────

    def _mark_lessons(self, where: str, params: Sequence[object]) -> int:
        if not self.exists():
            return 0
        now = float(self._clock())
        changed = 0
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT kind, body FROM records WHERE kind = 'lesson' AND " + where, params
            ).fetchall()
            for lesson in self._parse(rows):
                if not isinstance(lesson, LessonRecord) or lesson.memory_deleted_at is not None:
                    continue
                updated = replace(
                    lesson,
                    status="deleted",
                    memory_deleted_at=now,
                    updated_at=max(now, lesson.updated_at),
                )
                self._write(connection, updated)
                connection.execute(
                    "INSERT OR IGNORE INTO marks (kind, id, mark, thread_id, marked_at) VALUES (?, ?, ?, '', ?)",
                    ("lesson", lesson.id, MARK_MEMORY_DELETED, now),
                )
                changed += 1
        return changed

    def mark_lesson_memories_deleted(self, memory_ids: Iterable[str]) -> int:
        """Mark the lessons whose ``self_knowledge`` memory was deleted. Returns how many."""

        wanted = sorted({str(item) for item in memory_ids if item})
        changed = 0
        for chunk in _chunks(wanted):
            marks = ", ".join("?" for _ in chunk)
            changed += self._mark_lessons(f"memory_id IN ({marks})", chunk)
        return changed

    def mark_all_lesson_memories_deleted(self) -> int:
        """Every memory was deleted: mark every lesson that was one."""

        return self._mark_lessons("memory_id IS NOT NULL", ())

    # ── The audit vault's held-out marks, and deletion reaching the vault ──

    def add_marks(self, items: Iterable[tuple[str, str, str]], mark: str) -> int:
        """Mark records as (kind, id, conversation); the audit vault marks what it holds out."""

        rows = [(str(kind), str(record_id), str(mark), str(thread or ""), float(self._clock()))
                for kind, record_id, thread in items if record_id]
        if not rows or not self.exists():
            return 0
        with self._transaction() as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO marks (kind, id, mark, thread_id, marked_at) VALUES (?, ?, ?, ?, ?)", rows)
        return len(rows)

    def held_out_ids(self) -> set[str]:
        """Decisions the audit vault holds (or held), and corrections held out for it (O87): never
        training data, never drafting evidence."""

        with self._reader() as connection:
            if connection is None:
                return set()
            return {str(row[0]) for row in connection.execute(
                "SELECT id FROM marks WHERE kind = 'decision' AND mark = ?", (MARK_HELD_OUT,))}

    def held_out_correction_ids(self) -> set[str]:
        """The corrected decisions held out as hidden problems (O87), a subset of :meth:`held_out_ids`."""

        with self._reader() as connection:
            if connection is None:
                return set()
            return {str(row[0]) for row in connection.execute(
                "SELECT id FROM marks WHERE kind = 'decision' AND mark = ?", (MARK_HELD_OUT_CORRECTION,))}

    def meta_value(self, key: str, default: str = "") -> str:
        """One of the store's own values (``meta``), or ``default``."""

        with self._reader() as connection:
            if connection is None:
                return default
            row = connection.execute("SELECT value FROM meta WHERE key = ?", (str(key),)).fetchone()
        return str(row[0]) if row else default

    def set_meta_values(self, values: dict[str, str]) -> None:
        """Set some of the store's own values (one transaction)."""

        with self._transaction() as connection:
            connection.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [(str(key), str(value)) for key, value in values.items()])

    def _vault_exists(self) -> bool:
        try:
            return (gate.folder_path(self.root) / VAULT_FILE).is_file()
        except OSError:
            return False

    def _forget_in_vault(self, threads: Iterable[str]) -> int:
        """A conversation's hidden tasks leave the vault (only when there is one: a path check)."""

        if not self._vault_exists():
            return 0
        from row_bot.learning import audit

        return audit.forget_threads(threads, root=self.root)

    def _delete_vault(self) -> int:
        if not self._vault_exists():
            return 0
        from row_bot.learning import audit

        path = gate.folder_path(self.root) / VAULT_FILE
        size = sum(Path(str(path) + suffix).stat().st_size for suffix in ("", "-wal")
                   if Path(str(path) + suffix).is_file())
        try:
            audit.delete_vault(root=self.root)
        except audit.AuditVaultError as error:
            raise LearningStoreError("learning_data_busy") from error
        return size

    # ── Delete learning data… ────────────────────────────────────────────

    def storage_bytes(self) -> int:
        """Bytes Learning data takes on disk: the database and the blobs it lists. Every blob file is written
        before its row and goes with it, so the rows' sizes are the files' (an interrupted write's leftover
        file, swept by :meth:`gc_blobs`, is not counted). Read from the database, so a large store costs one
        query, not a walk of its blob folder."""

        total = 0
        for suffix in ("", "-wal"):
            try:
                total += Path(str(self.path) + suffix).stat().st_size
            except OSError:
                pass
        with self._reader() as connection:
            if connection is not None:
                total += int(connection.execute("SELECT COALESCE(SUM(size), 0) FROM blobs").fetchone()[0])
        return total

    def delete_all(self) -> DeleteAllReport:
        """Remove ``learning.db`` and the blobs, reporting the personal-model versions that
        were trained on them.

        The per-chat opt-outs are kept (a fresh store holds only them), so a chat the
        person switched off stays off. Model files outside the store are not touched.
        """

        with _LOCK:
            vault_bytes = self._delete_vault()
            if not self.exists():
                freed = self._remove_blobs() + vault_bytes
                return DeleteAllReport(deleted=bool(freed), bytes_freed=freed)
            size = self.storage_bytes()
            trained = tuple(
                ReleaseSummary(item.id, item.kind, item.version, item.label, item.status)
                for item in self.find("release", subkind="model", limit=100_000)
                if isinstance(item, Release)
            )
            opt_outs = self.opted_out_chats()
            staged = Path(str(self.path) + f".deleting-{uuid.uuid4().hex[:8]}")
            try:
                os.replace(self.path, staged)
            except OSError as error:
                raise LearningStoreError("learning_data_busy") from error
            for suffix in ("-wal", "-shm", "-journal"):
                try:
                    Path(str(self.path) + suffix).unlink(missing_ok=True)
                except OSError:
                    logger.debug("Learning store sidecar %s is busy", suffix, exc_info=True)
            staged.unlink(missing_ok=True)
            self._remove_blobs()
            if opt_outs:
                self.create()
                with self._transaction() as connection:
                    now = float(self._clock())
                    connection.executemany(
                        "INSERT OR IGNORE INTO chat_optouts (thread_id, created_at) VALUES (?, ?)",
                        [(thread, now) for thread in opt_outs],
                    )
        return DeleteAllReport(True, size + vault_bytes, trained, len(opt_outs))

    def _remove_blobs(self) -> int:
        folder = gate.folder_path(self.root)
        blobs = self.blobs_dir
        if blobs.is_symlink() or folder.is_symlink():
            raise LearningStoreError("learning_folder_unsafe")
        freed = 0
        if blobs.is_dir():
            resolved = blobs.resolve()
            if resolved.parent != folder.resolve():
                raise LearningStoreError("learning_folder_unsafe")
            for path in blobs.rglob("*"):
                try:
                    if path.is_file() and not path.is_symlink():
                        freed += path.stat().st_size
                except OSError:
                    pass
            shutil.rmtree(resolved)
        try:
            folder.rmdir()  # only when nothing else lives there
        except OSError:
            pass
        return freed


__all__ = [
    "BLOB_GRACE_SECONDS",
    "DeleteAllReport",
    "LearningStore",
    "LearningStoreError",
    "MARK_CHAT_DELETED",
    "MARK_CHAT_EXCLUDED",
    "MARK_HELD_OUT",
    "MARK_HELD_OUT_CORRECTION",
    "MARK_MEMORY_DELETED",
    "MAX_BLOB_BYTES",
    "MIGRATIONS",
    "Mark",
    "ORPHAN_KINDS",
    "PICTURE_CAP_BYTES",
    "PictureStats",
    "PurgeReport",
    "RECORDING_KINDS",
    "REQUEST_FORMAT",
    "ReleaseSummary",
    "SCHEMA_VERSION",
    "SWEEP_META_KEY",
    "SweepReport",
]
