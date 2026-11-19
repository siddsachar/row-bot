"""W6: `--reset-db`, `--reset-tasks-db` and `--restore-data` never replace a database under a running server.

They move the data folder's databases aside (and copy backups in), so they had the hole `--compact-chats` had: only
the launcher's lock refused them, and `python app.py` never takes it. Now they hold the data folder's lock alone,
and refuse, changing nothing, while a server holds it or another connection still has one of their databases
open. Data safety, so these stay in the PR lane.
"""
from __future__ import annotations

import pytest

from row_bot import launcher
from row_bot.data_folder_lock import RESET_IN_USE_MESSAGE
from tests.helpers.data_folder_server import folder_files, start_server, stop_server, wal_database

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

DATABASES = ("tasks.db", "memory.db", "threads.db", "mobile.db")
COMMANDS = {
    "--reset-tasks-db": (lambda: launcher._reset_tasks_db(), ("tasks.db",)),
    "--reset-db": (lambda: launcher._reset_all_local_dbs(), DATABASES),
    "--restore-data": (lambda: launcher._restore_data("profile-backup"), DATABASES),
}


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(launcher, "_timestamp", lambda: "20261116-090000")
    monkeypatch.setattr("row_bot.tasks.ensure_task_schema", lambda **_kwargs: {"status": "ok"})
    backup = tmp_path / "recovery" / "profile-backup"
    backup.mkdir(parents=True)
    for name in DATABASES:
        wal_database(tmp_path / name)
        wal_database(backup / name, rows=1)
    return tmp_path


def _paths(folder):
    return sorted(str(path.relative_to(folder)) for path in folder.rglob("*")
                  if not path.name.endswith("-shm") and path.name != "data-folder.lock")


@pytest.mark.parametrize("mode", ["lock", "nolock"], ids=["a server holding the lock", "a server without it"])
@pytest.mark.parametrize("command", list(COMMANDS))
def test_it_is_refused_while_a_server_has_the_data_folder_and_changes_nothing(profile, capsys, command, mode):
    run, opened = COMMANDS[command]
    server = start_server(profile, mode, opened)
    try:
        files, paths = folder_files(profile), _paths(profile)
        assert all(f"{name}-wal" in files for name in opened), "the server's writes are still in the WAL"
        assert run() == 1
        assert capsys.readouterr().out.strip() == RESET_IN_USE_MESSAGE
        assert folder_files(profile) == files, "every database and its WAL are untouched"
        assert _paths(profile) == paths, "nothing was moved aside or made"
    finally:
        stop_server(server)

    assert run() == 0  # The server has stopped: the system dropped its lock.
    assert (profile / "recovery").is_dir() and _paths(profile) != paths
