"""W6 (5.3.4 real-user test): compacting the chats never runs while anything has the data folder open.

`row-bot --compact-chats` was refused only by the launcher's lock, which `python app.py` never takes. With a
server running, Windows saved the data only by refusing to move the open file; macOS and Linux would have moved
the live threads.db aside under the server and unlinked its WAL. Now every server holds the data folder's lock
for its life, `compact()` takes it alone before it touches anything, and a database something else still has open
(an older server, another program) is caught by the WAL it keeps. Data safety, so these stay in the PR lane.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from row_bot import chat_compact
from row_bot.data_folder_lock import IN_USE_MESSAGE

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

SRC = Path(__file__).resolve().parents[3] / "src"
# A server's hold on the data folder, as app.py takes it, and its idle connection to threads.db with a write
# still in the WAL. `nolock` is a server that holds no lock (an older Row-Bot, another program).
SERVER = """
import sqlite3, sys
from pathlib import Path
folder = Path(sys.argv[1])
if sys.argv[2] == "lock":
    from row_bot.data_folder_lock import hold_for_server
    hold_for_server(folder)
conn = sqlite3.connect(folder / "threads.db")
conn.execute("PRAGMA wal_autocheckpoint=0")
with conn:
    conn.execute("INSERT INTO checkpoints VALUES ('film', '', 'cp-99', x'00')")
print("ready", flush=True)
sys.stdin.read()
"""


def _chat_database(folder: Path) -> Path:
    database = folder / "threads.db"
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE thread_meta (thread_id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE checkpoints (thread_id TEXT, checkpoint_ns TEXT, checkpoint_id TEXT, checkpoint BLOB)")
        conn.execute("INSERT INTO thread_meta VALUES ('film', 'Launch film')")
        for index in range(30):
            conn.execute("INSERT INTO checkpoints VALUES ('film', '', ?, ?)", (f"cp-{index:02d}", b"history" * 5_000))
    return database


def _start_server(folder: Path, mode: str) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(SRC), "ROW_BOT_DATA_DIR": str(folder), "ROW_BOT_INSTALL_COUNT": "0"}
    server = subprocess.Popen([sys.executable, "-c", SERVER, str(folder), mode], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True, env=env)
    assert server.stdout.readline().strip() == "ready"
    return server


def _stop(server: subprocess.Popen) -> None:
    server.stdin.close()
    server.wait(timeout=30)
    server.stdout.close()


def _files(folder: Path) -> dict[str, str]:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(folder.glob("threads.db*")) if not path.name.endswith("-shm")}


def _checkpoints(database: Path) -> int:
    with closing(sqlite3.connect(database)) as conn:
        return conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]


@pytest.mark.parametrize("mode", ["lock", "nolock"], ids=["a server holding the lock", "a server without it"])
def test_compacting_is_refused_while_a_server_has_the_data_folder_and_changes_nothing(tmp_path, mode) -> None:
    database = _chat_database(tmp_path)
    server = _start_server(tmp_path, mode)
    try:
        before = _files(tmp_path)
        assert "threads.db-wal" in before, "the server's write is still in the WAL"
        with pytest.raises(chat_compact.ChatsInUse) as refused:
            chat_compact.compact(database)
        assert str(refused.value) == IN_USE_MESSAGE
        assert _files(tmp_path) == before, "the file and its WAL are untouched, and nothing is left beside them"
        assert _checkpoints(database) == 31, "every saved state is still there"
    finally:
        _stop(server)

    result = chat_compact.compact(database)  # The server has stopped: the system dropped its lock.
    assert result["checkpoints"] == 31 - chat_compact.KEPT
    assert _checkpoints(database) == chat_compact.KEPT
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
    with pytest.raises(chat_compact.ChatsInUse, match=r"^Quit Row-Bot \(or stop the server\) before compacting"):
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
    server = _start_server(tmp_path, "lock")
    try:
        assert launcher._compact_chats() == 1
    finally:
        _stop(server)
    said = capsys.readouterr().out
    assert said.strip() == IN_USE_MESSAGE
    assert _checkpoints(database) == 31
