"""W6 (5.3.4 real-user test): compacting the chats never runs while anything has the data folder open.

`row-bot --compact-chats` was refused only by the launcher's lock, which `python app.py` never takes. With a
server running, Windows saved the data only by refusing to move the open file; macOS and Linux would have moved
the live threads.db aside under the server and unlinked its WAL. Now every server holds the data folder's lock
for its life, `compact()` takes it alone before it touches anything, and a database something else still has open
(an older server, another program) is caught by the WAL it keeps. Data safety, so these stay in the PR lane.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from row_bot import chat_compact
from row_bot.data_folder_lock import IN_USE_MESSAGE, FolderInUse
from tests.helpers.data_folder_server import folder_files, start_server, stop_server, wal_database

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


def _chat_database(folder: Path) -> Path:
    database = wal_database(folder / "threads.db")
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("CREATE TABLE thread_meta (thread_id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE checkpoints (thread_id TEXT, checkpoint_ns TEXT, checkpoint_id TEXT, checkpoint BLOB)")
        conn.execute("INSERT INTO thread_meta VALUES ('film', 'Launch film')")
        for index in range(30):
            conn.execute("INSERT INTO checkpoints VALUES ('film', '', ?, ?)", (f"cp-{index:02d}", b"history" * 5_000))
    return database


def _count(database: Path, table: str) -> int:
    with closing(sqlite3.connect(database)) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608 - the tests' own names


@pytest.mark.parametrize("mode", ["lock", "nolock"], ids=["a server holding the lock", "a server without it"])
def test_compacting_is_refused_while_a_server_has_the_data_folder_and_changes_nothing(tmp_path, mode) -> None:
    database = _chat_database(tmp_path)
    server = start_server(tmp_path, mode, ("threads.db",))
    try:
        before = folder_files(tmp_path)
        assert "threads.db-wal" in before, "the server's write is still in the WAL"
        with pytest.raises(FolderInUse) as refused:
            chat_compact.compact(database)
        assert str(refused.value) == IN_USE_MESSAGE
        assert folder_files(tmp_path) == before, "the file and its WAL are untouched, and nothing is left beside them"
        assert _count(database, "checkpoints") == 30 and _count(database, "rows") == 4
    finally:
        stop_server(server)

    result = chat_compact.compact(database)  # The server has stopped: the system dropped its lock.
    assert result["checkpoints"] == 30 - chat_compact.KEPT
    assert _count(database, "checkpoints") == chat_compact.KEPT and _count(database, "rows") == 4
    assert (tmp_path / ("threads.db" + chat_compact.KEPT_SUFFIX)).is_file()
    assert not (tmp_path / "threads.db.compact").exists()


@pytest.mark.parametrize("failing", [1, 2], ids=["moving the file aside", "moving the copy in"])
def test_a_failed_swap_leaves_the_chat_database_in_place_and_no_copy_behind(tmp_path, monkeypatch, failing) -> None:
    """The Windows run of W6: a move refused with a sharing violation. The file stays (or goes back) in place."""
    database = _chat_database(tmp_path)
    replace, calls = os.replace, []

    def refusing(source, target):
        calls.append(source)
        if len(calls) == failing:
            raise PermissionError(13, "The process cannot access the file because it is being used by another "
                                      "process", str(source))
        replace(source, target)

    monkeypatch.setattr(chat_compact.os, "replace", refusing)
    with pytest.raises(FolderInUse, match=r"^Quit Row-Bot \(or stop the server\) before compacting"):
        chat_compact.compact(database)
    monkeypatch.undo()

    assert sorted(path.name for path in tmp_path.glob("threads.db*")) == ["threads.db"]
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT name FROM thread_meta").fetchall() == [("Launch film",)]
        newest = conn.execute("SELECT checkpoint_id FROM checkpoints ORDER BY 1 DESC LIMIT 1").fetchone()[0]
    assert newest == "cp-29"
    chat_compact.compact(database)  # Nothing left over stands in the way of the next try.
    assert (tmp_path / ("threads.db" + chat_compact.KEPT_SUFFIX)).is_file()


def test_the_command_says_to_quit_row_bot_in_plain_words(tmp_path, monkeypatch, capsys) -> None:
    import row_bot.launcher as launcher

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    database = _chat_database(tmp_path)
    server = start_server(tmp_path, "lock")
    try:
        assert launcher._compact_chats() == 1
    finally:
        stop_server(server)
    assert capsys.readouterr().out.strip() == IN_USE_MESSAGE
    assert _count(database, "checkpoints") == 30
