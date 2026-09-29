import importlib
import json
import logging
from types import SimpleNamespace
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def data_dir(monkeypatch):
    root = Path(".tmp") / "pytest-app-stability-fixtures"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"case-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(path))
    yield path


def test_workflow_drafts_round_trip_and_delete(data_dir, monkeypatch):
    import row_bot.tasks as tasks

    monkeypatch.setattr(tasks, "_DB_PATH", str(data_dir / "tasks.db"))
    monkeypatch.setattr(tasks, "_scheduler", None)
    tasks._init_db()

    tasks.save_workflow_draft(
        None,
        {"name": "New draft", "prompts": ["draft prompt"], "channels": []},
    )
    new_draft = tasks.get_workflow_draft(None)

    assert new_draft is not None
    assert new_draft["mode"] == "new"
    assert new_draft["payload"]["name"] == "New draft"
    assert new_draft["payload"]["channels"] == []

    task_id = tasks.create_task(name="Existing", prompts=["original"])
    tasks.save_workflow_draft(
        task_id,
        {"name": "Edited draft", "prompts": ["changed"], "channels": ["telegram"]},
    )
    edit_draft = tasks.get_workflow_draft(task_id)

    assert edit_draft is not None
    assert edit_draft["mode"] == "edit"
    assert edit_draft["task_id"] == task_id
    assert edit_draft["payload"]["prompts"] == ["changed"]
    assert tasks.get_task(task_id)["name"] == "Existing"

    tasks.delete_workflow_draft(None)
    tasks.delete_workflow_draft(task_id)

    assert tasks.get_workflow_draft(None) is None
    assert tasks.get_workflow_draft(task_id) is None


def test_stability_client_error_writes_local_report(data_dir, monkeypatch):
    import row_bot.stability as stability

    stability = importlib.reload(stability)
    report_path = stability.record_client_error(
        {
            "message": "synthetic client failure",
            "source": "pytest",
            "details": object(),
        }
    )

    assert report_path is not None
    assert report_path.exists()
    assert report_path.parent == data_dir / "crashes"

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "client_error"
    assert payload["message"] == "synthetic client failure"
    assert payload["extra"]["client"]["source"] == "pytest"
    assert "object object" in payload["extra"]["client"]["details"]


def test_resize_observer_warning_is_benign_and_rate_limited(data_dir, monkeypatch, caplog):
    import row_bot.stability as stability

    stability = importlib.reload(stability)
    payload = {
        "kind": "error",
        "message": "ResizeObserver loop completed with undelivered notifications.",
        "source": "http://localhost:8080/",
        "line": 0,
        "column": 0,
        "stack": "",
    }

    with caplog.at_level(logging.INFO, logger="row_bot.stability"):
        first = stability.record_client_error(payload)
        second = stability.record_client_error(payload)
        third = stability.record_client_error(payload)

    assert first is None
    assert second is None
    assert third is None
    assert stability.classify_client_report(payload) == "browser_layout_warning"
    assert not list((data_dir / "crashes").glob("*client_error*.json"))
    state = next(iter(stability._client_warning_state.values()))
    assert state["count"] == 3
    assert state["suppressed_count"] == 2
    info_messages = [record.message for record in caplog.records if record.levelname == "INFO"]
    assert len([message for message in info_messages if "browser layout warning" in message]) == 1


