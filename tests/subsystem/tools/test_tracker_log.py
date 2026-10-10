"""Logging to a tracker that does not exist yet creates it once, however the logs arrive."""
import sqlite3

from row_bot.tools import tracker_tool


def test_two_logs_at_once_for_a_new_tracker_both_land_in_it(tmp_path, monkeypatch):
    """Found live: "I slept 7 hours last night and 6.5 the night before" logged both at once, and the
    second failed with "UNIQUE constraint failed: trackers.name"."""
    monkeypatch.setattr(tracker_tool, "_DB_PATH", tmp_path / "tracker.db")
    find = tracker_tool._find_tracker
    raced: list[str] = []

    def other_log_creates_it_first(conn, name):
        if not raced:  # The other log makes the tracker between this one's look and its insert.
            raced.append(name)
            raced.append(tracker_tool._tracker_log(name, "7", "numeric", "hours"))
            return None
        return find(conn, name)

    monkeypatch.setattr(tracker_tool, "_find_tracker", other_log_creates_it_first)
    second = tracker_tool._tracker_log("Sleep", "6.5", "numeric", "hours")

    assert raced[1].startswith("✅ Logged **Sleep** = 7") and "New tracker 'Sleep'" in raced[1]
    assert second.startswith("✅ Logged **Sleep** = 6.5") and "New tracker" not in second
    with sqlite3.connect(tmp_path / "tracker.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM trackers").fetchone()[0] == 1
        assert sorted(value for (value,) in conn.execute("SELECT value FROM entries")) == ["6.5", "7"]
