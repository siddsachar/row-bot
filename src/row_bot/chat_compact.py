"""Make the chat database small again, once, with Row-Bot closed (5.3.4 R13).

Before 5.3.4 a workflow chat saved its whole history at every step, and threads.db grew to tens of
gigabytes. 5.3.4 keeps each chat's newest saved states only, and the space freed is reused, but SQLite never
gives it back to the disk. ``row-bot --compact-chats`` does, with the app closed (the launcher refuses while
another one holds the data folder):

1. every chat keeps its newest ``KEPT`` saved states (the app keeps no more from 5.3.4 on);
2. a compact copy is written beside the file and checked;
3. it takes the file's place, and the old file stays beside it as ``threads.db.pre-compact`` until the
   person deletes it.

Plain SQLite and nothing else: no Row-Bot module that opens threads.db is imported, so nothing in this
process holds the file when it is moved (Windows won't move an open file).
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

KEPT = 3  # Saved states each chat keeps, as threads.CHECKPOINTS_KEPT.
KEPT_SUFFIX = ".pre-compact"


def compact(database: Path) -> dict[str, int]:
    """Compact *database* in place; {"before", "after"} sizes in bytes and the saved states removed."""
    database = Path(database)
    kept = database.with_name(database.name + KEPT_SUFFIX)
    if kept.exists():
        raise RuntimeError(f"{kept.name} is still there from the last time: delete it (or move it) first.")
    if not database.is_file():
        raise RuntimeError(f"There's no chat database at {database}.")
    before = database.stat().st_size
    fresh = database.with_name(database.name + ".compact")
    fresh.unlink(missing_ok=True)
    with closing(sqlite3.connect(database, timeout=30)) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        removed = 0
        if "checkpoints" in tables:
            with conn:
                removed = conn.execute(
                    "DELETE FROM checkpoints WHERE rowid IN (SELECT rowid FROM (SELECT rowid, ROW_NUMBER() OVER "
                    "(PARTITION BY thread_id, checkpoint_ns ORDER BY checkpoint_id DESC) AS place "
                    "FROM checkpoints) WHERE place > ?)", (KEPT,)).rowcount
                if "writes" in tables:
                    conn.execute(
                        "DELETE FROM writes WHERE NOT EXISTS (SELECT 1 FROM checkpoints AS kept WHERE "
                        "kept.thread_id = writes.thread_id AND kept.checkpoint_ns = writes.checkpoint_ns "
                        "AND kept.checkpoint_id = writes.checkpoint_id)")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM INTO ?", (str(fresh),))
    with closing(sqlite3.connect(fresh)) as check:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            fresh.unlink(missing_ok=True)
            raise RuntimeError("The compact copy didn't pass its check; the chat database is unchanged.")
    os.replace(database, kept)
    for leftover in (database.with_name(database.name + "-wal"), database.with_name(database.name + "-shm")):
        leftover.unlink(missing_ok=True)
    os.replace(fresh, database)
    return {"before": before, "after": database.stat().st_size, "checkpoints": max(0, removed)}
