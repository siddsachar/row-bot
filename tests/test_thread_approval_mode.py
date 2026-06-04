from __future__ import annotations

import importlib
import sys


def _fresh_threads(tmp_path, monkeypatch):
    monkeypatch.setenv("THOTH_DATA_DIR", str(tmp_path / "data"))
    sys.modules.pop("threads", None)
    import row_bot.threads as threads

    return importlib.reload(threads)


def test_thread_approval_mode_defaults_to_ask(tmp_path, monkeypatch):
    threads = _fresh_threads(tmp_path, monkeypatch)

    threads._save_thread_meta("t1", "Thread")

    assert threads._get_thread_approval_mode("t1") == "approve"


def test_thread_approval_mode_persists_and_normalizes_legacy_values(tmp_path, monkeypatch):
    threads = _fresh_threads(tmp_path, monkeypatch)

    threads._save_thread_meta("t1", "Thread")
    threads._set_thread_approval_mode("t1", "auto_edit")

    assert threads._get_thread_approval_mode("t1") == "allow_all"
    detailed = threads._list_threads(include_details=True)
    row = next(item for item in detailed if item[0] == "t1")
    assert row[8] == "allow_all"
