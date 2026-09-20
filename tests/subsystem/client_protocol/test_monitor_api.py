"""Passive Monitor projections and explicitly reviewed Dream execution."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from row_bot.application import client_monitor
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem


def _monitor_store(tmp_path, monkeypatch):
    root = tmp_path / "monitor-data"
    root.mkdir()
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(root))
    (root / "memory_extraction_state.json").write_text(
        json.dumps({"last_extraction": "2026-09-20T10:00:00Z", "threads_scanned": 4}),
        encoding="utf-8",
    )
    (root / "extraction_journal.json").write_text(
        json.dumps([{"timestamp": "now", "summary": "Saved 2 memories"}]),
        encoding="utf-8",
    )
    (root / "dream_config.json").write_text(
        json.dumps({"enabled": True, "window_start": 1, "window_end": 5}),
        encoding="utf-8",
    )
    (root / "dream_journal.json").write_text(
        json.dumps([{"timestamp": "then", "summary": "Connected memories"}]),
        encoding="utf-8",
    )
    logs = root / "logs"
    logs.mkdir()
    (logs / "row_bot.log").write_text(
        json.dumps(
            {
                "ts": "now",
                "level": "INFO",
                "logger": "row_bot.fixture",
                "msg": "token=private C:\\Users\\Fixture\\secret.txt",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return root


def test_monitor_snapshot_is_passive_bounded_and_redacts_local_logs(
    tmp_path, monkeypatch
):
    root = _monitor_store(tmp_path, monkeypatch)
    snapshot = client_monitor.read_monitor_snapshot(include_logs=True)
    assert snapshot["extraction"]["threads_scanned"] == 4
    assert snapshot["dream"]["last_summary"] == "Connected memories"
    assert snapshot["logs"]["authorized"] is True
    encoded = json.dumps(snapshot)
    assert "token=private" not in encoded and "Users\\\\Fixture" not in encoded
    assert root.exists()


def test_monitor_api_separates_remote_log_authority(tmp_path, monkeypatch):
    _monitor_store(tmp_path, monkeypatch)
    local, _, _ = client_app()
    remote, _, _ = client_app(remote=True)
    with local, remote:
        _, local_headers = bootstrap(local)
        _, remote_headers = bootstrap(remote)
        local_snapshot = local.get("/api/v1/monitor", headers=local_headers)
        remote_snapshot = remote.get("/api/v1/monitor", headers=remote_headers)
        denied = remote.get("/api/v1/monitor/logs", headers=remote_headers)
    assert local_snapshot.status_code == remote_snapshot.status_code == 200
    assert local_snapshot.json()["logs"]["authorized"] is True
    assert remote_snapshot.json()["logs"]["authorized"] is False
    assert denied.status_code == 403


def test_dream_run_requires_current_review_and_is_idempotent(tmp_path, monkeypatch):
    _monitor_store(tmp_path, monkeypatch)
    calls = []

    def run():
        calls.append("run")
        return {"summary": "Dream complete", "merges": [{}], "errors": []}

    import row_bot.dream_cycle as dream_cycle

    monkeypatch.setattr(dream_cycle, "run_dream_cycle", run)
    client, _, _ = client_app()
    with client:
        handshake, headers = bootstrap(client)
        snapshot = client.get("/api/v1/monitor", headers=headers).json()
        review = client.post(
            "/api/v1/monitor/dream/review",
            headers=headers,
            json={"snapshot_revision": snapshot["dream_revision"]},
        )
        assert review.status_code == 200, review.text
        reviewed = review.json()
        command_id = str(uuid4())
        command = {
            "command_id": command_id,
            "client_session_id": handshake["client_session_id"],
            "type": "dream.run",
            "payload": {
                "snapshot_revision": reviewed["snapshot_revision"],
                "action_digest": reviewed["action_digest"],
                "review_id": reviewed["review_id"],
            },
        }
        command_headers = {**headers, "Idempotency-Key": command_id}
        first = client.post(
            "/api/v1/monitor/dream/commands", headers=command_headers, json=command
        )
        second = client.post(
            "/api/v1/monitor/dream/commands", headers=command_headers, json=command
        )
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["status"] == "completed"
    assert calls == ["run"]
