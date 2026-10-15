from __future__ import annotations



def _fresh_threads(tmp_path, reload_for_data_dir):

    return reload_for_data_dir(tmp_path / "data", "row_bot.threads")[0]


def test_list_threads_detail_indexes_are_append_only(tmp_path, reload_for_data_dir):
    threads = _fresh_threads(tmp_path, reload_for_data_dir)

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
    assert row[8] == "auto"  # The legacy "auto_edit" is reviewed Auto.
    assert row[9] == "manual"
