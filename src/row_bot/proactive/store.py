"""``proactive.db``: moments, the Activity log, watcher cursors, the person's rules and per-kind counts.

Everything Proactive Row-Bot knows lives here, in the data folder, and nowhere else. A moment's dedupe key
is unique for good, so one thing gets one moment (one follow-up, never a second). The Activity log keeps
every pass, silent ones included, for 30 days.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from contextlib import closing
from typing import Any, Iterable

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.proactive.model import Candidate, Evidence

logger = logging.getLogger(__name__)

_DB_PATH = get_row_bot_data_dir() / "proactive.db"
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = ""
ACTIVITY_DAYS = 30
ACTIVITY_MAX = 5_000

_MOMENT_COLUMNS = ("id", "kind", "title", "body", "summary", "prepared_ref", "evidence", "urgency", "created_at",
                   "due_at", "expires_at", "state", "surface", "dedupe_key", "topic", "watcher", "actions", "why",
                   "shown_at", "ended_at")
_JSON_COLUMNS = ("evidence", "actions", "why")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS moments (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '', prepared_ref TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '[]',
    urgency TEXT NOT NULL DEFAULT 'normal', created_at REAL NOT NULL, due_at REAL NOT NULL,
    expires_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending', surface TEXT NOT NULL DEFAULT '',
    dedupe_key TEXT NOT NULL UNIQUE, topic TEXT NOT NULL DEFAULT '', watcher TEXT NOT NULL DEFAULT '',
    actions TEXT NOT NULL DEFAULT '[]', why TEXT NOT NULL DEFAULT '[]', shown_at REAL NOT NULL DEFAULT 0,
    ended_at REAL NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS moments_state ON moments(state, due_at);
CREATE INDEX IF NOT EXISTS moments_kind ON moments(kind, shown_at);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, kind TEXT NOT NULL, summary TEXT NOT NULL,
    moment_id TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS activity_at ON activity(at);
CREATE TABLE IF NOT EXISTS cursors (watcher TEXT PRIMARY KEY, cursor TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY, type TEXT NOT NULL, kind TEXT NOT NULL DEFAULT '', topic TEXT NOT NULL DEFAULT '',
    hour INTEGER NOT NULL DEFAULT -1, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS kind_stats (
    kind TEXT PRIMARY KEY, shown INTEGER NOT NULL DEFAULT 0, acted INTEGER NOT NULL DEFAULT 0,
    dismissed INTEGER NOT NULL DEFAULT 0, ignored INTEGER NOT NULL DEFAULT 0,
    ignored_streak INTEGER NOT NULL DEFAULT 0, quieted_at REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY, moment_id TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
    thread_id TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, ref TEXT NOT NULL DEFAULT '');
"""
# Columns added after a table first shipped in a preview build: added in place to an older proactive.db.
_ADDED_COLUMNS = (("drafts", "ref", "TEXT NOT NULL DEFAULT ''"),)


def exists() -> bool:
    """Whether Proactive Row-Bot ever wrote anything here (nothing else creates the file)."""
    return _DB_PATH.exists()


def _conn() -> sqlite3.Connection:
    global _SCHEMA_READY
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    with _SCHEMA_LOCK:
        if _SCHEMA_READY != str(_DB_PATH):
            conn.executescript(_SCHEMA)
            for table, column, kind in _ADDED_COLUMNS:
                if column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
            _SCHEMA_READY = str(_DB_PATH)
    return conn


def _publish() -> None:
    try:
        from row_bot.application.live_updates import publish

        publish("proactive")
    except Exception:
        logger.debug("Live update for Proactive skipped", exc_info=True)


# --- Moments -----------------------------------------------------------------------------------------------

def _moment(values: tuple) -> dict[str, Any]:
    record = dict(zip(_MOMENT_COLUMNS, values))
    for key in _JSON_COLUMNS:
        record[key] = json.loads(record[key] or "[]")
    return record


def evidence_of(record: dict[str, Any]) -> tuple[Evidence, ...]:
    return tuple(Evidence.from_view(item) for item in record.get("evidence") or [])


