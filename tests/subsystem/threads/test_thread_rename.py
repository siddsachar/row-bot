from __future__ import annotations

import importlib
import sys


def _fresh_threads(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    sys.modules.pop("threads", None)
    import row_bot.threads as threads

    return importlib.reload(threads)


def test_list_threads_detail_indexes_are_append_only(tmp_path, monkeypatch):
    threads = _fresh_threads(tmp_path, monkeypatch)

    tid = threads.create_thread(
        "Code task",
        thread_type="code",
        developer_workspace_id="dev_123",
        project_id="",
        approval_mode="auto_edit",
        model_override="model:test",
        name_source="manual",
    )

    row = next(item for item in threads._list_threads(include_details=True) if item[0] == tid)
    assert row[4] == "model:test"
    assert row[5] == ""
    assert row[6] == "code"
    assert row[7] == "dev_123"
    assert row[8] == "allow_all"
    assert row[9] == "manual"
