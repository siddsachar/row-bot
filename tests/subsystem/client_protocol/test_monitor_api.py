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


def test_system_diagnosis_requires_local_explicit_request(tmp_path, monkeypatch):
    """Reading the kept results runs nothing, on any device; running every
    check (it contacts providers and the internet) is the local owner's."""
    _monitor_store(tmp_path, monkeypatch)
    from row_bot import status_checks
    from row_bot.status_checks import CheckResult

    calls = []

    def fake_ollama():
        calls.append("ollama")
        return CheckResult("Ollama", "warn", "Server offline", checked_at=1.0, settings_tab="Models")

    def fake_disk():
        calls.append("disk")
        return CheckResult("Disk", "ok", "40.0 GB free", checked_at=1.0, settings_tab="System")

    monkeypatch.setattr(status_checks, "NETWORK_CHECKS", (fake_ollama,))
    monkeypatch.setattr(status_checks, "LOCAL_CHECKS", (fake_disk,))
    local, _, _ = client_app()
    remote, _, _ = client_app(remote=True)
    with local, remote:
        _, local_headers = bootstrap(local)
        _, remote_headers = bootstrap(remote)
        empty = {"schema_version": 1, "hourly_network_checks": True, "checks": []}
        assert local.get("/api/v1/monitor/diagnosis", headers=local_headers).json() == empty
        assert calls == []
        assert remote.post("/api/v1/monitor/diagnosis", headers=remote_headers).status_code == 403
        assert calls == []
        result = local.post("/api/v1/monitor/diagnosis", headers=local_headers)
        assert result.status_code == 200, result.text
        kept = remote.get("/api/v1/monitor/diagnosis", headers=remote_headers)
    assert result.json() == {"schema_version": 1, "hourly_network_checks": True, "checks": [
        {"id": "ollama", "name": "Ollama", "status": "warn", "detail": "Server offline",
         "checked_at": 1.0, "settings_tab": "Models", "network": True, "stale": True},
        {"id": "disk", "name": "Disk", "status": "ok", "detail": "40.0 GB free",
         "checked_at": 1.0, "settings_tab": "System", "network": False, "stale": True},
    ]}
    assert sorted(calls) == ["disk", "ollama"]
    assert kept.status_code == 200 and kept.json() == result.json()


def test_the_hourly_connection_switch_is_saved_and_read_back(tmp_path, monkeypatch):
    _monitor_store(tmp_path, monkeypatch)
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        saved = client.post("/api/v1/monitor/diagnosis/settings", headers=headers,
                            json={"hourly_network_checks": False})
        assert saved.status_code == 200, saved.text
        assert saved.json()["hourly_network_checks"] is False
        read = client.get("/api/v1/monitor/diagnosis", headers=headers).json()
        assert read["hourly_network_checks"] is False
        invalid = client.post("/api/v1/monitor/diagnosis/settings", headers=headers,
                              json={"hourly_network_checks": "no"})
    assert invalid.status_code == 422


def test_a_red_check_raises_the_attention_indicator_once(tmp_path, monkeypatch):
    """A kept check whose last result is an error needs the person (B252);
    the tunnel, channels, MCP and plugins keep their own finer readers."""
    from row_bot import status_checks
    from row_bot.application import client_diagnosis
    from row_bot.status_checks import CheckResult

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    for name in ("_channel_problems", "_tunnel_problems", "_plugin_problems", "_mcp_problems"):
        monkeypatch.setattr(client_monitor, name, lambda: [])
    monkeypatch.setattr(status_checks, "LOCAL_CHECKS", (
        lambda: CheckResult("Disk", "error", "1.2 GB free (97% used)", settings_tab="System"),
        lambda: CheckResult("Tunnel", "error", "Not running: agent failed", settings_tab="Access"),
        lambda: CheckResult("Documents", "warn", "rebuild recommended", settings_tab="Documents"),
    ))
    client_diagnosis.run_local_checks()
    assert client_monitor.read_attention(include_update=False)["problems"] == [{
        "id": "health:disk", "title": "Disk needs attention",
        "detail": "1.2 GB free (97% used)", "place": "health",
    }]


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


def test_attention_is_quiet_when_healthy_and_names_what_needs_you(tmp_path, monkeypatch):
    """Parity rows 12 and 13: one sidebar indicator for problems (to Monitor)
    and an update (to Updates), quiet when everything is healthy. The read is
    passive: it never probes a service or starts anything."""
    from types import SimpleNamespace

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    for name in ("_channel_problems", "_tunnel_problems", "_plugin_problems",
                 "_mcp_problems"):
        monkeypatch.setattr(client_monitor, name, lambda: [])
    monkeypatch.setattr(client_monitor, "_available_update", lambda: None)
    assert client_monitor.read_attention(include_update=True) == {
        "schema_version": 1, "problems": [], "update": None}

    monkeypatch.setattr(client_monitor, "_channel_problems", lambda: [{
        "id": "channel:telegram", "title": "Telegram stopped",
        "detail": "It is set to start with Row-Bot but isn't running.", "place": "channels"}])
    monkeypatch.setattr(client_monitor, "_available_update", lambda: SimpleNamespace(version="9.1.0"))
    value = client_monitor.read_attention(include_update=True)
    assert [problem["id"] for problem in value["problems"]] == ["channel:telegram"]
    assert value["update"] == {"version": "9.1.0"}
    # Another device reads the problems, never the update (it can't install).
    assert client_monitor.read_attention(include_update=False)["update"] is None

    local, _, _ = client_app()
    with local:
        _, headers = bootstrap(local)
        response = local.get("/api/v1/monitor/attention", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["problems"][0]["title"] == "Telegram stopped"