def add_moment(candidate: Candidate, *, title: str, body: str, now: float,
               expires_after: float = 24 * 3600) -> dict[str, Any] | None:
    """Keep a candidate as a pending moment; None when its dedupe key already had one (ever)."""
    record = {
        "id": uuid.uuid4().hex[:16], "kind": candidate.kind, "title": title, "body": body,
        "summary": candidate.summary, "prepared_ref": "",
        "evidence": json.dumps([item.view() for item in candidate.evidence]),
        "urgency": candidate.urgency if candidate.urgency in ("normal", "urgent") else "normal",
        "created_at": now, "due_at": candidate.due_at or now,
        "expires_at": candidate.expires_at or now + expires_after, "state": "pending", "surface": "",
        "dedupe_key": candidate.key, "topic": candidate.topic, "watcher": candidate.watcher,
        "actions": json.dumps(list(candidate.actions)), "why": "[]", "shown_at": 0.0, "ended_at": 0.0,
    }
    with closing(_conn()) as conn, conn:
        inserted = conn.execute(
            f"INSERT OR IGNORE INTO moments ({', '.join(_MOMENT_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(_MOMENT_COLUMNS))})",
            tuple(record[key] for key in _MOMENT_COLUMNS)).rowcount
    if not inserted:
        return None
    _publish()
    return get_moment(record["id"])


def seen_key(key: str) -> bool:
    """Whether a moment was ever made for this dedupe key."""
    with closing(_conn()) as conn:
        return conn.execute("SELECT 1 FROM moments WHERE dedupe_key = ?", (key,)).fetchone() is not None


def get_moment(moment_id: str) -> dict[str, Any] | None:
    with closing(_conn()) as conn:
        row = conn.execute(f"SELECT {', '.join(_MOMENT_COLUMNS)} FROM moments WHERE id = ?",
                           (moment_id,)).fetchone()
    return _moment(row) if row else None


def list_moments(states: Iterable[str] | None = None, *, since: float = 0.0, limit: int = 200) -> list[dict[str, Any]]:
    """Moments newest first; only those in *states*, created after *since*."""
    where, args = ["created_at >= ?"], [since]
    states = tuple(states or ())
    if states:
        where.append(f"state IN ({', '.join('?' * len(states))})")
        args.extend(states)
    with closing(_conn()) as conn:
        rows = conn.execute(f"SELECT {', '.join(_MOMENT_COLUMNS)} FROM moments WHERE {' AND '.join(where)} "
                            "ORDER BY created_at DESC, rowid DESC LIMIT ?", (*args, limit)).fetchall()
    return [_moment(row) for row in rows]


def move(moment_id: str, state: str, *, from_states: Iterable[str], now: float, **values: Any) -> bool:
    """Move a moment to *state* only if it is in one of *from_states* (one surface, one outcome)."""
    sources = tuple(from_states)
    for key in _JSON_COLUMNS:
        if key in values:
            values[key] = json.dumps(values[key])
    if state not in ("pending", "shown"):
        values.setdefault("ended_at", now)
    sets = ", ".join(["state = ?", *(f"{key} = ?" for key in values)])
    with closing(_conn()) as conn, conn:
        changed = conn.execute(
            f"UPDATE moments SET {sets} WHERE id = ? AND state IN ({', '.join('?' * len(sources))})",
            (state, *values.values(), moment_id, *sources)).rowcount
    if changed:
        _publish()
    return bool(changed)


def update_moment(moment_id: str, **values: Any) -> None:
    for key in _JSON_COLUMNS:
        if key in values:
            values[key] = json.dumps(values[key])
    with closing(_conn()) as conn, conn:
        conn.execute(f"UPDATE moments SET {', '.join(f'{key} = ?' for key in values)} WHERE id = ?",
                     (*values.values(), moment_id))
    _publish()


def shown_since(since: float, *, kind: str = "") -> int:
    """How many moments reached a surface after *since* (the daily cap counts these; the brief counts once,
    not each moment it carried)."""
    query, args = "SELECT COUNT(*) FROM moments WHERE shown_at >= ? AND surface NOT IN ('', 'brief')", [since]
    if kind:
        query, args = query + " AND kind = ?", [*args, kind]
    with closing(_conn()) as conn:
        return int(conn.execute(query, args).fetchone()[0])


def created_since(since: float, *, kind: str) -> int:
    with closing(_conn()) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM moments WHERE created_at >= ? AND kind = ?",
                                (since, kind)).fetchone()[0])


def last_shown(kind: str) -> float:
    with closing(_conn()) as conn:
        row = conn.execute("SELECT MAX(shown_at) FROM moments WHERE kind = ? AND surface NOT IN ('', 'brief')",
                           (kind,)).fetchone()
    return float(row[0] or 0.0)


# --- The Activity log --------------------------------------------------------------------------------------

