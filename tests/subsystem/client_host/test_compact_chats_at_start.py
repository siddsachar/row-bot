"""S9 (18 Nov): a chat database left large by 5.3.0-5.3.3 gives its space back by itself, once, at start.

The owner's threads.db was 22 GB and 96% unused pages after 5.3.4 stopped the growth: SQLite never hands that
back, live compaction skips a file that large, and nobody runs `row-bot --compact-chats`. The launcher now
compacts it before the server opens it, the start-up window says so, and the old file goes once the copy has
passed its check. Data safety, so this stays in the pull request lane.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from row_bot import chat_compact, launcher

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ROW_BOT_INSTALL_COUNT", "0")
    monkeypatch.setattr(launcher, "_row_bot_data_dir", lambda: tmp_path)
    monkeypatch.setattr(launcher, "get_threads_db_path", lambda: tmp_path / "threads.db")
    monkeypatch.setattr(launcher, "_LAUNCH_FILE_LOGGING", True)  # its lines are read here, not from a file
    return tmp_path


def _compaction_line(caplog) -> dict[str, str]:
    """launcher.log's line for the compaction at start, as its fields (T1)."""
    lines = [record.getMessage() for record in caplog.records
             if record.getMessage().startswith("launcher.compact_chats ")]
    assert len(lines) == 1, lines
    return dict(field.split("=", 1) for field in lines[0].split()[1:])


def _chats(folder: Path, *, states: int, pruned_to: int) -> Path:
    """A chat database as an older version left it: many saved states, most since pruned (free pages)."""
    database = folder / "threads.db"
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("CREATE TABLE thread_meta (thread_id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE checkpoints (thread_id TEXT, checkpoint_ns TEXT, checkpoint_id TEXT, checkpoint BLOB)")
        conn.execute("INSERT INTO thread_meta VALUES ('trends', 'Growth | Trends')")
        for index in range(states):
            conn.execute("INSERT INTO checkpoints VALUES ('trends', '', ?, ?)", (f"cp-{index:02d}", b"history" * 20_000))
        conn.execute("DELETE FROM checkpoints WHERE checkpoint_id < ?", (f"cp-{states - pruned_to:02d}",))
    return database


def test_a_large_mostly_unused_chat_database_is_compacted_at_start_and_says_so(folder, monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO, logger=launcher.logger.name)
    database = _chats(folder, states=30, pruned_to=6)
    before = database.stat().st_size
    monkeypatch.setattr(chat_compact, "AT_START_MIN_BYTES", 1)
    shown: list[str] = []
    real = chat_compact.compact

    def compacting(path):
        status = folder / f"launcher-{launcher._LAUNCH_SESSION_ID}-splash.status"
        shown.append(status.read_text(encoding="utf-8") if status.exists() else "")
        return real(path)

    monkeypatch.setattr(chat_compact, "compact", compacting)
    launcher._compact_chats_at_start()

    assert shown == ["Making your chat history smaller (one time)…"], "the start-up window says what it's doing"
    assert not list(folder.glob("*.status")), "and stops saying it when done"
    assert database.stat().st_size < before / 3
    assert not (folder / ("threads.db" + chat_compact.KEPT_SUFFIX)).exists(), "the old file goes after the check"
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        kept = [row[0] for row in conn.execute("SELECT checkpoint_id FROM checkpoints ORDER BY 1")]
        assert kept == ["cp-27", "cp-28", "cp-29"], "each chat keeps its newest states"
        assert conn.execute("SELECT name FROM thread_meta").fetchall() == [("Growth | Trends",)]
    line = _compaction_line(caplog)  # and launcher.log says what came of it
    assert line["status"] == "compacted"
    assert (int(line["bytes_before"]), int(line["bytes_after"])) == (before, database.stat().st_size)
    assert line["removed"] == "3"  # the six states left, down to the newest three


@pytest.mark.parametrize("smallest, pruned_to, why", [(chat_compact.AT_START_MIN_BYTES, 6, "small"),
                                                      (1, 30, "mostly_in_use")],
                         ids=["a small file", "a large file that is mostly in use"])
def test_a_chat_database_with_little_to_give_back_is_left_as_it_is(folder, monkeypatch, caplog, smallest, pruned_to,
                                                                    why) -> None:
    caplog.set_level(logging.INFO, logger=launcher.logger.name)
    database = _chats(folder, states=30, pruned_to=pruned_to)
    before = database.read_bytes()
    monkeypatch.setattr(chat_compact, "AT_START_MIN_BYTES", smallest)
    launcher._compact_chats_at_start()
    assert database.read_bytes() == before
    assert sorted(path.name for path in folder.iterdir()) == ["threads.db"]
    line = _compaction_line(caplog)  # and launcher.log says why
    assert (line["status"], line["reason"]) == ("skipped", why)


def test_a_start_without_a_chat_database_says_there_was_nothing_to_compact(folder, caplog) -> None:
    caplog.set_level(logging.INFO, logger=launcher.logger.name)
    launcher._compact_chats_at_start()
    line = _compaction_line(caplog)
    assert (line["status"], line["reason"]) == ("skipped", "no_database")
    assert not (folder / "threads.db").exists()


def test_a_compaction_refused_because_the_folder_is_in_use_says_so(folder, monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO, logger=launcher.logger.name)
    database = _chats(folder, states=30, pruned_to=6)
    monkeypatch.setattr(chat_compact, "AT_START_MIN_BYTES", 1)

    def in_use(_path):
        raise chat_compact.FolderInUse("in use")

    monkeypatch.setattr(chat_compact, "compact", in_use)
    before = database.read_bytes()
    launcher._compact_chats_at_start()
    assert database.read_bytes() == before
    line = _compaction_line(caplog)
    assert (line["status"], line["reason"]) == ("skipped", "in_use")
