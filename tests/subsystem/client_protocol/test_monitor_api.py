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


def test_every_signed_in_device_reads_the_redacted_logs(tmp_path, monkeypatch):
    """One owner: a paired phone or a server session reads what this computer does."""
    _monitor_store(tmp_path, monkeypatch)
    local, _, _ = client_app()
    remote, _, active = client_app(remote=True)
    with local, remote:
        _, local_headers = bootstrap(local)
        _, remote_headers = bootstrap(remote)
        local_snapshot = local.get("/api/v1/monitor", headers=local_headers)
        remote_snapshot = remote.get("/api/v1/monitor", headers=remote_headers)
        logs = remote.get("/api/v1/monitor/logs", headers=remote_headers)
        active["value"] = False
        signed_out = remote.get("/api/v1/monitor/logs", headers=remote_headers)
    assert local_snapshot.status_code == remote_snapshot.status_code == 200
    assert local_snapshot.json()["logs"]["authorized"] is True
    assert remote_snapshot.json()["logs"]["authorized"] is True
    assert logs.status_code == 200, logs.text
    assert "token=private" not in logs.text and "Users\\\\Fixture" not in logs.text
    assert signed_out.status_code == 401


def test_system_diagnosis_runs_only_on_an_explicit_request_from_any_device(tmp_path, monkeypatch):
    """Reading the kept results runs nothing; running every check (it contacts
    providers and the internet) takes an explicit request, from any of the
    owner's signed-in devices, never from a signed-out one."""
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
    remote, _, remote_active = client_app(remote=True)
    with local, remote:
        _, local_headers = bootstrap(local)
        _, remote_headers = bootstrap(remote)
        empty = {"schema_version": 1, "hourly_network_checks": True, "checks": []}
        assert local.get("/api/v1/monitor/diagnosis", headers=local_headers).json() == empty
        assert calls == []
        result = remote.post("/api/v1/monitor/diagnosis", headers=remote_headers)
        assert result.status_code == 200, result.text
        kept = local.get("/api/v1/monitor/diagnosis", headers=local_headers)
        signed_out_calls = list(calls)
        remote_active["value"] = False
        assert remote.post("/api/v1/monitor/diagnosis", headers=remote_headers).status_code == 401
        assert calls == signed_out_calls
    assert result.json() == {"schema_version": 1, "hourly_network_checks": True, "checks": [
        {"id": "ollama", "name": "Ollama", "status": "warn", "detail": "Server offline",
         "checked_at": 1.0, "settings_tab": "Models", "network": True, "stale": True,
         "fix": {"kind": "check_again", "href": "/settings/providers", "target": None, "name": "Ollama"}},
        {"id": "disk", "name": "Disk", "status": "ok", "detail": "40.0 GB free",
         "checked_at": 1.0, "settings_tab": "System", "network": False, "stale": True, "fix": None},
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
    for name in ("_channel_problems", "_tunnel_problems", "_plugin_problems", "_app_problems",
                 "_mcp_problems", "_workflow_problems"):
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
        "fix": {"kind": "check_again", "href": None, "target": None, "name": "Disk"},
    }]


def test_each_attention_problem_offers_its_one_fix(tmp_path, monkeypatch):
    """Phase 18: a channel set to start that stopped offers a restart; one
    waiting for a scan opens its connect sheet; the tunnel, plugins and MCP
    open their exact settings. Nothing is probed or started by the read."""
    from types import SimpleNamespace

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    telegram = SimpleNamespace(name="telegram", display_name="Telegram",
                               is_configured=lambda: True, is_running=lambda: False)
    whatsapp = SimpleNamespace(name="whatsapp", display_name="WhatsApp", is_configured=lambda: True,
                               is_running=lambda: False, link_status=lambda: {"state": "scan"})
    tunnel = SimpleNamespace(tunnel_manager=SimpleNamespace(status=lambda: ("error", "agent failed")))
    mcp = SimpleNamespace(get_status_summary=lambda: {
        "enabled": True, "enabled_server_count": 2, "connected_server_count": 1,
        "servers": {"files": {"enabled": True, "status": "connected"},
                    "notes": {"enabled": True, "status": "failed"}}})
    monkeypatch.setattr(client_monitor, "_MODULE", {
        "row_bot.channels.registry": SimpleNamespace(all_channels=lambda: [telegram, whatsapp]),
        "row_bot.channels.config": SimpleNamespace(get=lambda _name, _key, _default=None: True),
        "row_bot.tunnel": tunnel,
        "row_bot.mcp_client.runtime": mcp,
    })

    problems = client_monitor.read_attention(include_update=False)["problems"]

    assert [(problem["id"], problem["fix"]) for problem in problems] == [
        ("channel:telegram", {"kind": "restart_channel", "href": "/settings/apps/telegram",
                              "target": "telegram", "name": "Telegram"}),
        ("channel:whatsapp", {"kind": "open", "href": "/settings/apps/whatsapp",
                              "target": None, "name": "WhatsApp"}),
        ("tunnel", {"kind": "open", "href": "/settings/access#tunnel", "target": None, "name": "Public link"}),
        ("mcp", {"kind": "open", "href": "/settings/apps", "target": None, "name": "Apps"}),
    ]


