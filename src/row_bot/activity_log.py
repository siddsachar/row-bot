"""The activity log (D1): every tool action Row-Bot took, its outcome, and who let it.

One row per tool call, from chats, workflows, helper agents, goals, channels,
Proactive Row-Bot and Developer Studio: the tool, a short redacted summary,
the approval level, the outcome ("Ran", "You approved", "Reviewer approved",
"Hands-off", "You said no", "Blocked", ...) and who decided. Decisions are
captured where they are made (the approval gate notes them, the tool-call
middleware writes the row), never parsed from a result's text.

The log lives in ``activity.db`` in the data folder, on this computer only.
Rows older than 90 days are pruned during idle maintenance, the table is
capped at 200,000 rows, a conversation's rows go when it is deleted, and the
person can clear it all. No tool reads or writes it.

Writes never block a tool call and never raise: a call costs one append to a
bounded queue (the oldest entry is dropped when it is full) and one writer
thread does the rest. Rows are keyed by the tool call's id, so a call that
paused for an approval ("Waiting for you") becomes its final row when it runs.
Incognito chats are logged, with the summary left blank: the action happened,
its words stay out.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from row_bot.data_paths import get_activity_db_path

logger = logging.getLogger(__name__)

RETENTION_DAYS = 90
MAX_ROWS = 200_000
QUEUE_LIMIT = 4096
IDLE_SECONDS = 0.2
SUMMARY_MAX = 300
REASON_MAX = 180
INCOGNITO_SUMMARY = "In an incognito chat"

OUTCOMES: tuple[str, ...] = (
    "ran", "asked", "approved_by_you", "approved_by_reviewer", "hands_off", "allowed_by_rule",
    "allowed_in_chat", "denied", "changed", "blocked", "failed",
)
DECIDERS: tuple[str, ...] = ("none", "person", "reviewer", "hands_off", "rule", "chat_grant", "block", "policy")
SURFACES: tuple[str, ...] = ("chat", "workflow", "agent", "goal", "channel", "proactive", "developer")
LEVELS: tuple[str, ...] = ("block", "approve", "auto", "full")
RISKS: tuple[str, ...] = ("low", "medium", "high", "critical", "unknown")
# What a filter chip shows: everything that needed an approval, what the person (or a change) refused,
# and what was blocked.
OUTCOME_GROUPS: dict[str, tuple[str, ...]] = {
    "needed": ("asked", "approved_by_you", "approved_by_reviewer", "hands_off", "allowed_by_rule",
               "allowed_in_chat", "denied", "changed"),
    "denied": ("denied", "changed"),
    "blocked": ("blocked",),
}

_SURFACE_OF = {
    "": "chat", "normal_chat": "chat", "interactive": "chat", "remote_client": "chat", "designer": "chat",
    "workflow": "workflow", "agent": "agent", "agent_child": "agent", "goal": "goal",
    "channel": "channel", "approval": "channel", "proactive": "proactive", "developer": "developer",
}
_BY_ORDER = ("person", "reviewer", "chat_grant", "rule", "hands_off")

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS actions ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "at REAL NOT NULL, "
    "call_id TEXT NOT NULL UNIQUE, "
    "conversation_id TEXT NOT NULL DEFAULT '', "
    "surface TEXT NOT NULL DEFAULT 'chat', "
    "source_id TEXT NOT NULL DEFAULT '', "
    "tool TEXT NOT NULL DEFAULT '', "
    "title TEXT NOT NULL DEFAULT '', "
    "app_id TEXT NOT NULL DEFAULT '', "
    "app_name TEXT NOT NULL DEFAULT '', "
    "summary TEXT NOT NULL DEFAULT '', "
    "level TEXT NOT NULL DEFAULT '', "
    "outcome TEXT NOT NULL DEFAULT 'ran', "
    "decided_by TEXT NOT NULL DEFAULT 'none', "
    "reason TEXT NOT NULL DEFAULT '', "
    # Room for standing rules and their dry run (D1 WP3): which rule allowed it, the action's kind and scope.
    "rule_id TEXT NOT NULL DEFAULT '', "
    "kind TEXT NOT NULL DEFAULT '', "
    "scope_folder TEXT NOT NULL DEFAULT '', "
    "scope_app TEXT NOT NULL DEFAULT '', "
    "person_only INTEGER NOT NULL DEFAULT 0, "
    "risk TEXT NOT NULL DEFAULT 'unknown', "
    "status TEXT NOT NULL DEFAULT 'final', "
    "scope_command TEXT NOT NULL DEFAULT '')"
)
# Columns added after the table was first made, for a log written by an earlier version.
_ADDED_COLUMNS = (("scope_command", "TEXT NOT NULL DEFAULT ''"),)
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_actions_conversation ON actions(conversation_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_actions_at ON actions(at)",
    "CREATE INDEX IF NOT EXISTS idx_actions_outcome ON actions(outcome, id)",
)
_COLUMNS = ("at", "call_id", "conversation_id", "surface", "source_id", "tool", "title", "app_id", "app_name",
            "summary", "level", "outcome", "decided_by", "reason", "rule_id", "kind", "scope_folder", "scope_app",
            "person_only", "risk", "status", "scope_command")
# A paused call's row ("Waiting for you") becomes its final row when it runs; a final row stays as it is,
# except one settled as "asked" that was answered after all.
_UPSERT = (
    f"INSERT INTO actions ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))}) "
    "ON CONFLICT(call_id) DO UPDATE SET "
    "outcome = excluded.outcome, decided_by = excluded.decided_by, "
    "reason = CASE WHEN excluded.reason <> '' THEN excluded.reason ELSE actions.reason END, "
    "summary = CASE WHEN excluded.summary <> '' THEN excluded.summary ELSE actions.summary END, "
    "title = CASE WHEN excluded.title <> '' THEN excluded.title ELSE actions.title END, "
    "level = excluded.level, risk = excluded.risk, status = excluded.status, "
    "person_only = MAX(actions.person_only, excluded.person_only), "
    "rule_id = excluded.rule_id, "
    "kind = CASE WHEN excluded.kind <> '' THEN excluded.kind ELSE actions.kind END, "
    "scope_folder = CASE WHEN excluded.kind <> '' THEN excluded.scope_folder ELSE actions.scope_folder END, "
    "scope_app = CASE WHEN excluded.kind <> '' THEN excluded.scope_app ELSE actions.scope_app END, "
    "scope_command = CASE WHEN excluded.kind <> '' THEN excluded.scope_command ELSE actions.scope_command END "
    "WHERE actions.status = 'pending' OR actions.outcome = 'asked'"
)


@dataclass(frozen=True)
class ToolCall:
    """One tool call as the middleware saw it: what ran, where, and the decisions noted while it ran."""

    call_id: str
    tool: str
    args: Mapping[str, Any] = field(default_factory=dict)
    thread_id: str = ""
    surface: str = ""
    agent_run_id: str = ""
    level: str = ""
    decisions: tuple[Mapping[str, Any], ...] = ()
    paused: bool = False
    failed: bool = False
    blocked: bool = False
    at: float = 0.0


@dataclass(frozen=True)
class Outcome:
    outcome: str
    decided_by: str
    reason: str = ""
    level: str = ""
    summary: str = ""
    title: str = ""
    risk: str = "unknown"
    person_only: bool = False
    status: str = "final"
    # The rule that allowed it, and the action's kind and scope as a rule would match them (D1 WP3).
    rule_id: str = ""
    rule_kind: str = ""
    rule_app: str = ""
    rule_folder: str = ""
    rule_command: str = ""


# ── Outcome: decided by structure, never by a result's words ─────────────────


def outcome_of(call: ToolCall) -> Outcome | None:
    """The row a call's decisions make; None for a pause that wasn't an approval (a question card).

    No decision: "ran" (nothing needed approval). A refusal leads: Block, then the person's "no" or a
    change after approval. A result the call still refused (a ``BLOCKED`` line) is "blocked" by policy
    unless the gate noted Block. An allowed call whose tool raised or returned an error "failed".
    """

    decided = [note for note in call.decisions if note.get("stage") == "decided"]
    asking = [note for note in call.decisions if note.get("stage") == "asking"]
    notes = decided or asking
    last = notes[-1] if notes else {}
    described = next((note for note in reversed(notes) if note.get("description") or note.get("label")), {})
    scoped = next((note for note in reversed(notes) if note.get("rule_kind")), {})
    base = dict(
        level=str(last.get("level") or call.level or ""),
        summary=str(described.get("description") or described.get("label") or ""),
        title=str(described.get("label") or ""),
        risk=str(last.get("risk") or "unknown"),
        person_only=any(bool(note.get("person_only")) for note in notes),
        reason=next((str(note.get("reason") or "") for note in reversed(notes) if note.get("reason")), ""),
        rule_id=next((str(note.get("rule_id") or "") for note in reversed(decided) if note.get("rule_id")), ""),
        **{key: str(scoped.get(key) or "") for key in ("rule_kind", "rule_app", "rule_folder", "rule_command")},
    )
    if call.paused:
        if not call.decisions or call.decisions[-1].get("stage") != "asking":
            return None  # Paused at something that isn't an approval: a question, "Carry on?".
        return Outcome("asked", "person", status="pending", **base)
    if any(note.get("outcome") == "block" for note in decided):
        return Outcome("blocked", "block", **base)
    refused = next((note for note in decided if note.get("outcome") in {"deny", "take_over", "changed"}), None)
    if refused is not None:
        return Outcome("changed" if refused.get("outcome") == "changed" else "denied",
                       str(refused.get("by") or "person"), **base)
    if call.blocked:
        return Outcome("blocked", "policy", **base)
    by = next((who for who in _BY_ORDER if any(note.get("by") == who for note in decided)), "none")
    if call.failed:
        return Outcome("failed", by, **base)
    outcome = {"person": "approved_by_you", "reviewer": "approved_by_reviewer", "hands_off": "hands_off",
               "rule": "allowed_by_rule", "chat_grant": "allowed_in_chat"}.get(by, "ran")
    return Outcome(outcome, by, **base)


# ── The writer: one bounded queue, one thread ────────────────────────────────

_queue: deque[tuple[str, str, Any]] = deque(maxlen=QUEUE_LIMIT)
_wake = threading.Event()
_writer_lock = threading.Lock()
_writer: threading.Thread | None = None
_db_lock = threading.RLock()
_schema_ready: set[str] = set()
_full_warned = False


def _path() -> str:
    return str(get_activity_db_path(create_parent=False))


def _enqueue(kind: str, payload: Any) -> None:
    global _full_warned
    if len(_queue) >= QUEUE_LIMIT and not _full_warned:
        _full_warned = True
        logger.warning("The activity log is behind; its oldest waiting entries are dropped")
    _queue.append((kind, _path(), payload))
    _wake.set()
    _start_writer()


def _start_writer() -> None:
    global _writer
    with _writer_lock:
        if _writer is None:
            _writer = threading.Thread(target=_write_loop, name="row-bot-activity-log", daemon=True)
            _writer.start()


def _write_loop() -> None:
    global _writer
    while True:
        if not _wake.wait(IDLE_SECONDS):
            # Idle: the writer ends, and the next note starts another (nothing outlives the work).
            with _writer_lock:
                if not _queue:
                    _writer = None
                    return
        _wake.clear()
        while _queue:
            try:
                kind, path, payload = _queue.popleft()
            except IndexError:
                break
            if kind == "flush":
                payload.set()
                continue
            try:
                _apply(kind, path, payload)
            except Exception:
                logger.debug("Activity log write skipped", exc_info=True)
                _schema_ready.discard(path)


def record(call: ToolCall) -> None:
    """Note one tool call for the log. Never raises and never waits."""

    try:
        if call.paused and call.thread_id:
            _waiting_in().add(str(call.thread_id))
        _enqueue("call", call if call.at else replace(call, at=time.time()))
    except Exception:
        logger.debug("Activity log record skipped", exc_info=True)


# Conversations that may have a row waiting for the person: a turn's end in any other conversation (most of
# them) writes nothing. Read from the log once per process, so a card still waiting from before a restart
# settles too.
_waiting: set[str] | None = None
_waiting_lock = threading.Lock()


def _waiting_in() -> set[str]:
    global _waiting
    with _waiting_lock:
        if _waiting is None:
            _waiting = set()
            path = _existing()
            if path is not None:
                try:
                    with _db_lock, closing(_connect(path)) as connection:
                        _waiting.update(str(row[0]) for row in connection.execute(
                            "SELECT DISTINCT conversation_id FROM actions WHERE status = 'pending'"))
                except Exception:
                    logger.debug("Activity log could not read its waiting rows", exc_info=True)
        return _waiting


def settle_pending(conversation_id: str) -> None:
    """The turn ended without waiting: a card it left unanswered is no longer "Waiting for you"."""

    try:
        waiting = _waiting_in()
        if conversation_id and str(conversation_id) in waiting:
            waiting.discard(str(conversation_id))
            _enqueue("settle", str(conversation_id))
    except Exception:
        logger.debug("Activity log settle skipped", exc_info=True)


def rule_used(rule_id: str) -> None:
    """A standing rule just allowed an action: its count and last use, written by the writer."""

    try:
        _enqueue("rule_used", (str(rule_id), time.time()))
    except Exception:
        logger.debug("Activity log rule use skipped", exc_info=True)


def flush(timeout: float = 5.0) -> bool:
    """Wait until everything noted so far is written (tests, deletion, export)."""

    done = threading.Event()
    _queue.append(("flush", "", done))
    _wake.set()
    _start_writer()
    return done.wait(timeout)


def _connect(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5)
    if path not in _schema_ready:
        try:
            connection.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            logger.debug("Activity log could not switch to WAL", exc_info=True)
        connection.execute(_SCHEMA)
        present = {str(row[1]) for row in connection.execute("PRAGMA table_info(actions)")}
        for name, definition in _ADDED_COLUMNS:
            if name not in present:
                connection.execute(f"ALTER TABLE actions ADD COLUMN {name} {definition}")
        for statement in _INDEXES:
            connection.execute(statement)
        connection.commit()
        _schema_ready.add(path)
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def _apply(kind: str, path: str, payload: Any) -> None:
    if not Path(path).parent.is_dir():
        return  # The data folder is gone (a test's, or a moved profile): nothing to write to.
    written = None
    with _db_lock, closing(_connect(path)) as connection, connection:
        if kind == "call":
            row = _row(payload)
            if row is not None and connection.execute(_UPSERT, row).rowcount:
                written = row
        elif kind == "rule_used":
            rule_id, at = payload
            connection.execute("UPDATE approval_rules SET uses = uses + 1, last_used_at = ? WHERE id = ?",
                               (float(at), str(rule_id)))
        elif kind == "settle":
            # A helper agent's card waits on after its parent's turn ends: only the turn's own rows settle.
            connection.execute("UPDATE actions SET status = 'final' WHERE conversation_id = ? AND status = 'pending' "
                               "AND surface <> 'agent'", (payload,))
    if written is not None:
        _tell_company(dict(zip(_COLUMNS, written)))


def _tell_company(row: Mapping[str, Any]) -> None:
    """A joined Row-Bot tells its company about a gated action's final row (C4): its metadata fields only, by
    name. Never its title, summary, reason or arguments."""
    if row["status"] != "final":
        return
    try:
        from row_bot.company import audit

        audit.tool_called(tool=row["tool"], outcome=row["outcome"], decided_by=row["decided_by"],
                          approval_level=row["level"], surface=row["surface"], risk=row["risk"],
                          app=row["app_id"] or None, rule_id=row["rule_id"] or None)
    except Exception as error:  # noqa: BLE001 - the person's log is written all the same
        # The contract's own words say which field broke its form, never a value; anything else by its type.
        why = error if type(error).__name__ == "InvalidAuditEvent" else type(error).__name__
        logger.warning("A tool action couldn't be recorded for the company (%s)", why)


# ── One row ──────────────────────────────────────────────────────────────────


def _clean(value: Any, maximum: int) -> str:
    from row_bot.approval_review import _clean as redact

    return redact(" ".join(str(value or "").split()), maximum)


def _choice(value: Any, allowed: Iterable[str], default: str) -> str:
    text = str(value or "").strip().casefold()
    return text if text in allowed else default


def _loaded(name: str) -> Any:
    """A module only if it is already loaded: the writer never imports one (importing threads migrates its DB)."""

    return sys.modules.get(name)


def _conversation_of(thread_id: str) -> str:
    """A helper agent's own thread reports to the person's conversation behind it."""

    runs = _loaded("row_bot.agent_runs")
    current = str(thread_id or "")
    for _ in range(4):
        if runs is None or not current:
            return current
        try:
            run = runs.get_agent_run_for_thread(current)
        except Exception:
            return current
        parent = str((run or {}).get("parent_thread_id") or "")
        if not parent or parent == current:
            return current
        current = parent
    return current


def _workflow_of(conversation_id: str) -> str:
    tasks = _loaded("row_bot.tasks")
    if tasks is None or not conversation_id:
        return ""
    try:
        run = tasks.active_run_for_conversation(conversation_id)
    except Exception:
        return ""
    return str((run or {}).get("task_id") or "")


def _incognito(conversation_id: str) -> bool:
    threads = _loaded("row_bot.threads")
    if threads is None or not conversation_id:
        return False
    try:
        return bool(threads.incognito_state(conversation_id).incognito)
    except Exception:
        return False


def tool_words(tool: str) -> str:
    """A tool's own name in words ("Read url"): the title of a call that had no label or app tool. The client
    says such a call in the words its chat steps use instead (W19)."""

    from row_bot.application.conversation_traces import canonical_tool_name

    words = canonical_tool_name(tool).replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else "Tool"


def _title(tool: str, app: Mapping[str, Any] | None, label: str) -> str:
    if app and app.get("tool"):
        return str(app["tool"])
    return label or tool_words(tool)


def _row(call: ToolCall) -> tuple[Any, ...] | None:
    outcome = outcome_of(call)
    if outcome is None:
        return None
    surface = _SURFACE_OF.get(str(call.surface or "").strip().casefold(), "chat")
    conversation = _conversation_of(call.thread_id) if surface == "agent" else str(call.thread_id or "")
    source = str(call.agent_run_id or "") if surface == "agent" else ""
    if surface == "chat":
        workflow = _workflow_of(conversation)
        if workflow:
            surface, source = "workflow", workflow
    from row_bot.application.conversation_traces import app_of_tool

    app = app_of_tool(call.tool, dict(call.args))
    tool = str(call.tool or "")[:180]
    # What an approval card said; a call that asked nobody has none (its arguments are internal: W19).
    summary = outcome.summary
    reason = outcome.reason
    if _incognito(conversation):
        summary, reason = INCOGNITO_SUMMARY, ""
    return (
        float(call.at or time.time()),
        str(call.call_id or "")[:256] or f"row-bot:{uuid.uuid4().hex}",
        conversation[:256],
        surface,
        source[:256],
        tool,
        _clean(_title(tool, app, outcome.title), 160),
        str((app or {}).get("item_id") or "")[:256],
        _clean((app or {}).get("name") or "", 128),
        _clean(summary, SUMMARY_MAX),
        _choice(outcome.level, LEVELS, ""),
        outcome.outcome,
        outcome.decided_by if outcome.decided_by in DECIDERS else "none",
        _clean(reason, REASON_MAX),
        outcome.rule_id[:32],
        outcome.rule_kind[:256],
        outcome.rule_folder[:1024],
        outcome.rule_app[:256],
        int(outcome.person_only),
        _choice(outcome.risk, RISKS, "unknown"),
        outcome.status,
        outcome.rule_command[:500],
    )


# ── Reading, deleting and pruning (the person's, through the app only) ──────


def _existing() -> str | None:
    path = _path()
    return path if Path(path).is_file() else None


def _filters(*, surface: str = "", outcome: str = "", conversation: str = "", app: str = "", q: str = "",
             since: float | None = None) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    values: list[Any] = []
    if surface:
        where.append("surface = ?")
        values.append(surface)
    group = OUTCOME_GROUPS.get(outcome)
    if group:
        where.append(f"outcome IN ({', '.join('?' * len(group))})")
        values.extend(group)
    elif outcome in OUTCOMES:
        where.append("outcome = ?")
        values.append(outcome)
    if conversation:
        where.append("conversation_id = ?")
        values.append(conversation)
    if app:
        where.append("app_id = ?")
        values.append(app)
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("(title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\' OR tool LIKE ? ESCAPE '\\')")
        values.extend([f"%{escaped}%"] * 3)
    if since is not None:
        where.append("at >= ?")
        values.append(float(since))
    return where, values


def query(*, before: int = 0, limit: int = 50, **filters: Any) -> tuple[list[dict[str, Any]], int | None]:
    """Rows newest first, keyset-paged by id: (rows, the id to read before for the next page, or None)."""

    path = _existing()
    if path is None:
        return [], None
    where, values = _filters(**filters)
    if before > 0:
        where.append("id < ?")
        values.append(int(before))
    sql = "SELECT * FROM actions" + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY id DESC LIMIT ?"
    limit = max(1, int(limit))
    with _db_lock, closing(_connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute(sql, (*values, limit + 1))]
    more = len(rows) > limit
    rows = rows[:limit]
    return rows, (int(rows[-1]["id"]) if more and rows else None)


def rule_candidates(kind: str, outcomes: Iterable[str], *, since: float) -> list[dict[str, Any]]:
    """Rows since *since* of one action kind (a rule's ``kind:tool``) with one of *outcomes*, newest first,
    never an action only the person may approve, nor a card still waiting (the card a rule is being made
    from among them): what a draft rule's dry run weighs."""

    path = _existing()
    if path is None or not kind:
        return []
    wanted = tuple(outcomes)
    with _db_lock, closing(_connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(
            f"SELECT id, at, conversation_id, outcome, title, summary, app_name, scope_app, scope_folder, "
            f"scope_command FROM actions WHERE kind = ? AND at >= ? AND person_only = 0 AND status = 'final' "
            f"AND outcome IN ({', '.join('?' * len(wanted))}) ORDER BY id DESC LIMIT 5000",
            (kind, float(since), *wanted))]


def summary(*, since: float) -> dict[str, int]:
    """How many actions since *since*, how many asked the person, and how many they said no to."""

    path = _existing()
    if path is None:
        return {"total": 0, "asked": 0, "denied": 0}
    with _db_lock, closing(_connect(path)) as connection:
        total, asked, denied = connection.execute(
            "SELECT COUNT(*), SUM(CASE WHEN decided_by IN ('person', 'chat_grant') THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN outcome IN ('denied', 'changed') THEN 1 ELSE 0 END) FROM actions WHERE at >= ?",
            (float(since),),
        ).fetchone()
    return {"total": int(total or 0), "asked": int(asked or 0), "denied": int(denied or 0)}


def delete_conversation(conversation_id: str) -> int:
    """A deleted conversation's rows go with it (its helpers' too). Returns how many."""

    clean = str(conversation_id or "").strip()
    if not clean:
        return 0
    flush(2.0)
    path = _existing()
    if path is None:
        return 0
    with _db_lock, closing(_connect(path)) as connection, connection:
        return int(connection.execute("DELETE FROM actions WHERE conversation_id = ?", (clean,)).rowcount or 0)


def clear() -> int:
    """The person's "Clear activity": every row goes. Returns how many."""

    flush(2.0)
    path = _existing()
    if path is None:
        return 0
    with _db_lock, closing(_connect(path)) as connection, connection:
        return int(connection.execute("DELETE FROM actions").rowcount or 0)


def prune(*, now: float | None = None, retention_days: int = RETENTION_DAYS, max_rows: int = MAX_ROWS) -> int:
    """Drop rows past the retention window, then the oldest beyond the cap (idle maintenance)."""

    path = _existing()
    if path is None:
        return 0
    cutoff = float(now if now is not None else time.time()) - retention_days * 86_400
    with _db_lock, closing(_connect(path)) as connection, connection:
        removed = int(connection.execute("DELETE FROM actions WHERE at < ?", (cutoff,)).rowcount or 0)
        removed += int(connection.execute(
            "DELETE FROM actions WHERE id <= (SELECT id FROM actions ORDER BY id DESC LIMIT 1 OFFSET ?)",
            (max(0, int(max_rows)),),
        ).rowcount or 0)
    return removed
