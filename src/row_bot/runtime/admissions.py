"""Content-free client admissions and command receipts in the existing tasks DB."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import uuid
import stat
import time
import sys
from pathlib import Path
from collections.abc import Iterator

_LOCK = threading.RLock()


class AdmissionError(ValueError):
    def __init__(self, code: str, current_revision: str | None = None) -> None:
        super().__init__(code)
        self.current_revision = current_revision


@contextlib.contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    from row_bot.tasks import _get_conn

    with _LOCK:
        conn = _get_conn()
        try:
            from row_bot.docs_capture import is_docs_read_only_real_data_capture

            if is_docs_read_only_real_data_capture():
                yield conn
                return
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS client_instance (
                    id INTEGER PRIMARY KEY CHECK(id=1), instance_id TEXT NOT NULL, secret TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS client_commands (
                    owner_id TEXT NOT NULL, key TEXT NOT NULL, command_id TEXT NOT NULL,
                    target TEXT NOT NULL, type TEXT NOT NULL, verifier TEXT NOT NULL,
                    status TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(owner_id,key), UNIQUE(owner_id,command_id));
                CREATE TABLE IF NOT EXISTS conversation_lifecycle (
                    conversation_id TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'active',
                    next_sequence INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS generation_passes (
                    pass_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                    submission_id TEXT NOT NULL UNIQUE, generation_id TEXT NOT NULL,
                    admission_sequence INTEGER NOT NULL, state TEXT NOT NULL,
                    checkpoint_revision TEXT NOT NULL DEFAULT '', execution_id TEXT NOT NULL DEFAULT '',
                    lease_epoch TEXT NOT NULL DEFAULT '', terminal_status TEXT NOT NULL DEFAULT '',
                    UNIQUE(conversation_id,admission_sequence));
                CREATE TABLE IF NOT EXISTS generation_segments (
                    segment_id TEXT PRIMARY KEY, pass_id TEXT NOT NULL, invocation_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL, native_message_id TEXT NOT NULL DEFAULT '',
                    checkpoint_revision TEXT NOT NULL DEFAULT '', live_revision TEXT NOT NULL DEFAULT '',
                    canonical_version TEXT NOT NULL DEFAULT '1');
                CREATE TABLE IF NOT EXISTS conversation_deletion_receipts (
                    conversation_id TEXT PRIMARY KEY, outcome TEXT NOT NULL);
            """)
            conn.execute("INSERT OR IGNORE INTO client_instance VALUES(1,?,?)",
                         (str(uuid.uuid4()), secrets.token_hex(32)))
            if "command_id" not in {row[1] for row in conn.execute("PRAGMA table_info(generation_passes)")}:
                conn.execute("ALTER TABLE generation_passes ADD COLUMN command_id TEXT NOT NULL DEFAULT ''")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(generation_passes)")}
            for name, definition in (("owner_pid", "INTEGER NOT NULL DEFAULT 0"), ("owner_birth", "REAL NOT NULL DEFAULT 0")):
                if name not in columns:
                    conn.execute(f"ALTER TABLE generation_passes ADD COLUMN {name} {definition}")
            for name, definition in (
                ("queue_state", "TEXT NOT NULL DEFAULT ''"),
                ("queue_revision", "INTEGER NOT NULL DEFAULT 0"),
                ("queue_source_generation_id", "TEXT NOT NULL DEFAULT ''"),
                ("queue_epoch", "TEXT NOT NULL DEFAULT ''"),
                ("queue_checkpoint_revision", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE generation_passes ADD COLUMN {name} {definition}")
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


def instance_identity() -> str:
    with transaction() as conn:
        return str(conn.execute("SELECT instance_id FROM client_instance WHERE id=1").fetchone()[0])


def claim_command(owner_id: str, key: str, command: dict, target: str, *, exclusive_target: bool = False,
                  initial_result: dict | None = None) -> dict | None:
    with transaction() as conn:
        secret = conn.execute("SELECT secret FROM client_instance WHERE id=1").fetchone()[0]
        semantic = {key: value for key, value in command.items() if key != "client_session_id"}
        canonical = json.dumps({"target": target, "command": semantic}, sort_keys=True,
                               ensure_ascii=False, separators=(",", ":")).encode()
        verifier = hmac.new(bytes.fromhex(secret), canonical, hashlib.sha256).hexdigest()
        existing = conn.execute("SELECT * FROM client_commands WHERE owner_id=? AND key=?",
                                (owner_id, key)).fetchone()
        if existing:
            if not hmac.compare_digest(str(existing["verifier"]), verifier):
                raise AdmissionError("idempotency_mismatch")
            if existing["status"] == "completed":
                return json.loads(existing["result_json"])
            if existing["status"] == "rejected":
                failure = json.loads(existing["result_json"])
                raise AdmissionError(failure["code"], failure.get("current_revision"))
            raise AdmissionError("operation_uncertain")
        if exclusive_target and conn.execute(
            "SELECT 1 FROM client_commands WHERE target=? AND (status NOT IN ('completed','rejected') OR status IS NULL) LIMIT 1",
            (target,),
        ).fetchone():
            raise AdmissionError("operation_pending")
        try:
            if initial_result is not None and type(initial_result) is not dict:
                raise ValueError
            initial_json = json.dumps(initial_result or {}, allow_nan=False, separators=(",", ":"))
            if len(initial_json.encode("utf-8")) > 256 * 1024:
                raise ValueError
        except (TypeError, ValueError, RecursionError):
            raise AdmissionError("invalid_command") from None
        try:
            conn.execute("INSERT INTO client_commands(owner_id,key,command_id,target,type,verifier,status,result_json) "
                         "VALUES(?,?,?,?,?,?,'admitting',?)",
                         (owner_id, key, command["command_id"], target, command["type"], verifier, initial_json))
        except sqlite3.IntegrityError as exc:
            raise AdmissionError("idempotency_mismatch") from exc
    return None


def complete_command(owner_id: str, key: str, result: dict) -> dict:
    with transaction() as conn:
        conn.execute("UPDATE client_commands SET status='completed',result_json=? WHERE owner_id=? AND key=?",
                     (json.dumps(result, separators=(",", ":")), owner_id, key))
    return result


def reject_command(owner_id: str, key: str, code: str, current_revision: str | None = None) -> None:
    with transaction() as conn:
        conn.execute("UPDATE client_commands SET status='rejected',result_json=? WHERE owner_id=? AND key=? AND status='admitting'",
                     (json.dumps({"code": code, "current_revision": current_revision}), owner_id, key))


def receipt(owner_id: str, command_id: str, *, include_type: bool = False) -> dict | None:
    with transaction() as conn:
        row = conn.execute("SELECT status,result_json,type FROM client_commands WHERE owner_id=? AND command_id=?",
                           (owner_id, command_id)).fetchone()
        return ({"command_id": command_id, "status": row["status"], **json.loads(row["result_json"]),
                 **({"_command_type": row["type"]} if include_type else {})} if row else None)


@contextlib.contextmanager
def _read_command_connection() -> Iterator[sqlite3.Connection | None]:
    """Read canonical command metadata without initialization or mutation.

    SQLite may maintain its existing WAL coordination files. Preflight rejects
    linked paths; this is not a held-handle filesystem race guarantee.
    """
    from row_bot.data_paths import get_tasks_db_path
    loaded_tasks = sys.modules.get("row_bot.tasks")
    path = Path(loaded_tasks._DB_PATH if loaded_tasks is not None else get_tasks_db_path(create_parent=False)).absolute()
    try:
        for leaf in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            for component in (*reversed(leaf.parents), leaf):
                try:
                    info = component.lstat()
                except FileNotFoundError:
                    break
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError
        if not path.exists():
            yield None
            return
        with contextlib.closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
            conn.row_factory = sqlite3.Row
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
            deadline, steps = time.monotonic() + 2, 0
            def interrupted() -> bool:
                nonlocal steps
                steps += 1000
                return steps >= 10_000_000 or time.monotonic() >= deadline
            conn.set_progress_handler(interrupted, 1000)
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            tables = [row for row in conn.execute("PRAGMA table_list") if row[0] == "main" and row[1] == "client_commands"]
            if not tables:
                yield None
                return
            columns = list(conn.execute('PRAGMA table_xinfo("client_commands")'))
            if tables[0][2] != "table" or any(row[6] for row in columns) or not {"owner_id", "key", "command_id", "target", "type", "status"}.issubset({row[1] for row in columns}):
                raise ValueError
            yield conn
    except (OSError, ValueError, sqlite3.Error):
        raise AdmissionError("command_metadata_unavailable") from None


def read_command_metadata(owner_id: str, command_id: str) -> dict | None:
    """Private receipt lookup; never exposes result JSON or mutates admissions."""
    with _read_command_connection() as conn:
        if conn is None:
            return None
        row = conn.execute("SELECT key,target,type,status FROM client_commands WHERE owner_id=? AND command_id=?",
                           (owner_id, command_id)).fetchone()
        if row is not None and any(not isinstance(value, str) or len(value) > 1024 for value in row):
            raise AdmissionError("command_metadata_unavailable")
        return dict(row) if row is not None else None


def read_command_receipt(owner_id: str, command_id: str) -> dict | None:
    """Read one bounded saved receipt without schema, key or command writes."""
    with _read_command_connection() as conn:
        if conn is None:
            return None
        row = conn.execute("SELECT status,substr(result_json,1,262145) FROM client_commands "
                           "WHERE owner_id=? AND command_id=?", (owner_id, command_id)).fetchone()
        if row is None:
            return None
        try:
            if not isinstance(row[1], str) or len(row[1].encode('utf-8')) > 262144:
                raise ValueError
            result = json.loads(row[1])
            if not isinstance(result, dict):
                raise ValueError
            return {"command_id": command_id, "status": row[0], **result}
        except (ValueError, RecursionError):
            raise AdmissionError("command_metadata_unavailable") from None


def read_unfinished_target_commands(target: str, *, limit: int = 32) -> dict:
    """Bounded cross-owner guard; overflow must remain a recovery requirement."""
    if type(limit) is not int or not 1 <= limit <= 32:
        raise AdmissionError("command_metadata_unavailable")
    with _read_command_connection() as conn:
        if conn is None:
            return {"items": [], "overflow": False}
        rows = conn.execute("SELECT owner_id,key,command_id,type,status FROM client_commands "
                            "WHERE target=? AND (status NOT IN ('completed','rejected') OR status IS NULL) ORDER BY owner_id,key LIMIT ?",
                            (target, limit + 1)).fetchmany(limit + 1)
        if any(not isinstance(value, str) or len(value) > 1024 for row in rows for value in row):
            raise AdmissionError("command_metadata_unavailable")
        return {"items": [dict(row) for row in rows[:limit]], "overflow": len(rows) > limit}


def command_progress(owner_id: str, key: str, result: dict) -> None:
    """Persist confirmed setup identities without storing setup text or secrets."""
    with transaction() as conn:
        changed = conn.execute(
            "UPDATE client_commands SET result_json=? WHERE owner_id=? AND key=? AND status='admitting'",
            (json.dumps(result, separators=(",", ":")), owner_id, key),
        ).rowcount
        if changed != 1:
            raise AdmissionError("operation_uncertain")


def reserve(conversation_id: str, submission_id: str, generation_id: str, *, command_id: str = "",
            queued_pass_id: str = "", resume_pending: bool = False) -> dict:
    with transaction() as conn:
        if conn.execute("SELECT 1 FROM conversation_deletion_receipts WHERE conversation_id=?", (conversation_id,)).fetchone():
            raise AdmissionError("conversation_deleting")
        conn.execute("INSERT OR IGNORE INTO conversation_lifecycle(conversation_id) VALUES(?)", (conversation_id,))
        lifecycle = conn.execute("SELECT * FROM conversation_lifecycle WHERE conversation_id=?", (conversation_id,)).fetchone()
        if lifecycle["state"] != "active":
            raise AdmissionError("conversation_deleting")
        pending = conn.execute("SELECT 1 FROM generation_passes WHERE conversation_id=? AND state IN ('admitting','admitted','started')",
                               (conversation_id,)).fetchone()
        if pending:
            raise AdmissionError("generation_active")
        queued = conn.execute("SELECT * FROM generation_passes WHERE conversation_id=? AND state IN ('queue_preparing','queued') ORDER BY admission_sequence LIMIT 1",
                              (conversation_id,)).fetchone()
        if queued_pass_id:
            if (not queued or queued["pass_id"] != queued_pass_id or queued["submission_id"] != submission_id
                    or queued["generation_id"] != generation_id or queued["queue_state"] != "dispatching"):
                raise AdmissionError("queue_revision_conflict")
            conn.execute("UPDATE generation_passes SET state='admitting' WHERE pass_id=? AND state='queued'", (queued_pass_id,))
            return {"pass_id": queued_pass_id, "submission_id": submission_id, "generation_id": generation_id,
                    "admission_sequence": str(queued["admission_sequence"])}
        if queued and not resume_pending:
            raise AdmissionError("queue_pending")
        sequence = int(lifecycle["next_sequence"]) + 1
        pass_id = str(uuid.uuid4())
        conn.execute("UPDATE conversation_lifecycle SET next_sequence=? WHERE conversation_id=?", (sequence, conversation_id))
        conn.execute("INSERT INTO generation_passes(pass_id,conversation_id,submission_id,generation_id,admission_sequence,state) VALUES(?,?,?,?,?,'admitting')",
                     (pass_id, conversation_id, submission_id, generation_id, sequence))
        conn.execute("UPDATE generation_passes SET command_id=? WHERE pass_id=?", (command_id, pass_id))
        return {"pass_id": pass_id, "submission_id": submission_id, "generation_id": generation_id,
                "admission_sequence": str(sequence)}


def admit(pass_id: str, checkpoint_revision: str) -> None:
    with transaction() as conn:
        row = conn.execute("SELECT p.*,l.state AS lifecycle FROM generation_passes p JOIN conversation_lifecycle l USING(conversation_id) WHERE pass_id=?", (pass_id,)).fetchone()
        if not row or row["lifecycle"] != "active":
            raise AdmissionError("conversation_deleting")
        if conn.execute("UPDATE generation_passes SET state='admitted',checkpoint_revision=? WHERE pass_id=? AND state='admitting'", (checkpoint_revision, pass_id)).rowcount != 1:
            raise AdmissionError("admission_conflict")


def start(pass_id: str, execution_id: str, epoch: str) -> None:
    import os
    import psutil
    with transaction() as conn:
        if conn.execute("UPDATE generation_passes SET state='started',execution_id=?,lease_epoch=?,owner_pid=?,owner_birth=? WHERE pass_id=? AND state='admitted' AND EXISTS(SELECT 1 FROM conversation_lifecycle l WHERE l.conversation_id=generation_passes.conversation_id AND l.state='active')", (execution_id, epoch, os.getpid(), psutil.Process().create_time(), pass_id)).rowcount != 1:
            raise AdmissionError("admission_conflict")


def finish(pass_id: str, status: str, *, boundary: dict | None = None) -> str:
    with transaction() as conn:
        if status == "completed":
            proof = boundary or {}
            matched = conn.execute("SELECT 1 FROM generation_passes p JOIN generation_segments s ON s.pass_id=p.pass_id "
                "WHERE p.pass_id=? AND p.state='started' AND p.execution_id=? AND p.lease_epoch=? "
                "AND s.segment_id=? AND s.state='committed' AND s.native_message_id=? AND s.checkpoint_revision=?",
                (pass_id, proof.get("execution_id", ""), proof.get("server_epoch", ""), proof.get("segment_id", ""),
                 proof.get("message_id", ""), proof.get("checkpoint_revision", ""))).fetchone()
            if not matched:
                status = "interrupted"
        conn.execute("UPDATE generation_passes SET state=?,terminal_status=? WHERE pass_id=? AND state='started'",
                     ("terminal" if status in {"completed", "stopped", "waiting_approval"} else "interrupted", status, pass_id))
        conn.execute("UPDATE generation_segments SET state='interrupted' WHERE pass_id=? AND state='started'", (pass_id,))
    return status


def recover(epoch: str) -> None:
    """Record process loss without calling providers or replaying accepted work."""
    from row_bot import threads
    with transaction() as conn:
        pending = [dict(row) for row in conn.execute(
            "SELECT * FROM generation_passes WHERE state IN ('admitting','admitted') OR (state='started' AND lease_epoch!=?)", (epoch,))]
    for row in pending:
        if row["state"] == "started" and _owner_alive(row):
            continue
        saved = threads.checkpointer.get_tuple({"configurable": {"thread_id": row["conversation_id"], "checkpoint_ns": ""}})
        messages = saved.checkpoint.get("channel_values", {}).get("messages", []) if saved else []
        matching_input = any(str(getattr(message, "id", "")) == row["submission_id"] for message in messages)
        state = "interrupted" if matching_input or row["state"] != "admitting" else "cancelled"
        revision = str(saved.config["configurable"]["checkpoint_id"]) if saved else ""
        with transaction() as conn:
            changed = conn.execute("UPDATE generation_passes SET state=?,terminal_status=?,checkpoint_revision=? WHERE pass_id=? AND state=? AND lease_epoch=?",
                (state, state, revision, row["pass_id"], row["state"], row["lease_epoch"])).rowcount
            if changed and row["command_id"]:
                result = {"command_id": row["command_id"], "conversation_id": row["conversation_id"],
                          "submission_id": row["submission_id"], "generation_id": row["generation_id"],
                          "pass_id": row["pass_id"], "status": "accepted" if matching_input else "rejected"}
                conn.execute("UPDATE client_commands SET status='completed',result_json=? WHERE command_id=? AND target=? AND status='admitting'",
                             (json.dumps(result, separators=(",", ":")), row["command_id"], row["conversation_id"]))
    from row_bot.application.client_queue import recover_queue
    recover_queue(epoch)


def _owner_alive(row: dict) -> bool:
    """PID reuse does not prove ownership; inaccessible owners stay unproven."""
    import psutil
    if not row.get("owner_pid") or not row.get("owner_birth"):
        return True
    try:
        return abs(psutil.Process(int(row["owner_pid"])).create_time() - float(row["owner_birth"])) < 0.001
    except psutil.NoSuchProcess:
        return False
    except psutil.Error:
        return True


def keyed_digest(value: dict, *, read_only: bool = False) -> str:
    """Opaque equality proof over private authoritative action state."""
    with (_read_command_connection() if read_only else transaction()) as conn:
        if conn is None:
            raise AdmissionError("command_metadata_unavailable")
        try:
            row = conn.execute("SELECT substr(secret,1,65) FROM client_instance WHERE id=1").fetchone()
            if row is None or not isinstance(row[0], str) or len(row[0]) != 64:
                raise ValueError
            secret = bytes.fromhex(row[0])
        except (ValueError, sqlite3.Error):
            raise AdmissionError("command_metadata_unavailable") from None
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def queued_submission_ids(conversation_id: str) -> list[str]:
    with transaction() as conn:
        return [str(row[0]) for row in conn.execute("SELECT submission_id FROM generation_passes WHERE conversation_id=? "
                "AND state IN ('admitting','admitted','started') ORDER BY admission_sequence LIMIT 256", (conversation_id,))]


def start_segment(pass_id: str) -> str:
    segment_id = str(uuid.uuid4())
    with transaction() as conn:
        conn.execute("INSERT INTO generation_segments(segment_id,pass_id,invocation_id,state) VALUES(?,?,?,'started')",
                     (segment_id, pass_id, str(uuid.uuid4())))
    return segment_id


def bind_output(pass_id: str, segment_id: str, message_id: str, revision: str, live_revision: str) -> None:
    with transaction() as conn:
        changed = conn.execute("UPDATE generation_segments SET state='committed',native_message_id=?,checkpoint_revision=?,live_revision=? WHERE segment_id=? AND pass_id=? AND state='started'",
                               (message_id, revision, live_revision, segment_id, pass_id)).rowcount
        if not changed:
            raise AdmissionError("output_binding_conflict")


def deletion_state(conversation_id: str) -> str:
    with transaction() as conn:
        if conn.execute("SELECT 1 FROM conversation_deletion_receipts WHERE conversation_id=?", (conversation_id,)).fetchone():
            return "physical_delete_ready"
        row = conn.execute("SELECT state FROM conversation_lifecycle WHERE conversation_id=?", (conversation_id,)).fetchone()
        return str(row[0]) if row else "active"


def read_deletion_state(conversation_id: str) -> str:
    """Read initialized lifecycle state without nested schema or writer locks.

    Command claim initializes the canonical schema before this is used by
    authority callbacks inside task transactions. Missing schema fails closed.
    """
    from pathlib import Path
    from row_bot import tasks
    with contextlib.closing(sqlite3.connect(Path(tasks._DB_PATH).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        if conn.execute("SELECT 1 FROM conversation_deletion_receipts WHERE conversation_id=?", (conversation_id,)).fetchone():
            return "physical_delete_ready"
        row = conn.execute("SELECT state FROM conversation_lifecycle WHERE conversation_id=?", (conversation_id,)).fetchone()
        return str(row[0]) if row else "active"


def close_admission(conversation_id: str) -> None:
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO conversation_lifecycle(conversation_id) VALUES(?)", (conversation_id,))
        conn.execute("UPDATE conversation_lifecycle SET state='admission_closed' WHERE conversation_id=? AND state='active'", (conversation_id,))
        conn.execute("UPDATE generation_passes SET state='cancelled' WHERE conversation_id=? AND state IN ('admitting','admitted')", (conversation_id,))
        conn.execute("UPDATE generation_passes SET state='cancelled',queue_state='cancelled',queue_revision=queue_revision+1 WHERE conversation_id=? AND state IN ('queue_preparing','queued')", (conversation_id,))


def advance_deletion(conversation_id: str, phase: str) -> None:
    phases = ("active", "admission_closed", "stop_requested", "producer_released", "children_cleaned", "resources_cleaned", "physical_delete_ready")
    if phase not in phases:
        raise AdmissionError("invalid_deletion_phase")
    with transaction() as conn:
        row = conn.execute("SELECT state FROM conversation_lifecycle WHERE conversation_id=?", (conversation_id,)).fetchone()
        if row and phases.index(str(row[0])) < phases.index(phase):
            conn.execute("UPDATE conversation_lifecycle SET state=? WHERE conversation_id=?", (phase, conversation_id))


def unproven_producer(conversation_id: str) -> bool:
    with transaction() as conn:
        return conn.execute("SELECT 1 FROM generation_passes WHERE conversation_id=? AND state='started'", (conversation_id,)).fetchone() is not None


def deletion_completed(conversation_id: str) -> None:
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO conversation_deletion_receipts VALUES(?,'DeleteCompleted')", (conversation_id,))
        conn.execute("DELETE FROM conversation_lifecycle WHERE conversation_id=?", (conversation_id,))


def deletion_receipt(conversation_id: str) -> str | None:
    with transaction() as conn:
        row = conn.execute("SELECT outcome FROM conversation_deletion_receipts WHERE conversation_id=?", (conversation_id,)).fetchone()
        return str(row[0]) if row else None


def reopen_completed_conversation(conversation_id: str) -> None:
    """Permit an explicit channel recreation only after completed deletion."""
    with transaction() as conn:
        pending = conn.execute("SELECT 1 FROM generation_passes WHERE conversation_id=? AND state IN ('admitting','admitted','started')", (conversation_id,)).fetchone()
        lifecycle = conn.execute("SELECT state FROM conversation_lifecycle WHERE conversation_id=?", (conversation_id,)).fetchone()
        completed = conn.execute("SELECT 1 FROM conversation_deletion_receipts WHERE conversation_id=?", (conversation_id,)).fetchone()
        if (pending and completed) or (lifecycle and lifecycle[0] != "active" and not completed):
            raise AdmissionError("conversation_deleting")
        if completed:
            sequence = conn.execute("SELECT COALESCE(MAX(admission_sequence),0) FROM generation_passes WHERE conversation_id=?", (conversation_id,)).fetchone()[0]
            conn.execute("DELETE FROM conversation_deletion_receipts WHERE conversation_id=?", (conversation_id,))
            conn.execute("INSERT INTO conversation_lifecycle(conversation_id,state,next_sequence) VALUES(?,'active',?) ON CONFLICT(conversation_id) DO UPDATE SET state='active',next_sequence=excluded.next_sequence", (conversation_id, sequence))