def test_a_running_sandbox_without_a_model_needs_the_person_until_it_has_one(tmp_path, monkeypatch):
    """W12: a sandbox is up but its chats can't answer until the person signs it in once."""
    from types import SimpleNamespace

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    models = {"tea": False, "old": False}
    sandboxes = SimpleNamespace(
        list_sandboxes=lambda: [SimpleNamespace(sandbox_id="tea", name="Tea shop sandbox", status="running"),
                                SimpleNamespace(sandbox_id="old", name="Old demo", status="stopped")],
        has_model=lambda sandbox_id: models[sandbox_id])
    monkeypatch.setattr(client_monitor, "_MODULE", {"row_bot.sandboxes": sandboxes})

    [problem] = client_monitor.read_attention(include_update=False)["problems"]
    assert (problem["id"], problem["title"]) == ("sandbox:tea", "Tea shop sandbox needs a model")
    assert problem["fix"] == {"kind": "open", "href": "/settings/data#sandboxes", "target": None,
                              "name": "Sandboxes"}

    models["tea"] = True
    assert client_monitor.read_attention(include_update=False)["problems"] == []


def test_apps_still_starting_or_connecting_are_not_a_problem(tmp_path, monkeypatch):
    """B400: just after Row-Bot starts, every app is still starting or connecting. That isn't "An app isn't
    connected": only an app that tried and failed is."""
    from types import SimpleNamespace

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    statuses = {"files": "not_started", "notes": "connecting", "issues": "connected"}

    def summary():
        return {"enabled": True, "enabled_server_count": 3,
                "connected_server_count": list(statuses.values()).count("connected"),
                "servers": {name: {"enabled": True, "status": state} for name, state in statuses.items()}}

    monkeypatch.setattr(client_monitor, "_MODULE", {"row_bot.mcp_client.runtime": SimpleNamespace(
        get_status_summary=summary)})

    assert client_monitor.read_attention(include_update=False)["problems"] == []

    statuses.update(files="failed", notes="dependency_missing")
    problems = client_monitor.read_attention(include_update=False)["problems"]
    assert [(p["id"], p["title"], p["detail"]) for p in problems] == [
        ("mcp", "2 apps aren't connected", "2 of 3 apps that are on couldn't connect.")]


def test_failed_workflows_and_apps_that_need_you_are_in_the_one_attention_list(tmp_path, monkeypatch):
    """Home's Needs you and the sidebar count one list (B383): a workflow whose
    last run failed (not one running again or that succeeded) and an app that
    is on but signed out or missing its key, each with where to fix it. The
    apps by name replace "An app isn't connected"; channels keep their own
    reader; an app that is off needs nothing."""
    from types import SimpleNamespace

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    tasks = SimpleNamespace(iter_task_summary_snapshot=lambda: iter([
        {"id": "a1b2c3", "name": "Morning report", "last_status": "failed", "active_run_json": None},
        {"id": "d4e5f6", "name": "Stock check", "last_status": "failed",
         "active_run_json": '{"status": "running"}'},
        {"id": "0718ab", "name": "Weekly digest", "last_status": "completed", "active_run_json": None},
    ]))

    def app(item_id, name, readiness, lifecycle="installed", **extra):
        return {"id": item_id, "name": name, "readiness": readiness, "lifecycle": lifecycle,
                "app": None, "blockers": [], "children": [], **extra}

    facts = SimpleNamespace(inventory=lambda: ([
        app("mcp:linear", "Linear", "needs_sign_in",
            blockers=[{"message": "Sign in again to keep using Linear."}]),
        app("mcp:notion", "Notion", "needs_key", lifecycle="off"),
        app("mcp:files", "Files", "ready"),
    ], []))
    builtin = SimpleNamespace(rows=lambda wait=True: [
        app("builtin:google", "Google", "needs_key", app={"name": "Gmail"}, owner_ref="google"),
        app("builtin:whatsapp", "WhatsApp", "attention", owner_ref="channel:whatsapp"),
    ])
    mcp = SimpleNamespace(get_status_summary=lambda: {
        "enabled": True, "enabled_server_count": 2, "connected_server_count": 1,
        "servers": {"files": {"enabled": True, "status": "connected"},
                    "notes": {"enabled": True, "status": "failed"}}})
    monkeypatch.setattr(client_monitor, "_MODULE", {
        "row_bot.tasks": tasks, "row_bot.integrations.facts": facts,
        "row_bot.integrations.builtin": builtin, "row_bot.mcp_client.runtime": mcp,
    })
    monkeypatch.setattr(client_monitor, "_health_problems", lambda: [])

    problems = client_monitor.read_attention(include_update=False)["problems"]

    assert [(p["id"], p["title"], p["detail"], p["place"], p["fix"]["href"]) for p in problems] == [
        ("app:mcp:linear", "Sign in to Linear", "Sign in again to keep using Linear.", "mcp",
         "/settings/apps/item?id=mcp:linear&fix=1"),
        ("app:builtin:google", "Gmail needs its key", "It can't be used until this is fixed.", "mcp",
         "/settings/apps/item?id=builtin:google&fix=1"),
        ("workflow:a1b2c3", "Morning report failed", "Run it again, or open it to see why.", "workflows",
         "/?tab=workflows&workflow=a1b2c3"),
    ]


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
                 "_app_problems", "_mcp_problems", "_workflow_problems"):
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
    assert value["update"] == {"version": "9.1.0", "ready": False}
    # Without its update (for the projection's own callers).
    assert client_monitor.read_attention(include_update=False)["update"] is None

    local, _, _ = client_app()
    remote, _, _ = client_app(remote=True)
    with local, remote:
        _, headers = bootstrap(local)
        response = local.get("/api/v1/monitor/attention", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["problems"][0]["title"] == "Telegram stopped"
        # Every signed-in device hears about the update: any of them can install it.
        _, remote_headers = bootstrap(remote)
        elsewhere = remote.get("/api/v1/monitor/attention", headers=remote_headers)
        assert elsewhere.status_code == 200, elsewhere.text
        assert elsewhere.json()["update"] == {"version": "9.1.0", "ready": False}
