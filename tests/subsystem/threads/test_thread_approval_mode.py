from __future__ import annotations



def _fresh_threads(tmp_path, reload_for_data_dir):

    return reload_for_data_dir(tmp_path / "data", "row_bot.threads")[0]


def test_thread_approval_mode_defaults_to_ask(tmp_path, reload_for_data_dir):
    threads = _fresh_threads(tmp_path, reload_for_data_dir)

    threads._save_thread_meta("t1", "Thread")

    assert threads._get_thread_approval_mode("t1") == "approve"


def test_thread_approval_mode_persists_and_normalizes_legacy_values(tmp_path, reload_for_data_dir):
    threads = _fresh_threads(tmp_path, reload_for_data_dir)

    threads._save_thread_meta("t1", "Thread")
    threads._set_thread_approval_mode("t1", "auto_edit")

    # The old Auto is reviewed Auto now; nothing becomes Hands-off on its own.
    assert threads._get_thread_approval_mode("t1") == "auto"
    detailed = threads._list_threads(include_details=True)
    row = next(item for item in detailed if item[0] == "t1")
    assert row[8] == "auto"
