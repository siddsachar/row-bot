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

import logging
import os
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from row_bot.data_folder_lock import IN_USE_MESSAGE, FolderInUse, alone

logger = logging.getLogger(__name__)

KEPT = 3  # Saved states each chat keeps, as threads.CHECKPOINTS_KEPT.
KEPT_SUFFIX = ".pre-compact"
# At start, a file this large and at least this empty gives its space back by itself (S9): people who ran
# workflows on 5.3.0-5.3.3 have the same file the owner had (22 GB, 96% free), and nobody runs a command for it.
AT_START_MIN_BYTES = 1024 ** 3
AT_START_MIN_FREE = 0.5


def compact(database: Path) -> dict[str, int]:
    """Compact *database* in place; {"before", "after"} sizes in bytes and the saved states removed.

    Refused with FolderInUse, nothing changed, while anything has the data folder open (W6): every Row-Bot
    server holds its lock, and another connection to the file keeps its ``-wal`` (``data_folder_lock.alone``).
    """
    database = Path(database)
    kept = database.with_name(database.name + KEPT_SUFFIX)
    if kept.exists():
        raise RuntimeError(f"{kept.name} is still there from the last time: delete it (or move it) first.")
    if not database.is_file():
        raise RuntimeError(f"There's no chat database at {database}.")
    with alone(database.parent, [database], message=IN_USE_MESSAGE):
        return _compact_alone(database, kept)


def compact_at_start(database: Path, *, on_start: Callable[[], None] = lambda: None) -> dict[str, int | str]:
    """Give a large, mostly empty chat database's space back once, before Row-Bot opens it (S9).

    Only a file of at least ``AT_START_MIN_BYTES`` whose pages are at least ``AT_START_MIN_FREE`` unused, with
    room on the disk for the copy. *on_start* runs just before the work starts (the start-up window says so).
    The old file is removed once the copy has passed its check and taken its place. Returns the sizes
    ({"before", "after", "checkpoints"}), or {"skipped": why} when there was nothing to do or it couldn't be
    done now (the next start tries again): no_database, small, unreadable, mostly_in_use, low_disk, in_use
    or failed.
    """
    database = Path(database)
    try:
        if not database.is_file():
            return {"skipped": "no_database"}
        if database.stat().st_size < AT_START_MIN_BYTES:
            return {"skipped": "small"}
        with closing(sqlite3.connect(database, timeout=1)) as conn:
            pages, free, page_size = (conn.execute(f"PRAGMA {name}").fetchone()[0]
                                      for name in ("page_count", "freelist_count", "page_size"))
    except (OSError, sqlite3.Error):
        logger.info("The chat database's size couldn't be read at start", exc_info=True)
        return {"skipped": "unreadable"}
    if not pages or free / pages < AT_START_MIN_FREE:
        return {"skipped": "mostly_in_use"}
    in_use = (pages - free) * page_size
    if shutil.disk_usage(database.parent).free < in_use * 2 + 256 * 1024 * 1024:
        logger.info("Not enough free disk to compact the chat database at start (%d bytes in use)", in_use)
        return {"skipped": "low_disk"}
    on_start()
    try:
        result = compact(database)
    except FolderInUse:
        return {"skipped": "in_use"}
    except (RuntimeError, OSError, sqlite3.Error) as exc:
        logger.info("The chat database wasn't compacted at start: %s", exc)
        return {"skipped": "failed"}
    try:
        database.with_name(database.name + KEPT_SUFFIX).unlink()
    except OSError:
        logger.warning("The old chat database couldn't be removed after compacting", exc_info=True)
    logger.info("Chat database compacted at start: %d -> %d bytes, %d old saved states removed",
                result["before"], result["after"], result["checkpoints"])
    return result


def _compact_alone(database: Path, kept: Path) -> dict[str, int]:
    wal = database.with_name(database.name + "-wal")
    before = database.stat().st_size
    fresh = database.with_name(database.name + ".compact")
    fresh.unlink(missing_ok=True)
    try:
        removed = _trim_and_copy(database, fresh)
        with closing(sqlite3.connect(fresh)) as check:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("The compact copy didn't pass its check; the chat database stays in place.")
        if wal.exists():  # Opened by something else while the copy was made: the copy may lack its writes.
            raise FolderInUse(IN_USE_MESSAGE)
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
        raise FolderInUse(IN_USE_MESSAGE) from None
    try:
        database.with_name(database.name + "-shm").unlink(missing_ok=True)
        os.replace(fresh, database)
    except OSError:
        try:
            os.replace(kept, database)
        except OSError:
            raise RuntimeError(f"Your chats are in {kept.name}: rename it back to {database.name}.") from None
        raise FolderInUse(IN_USE_MESSAGE) from None