def test_stability_source_contracts_are_wired():
    app_src = Path("src/row_bot/app.py").read_text(encoding="utf-8")
    agent_src = Path("src/row_bot/agent.py").read_text(encoding="utf-8")
    discord_src = Path("src/row_bot/channels/discord_channel.py").read_text(encoding="utf-8")

    assert "setup_stability_monitoring()" in app_src
    assert 'app.add_route("/api/launcher-shutdown"' in app_src
    assert 'app.add_route("/api/startup-state"' in app_src
    assert "async def _cleanup_runtime" in app_src
    assert "_ch_registry.all_channels()" in app_src
    assert "await asyncio.wait_for(_ch.stop(), timeout=10)" in app_src
    assert "os._exit(0)" in app_src
    assert "_is_transient_stream_disconnect" in agent_src
    assert "provider stream disconnected" in agent_src
    assert "start_performance_monitor()" in app_src
    assert "schedule_idle_extraction" in app_src
    assert "cleanup_old_checkpoints" in app_src
    assert '_ch_config.set("tunnel", "tunnel_main_app", _main_app_tunnel)' in app_src
    assert "Tunnel auto-start skipped:" in app_src
    assert "install_asyncio_exception_handler(loop)" in discord_src

def test_provider_qualified_cloud_defaults_validate_after_refresh(monkeypatch):
    import row_bot.models as models
    import row_bot.providers.codex as codex

    with models._cloud_cache_lock:
        models._cloud_model_cache.clear()
        models._cloud_model_cache["gpt-4.1"] = {"provider": "openai"}

    assert models._cloud_model_available_after_refresh("model:openai:gpt-4.1")

    monkeypatch.setattr(
        codex,
        "list_codex_model_infos",
        lambda: [SimpleNamespace(model_id="gpt-5.5")],
    )

    assert models._cloud_model_available_after_refresh("model:codex:gpt-5.5")
    assert not models._cloud_model_available_after_refresh("model:codex:not-present")


def test_stability_suppresses_benign_windows_proactor_reset():
    import row_bot.stability as stability

    context = {
        "handle": "<Handle _ProactorBasePipeTransport._call_connection_lost()>",
    }
    benign = ConnectionResetError(
        10054,
        "An existing connection was forcibly closed by the remote host",
    )

    assert stability._is_benign_asyncio_connection_reset(
        "Exception in callback _ProactorBasePipeTransport._call_connection_lost()",
        benign,
        context,
    )
    assert not stability._is_benign_asyncio_connection_reset(
        "Exception in callback something_else()",
        ValueError("boom"),
        {},
    )


def test_checkpoint_cleanup_keeps_latest_and_prunes_old(data_dir, monkeypatch):
    import sqlite3
    import row_bot.threads as threads

    threads = importlib.reload(threads)
    monkeypatch.setattr(threads, "DB_PATH", str(data_dir / "threads.db"))
    threads._init_thread_db(raise_on_error=True)

    with sqlite3.connect(threads.DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE checkpoints (thread_id TEXT, checkpoint_ns TEXT, checkpoint_id TEXT)"
        )
        conn.execute(
            "CREATE TABLE writes (thread_id TEXT, checkpoint_ns TEXT, checkpoint_id TEXT, value TEXT)"
        )
        conn.execute(
            "INSERT INTO thread_meta (thread_id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("old-thread", "Old", "2000-01-01T00:00:00", "2000-01-01T00:00:00"),
        )
        for idx in range(5):
            cid = f"cp-{idx}"
            conn.execute("INSERT INTO checkpoints VALUES (?, ?, ?)", ("old-thread", "", cid))
            conn.execute("INSERT INTO writes VALUES (?, ?, ?, ?)", ("old-thread", "", cid, "x"))
        conn.commit()

    stats = threads.cleanup_old_checkpoints(keep_per_thread=2, min_age_minutes=0)

    with sqlite3.connect(threads.DB_PATH) as conn:
        checkpoint_ids = [
            row[0]
            for row in conn.execute(
                "SELECT checkpoint_id FROM checkpoints ORDER BY rowid"
            ).fetchall()
        ]
        write_ids = [
            row[0]
            for row in conn.execute(
                "SELECT checkpoint_id FROM writes ORDER BY rowid"
            ).fetchall()
        ]

    assert stats["checkpoints"] == 3
    assert stats["writes"] == 3
    assert checkpoint_ids == ["cp-3", "cp-4"]
    assert write_ids == ["cp-3", "cp-4"]
