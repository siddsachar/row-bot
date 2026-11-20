"""A stand-in for a running Row-Bot server on a data folder (W6): a real process that holds the data folder's
lock as ``app.py`` does (or, for an older Row-Bot or another program, holds none) and keeps an idle connection
to each database with a write still in its WAL."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
_SERVER = """
import sqlite3, sys
from pathlib import Path
folder = Path(sys.argv[1])
if sys.argv[2] == "lock":
    from row_bot.data_folder_lock import hold_for_server
    hold_for_server(folder)
connections = []
for name in sys.argv[3:]:
    conn = sqlite3.connect(folder / name)
    conn.execute("PRAGMA wal_autocheckpoint=0")
    with conn:
        conn.execute("INSERT INTO rows VALUES ('written by the server')")
    connections.append(conn)
print("ready", flush=True)
sys.stdin.read()
"""


def wal_database(path: Path, rows: int = 3) -> Path:
    """A database in WAL mode with a ``rows`` table, as Row-Bot's are."""
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS rows (value TEXT)")
        conn.executemany("INSERT INTO rows VALUES (?)", [(f"row {index}",) for index in range(rows)])
    return path


def start_server(folder: Path, mode: str, databases: tuple[str, ...] = ()) -> subprocess.Popen:
    """``mode`` "lock" holds the data folder like app.py; "nolock" holds no lock. Returns once it is ready."""
    env = {**os.environ, "PYTHONPATH": str(SRC), "ROW_BOT_DATA_DIR": str(folder), "ROW_BOT_INSTALL_COUNT": "0"}
    server = subprocess.Popen([sys.executable, "-c", _SERVER, str(folder), mode, *databases],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
    assert server.stdout.readline().strip() == "ready"
    return server


def stop_server(server: subprocess.Popen) -> None:
    server.stdin.close()
    server.wait(timeout=30)
    server.stdout.close()


def folder_files(folder: Path) -> dict[str, str]:
    """Every file in the folder, but the lock and the WAL indexes, by content hash."""
    return {str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(folder.rglob("*"))
            if path.is_file() and not path.name.endswith("-shm") and path.name != "data-folder.lock"}
