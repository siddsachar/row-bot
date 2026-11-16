"""Make the chat database small again, once, with Row-Bot closed (5.3.4 R13).

Before 5.3.4 a workflow chat saved its whole history at every step, and threads.db grew to tens of
gigabytes. 5.3.4 keeps each chat's newest saved states only, and the space freed is reused, but SQLite never
gives it back to the disk. ``row-bot --compact-chats`` does, with the app closed: it refuses, changing nothing,
while a launcher, a server (``python app.py`` and ``row-bot serve`` too) or anything else has the data folder open
(W6; see ``compact``):

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

from row_bot.data_folder_lock import IN_USE_MESSAGE, DataFolderLock, lock_path

KEPT = 3  # Saved states each chat keeps, as threads.CHECKPOINTS_KEPT.
KEPT_SUFFIX = ".pre-compact"


class ChatsInUse(RuntimeError):
    """Row-Bot, or something else, has the data folder open: nothing was changed."""


def compact(database: Path) -> dict[str, int]:
    """Compact *database* in place; {"before", "after"} sizes in bytes and the saved states removed.

    Refused, with nothing changed, while anything has the data folder open (W6): every Row-Bot server holds its
    lock (``data_folder_lock``), and a connection that still holds the file after this one closes it keeps its
    ``-wal`` (an idle connection in WAL mode holds no SQLite lock, but SQLite deletes the WAL only for the last one).
    """
    database = Path(database)
    kept = database.with_name(database.name + KEPT_SUFFIX)
    if kept.exists():
        raise RuntimeError(f"{kept.name} is still there from the last time: delete it (or move it) first.")
    if not database.is_file():
        raise RuntimeError(f"There's no chat database at {database}.")
    lock = DataFolderLock(lock_path(database.parent))
    try:
        held = lock.acquire(exclusive=True)
    except OSError as exc:
        raise ChatsInUse(f"{IN_USE_MESSAGE} ({lock.path.name} can't be opened: {exc.strerror or exc})") from None
    if not held:
        raise ChatsInUse(IN_USE_MESSAGE)
    try:
        return _compact_alone(database, kept)
    finally:
        lock.release()


def _compact_alone(database: Path, kept: Path) -> dict[str, int]:
    wal = database.with_name(database.name + "-wal")
    with closing(sqlite3.connect(database, timeout=0)) as probe:
        probe.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
    if wal.exists():
        raise ChatsInUse(IN_USE_MESSAGE)
    before = database.stat().st_size
    fresh = database.with_name(database.name + ".compact")
    fresh.unlink(missing_ok=True)
    try:
        removed = _trim_and_copy(database, fresh)
        with closing(sqlite3.connect(fresh)) as check:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("The compact copy didn't pass its check; the chat database stays in place.")
        if wal.exists():  # Opened by something else while the copy was made: the copy may lack its writes.
            raise ChatsInUse(IN_USE_MESSAGE)
        _swap(database, fresh, kept)
    finally:
        fresh.unlink(missing_ok=True)
    return {"before": before, "after": database.stat().st_size, "checkpoints": max(0, removed)}


def _trim_and_copy(database: Path, fresh: Path) -> int:
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
    return removed


def _swap(database: Path, fresh: Path, kept: Path) -> None:
    """The copy takes the file's place; on any failure the file stays, or is put back, where it was."""
    try:
        os.replace(database, kept)
    except OSError:  # Windows won't move a file something still has open.
        raise ChatsInUse(IN_USE_MESSAGE) from None
    try:
        database.with_name(database.name + "-shm").unlink(missing_ok=True)
        os.replace(fresh, database)
    except OSError:
        try:
            os.replace(kept, database)
        except OSError:
            raise RuntimeError(f"Your chats are in {kept.name}: rename it back to {database.name}.") from None
        raise ChatsInUse(IN_USE_MESSAGE) from None