def log(kind: str, summary: str, *, now: float, moment_id: str = "", detail: dict | None = None) -> None:
    """One line in the Activity log (a pass, silent ones too, or a moment's step)."""
    with closing(_conn()) as conn, conn:
        conn.execute("INSERT INTO activity (at, kind, summary, moment_id, detail) VALUES (?, ?, ?, ?, ?)",
                     (now, kind, summary[:500], moment_id, json.dumps(detail or {})))
        conn.execute("DELETE FROM activity WHERE at < ? OR id <= (SELECT MAX(id) FROM activity) - ?",
                     (now - ACTIVITY_DAYS * 86400, ACTIVITY_MAX))
    _publish()


def activity(*, before: int = 0, limit: int = 100, moment_id: str = "") -> list[dict[str, Any]]:
    where, args = ["1 = 1"], []
    if before:
        where.append("id < ?")
        args.append(before)
    if moment_id:
        where.append("moment_id = ?")
        args.append(moment_id)
    with closing(_conn()) as conn:
        rows = conn.execute(f"SELECT id, at, kind, summary, moment_id, detail FROM activity WHERE "
                            f"{' AND '.join(where)} ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    return [{"id": row[0], "at": row[1], "kind": row[2], "summary": row[3], "moment_id": row[4],
             "detail": json.loads(row[5] or "{}")} for row in rows]


# --- Watcher cursors and small state -----------------------------------------------------------------------

def cursor(watcher: str) -> dict[str, Any]:
    with closing(_conn()) as conn:
        row = conn.execute("SELECT cursor FROM cursors WHERE watcher = ?", (watcher,)).fetchone()
    return json.loads(row[0]) if row else {}


def save_cursor(watcher: str, value: dict[str, Any], *, now: float) -> None:
    with closing(_conn()) as conn, conn:
        conn.execute("INSERT INTO cursors (watcher, cursor, updated_at) VALUES (?, ?, ?) ON CONFLICT(watcher) "
                     "DO UPDATE SET cursor = excluded.cursor, updated_at = excluded.updated_at",
                     (watcher, json.dumps(value), now))


def get_state(key: str, default: Any = None) -> Any:
    with closing(_conn()) as conn:
        row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_state(key: str, value: Any) -> None:
    with closing(_conn()) as conn, conn:
        conn.execute("INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = "
                     "excluded.value", (key, json.dumps(value)))


# --- The person's rules ------------------------------------------------------------------------------------

_RULE_COLUMNS = ("id", "type", "kind", "topic", "hour", "created_at")


def rules() -> list[dict[str, Any]]:
    with closing(_conn()) as conn:
        rows = conn.execute(f"SELECT {', '.join(_RULE_COLUMNS)} FROM rules ORDER BY created_at").fetchall()
    return [dict(zip(_RULE_COLUMNS, row)) for row in rows]


def get_rule(rule_id: str) -> dict[str, Any] | None:
    return next((rule for rule in rules() if rule["id"] == rule_id), None)


def save_rule(rule: dict[str, Any], *, now: float) -> dict[str, Any]:
    record = {"id": rule.get("id") or uuid.uuid4().hex[:12], "type": rule["type"], "kind": rule.get("kind") or "",
              "topic": rule.get("topic") or "", "hour": int(rule.get("hour", -1)),
              "created_at": rule.get("created_at") or now}
    with closing(_conn()) as conn, conn:
        conn.execute(f"INSERT OR REPLACE INTO rules ({', '.join(_RULE_COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?)",
                     tuple(record[key] for key in _RULE_COLUMNS))
    _publish()
    return record


def delete_rule(rule_id: str) -> bool:
    with closing(_conn()) as conn, conn:
        deleted = conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,)).rowcount
    _publish()
    return bool(deleted)


# --- Prepared work: briefs and drafted replies, saved here and never sent ---------------------------------

#
# A draft made overnight (Dream Cycle) waits for its moment: no ``moment_id`` yet, and the dedupe key of the
# moment it is for in ``ref``. When a watcher later makes that moment, the draft is attached to it.

_DRAFT_COLUMNS = ("id", "moment_id", "kind", "title", "body", "thread_id", "created_at", "ref")
# A settled moment's draft, and an overnight draft no moment came for, is kept this long; then it goes, and
# its hidden preparation thread with it.
DRAFTS_KEPT_DAYS = 7


