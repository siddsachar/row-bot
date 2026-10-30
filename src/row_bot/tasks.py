"""Tasks — scheduled and on-demand agent actions with optional channel delivery.

A *task* is a named action (one or more prompts) that can run manually,
on a recurring schedule, or once at a specific time.  Tasks subsume both
the old "workflow" concept (multi-step prompt chains) and the old "timer"
concept (notify-only one-shot reminders).

Key features beyond v2.2.0 workflows
-------------------------------------
* Cron expressions via APScheduler ``CronTrigger``
* One-shot ``at`` field (ISO datetime) for "remind me at 3 PM" style tasks
* ``notify_only`` flag — fire a desktop / channel notification without
  invoking the agent (replaces timer_tool)
* ``delivery_channel`` / ``delivery_target`` — send results to Telegram
  or Email in addition to the always-on desktop + in-app notification
* ``model_override`` — per-task model selection
* ``persistent_thread_id`` — opt-in to reuse the same conversation thread
  across runs

Storage: SQLite at ``~/.row-bot/tasks.db``.
Migration: on first import, existing ``workflows.db`` data is migrated
automatically and the old file is kept as a backup.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import re
import shutil
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
from datetime import datetime, timedelta
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence, TypeVar

from row_bot.approval_policy import (
    legacy_safety_mode_to_approval_mode,
    normalize_approval_mode,
)
from row_bot.data_paths import get_tasks_db_path, get_row_bot_data_dir
from row_bot.application.live_updates import publish as _publish_live, publishes as _publishes_live

if TYPE_CHECKING:
    from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

# ── Subtask Depth Tracking ───────────────────────────────────────────────────
_subtask_depth_var: ContextVar[int] = ContextVar("subtask_depth", default=0)
# ── Persistence ──────────────────────────────────────────────────────────────
_DATA_DIR = get_row_bot_data_dir()
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_DB_PATH = str(get_tasks_db_path())
_OLD_WF_DB = str(_DATA_DIR / "workflows.db")
_TASK_CONFIG_PATH = str(_DATA_DIR / "task_config.json")
_SCHEMA_VERSION = 1
DEFAULT_WORKFLOW_AGENT_PROFILE_ID = "builtin:row_bot_default"
MISSED_RUN_RULES = ("skip", "run_once")
# When a workflow's results go to its channels (G8): after every run, only when a run needs the person
# (it didn't finish, or isn't sure something went through), or once a day in a digest.
SEND_RESULTS = ("every_run", "needs_me", "digest")
DIGEST_HOUR = 18  # A daily digest goes from 6 PM, the computer's time.
_SCHEMA_LOCK = threading.RLock()
_APPROVAL_CHANNEL_PUSH_LOCK = threading.RLock()
_SCHEMA_READY_PATH: str | None = None
_LAST_SCHEMA_REPAIR: dict[str, object] = {}
F = TypeVar("F", bound=Callable[..., object])

_CREATE_TABLE_SQL = {
    "tasks": """
        CREATE TABLE IF NOT EXISTS tasks (
            id                  TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            description         TEXT DEFAULT '',
            icon                TEXT DEFAULT '⚡',
            prompts             TEXT NOT NULL,
            schedule            TEXT,
            at                  TEXT,
            notify_only         INTEGER DEFAULT 0,
            notify_label        TEXT DEFAULT '',
            enabled             INTEGER DEFAULT 1,
            last_run            TEXT,
            created_at          TEXT NOT NULL,
            sort_order          INTEGER DEFAULT 0,
            delivery_channel    TEXT,
            delivery_target     TEXT,
            model_override      TEXT,
            persistent_thread_id TEXT,
            delete_after_run    INTEGER DEFAULT 0,
            allowed_commands    TEXT DEFAULT '[]',
            allowed_recipients  TEXT DEFAULT '[]',
            skills_override     TEXT,
            steps               TEXT DEFAULT '[]',
            safety_mode         TEXT,
            concurrency_group   TEXT,
            trigger             TEXT,
            tools_override      TEXT,
            channels            TEXT,
            advanced_mode       INTEGER DEFAULT 0,
            agent_profile_id    TEXT DEFAULT '',
            profile_migration_status TEXT DEFAULT '',
            profile_migration_note TEXT DEFAULT '',
            profile_migration_snapshot_json TEXT DEFAULT '{}',
            workspace_id        TEXT DEFAULT '',
            workspace_name      TEXT DEFAULT '',
            timezone            TEXT DEFAULT '',
            missed_runs         TEXT DEFAULT 'skip',
            always_ask          TEXT DEFAULT '[]',
            send_results        TEXT DEFAULT 'every_run'
        )
    """,
    "task_runs": """
        CREATE TABLE IF NOT EXISTS task_runs (
            id              TEXT PRIMARY KEY,
            task_id         TEXT NOT NULL,
            thread_id       TEXT NOT NULL,
            started_at      TEXT NOT NULL,
            finished_at     TEXT,
            status          TEXT DEFAULT 'running',
            status_message  TEXT DEFAULT '',
            steps_total     INTEGER DEFAULT 0,
            steps_done      INTEGER DEFAULT 0,
            pipeline_state_id TEXT,
            task_name       TEXT DEFAULT '',
            task_icon       TEXT DEFAULT '',
            workspace_id    TEXT DEFAULT '',
            workspace_name  TEXT DEFAULT '',
            late_for        TEXT DEFAULT '',
            notice          TEXT DEFAULT '',
            fix             TEXT DEFAULT '',
            first_message_id TEXT DEFAULT '',
            actions_json    TEXT DEFAULT '[]',
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        )
    """,
    "approval_requests": """
        CREATE TABLE IF NOT EXISTS approval_requests (
            id              TEXT PRIMARY KEY,
            run_id          TEXT NOT NULL,
            task_id         TEXT NOT NULL,
            step_id         TEXT NOT NULL,
            resume_token    TEXT UNIQUE NOT NULL,
            message         TEXT,
            channel         TEXT,
            status          TEXT DEFAULT 'pending',
            requested_at    TEXT DEFAULT (datetime('now')),
            responded_at    TEXT,
            timeout_at      TEXT,
            response_note   TEXT,
            agent_run_id    TEXT DEFAULT '',
            resume_kind     TEXT DEFAULT '',
            source_label    TEXT DEFAULT '',
            source_thread_id TEXT DEFAULT '',
            parent_thread_id TEXT DEFAULT '',
            approval_payload_json TEXT DEFAULT '{}'
        )
    """,
    "approval_channel_refs": """
        CREATE TABLE IF NOT EXISTS approval_channel_refs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            approval_id     TEXT NOT NULL,
            channel         TEXT NOT NULL,
            message_ref     TEXT NOT NULL,
            FOREIGN KEY (approval_id) REFERENCES approval_requests(id) ON DELETE CASCADE
        )
    """,
    "channel_thread_refs": """
        CREATE TABLE IF NOT EXISTS channel_thread_refs (
            thread_id        TEXT PRIMARY KEY,
            channel          TEXT NOT NULL,
            target           TEXT NOT NULL,
            external_conversation_id TEXT DEFAULT '',
            updated_at       TEXT NOT NULL,
            shared           INTEGER NOT NULL DEFAULT 0
        )
    """,
    "channel_thread_notifications": """
        CREATE TABLE IF NOT EXISTS channel_thread_notifications (
            key              TEXT PRIMARY KEY,
            thread_id        TEXT NOT NULL,
            channel          TEXT NOT NULL,
            target           TEXT NOT NULL,
            kind             TEXT NOT NULL,
            text             TEXT NOT NULL,
            payload_json     TEXT DEFAULT '{}',
            status           TEXT NOT NULL DEFAULT 'pending',
            attempts         INTEGER DEFAULT 0,
            last_error       TEXT DEFAULT '',
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            delivered_at     TEXT DEFAULT ''
        )
    """,
    "channel_conversations": """
        CREATE TABLE IF NOT EXISTS channel_conversations (
            channel          TEXT NOT NULL,
            conversation_key TEXT NOT NULL,
            thread_id        TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            PRIMARY KEY (channel, conversation_key)
        )
    """,
    # When the scheduler last ran, so a start can tell which runs fell while Row-Bot was closed.
    "scheduler_state": """
        CREATE TABLE IF NOT EXISTS scheduler_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """,
    # What a workflow keeps for its next run (G4), read when a run starts.
    "workflow_notes": """
        CREATE TABLE IF NOT EXISTS workflow_notes (
            task_id    TEXT PRIMARY KEY,
            text       TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """,
}

_COLUMN_MIGRATIONS = {
    "tasks": [
        ("description", "TEXT DEFAULT ''"),
        ("icon", "TEXT DEFAULT '⚡'"),
        ("prompts", "TEXT DEFAULT '[]'"),
        ("schedule", "TEXT"),
        ("at", "TEXT"),
        ("notify_only", "INTEGER DEFAULT 0"),
        ("notify_label", "TEXT DEFAULT ''"),
        ("enabled", "INTEGER DEFAULT 1"),
        ("last_run", "TEXT"),
        ("created_at", "TEXT DEFAULT ''"),
        ("sort_order", "INTEGER DEFAULT 0"),
        ("delivery_channel", "TEXT"),
        ("delivery_target", "TEXT"),
        ("persistent_thread_id", "TEXT"),
        ("delete_after_run", "INTEGER DEFAULT 0"),
        ("allowed_commands", "TEXT DEFAULT '[]'"),
        ("allowed_recipients", "TEXT DEFAULT '[]'"),
        ("model_override", "TEXT"),
        ("skills_override", "TEXT"),
        ("steps", "TEXT DEFAULT '[]'"),
        ("safety_mode", "TEXT"),
        ("concurrency_group", "TEXT"),
        ("trigger", "TEXT"),
        ("tools_override", "TEXT"),
        ("channels", "TEXT"),
        ("advanced_mode", "INTEGER DEFAULT 0"),
        ("agent_profile_id", "TEXT DEFAULT ''"),
        ("profile_migration_status", "TEXT DEFAULT ''"),
        ("profile_migration_note", "TEXT DEFAULT ''"),
        ("profile_migration_snapshot_json", "TEXT DEFAULT '{}'"),
        # A workflow's own code folder (N9); none keeps the conversation's.
        ("workspace_id", "TEXT DEFAULT ''"),
        ("workspace_name", "TEXT DEFAULT ''"),
        # The IANA zone its schedule keeps its times in; given the computer's on first start.
        ("timezone", "TEXT DEFAULT ''"),
        # A scheduled time while Row-Bot was closed: 'skip' it or 'run_once' on start.
        ("missed_runs", "TEXT DEFAULT 'skip'"),
        # Apps and tools whose actions always wait for the person ("Always ask me").
        ("always_ask", "TEXT DEFAULT '[]'"),
        # When its results go to its channels: after every run, only when it needs the person, or a daily digest.
        ("send_results", "TEXT DEFAULT 'every_run'"),
    ],
    "task_runs": [
        ("finished_at", "TEXT"),
        ("status", "TEXT DEFAULT 'running'"),
        ("status_message", "TEXT DEFAULT ''"),
        ("steps_total", "INTEGER DEFAULT 0"),
        ("steps_done", "INTEGER DEFAULT 0"),
        ("task_name", "TEXT DEFAULT ''"),
        ("task_icon", "TEXT DEFAULT ''"),
        ("pipeline_state_id", "TEXT"),
        ("workspace_id", "TEXT DEFAULT ''"),
        ("workspace_name", "TEXT DEFAULT ''"),
        ("late_for", "TEXT DEFAULT ''"),
        # A plain sentence for the person about how the run ended (N9), e.g. "Not approved in time".
        ("notice", "TEXT DEFAULT ''"),
        # The one thing the person can do about how it ended: "choose_folder" (its folder was removed).
        ("fix", "TEXT DEFAULT ''"),
        # The run's first message in its conversation: where its turns start (F31).
        ("first_message_id", "TEXT DEFAULT ''"),
        # What the run did outside Row-Bot, from its tools' results: [{tool, title, result, link}] (F31).
        ("actions_json", "TEXT DEFAULT '[]'"),
    ],
    # Other people share the channel conversation: it runs on Auto at most (full_autonomy).
    "channel_thread_refs": [
        ("shared", "INTEGER NOT NULL DEFAULT 0"),
    ],
    "approval_requests": [
        ("responded_at", "TEXT"),
        ("timeout_at", "TEXT"),
        ("response_note", "TEXT"),
        ("agent_run_id", "TEXT DEFAULT ''"),
        ("resume_kind", "TEXT DEFAULT ''"),
        ("source_label", "TEXT DEFAULT ''"),
        ("source_thread_id", "TEXT DEFAULT ''"),
        ("parent_thread_id", "TEXT DEFAULT ''"),
        ("approval_payload_json", "TEXT DEFAULT '{}'"),
    ],
}

_REQUIRED_COLUMNS = {
    "tasks": {
        "id", "name", "description", "icon", "prompts", "schedule", "at",
        "notify_only", "notify_label", "enabled", "last_run", "created_at",
        "sort_order", "delivery_channel", "delivery_target", "model_override",
        "persistent_thread_id", "delete_after_run", "allowed_commands",
        "allowed_recipients", "skills_override", "steps", "safety_mode",
        "concurrency_group", "trigger", "tools_override", "channels",
        "advanced_mode", "agent_profile_id", "profile_migration_status",
        "profile_migration_note", "profile_migration_snapshot_json",
        "workspace_id", "workspace_name", "timezone", "missed_runs", "always_ask", "send_results",
    },
    "task_runs": {
        "id", "task_id", "thread_id", "started_at", "finished_at", "status",
        "status_message", "steps_total", "steps_done", "pipeline_state_id",
        "task_name", "task_icon", "workspace_id", "workspace_name", "late_for", "notice", "fix",
        "first_message_id", "actions_json",
    },
    "approval_requests": {
        "id", "run_id", "task_id", "step_id", "resume_token", "message",
        "channel", "status", "requested_at", "responded_at", "timeout_at",
        "response_note", "agent_run_id", "resume_kind", "source_label",
        "source_thread_id", "parent_thread_id", "approval_payload_json",
    },
    "approval_channel_refs": {"id", "approval_id", "channel", "message_ref"},
    "channel_thread_refs": {
        "thread_id", "channel", "target", "external_conversation_id", "updated_at",
    },
    "channel_thread_notifications": {
        "key", "thread_id", "channel", "target", "kind", "text", "payload_json",
        "status", "attempts", "last_error", "created_at", "updated_at", "delivered_at",
    },
    "channel_conversations": {"channel", "conversation_key", "thread_id", "updated_at"},
    "scheduler_state": {"key", "value"},
    "workflow_notes": {"task_id", "text", "updated_at"},
}


def _raw_conn() -> sqlite3.Connection:
    from row_bot.docs_capture import is_docs_read_only_real_data_capture

    if is_docs_read_only_real_data_capture():
        db_path = pathlib.Path(_DB_PATH).expanduser().absolute()
        if not db_path.is_file():
            raise FileNotFoundError(db_path)
        conn = sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
            check_same_thread=False,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            return conn
        except Exception:
            conn.close()
            raise
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
    except Exception:
        conn.close()
        raise


def _is_corrupt_db_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        isinstance(exc, sqlite3.DatabaseError)
        and (
            "file is not a database" in text
            or "database disk image is malformed" in text
            or "database is malformed" in text
        )
    )


def _is_schema_operational_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return isinstance(exc, sqlite3.OperationalError) and (
        "no such table" in text
        or "no such column" in text
        or "database schema has changed" in text
        or "schema" in text
    )


def _has_invalid_sqlite_header(path: pathlib.Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        return path.read_bytes()[:16] != b"SQLite format 3\x00"
    except OSError:
        return False


def _backup_task_db_files(reason: str) -> pathlib.Path:
    data_dir = pathlib.Path(_DB_PATH).expanduser().parent
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = data_dir / "recovery" / f"tasks-db-{reason}-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        src = pathlib.Path(_DB_PATH + suffix)
        if src.exists():
            shutil.move(str(src), str(backup_dir / src.name))
    return backup_dir


def backup_and_recreate_task_db(reason: str = "reset") -> pathlib.Path | None:
    """Back up task DB files, recreate a clean schema, and return the backup."""
    global _SCHEMA_READY_PATH
    with _SCHEMA_LOCK:
        backup_dir = _backup_task_db_files(reason)
        _SCHEMA_READY_PATH = None
        ensure_task_schema(repair=True, force=True)
        logger.warning(
            "Task DB recreated after %s; previous files moved to %s",
            reason,
            backup_dir,
        )
        return backup_dir


def _apply_schema(conn: sqlite3.Connection) -> None:
    for sql in _CREATE_TABLE_SQL.values():
        conn.execute(sql)
    for table, migrations in _COLUMN_MIGRATIONS.items():
        for col, defn in migrations:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
                logger.info("Migrated %s table: added '%s' column", table, col)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_runs_latest_by_task "
        "ON task_runs(task_id, started_at DESC, id DESC)"
    )
    # Workflows saved before approval levels: the old "Auto" (allow almost everything) is reviewed Auto
    # now. Nothing is moved to Hands-off; only a person turns that on.
    conn.execute(
        "UPDATE tasks SET safety_mode = 'auto' WHERE safety_mode IS NULL "
        "OR safety_mode IN ('allow_all', 'allow-all', 'auto_edit', 'agent_run')"
    )
    # Schedules saved before they kept a zone (5.3) keep the computer's zone, so nothing moves.
    from row_bot.schedule_zones import local_zone_name

    conn.execute(
        "UPDATE tasks SET timezone = ? WHERE COALESCE(timezone, '') = '' "
        "AND (COALESCE(schedule, '') != '' OR COALESCE(at, '') != '')",
        (local_zone_name(),),
    )
    conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")


def _schema_snapshot(conn: sqlite3.Connection) -> dict[str, object]:
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing_tables = sorted(set(_REQUIRED_COLUMNS) - tables)
    missing_columns: dict[str, list[str]] = {}
    columns_by_table: dict[str, list[str]] = {}
    for table, required in _REQUIRED_COLUMNS.items():
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        columns_by_table[table] = cols
        missing = sorted(required - set(cols))
        if missing:
            missing_columns[table] = missing
    user_version = conn.execute("PRAGMA user_version").fetchone()[0]
    return {
        "tables": sorted(tables),
        "missing_tables": missing_tables,
        "columns_by_table": columns_by_table,
        "missing_columns": missing_columns,
        "user_version": user_version,
    }


def _validate_schema(conn: sqlite3.Connection) -> dict[str, object]:
    snapshot = _schema_snapshot(conn)
    if snapshot["missing_tables"] or snapshot["missing_columns"]:
        raise RuntimeError(
            "task schema incomplete: "
            f"missing_tables={snapshot['missing_tables']} "
            f"missing_columns={snapshot['missing_columns']}"
        )
    return snapshot


def ensure_task_schema(*, repair: bool = True, force: bool = False) -> dict[str, object]:
    """Ensure all required task DB tables and columns exist."""
    global _SCHEMA_READY_PATH, _LAST_SCHEMA_REPAIR
    db_path = str(pathlib.Path(_DB_PATH).expanduser())
    with _SCHEMA_LOCK:
        if not force and _SCHEMA_READY_PATH == db_path:
            return {"status": "ok", "db_path": db_path, "cached": True}

        from row_bot.docs_capture import is_docs_read_only_real_data_capture

        if is_docs_read_only_real_data_capture():
            conn = _raw_conn()
            try:
                snapshot = _validate_schema(conn)
            finally:
                conn.close()
            _SCHEMA_READY_PATH = db_path
            logger.info("Task DB schema read-only at %s", db_path)
            return {
                "status": "ok",
                "db_path": db_path,
                "before": snapshot,
                "after": snapshot,
                "last_repair": dict(_LAST_SCHEMA_REPAIR),
                "read_only": True,
            }

        db_file = pathlib.Path(db_path)
        existed_before = db_file.exists()
        db_file.parent.mkdir(parents=True, exist_ok=True)
        recreated = False
        try:
            if _has_invalid_sqlite_header(db_file):
                raise sqlite3.DatabaseError("file is not a database")
            conn = _raw_conn()
            try:
                before = _schema_snapshot(conn)
                _apply_schema(conn)
                after = _validate_schema(conn)
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            if repair and _is_corrupt_db_error(exc):
                backup_dir = _backup_task_db_files("corrupt")
                _LAST_SCHEMA_REPAIR = {
                    "status": "recreated",
                    "reason": str(exc),
                    "backup_dir": str(backup_dir),
                    "at": datetime.now().isoformat(),
                }
                logger.warning(
                    "Task DB was corrupt; moved files to %s and recreating",
                    backup_dir,
                )
                recreated = True
                conn = _raw_conn()
                try:
                    before = _schema_snapshot(conn)
                    _apply_schema(conn)
                    after = _validate_schema(conn)
                    conn.commit()
                finally:
                    conn.close()
            else:
                _SCHEMA_READY_PATH = None
                logger.exception("Task DB schema check failed for %s", db_path)
                raise

        _migrate_from_workflows()
        _SCHEMA_READY_PATH = db_path
        repaired = bool(
            not recreated
            and existed_before
            and (before["missing_tables"] or before["missing_columns"])
        )
        initialized = bool(
            not recreated
            and not existed_before
            and (before["missing_tables"] or before["missing_columns"])
        )
        if repaired:
            _LAST_SCHEMA_REPAIR = {
                "status": "repaired",
                "missing_tables": before["missing_tables"],
                "missing_columns": before["missing_columns"],
                "at": datetime.now().isoformat(),
            }
            logger.warning(
                "Task DB schema repaired in place at %s: tables=%s columns=%s",
                db_path,
                before["missing_tables"],
                before["missing_columns"],
            )
        elif recreated:
            logger.warning("Task DB schema recreated at %s", db_path)
        elif initialized:
            logger.info("Task DB schema initialized at %s", db_path)
        else:
            logger.info("Task DB schema ok at %s", db_path)
        return {
            "status": (
                "recreated" if recreated else
                "repaired" if repaired else
                "initialized" if initialized else
                "ok"
            ),
            "db_path": db_path,
            "before": before,
            "after": after,
            "last_repair": dict(_LAST_SCHEMA_REPAIR),
        }


def diagnose_task_schema() -> dict[str, object]:
    """Return support diagnostics for tasks.db."""
    path = pathlib.Path(_DB_PATH).expanduser()
    info: dict[str, object] = {
        "db_path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "wal_exists": pathlib.Path(str(path) + "-wal").exists(),
        "shm_exists": pathlib.Path(str(path) + "-shm").exists(),
        "last_repair": dict(_LAST_SCHEMA_REPAIR),
    }
    try:
        conn = _raw_conn()
        try:
            snapshot = _schema_snapshot(conn)
        finally:
            conn.close()
        info.update(snapshot)
        info["ok"] = not snapshot["missing_tables"] and not snapshot["missing_columns"]
    except Exception as exc:
        info["ok"] = False
        info["error"] = str(exc)
    return info


def _schema_retry(fn: F) -> F:
    @wraps(fn)
    def wrapper(*args, **kwargs):
        ensure_task_schema()
        try:
            return fn(*args, **kwargs)
        except sqlite3.OperationalError as exc:
            if not _is_schema_operational_error(exc):
                raise
            logger.warning(
                "Task DB schema error in %s; repairing and retrying once: %s",
                fn.__name__,
                exc,
            )
            ensure_task_schema(repair=True, force=True)
            return fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def _get_conn() -> sqlite3.Connection:
    ensure_task_schema()
    return _raw_conn()


def _init_db() -> None:
    """Create the tasks and task_runs tables if they don't exist."""
    ensure_task_schema(repair=True, force=True)
    return
    conn = _raw_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id                  TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            description         TEXT DEFAULT '',
            icon                TEXT DEFAULT '⚡',
            prompts             TEXT NOT NULL,          -- JSON list of prompt strings
            schedule            TEXT,                   -- recurring: daily:HH:MM / weekly:DAY:HH:MM / interval:H / cron:EXPR
            at                  TEXT,                   -- one-shot ISO datetime (mutually exclusive with schedule)
            notify_only         INTEGER DEFAULT 0,      -- 1 = fire notification only, no agent invocation
            notify_label        TEXT DEFAULT '',         -- label for notify-only tasks (replaces timer label)
            enabled             INTEGER DEFAULT 1,
            last_run            TEXT,
            created_at          TEXT NOT NULL,
            sort_order          INTEGER DEFAULT 0,
            delivery_channel    TEXT,                   -- null / 'telegram' / 'email'
            delivery_target     TEXT,                   -- chat_id or email address
            model_override      TEXT,                   -- null = use global default model
            persistent_thread_id TEXT,                  -- null = fresh thread each run
            delete_after_run    INTEGER DEFAULT 0,      -- 1 = auto-delete after one-shot execution
            allowed_commands    TEXT DEFAULT '[]',      -- JSON list of allowed shell command prefixes for background runs
            allowed_recipients  TEXT DEFAULT '[]',      -- JSON list of allowed email recipients for background runs
            skills_override     TEXT,                   -- JSON list of skill names (null = use global)
            steps               TEXT DEFAULT '[]',      -- JSON list of step dicts (pipeline mode)
            safety_mode         TEXT,                   -- null = inherit global / 'block' / 'approve' / 'auto' / 'full'
            concurrency_group   TEXT,                   -- null = no limit / 'local_gpu' / custom
            trigger             TEXT,                   -- JSON trigger config (null = schedule/manual only)
            tools_override      TEXT,                    -- JSON list of tool names (null = all enabled)
            channels            TEXT,                    -- JSON list of channel names (null = workflow default)
            advanced_mode       INTEGER DEFAULT 0,       -- 1 = reopen in Advanced editor mode
            agent_profile_id    TEXT DEFAULT '',         -- optional Agent Profile id/slug
            profile_migration_status TEXT DEFAULT '',
            profile_migration_note TEXT DEFAULT '',
            profile_migration_snapshot_json TEXT DEFAULT '{}'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_runs (
            id              TEXT PRIMARY KEY,
            task_id         TEXT NOT NULL,
            thread_id       TEXT NOT NULL,
            started_at      TEXT NOT NULL,
            finished_at     TEXT,
            status          TEXT DEFAULT 'running',     -- running / completed / failed / completed_delivery_failed / paused / stopped / cancelled
            status_message  TEXT DEFAULT '',             -- human-readable detail (delivery result, error reason)
            steps_total     INTEGER DEFAULT 0,
            steps_done      INTEGER DEFAULT 0,
            pipeline_state_id TEXT,                     -- FK to pipeline_state for resume support
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS approval_requests (
            id              TEXT PRIMARY KEY,
            run_id          TEXT NOT NULL,
            task_id         TEXT NOT NULL,
            step_id         TEXT NOT NULL,
            resume_token    TEXT UNIQUE NOT NULL,
            message         TEXT,
            channel         TEXT,
            status          TEXT DEFAULT 'pending',     -- pending / approved / denied / timed_out
            requested_at    TEXT DEFAULT (datetime('now')),
            responded_at    TEXT,
            timeout_at      TEXT,
            response_note   TEXT,
            agent_run_id    TEXT DEFAULT '',
            resume_kind     TEXT DEFAULT '',
            source_label    TEXT DEFAULT '',
            source_thread_id TEXT DEFAULT '',
            parent_thread_id TEXT DEFAULT '',
            approval_payload_json TEXT DEFAULT '{}'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS approval_channel_refs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            approval_id     TEXT NOT NULL,
            channel         TEXT NOT NULL,
            message_ref     TEXT NOT NULL,
            FOREIGN KEY (approval_id) REFERENCES approval_requests(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS channel_thread_refs (
            thread_id        TEXT PRIMARY KEY,
            channel          TEXT NOT NULL,
            target           TEXT NOT NULL,
            external_conversation_id TEXT DEFAULT '',
            updated_at       TEXT NOT NULL,
            shared           INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS channel_thread_notifications (
            key              TEXT PRIMARY KEY,
            thread_id        TEXT NOT NULL,
            channel          TEXT NOT NULL,
            target           TEXT NOT NULL,
            kind             TEXT NOT NULL,
            text             TEXT NOT NULL,
            payload_json     TEXT DEFAULT '{}',
            status           TEXT NOT NULL DEFAULT 'pending',
            attempts         INTEGER DEFAULT 0,
            last_error       TEXT DEFAULT '',
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            delivered_at     TEXT DEFAULT ''
        )
    """)
    # Migrations for pre-existing databases
    # Migrations for tasks table
    for col, defn in [
        ("allowed_commands", "TEXT DEFAULT '[]'"),
        ("allowed_recipients", "TEXT DEFAULT '[]'"),
        ("model_override", "TEXT"),
        ("skills_override", "TEXT"),
        ("steps", "TEXT DEFAULT '[]'"),
        ("safety_mode", "TEXT"),
        ("concurrency_group", "TEXT"),
        ("trigger", "TEXT"),
        ("tools_override", "TEXT"),
        ("channels", "TEXT"),
        ("advanced_mode", "INTEGER DEFAULT 0"),
        ("agent_profile_id", "TEXT DEFAULT ''"),
        ("profile_migration_status", "TEXT DEFAULT ''"),
        ("profile_migration_note", "TEXT DEFAULT ''"),
        ("profile_migration_snapshot_json", "TEXT DEFAULT '{}'"),
    ]:
        try:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {defn}")
            logger.info("Migrated tasks table: added '%s' column", col)
        except Exception:
            pass  # column already exists

    for col, defn in [
        ("status_message", "TEXT DEFAULT ''"),
        ("task_name", "TEXT DEFAULT ''"),
        ("task_icon", "TEXT DEFAULT ''"),
        ("pipeline_state_id", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE task_runs ADD COLUMN {col} {defn}")
            logger.info("Migrated task_runs table: added '%s' column", col)
        except Exception:
            pass  # column already exists

    # Existing tasks without a level run on reviewed Auto (new tasks default to 'block')
    for col, defn in [
        ("agent_run_id", "TEXT DEFAULT ''"),
        ("resume_kind", "TEXT DEFAULT ''"),
        ("source_label", "TEXT DEFAULT ''"),
        ("source_thread_id", "TEXT DEFAULT ''"),
        ("parent_thread_id", "TEXT DEFAULT ''"),
        ("approval_payload_json", "TEXT DEFAULT '{}'"),
    ]:
        try:
            conn.execute(f"ALTER TABLE approval_requests ADD COLUMN {col} {defn}")
            logger.info("Migrated approval_requests table: added '%s' column", col)
        except Exception:
            pass  # column already exists

    try:
        conn.execute(
            "UPDATE tasks SET safety_mode = 'auto' "
            "WHERE safety_mode IS NULL"
        )
    except Exception:
        pass

    conn.commit()
    conn.close()


def _migrate_from_workflows() -> None:
    """Migrate data from the old workflows.db to the new tasks.db.

    Runs once — only if workflows.db exists and a marker file has not been
    written yet.  The marker prevents re-migration when the user intentionally
    deletes tasks.db to get fresh defaults.
    """
    _MARKER = os.path.join(_DATA_DIR, ".workflows_migrated")
    if os.path.exists(_MARKER):
        return  # Already migrated in a prior run
    if not os.path.exists(_OLD_WF_DB):
        return

    conn = _raw_conn()
    count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    if count > 0:
        conn.close()
        # DB already has data (e.g. from a previous migration) — mark done
        open(_MARKER, "w").close()
        return

    try:
        old_conn = sqlite3.connect(_OLD_WF_DB, check_same_thread=False)
        old_conn.row_factory = sqlite3.Row

        # Check old schema exists
        tables = {r[0] for r in old_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "workflows" not in tables:
            old_conn.close()
            conn.close()
            return

        rows = old_conn.execute("SELECT * FROM workflows").fetchall()
        migrated = 0
        for row in rows:
            try:
                d = dict(row)
                conn.execute(
                    "INSERT INTO tasks "
                    "(id, name, description, icon, prompts, schedule, enabled, "
                    "last_run, created_at, sort_order) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        d["id"], d["name"], d.get("description", ""),
                        d.get("icon", "⚡"), d["prompts"],
                        d.get("schedule"), d.get("enabled", 1),
                        d.get("last_run"), d["created_at"],
                        d.get("sort_order", 0),
                    ),
                )
                migrated += 1
            except Exception as exc:
                logger.warning("Skipping malformed legacy workflow row: %s", exc)
            continue
            d = dict(row)
            conn.execute(
                "INSERT INTO tasks "
                "(id, name, description, icon, prompts, schedule, enabled, "
                "last_run, created_at, sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    d["id"], d["name"], d.get("description", ""),
                    d.get("icon", "⚡"), d["prompts"],
                    d.get("schedule"), d.get("enabled", 1),
                    d.get("last_run"), d["created_at"],
                    d.get("sort_order", 0),
                ),
            )

        # Migrate run history
        if "workflow_runs" in tables:
            runs = old_conn.execute("SELECT * FROM workflow_runs").fetchall()
            for run in runs:
                try:
                    r = dict(run)
                    conn.execute(
                        "INSERT INTO task_runs "
                        "(id, task_id, thread_id, started_at, finished_at, "
                        "status, steps_total, steps_done) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            r["id"], r["workflow_id"], r["thread_id"],
                            r["started_at"], r.get("finished_at"),
                            r.get("status", "completed"),
                            r.get("steps_total", 0), r.get("steps_done", 0),
                        ),
                    )
                except Exception as exc:
                    logger.warning("Skipping malformed legacy workflow run row: %s", exc)
                continue
                r = dict(run)
                conn.execute(
                    "INSERT INTO task_runs "
                    "(id, task_id, thread_id, started_at, finished_at, "
                    "status, steps_total, steps_done) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        r["id"], r["workflow_id"], r["thread_id"],
                        r["started_at"], r.get("finished_at"),
                        r.get("status", "completed"),
                        r.get("steps_total", 0), r.get("steps_done", 0),
                    ),
                )

        conn.commit()
        old_conn.close()
        # Mark migration done so we never re-migrate if the user deletes tasks.db
        open(_MARKER, "w").close()
        logger.info(
            "Migrated %d tasks from workflows.db → tasks.db", len(rows)
        )
    except Exception as exc:
        logger.warning("Workflow migration failed (non-fatal): %s", exc)
    finally:
        conn.close()


def _ordered_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _json_list_value(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            loaded = json.loads(text)
        except Exception:
            return _ordered_text_list(text)
        return _ordered_text_list(loaded)
    return _ordered_text_list(value)


# ── Workflows are messages (F31) ─────────────────────────────────────────────


def _step_words(step: Mapping[str, Any], names: Mapping[str, str]) -> str:
    """One old pipeline step in words, as part of a workflow's message."""
    kind = str(step.get("type") or "prompt")
    text = " ".join(str(step.get("prompt") or step.get("message") or "").split())
    text = re.sub(r"\{\{\s*(prev_output|step\.[^}]+\.output)\s*\}\}", "(the result above)", text)
    if kind == "condition":
        check = str(step.get("condition") or "").strip()
        words = check[len("llm:"):].strip() if check.startswith("llm:") else f"this check passes: {check}"
        return f"Check whether {words}. If not, stop here and say why."
    if kind == "notify":
        return f"Tell me: {text}" if text else ""
    if kind == "subtask":
        name = names.get(str(step.get("task_id") or ""), "")
        return f"Then run the workflow \u201c{name}\u201d." if name else ""
    if kind == "delegate_agent":
        return f"Hand this to a helper: {text}" if text else ""
    if kind == "approval":
        return ""  # The action's own card asks the person.
    return text


def messages_from_steps(steps: Sequence[Any], names: Mapping[str, str] | None = None) -> list[str]:
    """An old step pipeline as messages (F31): prompt-only pipelines keep one message per prompt; any
    other becomes one message that says the steps in order."""
    items = [step for step in steps or [] if isinstance(step, Mapping)]
    plain = [str(step) for step in steps or [] if isinstance(step, str) and step.strip()]
    if plain and not items:
        return plain
    if all(str(step.get("type") or "prompt") == "prompt" for step in items):
        return [words for step in items if (words := _step_words(step, names or {}))]
    parts = [words for step in items if (words := _step_words(step, names or {}))]
    if not parts:
        return []
    return ["Do these in order:\n" + "\n".join(f"{index}. {part}" for index, part in enumerate(parts, 1))]


def _convert_step_workflows() -> None:
    """Once: saved step pipelines become messages, and one that had an approval step asks at least (F31)."""
    conn = _get_conn()
    try:
        names = {str(row["id"]): str(row["name"] or "") for row in conn.execute("SELECT id, name FROM tasks")}
        rows = conn.execute(
            "SELECT id, steps, safety_mode FROM tasks WHERE COALESCE(steps, '[]') NOT IN ('', '[]')"
        ).fetchall()
        for row in rows:
            try:
                steps = json.loads(row["steps"] or "[]")
            except (TypeError, ValueError):
                steps = []
            asked = any(isinstance(step, Mapping) and step.get("type") == "approval" for step in steps)
            mode = legacy_safety_mode_to_approval_mode(row["safety_mode"])
            if asked and mode in {"auto", "full"}:
                mode = "approve"
            conn.execute("UPDATE tasks SET prompts = ?, steps = '[]', safety_mode = ?, advanced_mode = 0 WHERE id = ?",
                         (json.dumps(messages_from_steps(steps, names)), mode, row["id"]))
        conn.execute("DROP TABLE IF EXISTS pipeline_state")
        conn.execute("DROP TABLE IF EXISTS workflow_drafts")
        # Approvals of the old pipeline can no longer resume anything.
        conn.execute("UPDATE approval_requests SET status = 'cancelled', responded_at = ? "
                     "WHERE status = 'pending' AND COALESCE(resume_kind, '') IN ('', 'workflow')",
                     (datetime.now().isoformat(),))
        conn.commit()
    finally:
        conn.close()


def _convert_step_workflows_best_effort() -> None:
    try:
        _convert_step_workflows()
    except Exception:
        logger.warning("Saved workflows could not be converted to messages", exc_info=True)


_init_db()
_migrate_from_workflows()
_convert_step_workflows_best_effort()


# ── Template Variables ───────────────────────────────────────────────────────

def expand_template_vars(
    prompt: str,
    task_id: str | None = None,
    prev_output: str = "",
    step_outputs: dict[str, str] | None = None,
) -> str:
    """Replace ``{{variable}}`` placeholders with current values.

    Supports ``{{prev_output}}`` for the previous step's output and
    ``{{step.<step_id>.output}}`` for any named step's output.
    """
    now = datetime.now()
    replacements = {
        "date": now.strftime("%B %d, %Y"),
        "day": now.strftime("%A"),
        "time": now.strftime("%I:%M %p"),
        "month": now.strftime("%B"),
        "year": str(now.year),
        "prev_output": prev_output,
    }
    if task_id:
        replacements["task_id"] = task_id

    # "{{ date }}" and "{{date}}" alike: the step editor accepts both.
    result = re.sub(
        r"\{\{\s*(" + "|".join(map(re.escape, replacements)) + r")\s*\}\}",
        lambda match: replacements[match.group(1)],
        prompt,
    )
    # Resolve {{step.<step_id>.output}} references
    if step_outputs:
        def _resolve_step_ref(m):
            sid = m.group(1)
            return step_outputs.get(sid, "")
        result = re.sub(r"\{\{\s*step\.([^.\s]+)\.output\s*\}\}", _resolve_step_ref, result)
    return result


# ── CRUD ─────────────────────────────────────────────────────────────────────

def _canonicalize_workflow_model_override(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    from row_bot.providers.selection import resolve_catalog_model_selection

    canonical = resolve_catalog_model_selection(
        raw,
        surface="workflow",
        allow_default=True,
        require_agent_ready=True,
    )
    return canonical.ref or None


def _canonicalize_agent_profile_reference(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    from row_bot.agent_profiles import require_agent_profile

    profile = require_agent_profile(raw, enabled_only=True)
    return str(profile["id"])


def _diagnose_model_override_value(value: str | None, surface: str) -> dict:
    raw = str(value or "").strip()
    diagnostic = {
        "raw_value": raw,
        "surface": surface,
        "canonical_ref": "",
        "status": "empty" if not raw else "canonical" if raw.startswith("model:") else "legacy_bare",
        "message": "",
    }
    if not raw:
        return diagnostic
    try:
        from row_bot.providers.selection import canonicalize_model_selection

        canonical = canonicalize_model_selection(raw, surface, allow_default=True)
        diagnostic["canonical_ref"] = canonical.ref
        if raw.startswith("model:"):
            diagnostic["status"] = "canonical"
        elif canonical.ref:
            diagnostic["status"] = "canonicalizable"
            diagnostic["message"] = "Legacy bare value can be canonicalized explicitly."
    except Exception as exc:
        text = str(exc)
        diagnostic["message"] = text
        diagnostic["status"] = "ambiguous" if "Ambiguous model selection" in text else "unknown"
    return diagnostic


def diagnose_legacy_model_overrides() -> list[dict]:
    """Return read-only diagnostics for legacy bare task/thread model overrides."""
    diagnostics: list[dict] = []
    for task in list_tasks():
        task_id = str(task.get("id") or "")
        task_name = str(task.get("name") or "")
        model_override = str(task.get("model_override") or "")
        if model_override and not model_override.startswith("model:"):
            item = _diagnose_model_override_value(model_override, "workflow")
            item.update({"scope": "task", "task_id": task_id, "task_name": task_name})
            diagnostics.append(item)
    try:
        from row_bot.threads import _list_threads

        for thread_id, name, _created, _updated, model_override, *_rest in _list_threads():
            raw = str(model_override or "")
            if not raw or raw.startswith("model:"):
                continue
            item = _diagnose_model_override_value(raw, "channels")
            item.update({"scope": "thread", "thread_id": str(thread_id), "thread_name": str(name)})
            diagnostics.append(item)
    except Exception:
        logger.debug("Thread model override diagnostics unavailable", exc_info=True)
    return diagnostics


@_schema_retry
def create_task(
    name: str,
    prompts: list[str] | None = None,
    description: str = "",
    icon: str = "⚡",
    schedule: str | None = None,
    at: str | None = None,
    notify_only: bool = False,
    notify_label: str = "",
    delivery_channel: str | None = None,
    delivery_target: str | None = None,
    model_override: str | None = None,
    persistent_thread_id: str | None = None,
    delete_after_run: bool = False,
    delay_minutes: float | None = None,
    skills_override: list[str] | None = None,
    steps: list[dict] | None = None,
    safety_mode: str = "block",
    concurrency_group: str | None = None,
    trigger: dict | None = None,
    tools_override: list[str] | None = None,
    channels: list[str] | None = None,
    agent_profile_id: str | None = None,
    enabled: bool = True,
    apply_default_skills: bool = True,
    task_id: str | None = None,
    validate: Callable[[], None] | None = None,
    record_commit: Callable[[sqlite3.Connection, str], None] | None = None,
    timezone: str | None = None,
    workspace_id: str = "",
    workspace_name: str = "",
    missed_runs: str = "skip",
    always_ask: list[str] | None = None,
) -> str:
    """Create a new task and return its ID.

    *delay_minutes* is a convenience for quick timers: it computes
    ``at = now + N minutes`` and automatically sets ``delete_after_run``
    so the LLM never needs to compute an ISO datetime.

    Only ONE of *schedule*, *at*, *delay_minutes* may be provided.
    """
    # ── Mutual-exclusivity check ──────────────────────────────────────
    _set_count = sum(1 for v in (schedule, at, delay_minutes) if v)
    if _set_count > 1:
        raise ValueError(
            "Only one of schedule, at, or delay_minutes may be set."
        )
    from row_bot import schedule_zones

    if timezone and not schedule_zones.is_zone(timezone):
        raise ValueError("timezone must be an IANA zone such as Europe/London.")
    if missed_runs not in MISSED_RUN_RULES:
        raise ValueError("missed_runs must be 'skip' or 'run_once'.")

    # ── delay_minutes → at conversion ────────────────────────────────
    if delay_minutes is not None:
        if delay_minutes <= 0:
            raise ValueError("delay_minutes must be positive.")
        at = (datetime.now() + timedelta(minutes=delay_minutes)).isoformat()
        delete_after_run = True  # one-shot timers auto-delete
        timezone = None  # ``at`` is the computer's own time
    # A schedule keeps its times in a zone: the one chosen, else the computer's (N9).
    timezone = (timezone or schedule_zones.local_zone_name()) if (schedule or at) else (timezone or "")

    # ── Validate delivery settings ────────────────────────────────────
    _validate_delivery(delivery_channel, delivery_target)

    task_id = task_id or uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()
    if prompts is None:
        prompts = []
    model_override = _canonicalize_workflow_model_override(model_override)
    agent_profile_id = _canonicalize_agent_profile_reference(
        agent_profile_id or DEFAULT_WORKFLOW_AGENT_PROFILE_ID
    )
    safety_mode = legacy_safety_mode_to_approval_mode(safety_mode)
    profile_migration_status = "not_needed"
    profile_migration_note = "Profile-first workflow."
    profile_migration_snapshot: dict[str, Any] = {}
    # A workflow is what it sends and its approval level (F31): no tool or skill lists of its own.
    skills_override = None
    tools_override = None
    concurrency_group = None
    if steps:
        if any(isinstance(step, Mapping) and step.get("type") == "approval" for step in steps) \
                and safety_mode in {"auto", "full"}:
            safety_mode = "approve"
        prompts = messages_from_steps(steps) or prompts
        steps = None
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if validate is not None:
            validate()
        conn.execute(
            "INSERT INTO tasks "
            "(id, name, description, icon, prompts, schedule, at, notify_only, "
            "notify_label, delivery_channel, delivery_target, model_override, "
            "persistent_thread_id, delete_after_run, created_at, enabled, skills_override, "
            "steps, safety_mode, concurrency_group, trigger, tools_override, channels, "
            "advanced_mode, agent_profile_id, profile_migration_status, "
            "profile_migration_note, profile_migration_snapshot_json, timezone, "
            "workspace_id, workspace_name, missed_runs, always_ask) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?)",
            (
                task_id, name, description, icon, json.dumps(prompts),
                schedule, at, int(notify_only), notify_label,
                delivery_channel, delivery_target, model_override,
                persistent_thread_id, int(delete_after_run), now, int(enabled),
                json.dumps(skills_override) if skills_override is not None else None,
                json.dumps(steps) if steps else "[]",
                safety_mode,
                concurrency_group,
                json.dumps(trigger) if trigger else None,
                json.dumps(tools_override) if tools_override else None,
                json.dumps(channels) if channels is not None else None,
                0,  # advanced_mode: workflows are messages (F31)
                agent_profile_id,
                profile_migration_status,
                profile_migration_note,
                json.dumps(profile_migration_snapshot, sort_keys=True),
                timezone,
                str(workspace_id or ""),
                str(workspace_name or ""),
                missed_runs,
                json.dumps(list(always_ask or [])),
            ),
        )
        if validate is not None:
            validate()
        if record_commit is not None:
            record_commit(conn, task_id)
        conn.commit()
    finally:
        conn.close()

    # Reconcile from current canonical data, including recovery after commit.
    if _scheduler is not None:
        sync_task_schedule(task_id, validate=validate)

    return task_id


@_schema_retry
def get_task(task_id: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_dict(row)


@_schema_retry
def list_tasks() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT t.*, "
        "(SELECT r.status FROM task_runs r WHERE r.task_id = t.id "
        " ORDER BY r.started_at DESC LIMIT 1) AS last_status "
        "FROM tasks t ORDER BY t.sort_order, t.created_at"
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def iter_task_summary_snapshot() -> Iterator[dict[str, Any]]:
    """Yield bounded saved task metadata from one SQLite read snapshot.

    Prompt and step counts are calculated in SQLite. Their contents, delivery
    destinations, approval tokens and runtime configuration are absent, apart
    from the saved profile reference and approval mode a run starts with. This
    never starts a task or loads its channels.
    """
    conn = _get_conn()
    try:
        conn.execute("BEGIN")
        rows = conn.execute(
            "SELECT t.id, substr(t.name, 1, 256) AS name, "
            "substr(t.description, 1, 2048) AS description, "
            "substr(t.icon, 1, 32) AS icon, t.enabled, t.notify_only, "
            "substr(t.agent_profile_id, 1, 128) AS agent_profile_id, "
            "substr(t.safety_mode, 1, 32) AS safety_mode, "
            "CASE WHEN json_valid(t.steps) AND json_type(t.steps)='array' "
            "AND json_array_length(t.steps)>0 THEN json_array_length(t.steps) "
            "WHEN json_valid(t.prompts) AND json_type(t.prompts)='array' "
            "THEN json_array_length(t.prompts) ELSE 0 END AS step_count, "
            "substr(t.schedule, 1, 256) AS schedule, substr(t.at, 1, 80) AS at, "
            "substr(t.last_run, 1, 80) AS last_run, t.persistent_thread_id, "
            "(SELECT substr(r.status, 1, 80) FROM task_runs r WHERE r.task_id=t.id "
            "ORDER BY r.started_at DESC, r.id DESC LIMIT 1) AS last_status, "
            # The ten newest saved runs (status and start only), newest first.
            "(SELECT json_group_array(json_object('status', h.status, "
            "'started_at', h.started_at)) FROM (SELECT substr(r.status, 1, 80) "
            "AS status, substr(r.started_at, 1, 80) AS started_at FROM task_runs r "
            "WHERE r.task_id=t.id ORDER BY r.started_at DESC, r.id DESC LIMIT 10) h"
            ") AS recent_runs_json, "
            # The newest run that has not finished, with its saved step progress.
            "(SELECT json_object('id', r.id, 'status', substr(r.status, 1, 80), "
            "'started_at', substr(r.started_at, 1, 80), "
            "'steps_done', r.steps_done, 'steps_total', r.steps_total) "
            "FROM task_runs r WHERE r.task_id=t.id AND r.status IN "
            "('starting','running','resuming','paused','waiting_approval','stopping') "
            "ORDER BY r.started_at DESC, r.id DESC LIMIT 1) AS active_run_json "
            "FROM tasks t ORDER BY t.sort_order, t.created_at, t.id"
        )
        while batch := rows.fetchmany(128):
            for row in batch:
                yield dict(row)
    finally:
        conn.close()


class TaskMutationError(ValueError):
    def __init__(self, code: str, task_id: str, *, committed: bool = False):
        self.code = code
        self.task_id = task_id
        self.committed = committed
        super().__init__(code)


def _task_row_revision(row: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(dict(row), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _task_editor_row(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    """Read the complete row only after a same-snapshot 2 MiB envelope check."""
    columns = [str(column[1]) for column in conn.execute("PRAGMA table_info(tasks)")]
    sizes = " + ".join(
        'COALESCE(length(CAST("' + column.replace('"', '""') + '" AS BLOB)), 0)'
        for column in columns
    )
    size = conn.execute(f"SELECT {sizes} FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if size is None:
        return None
    if size[0] > 2 * 1024 * 1024:
        raise TaskMutationError("task_metadata_too_large", task_id)
    return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()


@_schema_retry
def read_task_for_edit(task_id: str) -> tuple[dict, str] | None:
    """Capture the existing task and exact full stored-row revision together."""
    conn = _get_conn()
    try:
        conn.execute("BEGIN")
        row = _task_editor_row(conn, task_id)
        if row is None:
            return None
        return _row_to_dict(row), _task_row_revision(row)
    finally:
        conn.close()


def _task_settings_profile(conn: sqlite3.Connection, values: dict, *, required: bool) -> dict | None:
    from row_bot.agent_profiles import AgentProfileError, resolve_profile_for_run

    try:
        return resolve_profile_for_run(
            values.get("agent_profile_id") or DEFAULT_WORKFLOW_AGENT_PROFILE_ID,
            parent_approval_mode=values.get("safety_mode") or "block",
            require_enabled=required, single_snapshot=True, connection=conn,
        )
    except AgentProfileError as exc:
        if required:
            raise TaskMutationError("task_settings_profile_unavailable", str(values.get("id") or "")) from exc
        return None


def _task_settings_profile_revision(profile: dict | None) -> str | None:
    if profile is None:
        return None
    snapshot = dict(profile["profile_snapshot"])
    snapshot.pop("snapshot_at", None)
    return hashlib.sha256(json.dumps({"profile": snapshot, "approval": profile["effective_approval_mode"]},
                                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@_schema_retry
def read_task_settings_review(task_id: str, values: dict | None = None) -> tuple[dict, str, dict | None]:
    """Read settings and their profile from one canonical, non-mutating snapshot."""
    conn = _get_conn()
    try:
        conn.execute("BEGIN")
        row = _task_editor_row(conn, task_id)
        if row is None:
            raise TaskMutationError("task_not_found", task_id)
        task = _row_to_dict(row)
        projected = {**task, **(values or {})}
        profile = _task_settings_profile(conn, projected, required=values is not None)
        return task, _task_row_revision(row), profile
    finally:
        conn.close()


@_schema_retry
def update_task_settings(
    task_id: str, *, expected_revision: str, values: dict,
    expected_profile_revision: str | None, validate: Callable[[], None],
    rotate_webhook: bool = False,
    record_commit: Callable[[sqlite3.Connection, str], None] | None = None,
) -> None:
    """Publish reviewed settings in their existing task row; never execute."""
    allowed = {"trigger", "model_override", "agent_profile_id", "safety_mode", "workspace_id",
               "workspace_name", "missed_runs", "always_ask", "send_results"}
    if set(values) - allowed:
        raise TaskMutationError("invalid_task_settings", task_id)
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        validate()
        row = _task_editor_row(conn, task_id)
        if row is None:
            raise TaskMutationError("task_not_found", task_id)
        if _task_row_revision(row) != expected_revision:
            raise TaskMutationError("task_revision_conflict", task_id)
        current = _row_to_dict(row)
        merged = {**current, **values}
        if not rotate_webhook:
            profile = _task_settings_profile(conn, merged, required=True)
            if _task_settings_profile_revision(profile) != expected_profile_revision:
                raise TaskMutationError("task_settings_profile_conflict", task_id)
            if "model_override" in values:
                try:
                    canonical = _canonicalize_workflow_model_override(values["model_override"])
                except ValueError as exc:
                    raise TaskMutationError("task_settings_model_unavailable", task_id) from exc
                if canonical != values["model_override"]:
                    raise TaskMutationError("task_settings_model_unavailable", task_id)
        trigger = merged.get("trigger")
        if rotate_webhook:
            if not isinstance(current.get("trigger"), dict) or current["trigger"].get("type") != "webhook":
                raise TaskMutationError("task_webhook_unavailable", task_id)
            trigger = {**current["trigger"], "secret": generate_webhook_secret()}
            values = {"trigger": trigger}
        elif "trigger" in values and isinstance(trigger, dict) and trigger.get("type") == "webhook":
            # Caller cannot supply or overwrite credentials. New webhook
            # activation generates its secret only inside this transaction.
            previous = current.get("trigger")
            secret = previous.get("secret") if isinstance(previous, dict) and previous.get("type") == "webhook" else None
            if not isinstance(secret, str) or not secret:
                secret = generate_webhook_secret()
            trigger = {**trigger, "secret": secret}
            values = {**values, "trigger": trigger}
        if isinstance(trigger, dict) and trigger.get("type") == "task_complete":
            target = trigger.get("target_task")
            visited = {task_id}
            while target:
                if target in visited:
                    raise TaskMutationError("task_trigger_cycle", task_id)
                if len(visited) >= 1000:
                    raise TaskMutationError("task_settings_too_large", task_id)
                visited.add(target)
                dependency = _task_editor_row(conn, target)
                if dependency is None:
                    raise TaskMutationError("task_trigger_target_unavailable", task_id)
                dependency_trigger = _row_to_dict(dependency).get("trigger")
                target = dependency_trigger.get("target_task") if isinstance(dependency_trigger, dict) and dependency_trigger.get("type") == "task_complete" else None
        for key, value in values.items():
            if key in ("trigger", "always_ask") and value is not None:
                value = json.dumps(value)
            conn.execute(f"UPDATE tasks SET {key} = ? WHERE id = ?", (value, task_id))
        _task_editor_row(conn, task_id)
        if record_commit is not None:
            record_commit(conn, task_id)
        validate()
        conn.commit()
    finally:
        conn.close()


@_schema_retry
def update_task(
    task_id: str, *, expected_revision: str | None = None,
    validate: Callable[[], None] | None = None,
    record_commit: Callable[[sqlite3.Connection, str], None] | None = None, **kwargs,
) -> None:
    """Update task fields.

    Accepted keys: name, description, icon, prompts (list[str]), schedule,
    at, notify_only, notify_label, enabled, sort_order, last_run,
    delivery_channel, delivery_target, model_override,
    persistent_thread_id, delete_after_run.
    """
    _ALLOWED = {
        "name", "description", "icon", "prompts", "schedule", "at",
        "notify_only", "notify_label", "enabled", "sort_order", "last_run",
        "delivery_channel", "delivery_target", "model_override",
        "persistent_thread_id", "delete_after_run",
        "allowed_commands", "allowed_recipients",
        "skills_override",
        "steps", "safety_mode", "concurrency_group", "trigger",
        "tools_override", "channels", "advanced_mode", "agent_profile_id",
        "profile_migration_status", "profile_migration_note",
        "profile_migration_snapshot_json",
        "timezone", "workspace_id", "workspace_name", "missed_runs", "always_ask", "send_results",
    }
    if expected_revision is not None and set(kwargs) - {
        "name", "description", "icon", "prompts", "schedule", "at", "enabled",
        "notify_only", "notify_label", "channels", "timezone",
    }:
        # The reviewed editor does not run legacy profile conversion or graph
        # rewrites before CAS. Existing unreviewed callers retain their API.
        raise TaskMutationError("task_review_unsupported_fields", task_id)
    # A workflow is what it sends and its approval level (F31): old step lists become messages,
    # and tool, skill and queue settings are no longer kept.
    for retired in ("tools_override", "skills_override", "concurrency_group", "advanced_mode"):
        kwargs.pop(retired, None)
    if "steps" in kwargs:
        steps = kwargs.pop("steps") or []
        if steps:
            kwargs["prompts"] = messages_from_steps(steps)

    if "delivery_channel" in kwargs or "delivery_target" in kwargs:
        # Merge with existing values to get full picture
        task = get_task(task_id)
        if task:
            ch = kwargs.get("delivery_channel", task.get("delivery_channel"))
            tgt = kwargs.get("delivery_target", task.get("delivery_target"))
            _validate_delivery(ch, tgt)

    conn = _get_conn()
    try:
        if expected_revision is not None or validate is not None or record_commit is not None:
            conn.execute("BEGIN IMMEDIATE")
        if expected_revision is not None:
            row = _task_editor_row(conn, task_id)
            if row is None:
                raise TaskMutationError("task_not_found", task_id)
            if _task_row_revision(row) != expected_revision:
                raise TaskMutationError("task_revision_conflict", task_id)
        if validate is not None:
            validate()
        for key, value in kwargs.items():
            if key not in _ALLOWED:
                continue
            if key == "model_override":
                value = _canonicalize_workflow_model_override(value)
            if key == "safety_mode":
                value = legacy_safety_mode_to_approval_mode(value)
            if key == "agent_profile_id":
                value = _canonicalize_agent_profile_reference(value or DEFAULT_WORKFLOW_AGENT_PROFILE_ID)
            if key == "timezone":
                from row_bot import schedule_zones

                if value and not schedule_zones.is_zone(value):
                    raise TaskMutationError("invalid_task_schedule", task_id)
                value = value or ""
            if key == "missed_runs" and value not in MISSED_RUN_RULES:
                raise TaskMutationError("invalid_task_settings", task_id)
            if key == "send_results" and value not in SEND_RESULTS:
                raise TaskMutationError("invalid_task_settings", task_id)
            if key == "always_ask":
                value = list(value or [])
            if key in ("prompts", "allowed_commands", "allowed_recipients",
                       "skills_override", "steps", "trigger", "always_ask",
                       "tools_override", "channels", "profile_migration_snapshot_json"):
                value = json.dumps(value, sort_keys=True) if value is not None else None
            if key in ("notify_only", "delete_after_run", "advanced_mode"):
                value = int(value)
            conn.execute(
                f"UPDATE tasks SET {key} = ? WHERE id = ?",
                (value, task_id),
            )
        if {"schedule", "at"} & set(kwargs):
            # A schedule keeps its times in a zone: the computer's unless one was chosen (N9).
            from row_bot import schedule_zones

            conn.execute(
                "UPDATE tasks SET timezone = ? WHERE id = ? AND COALESCE(timezone, '') = '' "
                "AND (COALESCE(schedule, '') != '' OR COALESCE(at, '') != '')",
                (schedule_zones.local_zone_name(), task_id),
            )
        if validate is not None:
            validate()
        if record_commit is not None:
            record_commit(conn, task_id)
        conn.commit()
    finally:
        conn.close()

    # Re-sync APScheduler job if schedule-related fields changed
    _SCHEDULE_KEYS = {"schedule", "at", "enabled", "notify_only", "delete_after_run", "timezone"}
    if _scheduler is not None and _SCHEDULE_KEYS & set(kwargs):
        sync_task_schedule(task_id, validate=validate)


def sync_task_schedule(task_id: str, *, validate: Callable[[], None] | None = None) -> None:
    """Reconcile a saved schedule without starting the scheduler or running it.

    Existing task writer admission holds the captured row stable through the
    local scheduler effect. A failure cannot undo the already saved task.
    """
    if _scheduler is None:
        if validate is not None:
            validate()
        return
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = _task_editor_row(conn, task_id) if validate is not None else conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,),
        ).fetchone()
        if row is None:
            raise TaskMutationError("task_not_found", task_id, committed=True)
        if validate is not None:
            validate()
        _sync_job(_row_to_dict(row))
        if validate is not None:
            validate()
    except TaskMutationError as exc:
        raise TaskMutationError(exc.code, task_id, committed=True) from exc
    except Exception as exc:
        if validate is not None:
            raise TaskMutationError("task_schedule_unconfirmed", task_id, committed=True) from exc
        raise
    finally:
        conn.close()


@_publishes_live("runs", "approvals", "conversations")
@_schema_retry
def delete_task(task_id: str, *, expected_revision: str | None = None,
                validate: Callable[[], None] | None = None,
                preserve_conversations: bool = False, keep_conversations: bool = False) -> None:
    """Delete a task. *keep_conversations*: a one-time run's answer stays where the person can read it."""
    if expected_revision is not None or validate is not None or preserve_conversations:
        if not preserve_conversations or not isinstance(expected_revision, str) or len(expected_revision) != 64:
            raise TaskMutationError("task_delete_review_required", task_id)
        conn = _get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = _task_editor_row(conn, task_id)
            if validate is not None:
                validate()
            if row is None:
                raise TaskMutationError("task_not_found", task_id)
            if _task_row_revision(row) != expected_revision:
                raise TaskMutationError("task_delete_revision_conflict", task_id)
            # New reviewed runs can share ordinary conversations. Run identity
            # is not conversation ownership, nor authority over other runs'
            # approvals or recovery rows. Only the exact task is removed.
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            if validate is not None:
                validate()
            conn.commit()
        finally:
            conn.close()
        # Remove only a now-orphaned timer. A newly created row with the same
        # ID owns its own schedule, so recheck under the canonical writer lock.
        conn = _get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
                _remove_job(task_id)
        except Exception as exc:
            raise TaskMutationError("task_delete_schedule_unconfirmed", task_id, committed=True) from exc
        finally:
            conn.close()
        return
    _remove_job(task_id)
    conn = _get_conn()
    # Cancel pending approval_requests up-front, co-located with the DELETE that removes the task
    # row. Task runs are preserved for audit history (get_recent_runs handles orphaned runs via
    # COALESCE to "(deleted)").
    conn.execute(
        "UPDATE approval_requests SET status = 'cancelled', responded_at = ? "
        "WHERE task_id = ? AND status = 'pending'",
        (datetime.now().isoformat(), task_id),
    )
    # Collect all threads linked to this task BEFORE we tear down the rows.
    linked_thread_ids: set[str] = set()
    try:
        row = conn.execute(
            "SELECT COALESCE(persistent_thread_id, '') FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row and row[0]:
            linked_thread_ids.add(row[0])
        for (tid,) in conn.execute(
            "SELECT DISTINCT thread_id FROM task_runs "
            "WHERE task_id = ? AND thread_id IS NOT NULL AND thread_id != ''",
            (task_id,),
        ):
            linked_thread_ids.add(tid)
    except Exception:
        logger.debug("Could not enumerate run threads for task %s",
                     task_id, exc_info=True)

    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()

    # Cascade thread cleanup (LangGraph checkpoints, media, external state).
    if linked_thread_ids and not keep_conversations:
        try:
            from row_bot.thread_cleanup import delete_thread
            for tid in linked_thread_ids:
                try:
                    delete_thread(tid)
                except Exception:
                    logger.exception(
                        "Failed to cascade thread deletion for task %s (thread %s)",
                        task_id, tid,
                    )
        except Exception:
            logger.exception(
                "threads module unavailable while cascading task %s", task_id,
            )


def duplicate_task(task_id: str, *, new_task_id: str | None = None,
                   validate: Callable[[], None] | None = None,
                   record_commit: Callable[[sqlite3.Connection, str], None] | None = None) -> str | None:
    """Clone a task and return the new ID.

    The copy has no schedule, one-off time or trigger (a webhook secret is
    never copied), so it never runs by itself, and it starts switched off
    until the person turns it on (B177).
    """
    task = get_task(task_id)
    if not task:
        return None
    return create_task(
        name=f"{task['name']} (copy)",
        prompts=task["prompts"],
        description=task["description"],
        icon=task["icon"],
        schedule=None,  # don't copy schedule
        at=None,
        notify_only=task.get("notify_only", False),
        notify_label=task.get("notify_label", ""),
        delivery_channel=task.get("delivery_channel"),
        delivery_target=task.get("delivery_target"),
        model_override=task.get("model_override"),
        safety_mode=task.get("safety_mode") or "block",
        channels=task.get("channels"),
        agent_profile_id=task.get("agent_profile_id"),
        apply_default_skills=False,
        enabled=False,
        **({"task_id": new_task_id} if new_task_id is not None else {}),
        **({"validate": validate} if validate is not None else {}),
        **({"record_commit": record_commit} if record_commit is not None else {}),
    )


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["prompts"] = json.loads(d["prompts"])
    d["notify_only"] = bool(d.get("notify_only", 0))
    d["delete_after_run"] = bool(d.get("delete_after_run", 0))
    d["enabled"] = bool(d.get("enabled", 1))
    d.pop("advanced_mode", None)
    d["allowed_commands"] = json.loads(d.get("allowed_commands") or "[]")
    d["allowed_recipients"] = json.loads(d.get("allowed_recipients") or "[]")
    raw_skills = d.get("skills_override")
    d["skills_override"] = json.loads(raw_skills) if raw_skills else None
    d.pop("steps", None)  # Workflows are messages (F31).
    raw_trigger = d.get("trigger")
    d["trigger"] = json.loads(raw_trigger) if raw_trigger else None
    raw_tools = d.get("tools_override")
    d["tools_override"] = json.loads(raw_tools) if raw_tools else None
    raw_channels = d.get("channels")
    d["channels"] = json.loads(raw_channels) if raw_channels else None
    d["agent_profile_id"] = str(d.get("agent_profile_id") or "")
    d["profile_migration_status"] = str(d.get("profile_migration_status") or "")
    d["profile_migration_note"] = str(d.get("profile_migration_note") or "")
    raw_migration = d.get("profile_migration_snapshot_json")
    try:
        d["profile_migration_snapshot_json"] = json.loads(raw_migration) if raw_migration else {}
    except Exception:
        d["profile_migration_snapshot_json"] = {}
    if d.get("safety_mode"):
        d["safety_mode"] = legacy_safety_mode_to_approval_mode(d.get("safety_mode"))
    d["workspace_id"] = str(d.get("workspace_id") or "")
    d["workspace_name"] = str(d.get("workspace_name") or "")
    d["timezone"] = str(d.get("timezone") or "")
    d["missed_runs"] = d.get("missed_runs") if d.get("missed_runs") in MISSED_RUN_RULES else "skip"
    d["send_results"] = d.get("send_results") if d.get("send_results") in SEND_RESULTS else "every_run"
    try:
        raw_ask = json.loads(d.get("always_ask") or "[]")
    except (TypeError, ValueError):
        raw_ask = []
    d["always_ask"] = [str(item) for item in raw_ask if isinstance(item, str)] if isinstance(raw_ask, list) else []
    return d


# ── Run History ──────────────────────────────────────────────────────────────

def _workflow_agent_profile_ref(task: dict | None) -> str:
    if not task:
        return DEFAULT_WORKFLOW_AGENT_PROFILE_ID
    profile_ref = str(task.get("agent_profile_id") or "")
    if profile_ref:
        return profile_ref
    return DEFAULT_WORKFLOW_AGENT_PROFILE_ID


def _workflow_profile_runtime_policy(
    task: dict,
    enabled_tool_names: Sequence[str],
    *, single_snapshot: bool = False, profile_connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    from row_bot.agent_profiles import resolve_profile_for_run

    parent_approval = get_task_approval_mode(task)
    options = {"single_snapshot": True} if single_snapshot else {}
    if profile_connection is not None:
        options["connection"] = profile_connection
    resolved = resolve_profile_for_run(
        _workflow_agent_profile_ref(task),
        parent_approval_mode=parent_approval,
        require_enabled=True,
        **options,
    )
    profile_snapshot = dict(resolved["profile_snapshot"])
    return {
        "agent_profile_id": str(resolved["profile_id"] or DEFAULT_WORKFLOW_AGENT_PROFILE_ID),
        "agent_profile_slug": str(resolved["profile_slug"] or ""),
        "agent_profile_snapshot": profile_snapshot,
        "approval_mode": normalize_approval_mode(
            str(resolved["effective_approval_mode"] or parent_approval),
            parent_approval,
        ),
        "model_override": str(task.get("model_override") or ""),
        "warnings": list(resolved.get("warnings") or []),
    }


def _reviewed_policy_revision(policy: dict) -> str:
    captured = json.loads(json.dumps(policy))
    captured.get("agent_profile_snapshot", {}).pop("snapshot_at", None)
    return hashlib.sha256(json.dumps(captured, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def capture_task_run_review(task_id: str, enabled_tool_names: Sequence[str]) -> tuple[dict, str, dict]:
    """Capture a task and one profile policy while their existing writer is held."""
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = _task_editor_row(conn, task_id)
        if row is None:
            raise TaskMutationError("task_not_found", task_id)
        task = _row_to_dict(row)
        policy = _workflow_profile_runtime_policy(task, enabled_tool_names, single_snapshot=True, profile_connection=conn)
        return task, _task_row_revision(row), policy
    finally:
        conn.close()


@_publishes_live("runs", "conversations")
def claim_reviewed_task_run(
    task_id: str, thread_id: str, run_id: str, *, expected_task_revision: str,
    expected_policy_revision: str, enabled_tool_names: Sequence[str],
    validate: Callable[[], None], record_commit: Callable[[sqlite3.Connection, str], None] | None = None,
    refresh_enabled_tool_names: Callable[[], Sequence[str]] | None = None,
) -> dict | None:
    """Persist one dispatch reservation in the existing run and pipeline owners.

    None means this exact run already exists. A starting run is deliberately
    never redispatched after a crash; its side-effect boundary is uncertain.
    """
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        validate()
        existing = conn.execute("SELECT task_id,thread_id FROM task_runs WHERE id=?", (run_id,)).fetchone()
        if existing is not None:
            if existing["task_id"] != task_id or existing["thread_id"] != thread_id:
                raise TaskMutationError("task_run_identity_conflict", task_id)
            return None
        row = _task_editor_row(conn, task_id)
        if row is None:
            raise TaskMutationError("task_not_found", task_id)
        if _task_row_revision(row) != expected_task_revision:
            raise TaskMutationError("task_revision_conflict", task_id)
        task = _row_to_dict(row)
        if task.get("persistent_thread_id") and task["persistent_thread_id"] != thread_id:
            raise TaskMutationError("task_run_target_conflict", task_id)
        if refresh_enabled_tool_names is not None:
            enabled_tool_names = refresh_enabled_tool_names()
        policy = _workflow_profile_runtime_policy(task, enabled_tool_names, single_snapshot=True, profile_connection=conn)
        if _reviewed_policy_revision(policy) != expected_policy_revision:
            raise TaskMutationError("task_policy_revision_conflict", task_id)
        if not task.get("notify_only") and not task.get("prompts"):
            raise TaskMutationError("task_has_no_steps", task_id)
        captured = {
            "v": 1, "task": task, "policy": policy,
            "task_revision": expected_task_revision, "policy_revision": expected_policy_revision,
            "run_id": run_id, "thread_id": thread_id,
        }
        if len(json.dumps(captured, ensure_ascii=False).encode()) > 4 * 1024 * 1024:
            raise TaskMutationError("task_metadata_too_large", task_id)
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO task_runs (id,task_id,thread_id,started_at,status,steps_total,steps_done,task_name,task_icon) "
            "VALUES (?,?,?,?,'starting',?,0,?,?)",
            (run_id, task_id, thread_id, now, 0 if task.get("notify_only") else len(task["prompts"]),
             task["name"], task["icon"]),
        )
        if record_commit is not None:
            record_commit(conn, run_id)
        validate()
        conn.commit()
        return captured
    finally:
        conn.close()


# A run's bounded status, as run lists and headers show it: never prompts, traces or delivery payloads.
_RUN_SUMMARY_COLUMNS = (
    "id,task_id,thread_id,substr(status,1,80) AS status,"
    "substr(started_at,1,80) AS started_at,substr(finished_at,1,80) AS finished_at,steps_total,steps_done,"
    "substr(COALESCE(workspace_id,''),1,128) AS workspace_id,substr(COALESCE(workspace_name,''),1,256) AS workspace_name,"
    "substr(COALESCE(late_for,''),1,80) AS late_for,substr(COALESCE(notice,''),1,512) AS notice,"
    "substr(COALESCE(fix,''),1,32) AS fix,substr(COALESCE(actions_json,'[]'),1,65536) AS actions_json"
)


def newest_run_fix(task_id: str) -> str:
    """What the person can do about how a workflow's newest run ended: "choose_folder" (N9), or ""."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT substr(COALESCE(fix,''),1,32) FROM task_runs WHERE task_id=? "
            "ORDER BY started_at DESC, id DESC LIMIT 1", (task_id,),
        ).fetchone()
    finally:
        conn.close()
    return str(row[0]) if row else ""


def read_task_run(run_id: str) -> dict | None:
    """Read bounded status without exposing prompt, trace or delivery payloads."""
    conn = _get_conn()
    try:
        row = conn.execute(
            f"SELECT {_RUN_SUMMARY_COLUMNS} FROM task_runs WHERE id=?", (run_id,),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


@_publishes_live("runs", "conversations")
def _reviewed_run_enter(
    run_id: str, task_id: str, thread_id: str, captured: dict,
    validate: Callable[[], None] | None,
) -> None:
    if validate is not None:
        validate()
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT task_id,thread_id,status FROM task_runs WHERE id=?", (run_id,)).fetchone()
        if row is None or row["task_id"] != task_id or row["thread_id"] != thread_id:
            raise TaskMutationError("task_run_identity_conflict", task_id, committed=True)
        if row["status"] == "stopping":
            raise InterruptedError("reviewed task run stopped before dispatch")
        if row["status"] not in {"starting", "running", "paused", "waiting_approval"}:
            raise TaskMutationError("task_run_not_active", task_id, committed=True)
        if validate is not None:
            validate()
        conn.execute("UPDATE task_runs SET status='running',finished_at=NULL WHERE id=?", (run_id,))
        conn.commit()
    finally:
        conn.close()


def iter_task_run_snapshot(task_id: str) -> Iterator[dict]:
    conn = _get_conn()
    try:
        conn.execute("BEGIN")
        rows = conn.execute(
            f"SELECT {_RUN_SUMMARY_COLUMNS} FROM task_runs WHERE task_id=? ORDER BY started_at DESC,id DESC",
            (task_id,),
        )
        while batch := rows.fetchmany(128):
            for row in batch:
                yield dict(row)
    finally:
        conn.close()


@_publishes_live("runs", "approvals", "conversations")
def stop_reviewed_task_run(task_id: str, run_id: str, *, validate: Callable[[], None]) -> tuple[bool, bool]:
    """Stop this run: its turn stops and the card it waits on is withdrawn. (stop requested, quiesced)."""
    from row_bot.application.client_platform import client_platform_service

    validate()
    conn = _get_conn()
    try:
        row = conn.execute("SELECT id,task_id,thread_id,status FROM task_runs WHERE id=?", (run_id,)).fetchone()
    finally:
        conn.close()
    if row is None or row["task_id"] != task_id:
        raise TaskMutationError("task_run_not_found", task_id)
    if row["status"] in TERMINAL_RUN_STATUSES:
        return False, not client_platform_service.registry.active(row["thread_id"])
    stop_workflow_run(dict(row))
    return True, not client_platform_service.registry.active(row["thread_id"])


@_publishes_live("runs", "conversations")
@_schema_retry
def _record_run_start(task_id: str, thread_id: str, steps_total: int,
                      task_name: str = "", task_icon: str = "", late_for: str = "") -> str:
    run_id = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()
    conn = _get_conn()
    conn.execute(
        "INSERT INTO task_runs (id, task_id, thread_id, started_at, "
        "status, steps_total, steps_done, task_name, task_icon, late_for) "
        "VALUES (?, ?, ?, ?, 'running', ?, 0, ?, ?, ?)",
        (run_id, task_id, thread_id, now, steps_total, task_name, task_icon, late_for),
    )
    conn.commit()
    conn.close()
    return run_id


@_schema_retry
def _update_run_progress(run_id: str, steps_done: int) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE task_runs SET steps_done = ? WHERE id = ?",
        (steps_done, run_id),
    )
    conn.commit()
    conn.close()


_MEMORY_FALLBACK_STATUS_PREFIX = "Memory search is limited"


def _merge_memory_fallback_status(existing: str, status_message: str) -> str:
    warnings = [
        line
        for line in str(existing or "").splitlines()
        if line.startswith(_MEMORY_FALLBACK_STATUS_PREFIX)
    ]
    parts = [str(status_message or "").strip(), *warnings]
    return "\n".join(part for part in parts if part)


@_schema_retry
def _record_run_recall_notices(run_id: str, generation_id: str) -> None:
    """Persist any degraded memory-recall notice on the workflow run."""
    try:
        from row_bot.memory_policy import consume_recall_fallback_notices

        notices = consume_recall_fallback_notices(generation_id)
    except Exception:
        logger.debug("Could not consume workflow memory fallback notices", exc_info=True)
        return
    if not notices:
        return
    conn = _get_conn()
    row = conn.execute(
        "SELECT status_message FROM task_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    existing = str(row[0] or "") if row else ""
    lines = list(existing.splitlines()) if existing else []
    for notice in notices:
        line = (
            f"{_MEMORY_FALLBACK_STATUS_PREFIX}: {notice.get('message') or ''} {notice.get('detail') or ''} "
            f"To fix it: {notice.get('action') or ''}"
        ).strip()
        if line not in lines:
            lines.append(line)
    conn.execute(
        "UPDATE task_runs SET status_message = ? WHERE id = ?",
        ("\n".join(lines), run_id),
    )
    conn.commit()
    conn.close()


@_publishes_live("runs", "approvals", "conversations")
@_schema_retry
def _finish_run(run_id: str, status: str = "completed",
                status_message: str = "", terminal_reason: str = "", notice: str = "",
                fix: str = "", actions: list[dict] | None = None) -> None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT status, status_message FROM task_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if (
        row
        and str(row["status"] or "") in TERMINAL_RUN_STATUSES
        and status not in TERMINAL_RUN_STATUSES
    ):
        conn.close()
        return
    existing_status = str(row["status_message"] or "") if row else ""
    if row and row["status"] == "stopping":
        status = "stopped"
        conn.execute("UPDATE approval_requests SET status='cancelled',responded_at=? WHERE run_id=? AND status='pending'",
                     (datetime.now().isoformat(), run_id))
    status_message = _merge_memory_fallback_status(existing_status, status_message)
    conn.execute(
        "UPDATE task_runs SET status = ?, status_message = ?, finished_at = ? "
        "WHERE id = ?",
        (status, status_message, datetime.now().isoformat(), run_id),
    )
    if notice:
        conn.execute("UPDATE task_runs SET notice = ? WHERE id = ?", (notice, run_id))
    if fix:
        conn.execute("UPDATE task_runs SET fix = ? WHERE id = ?", (fix, run_id))
    if actions is not None:
        conn.execute("UPDATE task_runs SET actions_json = ? WHERE id = ?",
                     (json.dumps(actions, ensure_ascii=False), run_id))
    if status in ("completed", "completed_delivery_failed"):
        # A finished run is done, however it got there: a last notify step, "next": "end", a condition that
        # ended early or a resumed approval all used to leave it at "2 of 3 steps" (B313).
        conn.execute("UPDATE task_runs SET steps_done = steps_total WHERE id = ?", (run_id,))
    conn.commit()
    conn.close()
    if status in ("failed", "blocked", "completed_delivery_failed"):
        _publish_live("monitor")  # A run that went wrong is one of Monitor's problems.


def _emit_buddy_workflow_event(
    status: str,
    *,
    task_id: str = "",
    thread_id: str = "",
    label: str = "",
    error: str = "",
) -> None:
    try:
        from row_bot.buddy.events import BuddyEventType, emit_buddy_event
        event_type = {
            "done": BuddyEventType.WORKFLOW_DONE,
            "error": BuddyEventType.WORKFLOW_ERROR,
            "cancelled": BuddyEventType.WORKFLOW_CANCELLED,
        }.get(status)
        if event_type is None:
            return
        payload = {
            "task_id": task_id,
            "thread_id": thread_id,
            "label": label or (
                "Workflow error" if status == "error"
                else "Workflow cancelled" if status == "cancelled"
                else "Workflow done"
            ),
        }
        if error:
            payload["error"] = error
        emit_buddy_event(event_type, source="tasks", payload=payload)
    except Exception:
        logger.debug("Buddy workflow event failed", exc_info=True)


def _fire_completion_triggers(completed_task_id: str, *, validate: Callable[[], None] | None = None) -> None:
    """Check if any task has a trigger of type 'task_complete' matching this task.
    If so, fire those tasks in the background.
    """
    from row_bot.tools import registry as tool_registry

    all_tasks = list_tasks()
    enabled_tools = [t.name for t in tool_registry.get_enabled_tools()]

    for t in all_tasks:
        trigger = t.get("trigger")
        if not trigger or not isinstance(trigger, dict):
            continue
        if trigger.get("type") != "task_complete":
            continue
        if trigger.get("target_task") != completed_task_id:
            continue
        if not t.get("enabled", True):
            continue

        logger.info(
            "Completion trigger: task '%s' completed → firing '%s'",
            completed_task_id, t["name"],
        )
        _check_workflow_effect(validate)
        thread_id = _prepare_task_thread(t)
        _check_workflow_effect(validate)
        run_task_background(t["id"], thread_id, enabled_tools,
                            **({"validate": validate} if validate is not None else {}))


@_schema_retry
def get_run_history(task_id: str, limit: int = 5) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM task_runs WHERE task_id = ? "
        "ORDER BY started_at DESC LIMIT ?",
        (task_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@_schema_retry
def get_recent_runs(limit: int = 10) -> list[dict]:
    """Return the most recent task runs across all tasks.

    Run rows carry their own ``task_name`` / ``task_icon`` so they remain
    visible in the Activity panel even after the parent task is deleted.
    """
    conn = _get_conn()
    rows = conn.execute(
        "SELECT r.id, r.task_id, r.thread_id, r.started_at, r.finished_at, "
        "r.status, r.status_message, r.steps_total, r.steps_done, "
        "COALESCE(NULLIF(r.task_name, ''), t.name, '(deleted)') AS task_name, "
        "COALESCE(NULLIF(r.task_icon, ''), t.icon, '⚡') AS task_icon "
        "FROM task_runs r LEFT JOIN tasks t ON r.task_id = t.id "
        "ORDER BY r.started_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@_schema_retry
def get_upcoming_tasks(limit: int = 5) -> list[dict]:
    """Return tasks that have a schedule or an ``at`` time, sorted by next run."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE enabled = 1 "
        "AND (schedule IS NOT NULL OR at IS NOT NULL) "
        "ORDER BY COALESCE(at, '') DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


# ── Background Execution Engine ──────────────────────────────────────────────

_active_runs: dict[str, dict] = {}  # thread_id -> {task_id, run_id, step, total, name}
_active_lock = threading.Lock()
# A run still going: its turn runs, or it waits for the person on an approval card (F31).
ACTIVE_RUN_STATUSES = ("running", "waiting")
# How a run ended. Denied: the person said no on a card. Expired: a newer run replaced it while it
# waited. Unsure: a tool couldn't confirm its action went through.
TERMINAL_RUN_STATUSES = frozenset({"completed", "completed_delivery_failed", "failed", "stopped", "blocked",
                                   "denied", "expired", "unsure", "skipped"})


def _workflow_entry_failed(run_id: str, thread_id: str, exc: BaseException) -> None:
    """Retain a truthful workflow terminal fact when its worker cannot enter."""
    from row_bot.cancellation import current_cancellation_scope
    scope = current_cancellation_scope()
    stopped = isinstance(exc, InterruptedError) or bool(scope and scope.is_cancelled())
    status = "stopped" if stopped else "failed"
    try:
        _finish_run(run_id, status, status_message=("Stop requested before dispatch" if stopped else "Execution resources are unavailable"))
    finally:
        with _active_lock:
            entry = _active_runs.get(thread_id)
            if entry and entry.get("run_id") == run_id:
                _active_runs.pop(thread_id, None)


def get_running_tasks() -> dict[str, dict]:
    """Return ``{thread_id: {task_id, run_id, step, total, name, icon,
    started_at, step_label, log}}`` for all in-flight task executions."""
    with _active_lock:
        return dict(_active_runs)


def active_run_for_conversation(thread_id: str) -> dict | None:
    """The workflow run going on in this conversation, if any (F31)."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, task_id, thread_id, status, started_at, steps_done, steps_total, first_message_id "
            f"FROM task_runs WHERE thread_id = ? AND status IN ({', '.join('?' * len(ACTIVE_RUN_STATUSES))}) "
            "ORDER BY started_at DESC LIMIT 1", (thread_id, *ACTIVE_RUN_STATUSES),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _active_run_for_task(task_id: str, *, besides: str = "") -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, task_id, thread_id, status, started_at FROM task_runs WHERE task_id = ? AND id != ? "
            f"AND status IN ({', '.join('?' * len(ACTIVE_RUN_STATUSES))}) ORDER BY started_at DESC LIMIT 1",
            (task_id, besides, *ACTIVE_RUN_STATUSES),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def forget_active_run(thread_id: str) -> None:
    with _active_lock:
        _active_runs.pop(thread_id, None)


@_schema_retry
def note_run_first_message(run_id: str, message_id: str) -> None:
    conn = _get_conn()
    try:
        conn.execute("UPDATE task_runs SET first_message_id = ? WHERE id = ?", (message_id, run_id))
        conn.commit()
    finally:
        conn.close()


@_publishes_live("runs")
@_schema_retry
def note_run_retry(run_id: str, notice: str) -> None:
    """A run about to try a failed turn again says so, and why."""
    conn = _get_conn()
    try:
        conn.execute("UPDATE task_runs SET status = 'running', notice = ? WHERE id = ?", (notice[:512], run_id))
        conn.commit()
    finally:
        conn.close()


def pending_approval_id(thread_id: str) -> str | None:
    """The approval card a workflow's conversation waits on, if any."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM approval_requests WHERE source_thread_id = ? AND status = 'pending' "
            "ORDER BY requested_at DESC LIMIT 1", (thread_id,),
        ).fetchone()
    finally:
        conn.close()
    return str(row[0]) if row else None


def _pending_question(thread_id: str) -> str:
    """The question on the approval card a conversation waits on, in its own words."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT message FROM approval_requests WHERE source_thread_id = ? AND status = 'pending' "
            "ORDER BY requested_at DESC LIMIT 1", (thread_id,),
        ).fetchone()
    finally:
        conn.close()
    return " ".join(str(row[0] or "").split())[:240] if row else ""


@_publishes_live("runs", "approvals", "conversations")
@_schema_retry
def mark_run_waiting(run_id: str, task: Mapping[str, Any]) -> None:
    """The run's turn paused on an approval card: it waits for the person, and they hear about it."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT thread_id, status FROM task_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None or row["status"] not in ACTIVE_RUN_STATUSES:
            return
        question = _pending_question(str(row["thread_id"])) or "It needs your approval to go on."
        conn.execute("UPDATE task_runs SET status = 'waiting', notice = ? WHERE id = ?", (question, run_id))
        conn.commit()
    finally:
        conn.close()
    forget_active_run(str(row["thread_id"]))
    from row_bot.notifications import notify
    notify(title=f"{task.get('name') or 'A workflow'} needs your approval", message=question,
           sound="workflow", toast_type="warning", source="workflow", in_app=False)


def after_run_finished(task: dict, run_id: str, thread_id: str, status: str, notice: str,
                       delivery_status: str, delivery_detail: str) -> None:
    """What follows a finished run: its notices, the workflows that run after it, a one-time workflow's removal."""
    from row_bot.notifications import notify
    if status != "stopped":
        update_task(task["id"], last_run=datetime.now().isoformat())
    if status in {"completed", "completed_delivery_failed"}:
        _emit_buddy_workflow_event("done", task_id=task["id"], thread_id=thread_id, label=task.get("name", ""))
        notify(title=f"{task['name']} finished", message=notice, sound="workflow", source="workflow",
               requested=True)
        try:
            _fire_completion_triggers(task["id"])
        except Exception:
            logger.error("Workflows after %s could not start", task["name"], exc_info=True)
    elif status == "stopped":
        _emit_buddy_workflow_event("cancelled", task_id=task["id"], thread_id=thread_id, label=task.get("name", ""))
    else:
        _emit_buddy_workflow_event("error", task_id=task["id"], thread_id=thread_id,
                                   label=task.get("name", ""), error=notice)
        notify(title=f"{task['name']}: {_RUN_ENDING_WORDS.get(status, 'needs a look')}", message=notice,
               sound="workflow", toast_type="warning", source="workflow")
    if delivery_status == "delivery_failed":
        notify(title="Delivery failed", message=f"{task['name']} — {delivery_detail}", sound="timer",
               toast_type="warning", source="workflow")
    if task.get("delete_after_run") and status != "stopped":
        delete_task(task["id"], keep_conversations=True)


_RUN_ENDING_WORDS = {"denied": "you said no", "unsure": "not sure it went through", "failed": "didn't finish",
                     "expired": "replaced by a newer run"}


def stop_workflow_run(run: Mapping[str, Any], *, status: str = "stopped", notice: str = "") -> None:
    """End a run that is going: its turn stops, and the card it waits on is withdrawn."""
    from row_bot.application.client_platform import client_platform_service
    thread_id = str(run["thread_id"])
    waiting = str(run.get("status") or "") == "waiting"
    client_platform_service.stop_conversation(thread_id)
    if waiting or not client_platform_service.registry.active(thread_id):
        # No turn is running, so none ends to finish the run: it is finished here.
        forget_active_run(thread_id)
        _finish_run(str(run["id"]), status, notice=notice or ("You stopped it." if status == "stopped" else ""))


def stop_task(thread_id: str) -> bool:
    """Signal a running task to stop.  Returns True if found & signalled."""
    from row_bot.runtime.executions import generation_registry

    run = active_run_for_conversation(thread_id)
    if run is not None:
        stop_workflow_run(run)
        return True
    registered = generation_registry.stop(thread_id)
    with _active_lock:
        info = _active_runs.get(thread_id)
        if info and "stop_event" in info:
            info["stop_event"].set()
            logger.info("stop_task: signalled stop for thread %s (task %s)",
                        thread_id, info.get("name", "?"))
            _emit_buddy_workflow_event(
                "cancelled",
                task_id=str(info.get("task_id") or ""),
                thread_id=thread_id,
                label=f"Stopping {info.get('name') or 'workflow'}",
            )
            return True
    return registered


@_publishes_live("runs", "approvals", "conversations")
@_schema_retry
def cleanup_thread_state(thread_id: str) -> dict[str, int]:
    """Remove queued/thread-owned workflow state while retaining run audits."""

    clean_thread_id = str(thread_id or "").strip()
    stats = {
        "approvals": 0,
        "channel_refs": 0,
        "notifications": 0,
        "task_links": 0,
    }
    if not clean_thread_id:
        return stats
    now = datetime.now().isoformat()
    conn = _get_conn()
    try:
        approval_clauses = ["source_thread_id = ?", "parent_thread_id = ?"]
        approval_params: list[Any] = [clean_thread_id, clean_thread_id]
        approvals = conn.execute(
            "SELECT id, COALESCE(task_id, '') FROM approval_requests WHERE "
            + " OR ".join(approval_clauses),
            approval_params,
        ).fetchall()
        delete_approval_ids = [str(row[0]) for row in approvals if not str(row[1] or "")]
        audit_approval_ids = [str(row[0]) for row in approvals if str(row[1] or "")]

        before = conn.total_changes
        affected_approval_ids = [*delete_approval_ids, *audit_approval_ids]
        if affected_approval_ids:
            placeholders = ", ".join("?" for _ in affected_approval_ids)
            conn.execute(
                f"DELETE FROM approval_channel_refs WHERE approval_id IN ({placeholders})",
                affected_approval_ids,
            )
        if delete_approval_ids:
            placeholders = ", ".join("?" for _ in delete_approval_ids)
            conn.execute(
                f"DELETE FROM approval_requests WHERE id IN ({placeholders})",
                delete_approval_ids,
            )
        if audit_approval_ids:
            placeholders = ", ".join("?" for _ in audit_approval_ids)
            conn.execute(
                "UPDATE approval_requests SET "
                "status = CASE WHEN status = 'pending' THEN 'cancelled' ELSE status END, "
                "responded_at = CASE WHEN status = 'pending' THEN ? ELSE responded_at END, "
                "message = '', channel = '', source_thread_id = '', parent_thread_id = '', "
                "approval_payload_json = '{}' "
                f"WHERE id IN ({placeholders})",
                [now, *audit_approval_ids],
            )
        stats["approvals"] = max(0, conn.total_changes - before)

        before = conn.total_changes
        conn.execute("DELETE FROM channel_thread_refs WHERE thread_id = ?", (clean_thread_id,))
        # The chat keeps its place, so its next message starts a fresh
        # conversation instead of falling back to one it moved away from.
        conn.execute(
            "UPDATE channel_conversations SET thread_id = '' WHERE thread_id = ?",
            (clean_thread_id,),
        )
        stats["channel_refs"] = max(0, conn.total_changes - before)

        before = conn.total_changes
        conn.execute(
            "DELETE FROM channel_thread_notifications WHERE thread_id = ?",
            (clean_thread_id,),
        )
        stats["notifications"] = max(0, conn.total_changes - before)

        before = conn.total_changes
        conn.execute(
            "UPDATE tasks SET persistent_thread_id = NULL WHERE persistent_thread_id = ?",
            (clean_thread_id,),
        )
        stats["task_links"] = max(0, conn.total_changes - before)
        conn.commit()
    finally:
        conn.close()
    return stats


# Backward-compat alias used by app sidebar
get_running_workflows = get_running_tasks


def _validate_delivery(channel: str | None, target: str | None) -> None:
    """Raise ``ValueError`` if delivery settings are invalid.

    Delegates to the channel registry for validation when available.
    """
    if not channel and not target:
        return  # no delivery — valid
    if channel and not target and channel != "telegram":
        raise ValueError(
            f"delivery_channel is '{channel}' but delivery_target is empty."
        )
    if target and not channel:
        raise ValueError(
            "delivery_target is set but delivery_channel is empty."
        )
    # Validate via channel registry
    try:
        from row_bot.channels import registry as _ch_reg
        _ch_reg.validate_delivery(channel, target)
    except ImportError:
        # Fallback: accept known channel names
        if channel not in ("telegram",):
            raise ValueError(
                f"Unknown delivery_channel '{channel}'."
            )


def _load_task_config() -> dict:
    try:
        with open(_TASK_CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_task_config(data: dict) -> None:
    with open(_TASK_CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)


def get_workflow_default_channels() -> list[str]:
    """Return external channels for workflows that inherit defaults.

    The web app is not stored here: it always receives workflow run status.
    Defaults to an empty list, which means "web app only".
    """
    value = _load_task_config().get("workflow_default_channels", [])
    if not isinstance(value, list):
        return []
    return [str(name) for name in value if isinstance(name, str) and name]


def set_workflow_default_channels(channels: list[str] | None) -> None:
    """Save the external channel default for inheriting workflows."""
    data = _load_task_config()
    clean: list[str] = []
    seen: set[str] = set()
    for name in channels or []:
        if not isinstance(name, str) or not name or name in seen:
            continue
        clean.append(name)
        seen.add(name)
    data["workflow_default_channels"] = clean
    _save_task_config(data)


def get_effective_task_channel_names(task: dict) -> list[str]:
    """Return configured external channel names for a task.

    ``task["channels"] is None`` means inherit the workflow-level default.
    Legacy ``delivery_channel`` values are treated as explicit overrides so
    older workflows keep their specific destination until they are edited.
    """
    override = task.get("channels")
    if override is None:
        legacy = task.get("delivery_channel")
        if legacy:
            return [legacy]
        return get_workflow_default_channels()
    return [str(name) for name in override if isinstance(name, str) and name]


def get_task_channels(task: dict) -> list:
    """Return running external channel objects for this task.

    ``task["channels"] is None`` inherits the workflow-level default.
    ``task["channels"] == []`` means web app only. The web app always
    receives run status and is not represented by a channel adapter.
    """
    from row_bot.channels import registry as _ch_reg
    # Preserve the old distinction in one place: override is None means
    # inherited defaults, while not override means web app only.
    channel_names = get_effective_task_channel_names(task)
    if not channel_names:
        return []
    selected = set(channel_names)
    return [ch for ch in _ch_reg.running_channels() if ch.name in selected]


class _WorkflowEffectDenied(PermissionError):
    """An authoritative refusal must not become an ordinary delivery failure."""


def _check_workflow_effect(validate: Callable[[], None] | None) -> None:
    if validate is not None:
        try:
            validate()
        except _WorkflowEffectDenied:
            raise
        except Exception as exc:
            raise _WorkflowEffectDenied("task_run_authority_lost") from exc


def _deliver_to_channel(task: dict, text: str, *, validate: Callable[[], None] | None = None) -> tuple[str, str]:
    """Send task output to the configured delivery channel (if any).

    Uses the channel registry for routing.

    Returns
    -------
    tuple[str, str]
        ``("delivered", detail)`` on success,
        ``("delivery_failed", reason)`` on error,
        or ``("", "")`` if no delivery was configured.
    """
    channel = task.get("delivery_channel")
    target = task.get("delivery_target")
    if not channel:
        return "", ""
    try:
        from row_bot.channels import registry as _ch_reg
        prefix = f"📋 {task['name']}\n\n"
        ch = _ch_reg.get(channel)
        if ch is None:
            raise RuntimeError(f"Unknown channel: {channel}")
        if not ch.is_running():
            raise RuntimeError(f"{ch.display_name} is not running")

        # Resolve target (Telegram uses configured user ID)
        if channel == "telegram":
            from row_bot.channels.telegram import _get_allowed_user_id
            resolved_target = _get_allowed_user_id()
            if resolved_target is None:
                raise RuntimeError("TELEGRAM_USER_ID is not configured")
        else:
            resolved_target = target
            if not resolved_target:
                # No explicit target — fall back to the channel's
                # configured default (we no longer target-filter in
                # the workflow UI).
                try:
                    resolved_target = ch.get_default_target()
                except Exception as exc:
                    raise RuntimeError(
                        f"{ch.display_name} has no default target configured"
                    ) from exc

        _check_workflow_effect(validate)
        ch.send_message(resolved_target, prefix + text)
        logger.info(
            "Delivery to %s succeeded for task %s", channel, task["name"],
        )
        return "delivered", f"Delivered to {channel}"
    except _WorkflowEffectDenied:
        raise
    except Exception as exc:
        logger.warning(
            "Delivery to %s failed for task %s: %s",
            channel, task["name"], exc,
        )
        return "delivery_failed", f"{channel} delivery failed: {exc}"


def _deliver_to_channels(task: dict, text: str, *, validate: Callable[[], None] | None = None) -> tuple[str, str]:
    """Send task output to all configured channels via ``get_task_channels``.

    Uses the unified ``channels`` field (null = workflow default).
    Falls back to legacy ``delivery_channel`` if ``channels`` is not set
    and ``delivery_channel`` is.

    Returns
    -------
    tuple[str, str]
        ``("delivered", detail)`` on success (at least one channel),
        ``("delivery_failed", reason)`` if all channels failed,
        or ``("", "")`` if no channels are configured/running.
    """
    channels = get_task_channels(task)
    if not channels:
        # Fallback: legacy single-channel field
        if task.get("delivery_channel"):
            return _deliver_to_channel(task, text, **({"validate": validate} if validate is not None else {}))
        return "", ""

    prefix = f"📋 {task['name']}\n\n"
    delivered_to: list[str] = []
    failed: list[str] = []

    for ch in channels:
        try:
            # Resolve target per channel type
            if ch.name == "telegram":
                from row_bot.channels.telegram import _get_allowed_user_id
                target = _get_allowed_user_id()
                if target is None:
                    failed.append(f"{ch.display_name} (no user ID)")
                    continue
            else:
                # Prefer task-level delivery_target; fall back to the
                # channel's configured default. No target configured =>
                # delivery fails for that channel.
                target = task.get("delivery_target") or None
                if target is None:
                    try:
                        target = ch.get_default_target()
                    except Exception as exc:
                        logger.debug(
                            "No default target for %s: %s", ch.name, exc,
                        )
                        target = None
                if target is None:
                    failed.append(f"{ch.display_name} (no target configured)")
                    continue

            _check_workflow_effect(validate)
            ch.send_message(target, prefix + text)
            delivered_to.append(ch.display_name)
            logger.info(
                "Delivery to %s succeeded for task '%s'",
                ch.name, task["name"],
            )
        except _WorkflowEffectDenied:
            raise
        except Exception as exc:
            failed.append(f"{ch.display_name}: {exc}")
            logger.warning(
                "Delivery to %s failed for task '%s': %s",
                ch.name, task["name"], exc,
            )

    if delivered_to:
        detail = "Delivered to " + ", ".join(delivered_to)
        if failed:
            detail += f" (failed: {', '.join(failed)})"
        return "delivered", detail
    elif failed:
        return "delivery_failed", "; ".join(failed)
    return "", ""


def _workflow_final_status_for_delivery(delivery_status: str) -> str:
    return "completed_delivery_failed" if delivery_status == "delivery_failed" else "completed"


def _push_approval_to_channels(task: dict, approval_id: str,
                               resume_token: str,
                               approval_msg: str, *, validate: Callable[[], None] | None = None,
                               question: dict | None = None) -> None:
    """Push a task approval request to all configured channels.

    Uses the unified ``channels`` field (null = workflow default).
    Stores message refs in ``approval_channel_refs`` for cross-channel
    resolution.

    Both button-capable channels (Telegram inline buttons) and text-based
    channels (Slack, Discord, WhatsApp, SMS) receive the request.  Each
    channel's ``send_approval_request`` handles storage of pending state.
    """
    channels = get_task_channels(task)
    for ch in channels:
        try:
            target = ch.get_default_target()
        except Exception:
            logger.debug("Skipping %s — no default target configured", ch.name)
            continue
        try:
            config = {
                "task_name": task["name"],
                "resume_token": resume_token,
                "message": approval_msg,
            }
            _check_workflow_effect(validate)
            # Row-Bot's question (N4) goes as the question itself: each channel asks it its own way.
            msg_ref = ch.send_approval_request(target, question or {}, config)
            if msg_ref:
                _store_approval_channel_ref(approval_id, ch.name, msg_ref)
        except _WorkflowEffectDenied:
            raise
        except Exception as exc:
            logger.warning("Failed to push approval to %s: %s", ch.name, exc)


def record_thread_channel_ref(
    thread_id: str,
    *,
    channel: str,
    target: str | int,
    external_conversation_id: str = "",
    shared: bool | None = None,
) -> None:
    """Remember the channel conversation that owns a Row-Bot thread.

    ``shared``: other people share it (a channel thread or a group chat), so it runs on Auto at most;
    None keeps what was recorded.
    """

    clean_thread_id = str(thread_id or "").strip()
    clean_channel = str(channel or "").strip()
    clean_target = str(target or "").strip()
    if not clean_thread_id or not clean_channel or not clean_target:
        return
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO channel_thread_refs "
            "(thread_id, channel, target, external_conversation_id, updated_at, shared) "
            "VALUES (?, ?, ?, ?, ?, COALESCE(?, 0)) "
            "ON CONFLICT(thread_id) DO UPDATE SET "
            "channel = excluded.channel, target = excluded.target, "
            "external_conversation_id = excluded.external_conversation_id, "
            "updated_at = excluded.updated_at, "
            "shared = CASE WHEN ? IS NULL THEN channel_thread_refs.shared ELSE excluded.shared END",
            (
                clean_thread_id,
                clean_channel,
                clean_target,
                str(external_conversation_id or ""),
                datetime.now().isoformat(),
                None if shared is None else int(bool(shared)),
                None if shared is None else int(bool(shared)),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_thread_channel_ref(thread_id: str) -> dict | None:
    """Return the channel origin for a Row-Bot thread, if known."""

    clean_thread_id = str(thread_id or "").strip()
    if not clean_thread_id:
        return None
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM channel_thread_refs WHERE thread_id = ?",
            (clean_thread_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_channel_conversation(channel: str, conversation_key: str) -> dict | None:
    """Return the current-conversation pointer of one channel chat or thread.

    ``thread_id`` is empty when the conversation it pointed to was deleted.
    ``None`` means the chat has never had a pointer.
    """

    clean_channel = str(channel or "").strip()
    clean_key = str(conversation_key or "").strip()
    if not clean_channel or not clean_key:
        return None
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM channel_conversations WHERE channel = ? AND conversation_key = ?",
            (clean_channel, clean_key),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_channel_conversation(channel: str, conversation_key: str, thread_id: str) -> None:
    """Point one channel chat or thread at the Row-Bot conversation it continues."""

    clean_channel = str(channel or "").strip()
    clean_key = str(conversation_key or "").strip()
    clean_thread_id = str(thread_id or "").strip()
    if not clean_channel or not clean_key or not clean_thread_id:
        return
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO channel_conversations (channel, conversation_key, thread_id, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(channel, conversation_key) DO UPDATE SET "
            "thread_id = excluded.thread_id, updated_at = excluded.updated_at",
            (clean_channel, clean_key, clean_thread_id, datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_channel_thread_notification(key: str) -> dict | None:
    """Return a durable parent-thread channel notification by key."""

    clean_key = str(key or "").strip()
    if not clean_key:
        return None
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM channel_thread_notifications WHERE key = ?",
            (clean_key,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_channel_thread_notification(
    *,
    key: str,
    thread_id: str,
    channel: str,
    target: str | int,
    kind: str,
    text: str,
    payload: Mapping[str, Any] | None = None,
) -> dict | None:
    """Create an immutable durable notification intent for a channel thread."""

    clean_key = str(key or "").strip()
    clean_thread_id = str(thread_id or "").strip()
    clean_channel = str(channel or "").strip()
    clean_target = str(target or "").strip()
    clean_kind = str(kind or "").strip()
    clean_text = str(text or "").strip()
    if not all((clean_key, clean_thread_id, clean_channel, clean_target, clean_kind, clean_text)):
        return None
    now = datetime.now().isoformat()
    payload_text = json.dumps(dict(payload or {}), sort_keys=True)
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO channel_thread_notifications "
            "(key, thread_id, channel, target, kind, text, payload_json, status, "
            "attempts, last_error, created_at, updated_at, delivered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, '', ?, ?, '')",
            (
                clean_key,
                clean_thread_id,
                clean_channel,
                clean_target,
                clean_kind,
                clean_text,
                payload_text,
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM channel_thread_notifications WHERE key = ?",
            (clean_key,),
        ).fetchone()
        if row and tuple(row[field] for field in (
            "thread_id", "channel", "target", "kind", "text", "payload_json",
        )) != (
            clean_thread_id, clean_channel, clean_target, clean_kind, clean_text, payload_text,
        ):
            raise ValueError("Notification key is already bound to another delivery.")
        conn.commit()
        row = conn.execute(
            "SELECT * FROM channel_thread_notifications WHERE key = ?",
            (clean_key,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def claim_channel_thread_notification(key: str) -> dict | None:
    """Reserve one attempt before dispatch; an interrupted send stays uncertain.

    Only explicitly known pre-send failures may be retried. Historical generic
    failures may have happened after a downstream effect and are not replayed.
    """
    key = str(key or "").strip()
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute(
            "UPDATE channel_thread_notifications SET status = 'uncertain', "
            "attempts = attempts + 1, last_error = 'Delivery outcome unconfirmed.', "
            "updated_at = ? WHERE key = ? AND (status = 'pending' OR "
            "(status = 'failed' AND last_error LIKE 'not_sent: %'))",
            (datetime.now().isoformat(), str(key or "").strip()),
        ).rowcount
        row = conn.execute(
            "SELECT * FROM channel_thread_notifications WHERE key = ?", (key,),
        ).fetchone() if changed else None
        conn.commit()
        return dict(row) if row else None
    finally:
        conn.close()


def mark_channel_thread_notification_delivered(key: str, *, attempt: int) -> bool:
    """Mark a parent-thread notification as delivered to its channel."""

    clean_key = str(key or "").strip()
    if not clean_key:
        return False
    now = datetime.now().isoformat()
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE channel_thread_notifications SET status = 'delivered', "
            "last_error = '', updated_at = ?, delivered_at = ? WHERE key = ? "
            "AND status = 'uncertain' AND attempts = ?",
            (now, now, clean_key, attempt),
        )
        changed = conn.total_changes
        conn.commit()
        return bool(changed)
    finally:
        conn.close()


def mark_channel_thread_notification_failed(key: str, error: str, *, attempt: int) -> bool:
    """Record a provably pre-send failure for the exact admitted attempt."""

    clean_key = str(key or "").strip()
    if not clean_key:
        return False
    now = datetime.now().isoformat()
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE channel_thread_notifications SET status = 'failed', "
            "last_error = ?, updated_at = ? WHERE key = ? "
            "AND status = 'uncertain' AND attempts = ?",
            ("not_sent: " + str(error or "")[:990], now, clean_key, attempt),
        )
        changed = conn.total_changes
        conn.commit()
        return bool(changed)
    finally:
        conn.close()


def list_pending_channel_thread_notifications(limit: int = 50) -> list[dict]:
    """Return only pending or proven pre-send failures for retry."""

    safe_limit = max(1, min(500, int(limit or 50)))
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM channel_thread_notifications "
            "WHERE status = 'pending' OR "
            "(status = 'failed' AND last_error LIKE 'not_sent: %') "
            "ORDER BY created_at ASC LIMIT ?",
            (safe_limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _store_approval_channel_ref(approval_id: str, channel: str,
                                message_ref: str) -> None:
    """Store a channel message reference for an approval request."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO approval_channel_refs (approval_id, channel, message_ref) "
        "VALUES (?, ?, ?)",
        (approval_id, channel, message_ref),
    )
    conn.commit()
    conn.close()


def _approval_channel_ref_exists(approval_id: str, channel: str) -> bool:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM approval_channel_refs "
            "WHERE approval_id = ? AND channel = ? LIMIT 1",
            (str(approval_id), str(channel)),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


def push_approval_to_parent_channel(approval_id: str) -> bool:
    """Push a child Agent approval to the parent thread's originating channel."""

    rows = get_pending_approvals(approval_id=str(approval_id or ""))
    approval = rows[0] if rows else None
    if not approval:
        return False
    parent_thread_id = str(approval.get("parent_thread_id") or "").strip()
    if not parent_thread_id:
        return False
    ref = get_thread_channel_ref(parent_thread_id)
    if not ref:
        return False
    channel_name = str(ref.get("channel") or "").strip()
    target = str(ref.get("target") or "").strip()
    if not channel_name or not target:
        return False
    with _APPROVAL_CHANNEL_PUSH_LOCK:
        if _approval_channel_ref_exists(str(approval.get("id") or ""), channel_name):
            return True
        try:
            from row_bot.approval_messages import channel_message, payload_from_row
            from row_bot.channels import registry as channel_registry

            ch = channel_registry.get(channel_name)
            if not ch or not ch.is_running():
                return False
            payload = payload_from_row(approval)
            from row_bot.questions import stored_question

            msg_ref = ch.send_approval_request(
                target,
                # A helper's question (N4) goes as the question, answered in the chat app.
                stored_question(approval.get("approval_payload_json")) or {},
                {
                    "approval_kind": "agent_run",
                    "task_name": str(
                        payload.get("source_label")
                        or approval.get("source_label")
                        or "Child Agent"
                    ),
                    "message": channel_message(payload),
                    "resume_token": str(approval.get("resume_token") or ""),
                    "approval_id": str(approval.get("id") or ""),
                    "agent_run_id": str(approval.get("agent_run_id") or ""),
                    "parent_thread_id": parent_thread_id,
                },
            )
            if msg_ref:
                _store_approval_channel_ref(
                    str(approval.get("id") or ""),
                    channel_name,
                    str(msg_ref),
                )
            return bool(msg_ref)
        except Exception as exc:
            logger.warning(
                "Failed to push approval %s to parent channel %s: %s",
                approval_id,
                channel_name,
                exc,
            )
            return False


def _resolve_approval_on_channels(approval_id: str, status: str,
                                  source_channel: str = "web", *,
                                  validate: Callable[[], None] | None = None) -> None:
    """Update approval messages on all channels except the source.

    Called after ``respond_to_approval()`` to mark the approval as
    resolved on every channel that received the original request.
    """
    conn = _get_conn()
    refs = conn.execute(
        "SELECT channel, message_ref FROM approval_channel_refs "
        "WHERE approval_id = ?",
        (approval_id,),
    ).fetchall()
    conn.close()

    for ref in refs:
        ch_name, msg_ref = ref["channel"], ref["message_ref"]
        if ch_name == source_channel:
            continue  # the source channel already handled its own UI
        try:
            from row_bot.channels import registry as _ch_reg
            ch = _ch_reg.get(ch_name)
            if ch and ch.is_running():
                _check_workflow_effect(validate)
                ch.update_approval_message(msg_ref, status, source=source_channel)
        except _WorkflowEffectDenied:
            raise
        except Exception as exc:
            logger.warning(
                "Failed to update approval on %s (ref=%s): %s",
                ch_name, msg_ref, exc,
            )


def code_folder_state(workspace_id: str) -> tuple[str, bool]:
    """A code folder's name and whether a workflow can run in it (N9): saved, not removed from
    Row-Bot's code folders, and still on disk. ("", False) when it isn't saved at all."""
    from row_bot.developer.storage import get_workspace

    workspace = get_workspace(str(workspace_id or ""))
    if workspace is None:
        return "", False
    try:
        on_disk = pathlib.Path(workspace.path).is_dir()
    except OSError:
        on_disk = False
    return workspace.name, bool(on_disk and not workspace.hidden)


def _open_workflow_folder(task: Mapping[str, Any], thread_id: str) -> tuple[str, str, str]:
    """(folder id, its name, why the run can't use it) for a run of *task* in its own code folder.

    The folder is bound to the run's conversation as its one code folder, replacing another a
    kept conversation had, so every step (and a child agent that doesn't pick its own) works in it.
    """
    workspace_id = str(task.get("workspace_id") or "")
    name, available = code_folder_state(workspace_id)
    name = name or str(task.get("workspace_name") or "") or "The workflow's code folder"
    if not available:
        return workspace_id, name, f"{name} isn't a code folder any more. Choose another in this workflow's settings."
    from row_bot import conversation_resources as resources

    snapshot = resources.list_bindings(thread_id)
    current = [binding for binding in snapshot.bindings if binding.kind == "workspace"]
    if [binding.resource_id for binding in current] == [workspace_id]:
        return workspace_id, name, ""
    for binding in current:
        snapshot = resources.unbind(thread_id, binding.binding_id, expected_revision=snapshot.revision)
    resources.bind(thread_id, "workspace", workspace_id, expected_revision=snapshot.revision, role="primary")
    return workspace_id, name, ""


@_schema_retry
def _note_run_folder(run_id: str, workspace_id: str, workspace_name: str) -> None:
    conn = _get_conn()
    try:
        conn.execute("UPDATE task_runs SET workspace_id = ?, workspace_name = ? WHERE id = ?",
                     (workspace_id, workspace_name, run_id))
        conn.commit()
    finally:
        conn.close()


def run_task_background(
    task_id: str,
    thread_id: str,
    enabled_tool_names: list[str],
    *, reviewed_capture: dict | None = None, validate: Callable[[], None] | None = None,
    late_for: str = "", trigger: str = "Run",
) -> None:
    """Start a run: its first message as a chat turn in the workflow's own conversation (F31).

    *late_for* is the scheduled time a run missed while Row-Bot was closed, when
    it runs once on start instead (N9). A reminder (notify-only) notifies at once
    and finishes, with no turn.
    """
    task = json.loads(json.dumps(reviewed_capture["task"])) if reviewed_capture else get_task(task_id)
    if reviewed_capture and validate is not None:
        validate()
    if not task:
        return

    def _validate_effect():
        _check_workflow_effect(validate)
        if reviewed_capture and (read_task_run(str(reviewed_capture["run_id"])) or {}).get("status") == "stopping":
            raise _WorkflowEffectDenied("task_run_stopping")

    effect_kwargs = {"validate": _validate_effect} if validate is not None or reviewed_capture else {}

    logger.info("run_task_background: starting '%s' (id=%s)", task.get("name", "?"), task_id[:8])
    try:
        from row_bot.buddy.events import BuddyEventType, emit_buddy_event
        emit_buddy_event(
            BuddyEventType.WORKFLOW_STARTED,
            source="tasks",
            payload={
                "task_id": task_id,
                "thread_id": thread_id,
                "label": task.get("name", "Workflow running"),
            },
        )
    except _WorkflowEffectDenied:
        raise
    except Exception:
        logger.debug("Buddy workflow start event failed", exc_info=True)

    # ── Notify-only tasks (timer replacement) ────────────────────────
    if task.get("notify_only"):
        run_id = ""
        try:
            if reviewed_capture:
                run_id = str(reviewed_capture["run_id"])
                _reviewed_run_enter(run_id, task_id, thread_id, reviewed_capture, validate)
            else:
                run_id = _record_run_start(task_id, thread_id, 0,
                                          task_name=task["name"], task_icon=task["icon"], late_for=late_for)
            label = task.get("notify_label") or task["name"]
            from row_bot.notifications import notify
            _validate_effect()
            notify(
                title="⏰ Row-Bot Reminder",
                message=label,
                sound="timer",
                source="workflow",
                requested=True,
            )
            try:
                _validate_effect()
                delivery_status, delivery_detail = _deliver_to_channels(
                    task, f"⏰ Reminder: {label}", **effect_kwargs,
                )
            except _WorkflowEffectDenied:
                raise
            except Exception as exc:
                logger.error("Notify-only delivery crashed for task %s: %s",
                             task["name"], exc)
                delivery_status = "delivery_failed"
                delivery_detail = "channel delivery failed: " + str(exc)
            if not (reviewed_capture and task.get("delete_after_run")):
                update_task(task_id, last_run=datetime.now().isoformat())
            final_status = _workflow_final_status_for_delivery(delivery_status)
            _finish_run(run_id, final_status, status_message=delivery_detail)
            _emit_buddy_workflow_event(
                "done",
                task_id=task_id,
                thread_id=thread_id,
                label=task.get("name", "Workflow done"),
            )
            # Fire any tasks triggered by this task's completion
            if final_status.startswith("completed"):
                try:
                    _validate_effect()
                    _fire_completion_triggers(task_id, **effect_kwargs)
                except _WorkflowEffectDenied:
                    raise
                except Exception as exc_ct:
                    logger.error("Completion trigger error for %s: %s",
                                 task["name"], exc_ct)
            if delivery_status == "delivery_failed":
                _validate_effect()
                notify(
                    title="Delivery failed",
                    message=f"{task['name']} — {delivery_detail}",
                    sound="timer",
                    toast_type="warning",
                    source="workflow",
                )
            if task.get("delete_after_run"):
                _validate_effect()
                delete_task(task_id, **({"expected_revision": reviewed_capture["task_revision"], "validate": _validate_effect, "preserve_conversations": True} if reviewed_capture else {"keep_conversations": True}))
            return

        except BaseException as exc:
            if run_id:
                code = exc.code if isinstance(exc, TaskMutationError) else (
                    str(exc) if isinstance(exc, _WorkflowEffectDenied) else "task_run_effect_unconfirmed"
                )
                _finish_run(run_id, "stopped" if isinstance(exc, InterruptedError) else "failed", status_message=code)
            raise

    # ── A run is a chat turn in the workflow's own conversation (F31) ──
    from row_bot import workflow_turns

    total = len(workflow_turns.messages(task))
    run_id = str(reviewed_capture["run_id"]) if reviewed_capture else ""
    earlier = _active_run_for_task(task_id, besides=run_id)
    if earlier is not None and earlier["status"] == "waiting":
        # A run still waiting on its card is replaced: what it asked about is a day old.
        stop_workflow_run(earlier, status="expired", notice="A newer run replaced it before it was approved.")
    elif earlier is not None:
        message = "Skipped: the run before it was still going."
        if run_id:
            _finish_run(run_id, "skipped", notice=message)
        else:
            _record_skipped_run(task, thread_id, message)
        return
    if reviewed_capture:
        try:
            _reviewed_run_enter(run_id, task_id, thread_id, reviewed_capture, validate)
        except BaseException as exc:
            _workflow_entry_failed(run_id, thread_id, exc)
            raise
        _set_run_total(run_id, total)
    else:
        run_id = _record_run_start(task_id, thread_id, total,
                                   task_name=task["name"], task_icon=task["icon"], late_for=late_for)

    if task.get("workspace_id"):
        # The workflow's own code folder (N9): bound to its conversation before the turn starts.
        try:
            folder_id, folder_name, refusal = _open_workflow_folder(task, thread_id)
        except Exception:
            logger.warning("Could not open the code folder of workflow %s", task_id, exc_info=True)
            folder_id, folder_name = str(task["workspace_id"]), str(task.get("workspace_name") or "")
            refusal = f"Row-Bot couldn't open {folder_name or 'the code folder'} for this run. Try again."
        _note_run_folder(run_id, folder_id, folder_name)
        if refusal:
            _finish_run(run_id, "blocked", status_message=refusal, terminal_reason="code_folder_unavailable",
                        notice=refusal, fix="choose_folder")
            _emit_buddy_workflow_event("error", task_id=task_id, thread_id=thread_id,
                                       label="Workflow needs attention", error=refusal)
            return

    with _active_lock:
        _active_runs[thread_id] = {
            "task_id": task_id, "run_id": run_id, "step": 0, "total": total, "name": task["name"],
            "icon": task.get("icon", "⚡"), "started_at": datetime.now().isoformat(), "step_label": "", "log": [],
        }
    if late_for:
        trigger = "Late run"
    workflow_turns.start(task, thread_id, run_id, trigger=trigger)


@_publishes_live("runs")
@_schema_retry
def _set_run_total(run_id: str, total: int) -> None:
    conn = _get_conn()
    try:
        conn.execute("UPDATE task_runs SET steps_total = ? WHERE id = ?", (total, run_id))
        conn.commit()
    finally:
        conn.close()


@_publishes_live("runs", "conversations")
def _record_skipped_run(task: Mapping[str, Any], thread_id: str, message: str) -> None:
    now = datetime.now().isoformat()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO task_runs (id, task_id, thread_id, started_at, finished_at, status, notice, "
            "steps_total, steps_done, task_name, task_icon) VALUES (?, ?, ?, ?, ?, 'skipped', ?, 0, 0, ?, ?)",
            (uuid.uuid4().hex[:12], task["id"], thread_id, now, now, message, task.get("name", ""),
             task.get("icon", "")),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("Skipped a run of '%s': %s", task.get("name", ""), message)


# ── Scheduler (APScheduler) ──────────────────────────────────────────────────
# Each enabled task gets a real APScheduler job with the appropriate trigger
# (CronTrigger, IntervalTrigger, DateTrigger).  Adding/updating/deleting a
# task automatically syncs the scheduler.

_scheduler: "BackgroundScheduler | None" = None
_scheduler_lock = threading.Lock()


def _get_scheduler():
    """Return the singleton BackgroundScheduler, creating it if needed."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    with _scheduler_lock:
        if _scheduler is not None:
            return _scheduler
        from apscheduler.schedulers.background import BackgroundScheduler
        _scheduler = BackgroundScheduler(
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 120},
        )
        _scheduler.start()
        logger.info("APScheduler BackgroundScheduler started")
    return _scheduler


def _parse_schedule(schedule: str | None) -> dict | None:
    """Parse schedule strings into a dict.

    Formats:
        "daily:HH:MM"             → run every day at HH:MM
        "weekly:DAY:HH:MM"        → run every week on DAY at HH:MM
        "interval:HOURS"           → run every N hours (float OK, e.g. 0.5 = 30 min)
        "interval_minutes:MINUTES" → run every N minutes
        "cron:EXPR"                → cron expression (5-field)
    """
    if not schedule:
        return None
    parts = schedule.split(":", 1)
    if len(parts) < 2:
        return None

    kind = parts[0].lower()
    rest = parts[1]

    try:
        if kind == "daily":
            sub = rest.split(":")
            if len(sub) >= 2:
                return {"kind": "daily", "hour": int(sub[0]), "minute": int(sub[1])}
        elif kind == "weekly":
            sub = rest.split(":")
            if len(sub) >= 3:
                raw_day = sub[0].lower()
                _FULL_TO_ABBR = {
                    "monday": "mon", "tuesday": "tue", "wednesday": "wed",
                    "thursday": "thu", "friday": "fri", "saturday": "sat",
                    "sunday": "sun",
                }
                day_abbr = _FULL_TO_ABBR.get(raw_day, raw_day)
                return {
                    "kind": "weekly",
                    "day": day_abbr,
                    "hour": int(sub[1]),
                    "minute": int(sub[2]),
                }
        elif kind == "interval":
            return {"kind": "interval", "hours": float(rest)}
        elif kind == "interval_minutes":
            return {"kind": "interval_minutes", "minutes": float(rest)}
        elif kind == "cron":
            return {"kind": "cron", "expr": rest.strip()}
    except (ValueError, IndexError):
        pass
    return None


_DAY_MAP = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3,
    "fri": 4, "sat": 5, "sun": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# Reverse map: weekday int → 3-letter APScheduler day string
_DAY_TO_AP = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}


def _schedule_trigger(sched: Mapping[str, Any], tz):
    """The wall-clock trigger of a daily, weekly or cron schedule in its zone (None for others)."""
    from row_bot import schedule_zones

    kind = sched["kind"]
    if kind == "daily":
        cron = schedule_zones.cron_trigger(hour=sched["hour"], minute=sched["minute"])
    elif kind == "weekly":
        day_int = _DAY_MAP.get(sched["day"])
        if day_int is None:
            return None
        cron = schedule_zones.cron_trigger(
            day_of_week=_DAY_TO_AP[day_int], hour=sched["hour"], minute=sched["minute"]
        )
    elif kind == "cron":
        try:
            cron = schedule_zones.crontab_trigger(sched["expr"])
        except Exception:
            logger.warning("Invalid cron expression: %s", sched["expr"])
            return None
    else:
        return None
    return schedule_zones.WallClockTrigger(cron, tz)


def _saved_instant(value: object, tz=None) -> datetime | None:
    """A saved time as an instant: a wall-clock time in *tz* (a one-time ``at``), else the
    computer's local time (``last_run``, written by ``datetime.now()``)."""
    try:
        parsed = datetime.fromisoformat(str(value or "").strip())
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed
    if tz is not None:
        from row_bot import schedule_zones

        return schedule_zones.resolve(parsed, tz)
    return parsed.astimezone()


def _build_trigger(task: dict, *, now: datetime | None = None):
    """Build an APScheduler trigger from a task's schedule/at fields, in its zone.

    Returns a trigger object or *None* if the task has no valid schedule.
    """
    from row_bot import schedule_zones

    tz = schedule_zones.zone(task.get("timezone"))
    now = (now or datetime.now()).astimezone()
    # One-shot ``at`` tasks — fire once at a specific wall-clock time in the zone
    at_str = task.get("at")
    if at_str:
        from apscheduler.triggers.date import DateTrigger
        at_dt = _saved_instant(at_str, tz)
        if at_dt is None:
            return None
        # Only schedule if in the future
        if at_dt > now:
            return DateTrigger(run_date=at_dt)
        # Already past — check if it already fired
        last = _saved_instant(task.get("last_run"))
        if last and last >= at_dt:
            return None  # Already fired
        # Past but never fired — fire immediately
        return DateTrigger(run_date=now + timedelta(seconds=2))

    sched = _parse_schedule(task.get("schedule"))
    if not sched:
        return None

    kind = sched["kind"]
    if kind == "interval":
        from apscheduler.triggers.interval import IntervalTrigger
        hours = sched["hours"]
        if hours <= 0:
            return None
        return IntervalTrigger(hours=hours)

    elif kind == "interval_minutes":
        from apscheduler.triggers.interval import IntervalTrigger
        minutes = sched["minutes"]
        if minutes <= 0:
            return None
        return IntervalTrigger(minutes=int(minutes))
    return _schedule_trigger(sched, tz)


def estimate_next_run(task: Mapping[str, Any], now: datetime | None = None) -> str | None:
    """Next fire time implied by a saved schedule, as an ISO timestamp.

    In the schedule's zone with its offset when it keeps one; a schedule saved
    without a zone reads as local time with no offset. This reads saved
    metadata only and never asks the scheduler, so list views stay free of
    runtime probes. Daily, weekly, cron and one-time schedules are exact;
    interval schedules are estimated from the last run. Disabled, unscheduled,
    finished or unreadable schedules return None.
    """
    if not task.get("enabled"):
        return None
    from row_bot import schedule_zones

    named = str(task.get("timezone") or "")
    tz = schedule_zones.zone(named)
    now = (now or datetime.now()).astimezone().replace(microsecond=0)

    def shown(value: datetime) -> str:
        value = value.astimezone(tz) if named else value.astimezone().replace(tzinfo=None)
        return value.isoformat(timespec="seconds")

    at = str(task.get("at") or "").strip()
    if at:
        at_dt = _saved_instant(at, tz)
        if at_dt is None:
            return None
        if at_dt > now:
            return shown(at_dt)
        last = _saved_instant(task.get("last_run"))
        # A past one-time task that never ran is scheduled immediately.
        return None if last and last >= at_dt else shown(now)
    sched = _parse_schedule(task.get("schedule"))
    if not sched:
        return None
    kind = sched["kind"]
    if kind in ("interval", "interval_minutes"):
        delta = (
            timedelta(hours=sched["hours"])
            if kind == "interval"
            else timedelta(minutes=int(sched["minutes"]))
        )
        last = _saved_instant(task.get("last_run"))
        if delta <= timedelta(0) or last is None:
            return None
        upcoming = last + delta
        if upcoming <= now:
            upcoming = last + delta * (int((now - last) / delta) + 1)
        return shown(upcoming)
    try:
        trigger = _schedule_trigger(sched, tz)
        fire = trigger.get_next_fire_time(None, now) if trigger else None
    except Exception:
        return None
    return shown(fire) if fire else None


def _job_id(task_id: str) -> str:
    """Deterministic APScheduler job ID for a task."""
    return f"task_{task_id}"


def _prepare_task_thread(task: dict) -> str:
    """Canonical thread setup for firing a task.

    Returns the thread_id to use.  Handles:
    - persistent_thread_id (reuse) vs fresh UUID
    - thread_meta creation
    - model_override propagation
    """
    from row_bot.threads import (
        _save_thread_meta,
        _set_thread_agent_profile,
        _set_thread_approval_mode,
        _set_thread_model_override,
    )

    from row_bot.threads import _thread_exists, _thread_write_blocked, create_thread

    thread_id = str(task.get("persistent_thread_id") or "")
    if task.get("notify_only"):
        return thread_id or uuid.uuid4().hex[:12]
    # Every workflow has one conversation of its own; each run is a new turn in it (F31).
    if not thread_id or not _thread_exists(thread_id):
        thread_id = thread_id if thread_id and not _thread_write_blocked(thread_id) else uuid.uuid4().hex[:12]
        create_thread(f"\u26a1 {task['name']}", thread_id=thread_id, name_source="manual",
                      seed_default_skills=False)
        update_task(task["id"], persistent_thread_id=thread_id)
    else:
        _save_thread_meta(thread_id, f"\u26a1 {task['name']}")
    if task.get("model_override"):
        _set_thread_model_override(thread_id, task["model_override"])
    try:
        _set_thread_agent_profile(thread_id, _workflow_agent_profile_ref(task))
    except Exception:
        logger.debug("Could not set workflow thread Agent Profile", exc_info=True)
    _set_thread_approval_mode(thread_id, get_task_approval_mode(task))
    return thread_id


def _on_task_fire(task_id: str, late_for: str = "") -> None:
    """Callback invoked by APScheduler when a task's trigger fires.

    A recurring run never starts while the workflow's last run still waits
    for an approval: it is recorded as skipped instead, so runs don't pile up.
    *late_for*: the scheduled time this run missed while Row-Bot was closed (N9).
    """
    from row_bot.tools import registry as tool_registry

    task = get_task(task_id)
    if not task:
        return
    if not task.get("enabled", True):
        return

    logger.info("Scheduler firing task: %s", task["name"])
    update_task(task_id, last_run=datetime.now().isoformat())

    thread_id = _prepare_task_thread(task)
    enabled = [t.name for t in tool_registry.get_enabled_tools()]

    run_task_background(task_id, thread_id, enabled, late_for=late_for,
                        trigger="Scheduled run")

    # Auto-remove one-shot `at` tasks after firing
    if task.get("at") and task.get("delete_after_run"):
        _remove_job(task_id)


def _sync_job(task: dict) -> None:
    """Add or update the APScheduler job for a single task."""
    scheduler = _get_scheduler()
    jid = _job_id(task["id"])

    if not task.get("enabled", True):
        # Disabled — remove job if it exists
        try:
            scheduler.remove_job(jid)
        except Exception:
            pass
        return

    trigger = _build_trigger(task)
    if trigger is None:
        # No valid schedule — remove any leftover job
        try:
            scheduler.remove_job(jid)
        except Exception:
            pass
        return

    # Add or replace the job
    scheduler.add_job(
        _on_task_fire,
        trigger=trigger,
        args=[task["id"]],
        id=jid,
        name=task["name"],
        replace_existing=True,
    )


def _remove_job(task_id: str) -> None:
    """Remove the APScheduler job for a task (if it exists)."""
    if _scheduler is None:
        return
    try:
        _scheduler.remove_job(_job_id(task_id))
    except Exception:
        pass


@_schema_retry
def sync_all_jobs() -> None:
    """(Re-)sync every task to the APScheduler job store."""
    tasks = list_tasks()
    for task in tasks:
        _sync_job(task)
    logger.info("Synced %d task(s) to APScheduler", len(tasks))


@_schema_retry
def count_scheduled_workflows() -> int:
    """How many workflows are on and run on a schedule: those the scheduler holds a job for (B414).

    One that runs only when started, or a one-time run that has happened, isn't scheduled.
    """
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT schedule, at, last_run, timezone FROM tasks WHERE enabled = 1").fetchall()
    finally:
        conn.close()
    return sum(1 for row in rows if _build_trigger(dict(row)) is not None)


def count_waiting_runs() -> int:
    """How many workflow runs wait on the person's answer to a card in their conversation (F31)."""
    conn = _get_conn()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM task_runs WHERE status = 'waiting'").fetchone()[0])
    finally:
        conn.close()


# ── Runs missed while Row-Bot was closed (N9) ─────────────────────────────────
# The scheduler notes the time every minute while it runs. On start, a time a schedule had
# between that note and now fell while Row-Bot was closed: a workflow set to "Run once when
# Row-Bot starts" runs once for the latest such time; the others are skipped, as before.

_SCHEDULER_SEEN_KEY = "seen_at"


@_schema_retry
def scheduler_seen_at() -> datetime | None:
    """When the scheduler last noted it was running, or None (never, before 5.3)."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT value FROM scheduler_state WHERE key = ?", (_SCHEDULER_SEEN_KEY,)).fetchone()
    finally:
        conn.close()
    return _saved_instant(row["value"]) if row else None


@_schema_retry
def note_scheduler_alive(now: datetime | None = None) -> None:
    """Note that the scheduler is running now."""
    stamp = (now or datetime.now()).astimezone().isoformat(timespec="seconds")
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO scheduler_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_SCHEDULER_SEEN_KEY, stamp),
        )
        conn.commit()
    finally:
        conn.close()


def _digest_since(task_id: str) -> datetime | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT value FROM scheduler_state WHERE key = ?", (f"digest:{task_id}",)).fetchone()
    finally:
        conn.close()
    return _saved_instant(row["value"]) if row else None


@_schema_retry
def send_due_digests(now: datetime | None = None) -> int:
    """Send each "daily digest" workflow its runs since its last digest, once a day from DIGEST_HOUR (G8).

    Nothing goes on a day with no finished runs; a workflow's first digest covers the day before it.
    Returns how many digests went out.
    """
    from row_bot import workflow_turns

    moment = (now or datetime.now()).astimezone()
    if moment.hour < DIGEST_HOUR:
        return 0
    sent = 0
    for task in list_tasks():
        if task.get("send_results") != "digest":
            continue
        since = _digest_since(task["id"])
        if since is not None and since.astimezone().date() == moment.date():
            continue
        start = since or moment - timedelta(days=1)
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT finished_at, status, notice, actions_json FROM task_runs WHERE task_id = ? "
                "AND finished_at IS NOT NULL AND status NOT IN ('running', 'waiting', 'stopped') "
                "ORDER BY finished_at", (task["id"],)).fetchall()
            conn.execute("INSERT INTO scheduler_state (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         (f"digest:{task['id']}", moment.isoformat(timespec="seconds")))
            conn.commit()
        finally:
            conn.close()
        lines = []
        for row in rows:
            finished = _saved_instant(row["finished_at"])
            if finished is None or not start < finished <= moment:
                continue
            try:
                done = json.loads(row["actions_json"] or "[]")
            except ValueError:
                done = []
            when = finished.astimezone()
            stamp = f"{when:%H:%M}" if when.date() == moment.date() else f"{when:%a %H:%M}"
            lines.append(f"{stamp} · " + workflow_turns.outcome(str(row["status"]), str(row["notice"] or ""),
                                                                 done if isinstance(done, list) else []))
        if not lines:
            continue
        runs = "1 run" if len(lines) == 1 else f"{len(lines)} runs"
        try:
            _deliver_to_channels(task, f"Daily digest · {runs}\n" + "\n".join(lines))
            sent += 1
        except Exception:
            logger.warning("The daily digest of %s could not be delivered", task["name"], exc_info=True)
    return sent


def missed_run_time(task: Mapping[str, Any], since: datetime, now: datetime) -> datetime | None:
    """The latest time *task*'s repeating schedule had in (since, now] that it hasn't run for."""
    sched = _parse_schedule(task.get("schedule"))
    if not sched or task.get("at"):
        return None
    last = _saved_instant(task.get("last_run"))
    after = max(since, last) if last else since
    kind = sched["kind"]
    if kind in ("interval", "interval_minutes"):
        delta = timedelta(hours=sched["hours"]) if kind == "interval" else timedelta(minutes=int(sched["minutes"]))
        if last is None or delta <= timedelta(0) or now - last < delta:
            return None
        due = last + delta * int((now - last) / delta)
        return due if due > after else None
    from row_bot import schedule_zones

    trigger = _schedule_trigger(sched, schedule_zones.zone(task.get("timezone")))
    return schedule_zones.latest_fire_time(trigger, after, now) if trigger else None


def _fire_late(task_id: str, late_for: str) -> None:
    """Start a missed run shortly after start-up, off the start-up path."""
    from apscheduler.triggers.date import DateTrigger

    _get_scheduler().add_job(
        _on_task_fire, trigger=DateTrigger(run_date=datetime.now().astimezone() + timedelta(seconds=2)),
        args=[task_id], kwargs={"late_for": late_for}, id=f"late_{task_id}", replace_existing=True,
    )


def run_missed_runs(
    since: datetime | None, now: datetime | None = None, *,
    fire: Callable[[str, str], None] | None = None,
) -> list[tuple[str, str]]:
    """Run once, for its latest missed time, each workflow set to "Run once when Row-Bot starts".

    *since*: when the scheduler last noted it was running; None runs nothing (it can't be told
    what was missed). Returns (task id, missed time) for each run started.
    """
    if since is None:
        return []
    now = (now or datetime.now()).astimezone()
    started: list[tuple[str, str]] = []
    for task in list_tasks():
        if not task.get("enabled") or task.get("missed_runs") != "run_once":
            continue
        try:
            missed = missed_run_time(task, since, now)
        except Exception:
            logger.warning("Could not tell what workflow %s missed", task.get("id"), exc_info=True)
            continue
        if missed is None:
            continue
        late_for = missed.isoformat(timespec="seconds")
        logger.info("Workflow '%s' missed %s while Row-Bot was closed: running it once", task["name"], late_for)
        (fire or _fire_late)(task["id"], late_for)
        started.append((task["id"], late_for))
    return started


_PROCESS_STARTED_AT = datetime.now()
_interrupted_runs_settled = False
_INTERRUPTED_RUN_STATUSES = ("starting", "running", "resuming", "stopping")


def settle_interrupted_runs(before: datetime | None = None) -> list[str]:
    """Mark runs an earlier Row-Bot process left unfinished as stopped.

    Their worker died with that process, so they could never report back:
    they read as running forever and Stop could not settle them. A run waiting
    on a card is kept: its conversation still holds the card, which goes on
    when answered. A run that started in this process is never touched. No
    delivery or notification is sent.
    """
    cutoff = (before or _PROCESS_STARTED_AT).isoformat()
    placeholders = ",".join("?" for _ in _INTERRUPTED_RUN_STATUSES)
    conn = _get_conn()
    try:
        rows = conn.execute(
            f"SELECT id FROM task_runs WHERE status IN ({placeholders}) "
            "AND started_at < ? ORDER BY started_at",
            (*_INTERRUPTED_RUN_STATUSES, cutoff),
        ).fetchall()
    finally:
        conn.close()
    settled = [str(row["id"]) for row in rows]
    for run_id in settled:
        _finish_run(
            run_id,
            "stopped",
            status_message="Interrupted: Row-Bot closed before this run finished.",
            terminal_reason="interrupted",
        )
    if settled:
        logger.info("Marked %d interrupted workflow run(s) as stopped", len(settled))
    return settled


@_schema_retry
def start_task_scheduler() -> None:
    """Start the APScheduler and sync all task jobs (idempotent)."""
    global _interrupted_runs_settled
    first_start = not _interrupted_runs_settled
    closed_since = None
    if first_start:
        _interrupted_runs_settled = True
        try:
            settle_interrupted_runs()
        except Exception:
            logger.warning("Could not settle interrupted workflow runs", exc_info=True)
        try:
            from row_bot.goals import settle_interrupted_goals
            settle_interrupted_goals()
        except Exception:
            logger.warning("Could not pause goals left working", exc_info=True)
        try:
            closed_since = scheduler_seen_at()
            note_scheduler_alive()
        except Exception:
            logger.warning("Could not read when the scheduler last ran", exc_info=True)
    _get_scheduler()
    sync_all_jobs()
    if first_start:
        try:
            run_missed_runs(closed_since)
        except Exception:
            logger.warning("Could not run workflows missed while Row-Bot was closed", exc_info=True)
    start_approval_monitor()


# Backward-compat alias
start_workflow_scheduler = start_task_scheduler


# ── Webhook Trigger ──────────────────────────────────────────────────────────

def handle_webhook(task_id: str, secret: str | None = None,
                   payload: dict | None = None) -> dict:
    """Handle an incoming webhook trigger for a task.

    Returns a dict with ``status`` and ``message`` keys.
    """
    task = get_task(task_id)
    if not task:
        return {"status": "error", "message": "Task not found"}

    trigger = task.get("trigger")
    if not trigger or trigger.get("type") != "webhook":
        return {"status": "error", "message": "Task does not have a webhook trigger"}

    # Validate the secret in constant time. A webhook without a secret never
    # runs: an empty stored secret used to accept any caller.
    import hmac

    expected_secret = str(trigger.get("secret") or "")
    if not expected_secret or not hmac.compare_digest(
        expected_secret.encode(), str(secret or "").encode()
    ):
        return {"status": "error", "message": "Invalid secret"}

    if not task.get("enabled", True):
        return {"status": "error", "message": "Task is disabled"}

    from row_bot.tools import registry as tool_registry

    thread_id = _prepare_task_thread(task)
    enabled = [t.name for t in tool_registry.get_enabled_tools()]

    run_task_background(task_id, thread_id, enabled)
    logger.info("Webhook triggered task '%s'", task["name"])
    return {"status": "ok", "message": f"Task '{task['name']}' triggered"}


WEBHOOK_SECRET_HEADER = "X-Row-Bot-Webhook-Secret"


def webhook_request_secret(headers: Any, query: Any) -> str | None:
    """The secret a webhook request carries: a header, or the older query form.

    The header keeps the secret out of addresses, which proxies and logs keep
    (B132). Addresses copied before the header existed still carry
    ``?secret=`` and keep working. A request carrying two different secrets
    never runs anything.
    """
    lowered = {str(key).lower(): str(value) for key, value in dict(headers or {}).items()}
    header = lowered.get(WEBHOOK_SECRET_HEADER.lower(), "").strip()
    if not header:
        authorization = lowered.get("authorization", "").strip()
        if authorization[:7].lower() == "bearer ":
            header = authorization[7:].strip()
    query_secret = str(dict(query or {}).get("secret") or "")
    if header and query_secret and header != query_secret:
        return None
    return header or query_secret or None


def generate_webhook_secret() -> str:
    """Generate a random webhook secret."""
    import secrets
    return secrets.token_urlsafe(24)


# ── Global Safety Mode ──────────────────────────────────────────────────────

_VALID_APPROVAL_MODES = ("block", "approve", "auto", "full")
_RETRY_CONFIG_PATH = _TASK_CONFIG_PATH


def get_global_approval_mode() -> str:
    """Return the global default approval mode for new tasks."""
    try:
        with open(_RETRY_CONFIG_PATH) as f:
            mode = json.load(f).get("safety_mode", "block")
            return legacy_safety_mode_to_approval_mode(mode)
    except (FileNotFoundError, json.JSONDecodeError):
        return "block"


def set_global_approval_mode(mode: str) -> None:
    """Save the global approval mode setting."""
    normalized = normalize_approval_mode(mode, "")
    if normalized not in _VALID_APPROVAL_MODES:
        raise ValueError(f"Invalid approval mode: {mode!r}. Must be one of {_VALID_APPROVAL_MODES}")
    data = {}
    try:
        with open(_RETRY_CONFIG_PATH) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    data["safety_mode"] = normalized
    with open(_RETRY_CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)


def get_task_approval_mode(task: dict) -> str:
    """Return the effective approval mode for a task.

    Uses the task's own setting if present, otherwise falls back to
    the global default.
    """
    mode = task.get("safety_mode")
    if mode:
        return legacy_safety_mode_to_approval_mode(mode)
    return get_global_approval_mode()


# ── Pipeline State Persistence (for approval pause/resume) ──────────────────

# ── Approval Request Management ─────────────────────────────────────────────

# ── "Always ask me" (N9) ─────────────────────────────────────────────────────
# An approval the person's own rule asks for: only they can give it, it is bound to exactly what
# its card shows, and it expires at the step's timeout (24 hours unless the step sets one).

PERSON_APPROVAL_TIMEOUT_MINUTES = 24 * 60
NOT_APPROVED_IN_TIME = "Not approved in time"
NOT_APPROVED = "You didn't approve it, so it didn't happen."


def _person_only_interrupt(interrupts: Sequence[Any]) -> dict | None:
    """The person-only card among a step's paused questions, if any."""
    return next((item for item in interrupts or ()
                 if isinstance(item, Mapping) and item.get("person_only") and item.get("binding")), None)


def _person_payload_of(approval: Mapping[str, Any] | None) -> dict | None:
    """The person-only terms an approval request was made with, or None."""
    if not approval:
        return None
    try:
        payload = json.loads(approval.get("approval_payload_json") or "{}")
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) and payload.get("person_only") and payload.get("binding") else None


@_publishes_live("approvals", "conversations")
@_schema_retry
def create_approval_request(
    run_id: str,
    task_id: str,
    step_id: str,
    message: str,
    channel: str | None = None,
    timeout_minutes: int = 0,
    agent_run_id: str = "",
    resume_kind: str = "",
    source_label: str = "",
    source_thread_id: str = "",
    parent_thread_id: str = "",
    approval_payload_json: Mapping[str, Any] | str | None = None,
) -> tuple[str, str]:
    """Create an approval request and return ``(resume_token, request_id)``.

    It waits until someone answers it; only a positive *timeout_minutes* (a
    workflow step that sets one) makes it expire, as a denial (B255).
    """
    req_id = uuid.uuid4().hex[:12]
    resume_token = uuid.uuid4().hex
    timeout_at = None
    if timeout_minutes > 0:
        timeout_at = (datetime.now() + timedelta(minutes=timeout_minutes)).isoformat()
    conn = _get_conn()
    if isinstance(approval_payload_json, str):
        payload_text = approval_payload_json.strip() or "{}"
    elif approval_payload_json:
        payload_text = json.dumps(approval_payload_json, ensure_ascii=False, sort_keys=True, default=str)
    else:
        payload_text = "{}"
    conn.execute(
        "INSERT INTO approval_requests "
        "(id, run_id, task_id, step_id, resume_token, message, channel, "
        "status, requested_at, timeout_at, agent_run_id, resume_kind, "
        "source_label, source_thread_id, parent_thread_id, approval_payload_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?)",
        (req_id, run_id, task_id, step_id, resume_token, message,
         channel, datetime.now().isoformat(), timeout_at,
         agent_run_id, resume_kind, source_label, source_thread_id,
         parent_thread_id, payload_text),
    )
    conn.commit()
    conn.close()
    from row_bot.questions import stored_question

    _emit_buddy_approval_event(
        "needed",
        run_id=run_id,
        task_id=task_id,
        step_id=step_id,
        approval_id=req_id,
        resume_token=resume_token,
        label="Row-Bot has a question" if stored_question(payload_text) is not None else "Approval pending",
        message=message,
        question=stored_question(payload_text) is not None,
    )
    return resume_token, req_id


def _emit_buddy_approval_event(
    status: str,
    *,
    run_id: str = "",
    task_id: str = "",
    step_id: str = "",
    approval_id: str = "",
    resume_token: str = "",
    label: str = "",
    message: str = "",
    question: bool = False,
) -> None:
    try:
        from row_bot.buddy.events import BuddyEventType, emit_buddy_event
        event_type = {
            "needed": BuddyEventType.APPROVAL_NEEDED,
            "approved": BuddyEventType.APPROVAL_APPROVED,
            "denied": BuddyEventType.APPROVAL_DENIED,
            "timed_out": BuddyEventType.APPROVAL_TIMED_OUT,
        }.get(status)
        if event_type is None:
            return
        emit_buddy_event(
            event_type,
            source="tasks",
            payload={
                "run_id": run_id,
                "task_id": task_id,
                "step_id": step_id,
                "approval_id": approval_id,
                "resume_token": resume_token,
                "label": label or message or status.replace("_", " ").title(),
                "message": message,
                # Row-Bot's question (N4): Buddy says so, in its own fixed words.
                **({"question": True} if question else {}),
            },
        )
    except Exception:
        logger.debug("Buddy approval event failed", exc_info=True)


@_schema_retry
def get_pending_approvals(
    *,
    parent_thread_id: str = "",
    agent_run_id: str = "",
    approval_id: str = "",
) -> list[dict]:
    """Return all pending approval requests."""
    conn = _get_conn()
    clauses = ["a.status = 'pending'"]
    params: list[str] = []
    if parent_thread_id:
        clauses.append("a.parent_thread_id = ?")
        params.append(str(parent_thread_id))
    if agent_run_id:
        clauses.append("a.agent_run_id = ?")
        params.append(str(agent_run_id))
    if approval_id:
        clauses.append("a.id = ?")
        params.append(str(approval_id))
    where_sql = " AND ".join(clauses)
    rows = conn.execute(
        "SELECT a.*, t.name AS task_name, t.icon AS task_icon "
        "FROM approval_requests a "
        "LEFT JOIN tasks t ON a.task_id = t.id "
        f"WHERE {where_sql} "
        "ORDER BY a.requested_at DESC",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@_publishes_live("approvals", "conversations")
def cancel_agent_run_approvals(agent_run_id: str) -> int:
    """Withdraw a stopped Agent's pending approvals; its question no longer applies.

    Stop used to leave them pending, so "Needs approval" stayed on the Buddy and
    in Home until they timed out (B165). Channels that received one show it
    resolved, as a timeout does.
    """
    if not agent_run_id:
        return 0
    conn = _get_conn()
    try:
        ids = [str(row["id"]) for row in conn.execute(
            "SELECT id FROM approval_requests WHERE agent_run_id = ? AND status = 'pending'",
            (str(agent_run_id),),
        )]
        if ids:
            conn.execute(
                "UPDATE approval_requests SET status = 'cancelled', responded_at = ? "
                "WHERE agent_run_id = ? AND status = 'pending'",
                (datetime.now().isoformat(), str(agent_run_id)),
            )
            conn.commit()
    finally:
        conn.close()
    for approval_id in ids:
        try:
            _resolve_approval_on_channels(approval_id, "cancelled", source_channel="system")
        except Exception:
            logger.warning("Could not mark approval %s withdrawn on channels", approval_id, exc_info=True)
    return len(ids)


@_publishes_live("approvals", "conversations")
@_schema_retry
def respond_to_approval(resume_token: str, approved: bool,
                         note: str = "",
                         source: str = "web",
                         answer: dict | None = None) -> bool:
    """Approve or deny a pending request. Returns True if found and processed.

    Row-Bot's question (N4) takes ``answer`` instead (``questions.answer`` or ``questions.skipped``); a denial
    skips it. An approval never answers a question and an answer never approves an action: either is refused.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM approval_requests WHERE resume_token = ? AND status = 'pending'",
        (resume_token,),
    ).fetchone()
    if not row:
        conn.close()
        return False

    r = dict(row)
    from row_bot import questions

    asked = questions.stored_question(r.get("approval_payload_json"))
    if asked is None and answer is not None:
        conn.close()
        return False
    if asked is not None:
        if answer is None and approved:
            conn.close()
            return False
        answer = answer if answer is not None else questions.skipped()
        if questions.outcome(answer) == "answered":
            try:
                answer = questions.answer(asked, answer.get("answers"))
            except questions.QuestionError:
                conn.close()
                return False
        approved = questions.outcome(answer) == "answered"
    if str(r.get("resume_kind") or "") == "conversation":
        conn.close()
        from row_bot.application.client_platform import client_platform_service, ClientPlatformError
        decision = {"decision": "approve" if approved else "reject"}
        if asked is not None and approved:
            decision["answers"] = answer["answers"]
        try:
            client_platform_service._resolve_approval(str(r["id"]), decision)
            return True
        except ClientPlatformError:
            return False
    if str(r.get("resume_kind") or "") in {"", "workflow"}:
        conn.close()
        return False  # A step pipeline's approval: nothing it could resume runs any more (F31).
    # Check if the approval has expired
    timeout_at = r.get("timeout_at")
    if timeout_at and timeout_at < datetime.now().isoformat():
        conn.execute(
            "UPDATE approval_requests SET status = 'timed_out', responded_at = ? "
            "WHERE id = ?",
            (datetime.now().isoformat(), r["id"]),
        )
        conn.commit()
        conn.close()
        logger.info("Approval %s was already expired when user responded", r["id"])
        if asked is not None:
            # Too late for the answer: the question goes on as nobody answered in time (N4).
            _apply_approval_timeout(r)
            return False
        _emit_buddy_approval_event(
            "timed_out",
            run_id=str(r.get("run_id") or ""),
            task_id=str(r.get("task_id") or ""),
            step_id=str(r.get("step_id") or ""),
            approval_id=str(r.get("id") or ""),
            resume_token=str(r.get("resume_token") or ""),
            label="Approval timed out",
            message=str(r.get("message") or ""),
        )
        return False

    new_status = "approved" if approved else "denied"
    changed = conn.execute(
        "UPDATE approval_requests SET status = ?, responded_at = ?, response_note = ? "
        "WHERE resume_token = ? AND status = 'pending'",
        (new_status, datetime.now().isoformat(), note, resume_token),
    ).rowcount
    if not changed:
        conn.rollback()
        conn.close()
        return False
    conn.commit()

    approval_id = dict(row)["id"]
    conn.close()

    _emit_buddy_approval_event(
        new_status,
        run_id=str(r.get("run_id") or ""),
        task_id=str(r.get("task_id") or ""),
        step_id=str(r.get("step_id") or ""),
        approval_id=approval_id,
        resume_token=resume_token,
        label=("Answered" if approved else "Skipped") if asked is not None else ("Approved" if approved else "Denied"),
        message=str(r.get("message") or ""),
    )

    # Update all channel messages (cross-channel resolution)
    _resolve_approval_on_channels(approval_id, questions.outcome(answer) if asked is not None else new_status,
                                  source_channel=source)

    if asked is not None:
        # The question's own interrupt goes on with the answer; anything asked beside it asks again (N4).
        _answer_question(r, answer)
        return True

    # Resume the pipeline — for graph-interrupted steps, denial resumes
    # the graph with approved=False so the tool returns "cancelled" and
    # the step (and subsequent steps) can still complete.
    if str(r.get("resume_kind") or "") == "agent_run":
        from row_bot.agent_runner import resume_agent_run

        resume_agent_run(
            str(r.get("agent_run_id") or r.get("run_id") or ""),
            resume_token=resume_token,
            approved=approved,
        )
    elif str(r.get("resume_kind") or "") == "parent_orchestration":
        from row_bot.agent_orchestrator import resume_parent_orchestration

        orchestration_id = str(r.get("step_id") or "").removeprefix(
            "orchestration:"
        )
        resume_parent_orchestration(
            orchestration_id,
            resume_token=resume_token,
            approved=approved,
        )
    elif str(r.get("resume_kind") or "") == "mcp_app":
        from row_bot.integrations import views

        views.decided(approval_id, approved)  # A tool call from an app's view waits for this.
    return True


@_publishes_live("approvals", "conversations")
def claim_conversation_approval(approval_id: str, approved: bool, *, expected_payload: str,
                                expected_timeout: str | None) -> dict | None:
    """CAS the existing approval owner without dispatching a second worker."""
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM approval_requests WHERE id=? AND resume_kind='conversation' AND status='pending'",
                           (approval_id,)).fetchone()
        if not row or row["approval_payload_json"] != expected_payload or row["timeout_at"] != expected_timeout:
            conn.rollback()
            return None
        if row["timeout_at"] and row["timeout_at"] < datetime.now().isoformat():
            conn.execute("UPDATE approval_requests SET status='timed_out',responded_at=? WHERE id=? AND status='pending'",
                         (datetime.now().isoformat(), approval_id))
            conn.commit()
            return None
        changed = conn.execute("UPDATE approval_requests SET status=?,responded_at=? WHERE id=? AND status='pending'",
                               ("approved" if approved else "denied", datetime.now().isoformat(), approval_id)).rowcount
        conn.commit()
        return dict(row) if changed else None
    finally:
        conn.close()


def _check_approval_timeouts() -> None:
    """Check for expired approval requests and apply timeout action.

    Each expiry is committed before its effects run: resuming an Agent or a
    pipeline writes this same database, and an open write here made that wait
    until "database is locked", so the approval never timed out and the
    monitor failed every minute (B166).
    """
    now = datetime.now().isoformat()
    conn = _get_conn()
    try:
        expired = [dict(row) for row in conn.execute(
            "SELECT * FROM approval_requests "
            "WHERE status = 'pending' AND timeout_at IS NOT NULL AND timeout_at < ?",
            (now,),
        ).fetchall()]
        claimed = [
            r for r in expired
            if conn.execute(
                "UPDATE approval_requests SET status = 'timed_out', responded_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (now, r["id"]),
            ).rowcount == 1
        ]
        conn.commit()
    finally:
        conn.close()
    if claimed:
        _publish_live("approvals", "conversations")
    for r in claimed:
        try:
            _apply_approval_timeout(r)
        except Exception:
            logger.exception("Timing out approval request %s failed", r["id"])


def _apply_approval_timeout(r: dict) -> None:
    """Resume whatever waited on an approval that timed out, as a denial."""
    # Resume pipeline with denial — for graph-interrupted steps
    # this lets the tool return "cancelled" and the pipeline
    # continues to subsequent steps.  For explicit approval steps
    # this stops the pipeline.
    from row_bot import questions

    asked = questions.stored_question(r.get("approval_payload_json"))
    _resolve_approval_on_channels(r["id"], "timed_out",
                                  source_channel="system")
    _emit_buddy_approval_event(
        "timed_out",
        run_id=str(r.get("run_id") or ""),
        task_id=str(r.get("task_id") or ""),
        step_id=str(r.get("step_id") or ""),
        approval_id=str(r.get("id") or ""),
        resume_token=str(r.get("resume_token") or ""),
        label=questions.NO_ANSWER_IN_TIME if asked is not None else "Approval timed out",
        message=str(r.get("message") or ""),
    )
    if asked is not None:
        # Nobody answered Row-Bot's question (N4): it goes on with its recommendation; with none, the run stops.
        if questions.has_recommendation(asked):
            _answer_question(r, questions.timed_out())
        logger.info("Question %s timed out for task %s", r["id"], r["task_id"])
        return
    if str(r.get("resume_kind") or "") == "agent_run":
        from row_bot.agent_runner import resume_agent_run

        resume_agent_run(
            str(r.get("agent_run_id") or r.get("run_id") or ""),
            resume_token=str(r.get("resume_token") or ""),
            approved=False,
        )
    elif str(r.get("resume_kind") or "") == "parent_orchestration":
        from row_bot.agent_orchestrator import resume_parent_orchestration

        resume_parent_orchestration(
            str(r.get("step_id") or "").removeprefix("orchestration:"),
            resume_token=str(r.get("resume_token") or ""),
            approved=False,
        )
    elif str(r.get("resume_kind") or "") == "mcp_app":
        from row_bot.integrations import views

        views.decided(str(r["id"]), False)
    logger.info("Approval request %s timed out for task %s",
                 r["id"], r["task_id"])


def _answer_question(r: dict, answer: dict) -> None:
    """Go on from Row-Bot's question (N4) with the person's answer, a skip or no answer in time.

    Only the question's own interrupt is resumed with it; never an approval of anything.
    """
    from row_bot import questions

    try:
        context = json.loads(str(r.get("approval_payload_json") or "{}"))
    except (TypeError, ValueError):
        context = {}
    group = (context.get("interrupt") or context.get("interrupts")) if isinstance(context, dict) else None
    try:
        values = questions.resume_values(group, answer)
    except questions.QuestionError:
        logger.warning("Question %s can no longer be answered", r.get("id"))
        return
    kind = str(r.get("resume_kind") or "")
    if kind == "agent_run":
        from row_bot.agent_runner import resume_agent_run

        resume_agent_run(str(r.get("agent_run_id") or r.get("run_id") or ""),
                         resume_token=str(r.get("resume_token") or ""), approved=True, resume_values=values)
    elif kind == "parent_orchestration":
        from row_bot.agent_orchestrator import resume_parent_orchestration

        resume_parent_orchestration(str(r.get("step_id") or "").removeprefix("orchestration:"),
                                    resume_token=str(r.get("resume_token") or ""), approved=True, answer=answer)


# ── Approval Timeout Monitor ────────────────────────────────────────────────

_approval_monitor_started = False
_approval_monitor_lock = threading.Lock()


def start_approval_monitor() -> None:
    """Start a background thread that checks for expired approvals."""
    global _approval_monitor_started
    with _approval_monitor_lock:
        if _approval_monitor_started:
            return
        _approval_monitor_started = True

    def _monitor():
        import time
        while True:
            try:
                _check_approval_timeouts()
            except Exception as exc:
                logger.error("Approval monitor error: %s", exc)
            try:
                note_scheduler_alive()  # So a later start can tell which runs fell while closed (N9).
            except Exception:
                logger.debug("Could not note the scheduler running", exc_info=True)
            try:
                send_due_digests()
            except Exception:
                logger.warning("Could not send the workflows' daily digests", exc_info=True)
            try:
                from row_bot.tools import x_posting
                x_posting.send_due()  # Posts approved for a time slot (G3).
            except Exception:
                logger.warning("Could not send the scheduled X posts", exc_info=True)
            time.sleep(60)  # check every 60 seconds

    t = threading.Thread(target=_monitor, daemon=True, name="approval-monitor")
    t.start()
    logger.info("Approval timeout monitor started")


# ── Default Templates ────────────────────────────────────────────────────────

_DEFAULT_TASKS = [
    {
        "name": "Daily Operating Brief",
        "description": "Manual daily planning brief across calendar, weather, news, and priorities",
        "icon": "🌅",
        "prompts": [
            "Collect today's planning context: my calendar, the weather and current news, where those are set up, "
            "and any known priorities from memory. Mark anything not set up as unavailable and go on. Then write a "
            "concise daily operating brief: schedule, weather, important outside context, top 3 priorities, risks "
            "and a recommended first action.",
        ],
        "schedule": None,
        "enabled": False,
    },
    {
        "name": "Document Decision Brief",
        "description": "Manual document review for a chosen topic, decision, or question",
        "icon": "📄",
        "prompts": [
            "Search my uploaded documents and knowledge base for material on <topic-or-decision> (replace this "
            "before enabling the workflow). Pull out the most relevant facts, quotes or source names, open questions "
            "and contradictions, then turn them into a decision brief: answer or recommendation, supporting "
            "evidence, risks, open questions and next actions.",
        ],
        "schedule": None,
        "enabled": False,
    },
    {
        "name": "Launch Content Pack",
        "description": "Manual content pack for a product, project, or announcement",
        "icon": "✍️",
        "prompts": [
            "Write a positioning brief for <product-or-project> aimed at <audience> (replace both before enabling "
            "the workflow): core value proposition, proof points, objections and tone. Then use it to draft a "
            "launch content pack: a short announcement, an email draft, a social post, landing page hero copy and "
            "5 FAQ bullets. Draft only: don't post or send anything.",
        ],
        "schedule": None,
        "enabled": False,
    },
    {
        "name": "Research Pipeline With Review",
        "description": "Manual research with sources, checked against them, ending in a shareable brief",
        "icon": "🔬",
        "prompts": [
            "Research <topic> for <audience> (replace both before enabling the workflow). Find credible, recent "
            "sources and capture the strongest facts, disagreements and source links. Then write a shareable "
            "brief: executive summary, key evidence, risks and unknowns, and recommended next steps, with sources. "
            "Before you finish, review the brief against the sources and fix or drop anything they don't support.",
        ],
        "schedule": None,
        "enabled": False,
    },
    {
        "name": "Opportunity Monitor",
        "description": "Manual scan for actionable opportunities, ranked",
        "icon": "📣",
        "prompts": [
            "Scan for opportunities related to <market-or-customer-segment> (replace this before enabling the "
            "workflow): leads, partnership openings, product ideas, grants, hiring signals, events or customer pain "
            "points. If none look actionable, say so in one line and stop. Otherwise rank them by relevance, "
            "urgency and effort, with the next action and why each matters.",
        ],
        "schedule": None,
        "enabled": False,
    },
]


def add_default_workflow_templates() -> int:
    """Add any missing starter workflow templates as disabled manual workflows."""
    existing_names = {str(t.get("name") or "") for t in list_tasks()}
    created = 0
    for t in _DEFAULT_TASKS:
        if t["name"] in existing_names:
            continue
        create_task(
            name=t["name"],
            prompts=t.get("prompts", []),
            description=t.get("description", ""),
            icon=t.get("icon", "⚡"),
            schedule=t.get("schedule"),
            notify_only=t.get("notify_only", False),
            notify_label=t.get("notify_label", ""),
            enabled=t.get("enabled", False),
            channels=t.get("channels"),
            agent_profile_id=DEFAULT_WORKFLOW_AGENT_PROFILE_ID,
            apply_default_skills=False,
        )
        created += 1
    return created


@_schema_retry
def seed_default_tasks() -> None:
    """Insert default task templates on first-ever run only.

    Uses a marker file so that if the user deletes all tasks the defaults
    do NOT reappear on the next restart.
    """
    _MARKER = os.path.join(_DATA_DIR, ".tasks_seeded")
    if os.path.exists(_MARKER):
        return  # Already seeded in a prior run — user may have deleted them
    existing = list_tasks()
    if existing:
        # DB has tasks (e.g. migrated from workflows) — mark as seeded
        open(_MARKER, "w").close()
        return
    for t in _DEFAULT_TASKS:
        create_task(
            name=t["name"],
            prompts=t.get("prompts", []),
            description=t.get("description", ""),
            icon=t.get("icon", "⚡"),
            schedule=t.get("schedule"),
            notify_only=t.get("notify_only", False),
            notify_label=t.get("notify_label", ""),
            enabled=t.get("enabled", False),
            channels=t.get("channels"),
            agent_profile_id=DEFAULT_WORKFLOW_AGENT_PROFILE_ID,
            apply_default_skills=False,
        )
    open(_MARKER, "w").close()
    logger.info("Seeded %d default tasks", len(_DEFAULT_TASKS))


# Backward-compat aliases for legacy transition
seed_default_workflows = seed_default_tasks
list_workflows = list_tasks


update_workflow = update_task
delete_workflow = delete_task
duplicate_workflow = duplicate_task
run_workflow_background = run_task_background
