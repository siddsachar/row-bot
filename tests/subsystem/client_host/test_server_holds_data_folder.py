"""W6: every way a Row-Bot server starts takes the data folder's lock first, before it opens anything there.

`python app.py` (README's "server only"), the launcher's own server, which the desktop app, `--server`,
`row-bot serve`, the container image and the native launchers on Windows, macOS and Linux all start, and a
sandbox's `python -m row_bot.app`. While chats are being compacted, each refuses to start and leaves the data
folder as it was; `test_compact_while_served` shows a running server's hold refusing the compact.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from row_bot import launcher
from row_bot.brand import LAUNCHER_APP_LOG_FILENAME
from row_bot.data_folder_lock import DataFolderLock, lock_path

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

ROOT = Path(__file__).resolve().parents[3]
REFUSED = "Row-Bot didn't start: its data is being compacted, reset or restored."


@pytest.fixture
def compacting(tmp_path, monkeypatch):
    """A data folder whose chats are being compacted: the compact holds its lock alone."""
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ROW_BOT_INSTALL_COUNT", "0")
    lock = DataFolderLock(lock_path(tmp_path))
    assert lock.acquire(exclusive=True)
    yield tmp_path
    lock.release()


def _left_in(folder: Path) -> set[str]:
    return {path.name for path in folder.iterdir()}


@pytest.mark.parametrize("command", [["app.py"], ["-m", "row_bot.app"]], ids=["python app.py", "sandbox -m"])
def test_a_server_started_by_hand_waits_for_the_compact(compacting, command) -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    done = subprocess.run([sys.executable, *command], cwd=ROOT, env=env, capture_output=True, text=True,
                          timeout=120, check=False)
    assert done.returncode != 0
    assert REFUSED in done.stderr
    assert _left_in(compacting) == {"data-folder.lock"}, "nothing in the data folder was opened"


def test_the_launchers_server_waits_for_the_compact(compacting, monkeypatch) -> None:
    """The process the desktop app, `row-bot serve` and the container run as their server."""
    monkeypatch.setattr(launcher, "_row_bot_data_dir", lambda: compacting)
    server = launcher._RowBotProcess(port=8765, host="127.0.0.1")
    server.start()
    try:
        assert server._proc.wait(timeout=120) != 0
    finally:
        server._close_log_handle()
    assert REFUSED in (compacting / LAUNCHER_APP_LOG_FILENAME).read_text(encoding="utf-8")
    assert _left_in(compacting) == {"data-folder.lock", LAUNCHER_APP_LOG_FILENAME}