def add_draft(moment_id: str, kind: str, title: str, body: str, *, thread_id: str, now: float,
              ref: str = "") -> dict[str, Any]:
    record = {"id": uuid.uuid4().hex[:16], "moment_id": moment_id, "kind": kind, "title": title, "body": body,
              "thread_id": thread_id, "created_at": now, "ref": ref}
    with closing(_conn()) as conn, conn:
        conn.execute(f"INSERT INTO drafts ({', '.join(_DRAFT_COLUMNS)}) "
                     f"VALUES ({', '.join('?' * len(_DRAFT_COLUMNS))})", tuple(record[key] for key in _DRAFT_COLUMNS))
    _publish()
    return record


def get_draft(draft_id: str) -> dict[str, Any] | None:
    with closing(_conn()) as conn:
        row = conn.execute(f"SELECT {', '.join(_DRAFT_COLUMNS)} FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    return dict(zip(_DRAFT_COLUMNS, row)) if row else None


def drafts(limit: int = 100, *, since: float = 0.0, waiting: bool = False) -> list[dict[str, Any]]:
    """Drafts newest first: those attached to a moment, and with ``waiting`` the overnight ones still
    waiting for theirs too."""
    where = "created_at >= ?" + ("" if waiting else " AND moment_id <> ''")
    with closing(_conn()) as conn:
        rows = conn.execute(f"SELECT {', '.join(_DRAFT_COLUMNS)} FROM drafts WHERE {where} "
                            "ORDER BY created_at DESC, rowid DESC LIMIT ?", (since, limit)).fetchall()
    return [dict(zip(_DRAFT_COLUMNS, row)) for row in rows]


def waiting_draft(ref: str) -> dict[str, Any] | None:
    """The overnight draft waiting for the moment with this dedupe key, if one was made."""
    with closing(_conn()) as conn:
        row = conn.execute(f"SELECT {', '.join(_DRAFT_COLUMNS)} FROM drafts WHERE ref = ? AND moment_id = '' "
                           "ORDER BY created_at DESC LIMIT 1", (ref,)).fetchone()
    return dict(zip(_DRAFT_COLUMNS, row)) if row else None


def prepared_ahead(ref: str) -> bool:
    """Whether something was prepared ahead for this dedupe key (still waiting, or attached since)."""
    with closing(_conn()) as conn:
        return conn.execute("SELECT 1 FROM drafts WHERE ref = ? LIMIT 1", (ref,)).fetchone() is not None


def attach_draft(draft_id: str, moment_id: str) -> bool:
    with closing(_conn()) as conn, conn:
        changed = conn.execute("UPDATE drafts SET moment_id = ? WHERE id = ? AND moment_id = ''",
                               (moment_id, draft_id)).rowcount
    if changed:
        _publish()
    return bool(changed)


def prune_drafts(now: float) -> int:
    """Remove drafts older than ``DRAFTS_KEPT_DAYS`` whose moment settled that long ago, or that no moment
    ever came for. A draft whose moment is still open stays, however old."""
    cutoff = now - DRAFTS_KEPT_DAYS * 86400
    with closing(_conn()) as conn, conn:
        removed = conn.execute(
            "DELETE FROM drafts WHERE created_at < ? AND (moment_id = '' OR moment_id NOT IN "
            "(SELECT id FROM moments WHERE state IN ('pending', 'shown') OR ended_at >= ?))",
            (cutoff, cutoff)).rowcount
    if removed:
        _publish()
    return int(removed)


def draft_threads() -> set[str]:
    """The hidden preparation threads a draft still links to."""
    with closing(_conn()) as conn:
        rows = conn.execute("SELECT DISTINCT thread_id FROM drafts WHERE thread_id <> ''").fetchall()
    return {str(row[0]) for row in rows}


# --- The person's edits to a drafted reply, and its sending ------------------------------------------------
#
# Kept beside the draft Row-Bot wrote (which stays as it was): who it goes to, its subject and text as the
# person last saved them, and when it was sent (only ever after the person approved it on its card).

_EDIT_COLUMNS = ("draft_id", "to_addr", "subject", "body", "updated_at", "sent_at")
_EDITS_SCHEMA = ("CREATE TABLE IF NOT EXISTS draft_edits (draft_id TEXT PRIMARY KEY, to_addr TEXT NOT NULL "
                 "DEFAULT '', subject TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '', updated_at REAL NOT "
                 "NULL DEFAULT 0, sent_at REAL NOT NULL DEFAULT 0)")


def draft_edit(draft_id: str) -> dict[str, Any] | None:
    """The person's saved edits to a draft (and when it was sent), or None when it is as Row-Bot wrote it."""
    with closing(_conn()) as conn:
        conn.execute(_EDITS_SCHEMA)
        row = conn.execute(f"SELECT {', '.join(_EDIT_COLUMNS)} FROM draft_edits WHERE draft_id = ?",
                           (draft_id,)).fetchone()
    return dict(zip(_EDIT_COLUMNS, row)) if row else None


def save_draft_edit(draft_id: str, *, to: str, subject: str, body: str, now: float) -> dict[str, Any]:
    with closing(_conn()) as conn, conn:
        conn.execute(_EDITS_SCHEMA)
        conn.execute("INSERT INTO draft_edits (draft_id, to_addr, subject, body, updated_at) VALUES (?, ?, ?, ?, ?) "
                     "ON CONFLICT(draft_id) DO UPDATE SET to_addr = excluded.to_addr, subject = excluded.subject, "
                     "body = excluded.body, updated_at = excluded.updated_at", (draft_id, to, subject, body, now))
    _publish()
    return draft_edit(draft_id) or {}


def mark_draft_sent(draft_id: str, *, now: float) -> None:
    with closing(_conn()) as conn, conn:
        conn.execute(_EDITS_SCHEMA)
        conn.execute("INSERT INTO draft_edits (draft_id, sent_at) VALUES (?, ?) ON CONFLICT(draft_id) DO UPDATE SET "
                     "sent_at = excluded.sent_at", (draft_id, now))
    _publish()


# --- The last 30 days, per kind ----------------------------------------------------------------------------

def kind_counts(since: float) -> dict[str, dict[str, int]]:
    """Per kind since *since*: how many reached a surface (shown), and how many the person acted on,
    dismissed or left alone (ignored). The person's own numbers, kept here and never sent anywhere."""
    counts: dict[str, dict[str, int]] = {}
    with closing(_conn()) as conn:
        for kind, shown in conn.execute("SELECT kind, COUNT(*) FROM moments WHERE shown_at >= ? AND surface "
                                        "NOT IN ('', 'brief') GROUP BY kind", (since,)):
            counts.setdefault(kind, {})["shown"] = int(shown)
        for kind, state, number in conn.execute(
                "SELECT kind, state, COUNT(*) FROM moments WHERE ended_at >= ? AND state IN ('acted', 'dismissed', "
                "'ignored') GROUP BY kind, state", (since,)):
            counts.setdefault(kind, {})[state] = int(number)
    return {kind: {key: values.get(key, 0) for key in ("shown", "acted", "dismissed", "ignored")}
            for kind, values in counts.items()}


# --- Per-kind counts ---------------------------------------------------------------------------------------

_STAT_COLUMNS = ("kind", "shown", "acted", "dismissed", "ignored", "ignored_streak", "quieted_at")


def kind_stats(kind: str) -> dict[str, Any]:
    with closing(_conn()) as conn:
        row = conn.execute(f"SELECT {', '.join(_STAT_COLUMNS)} FROM kind_stats WHERE kind = ?", (kind,)).fetchone()
    return dict(zip(_STAT_COLUMNS, row)) if row else dict(zip(_STAT_COLUMNS, (kind, 0, 0, 0, 0, 0, 0.0)))


def count(kind: str, outcome: str, *, now: float) -> dict[str, Any]:
    """Count one outcome (shown, acted, dismissed, ignored) for a kind; acting or dismissing ends a streak
    of ignores, since the person looked."""
    stats = kind_stats(kind)
    stats[outcome] = int(stats[outcome]) + 1
    if outcome == "ignored":
        stats["ignored_streak"] = int(stats["ignored_streak"]) + 1
    elif outcome in ("acted", "dismissed"):
        stats["ignored_streak"] = 0
    with closing(_conn()) as conn, conn:
        conn.execute(f"INSERT OR REPLACE INTO kind_stats ({', '.join(_STAT_COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     tuple(stats[key] for key in _STAT_COLUMNS))
    return stats


def set_quieted(kind: str, at: float) -> None:
    stats = kind_stats(kind)
    stats["quieted_at"] = at
    if not at:
        stats["ignored_streak"] = 0
    with closing(_conn()) as conn, conn:
        conn.execute(f"INSERT OR REPLACE INTO kind_stats ({', '.join(_STAT_COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     tuple(stats[key] for key in _STAT_COLUMNS))

