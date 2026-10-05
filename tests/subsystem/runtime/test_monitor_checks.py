"""Monitor's checks run by themselves and their last results are kept (B252).

Local checks run shortly after start and every 15 minutes; connection checks
(providers, accounts, the internet) run hourly only while "Check connections
every hour" is on. Every result is kept on disk with its time, so Monitor,
Overview and the attention indicator read it after tab switches and restarts.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from row_bot import status_checks
from row_bot.application import client_diagnosis
from row_bot.status_checks import CheckResult

pytestmark = pytest.mark.subsystem

T0 = datetime(2026, 9, 30, 9, 0, 0)
T0_S = T0.timestamp()


class FakeScheduler:
    """Records jobs as APScheduler's BackgroundScheduler would hold them."""

    def __init__(self) -> None:
        self.jobs: dict[str, SimpleNamespace] = {}

    def add_job(self, func, trigger=None, *, id, replace_existing=False, next_run_time=None, **options):
        assert replace_existing
        self.jobs[id] = SimpleNamespace(func=func, trigger=trigger, next_run_time=next_run_time, options=options)

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def remove_job(self, job_id):
        del self.jobs[job_id]


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(client_diagnosis, "_scheduler", None)
    return tmp_path


def _fixed(name: str, status: str, detail: str, at: float, tab: str = "System"):
    def check() -> CheckResult:
        return CheckResult(name, status, detail, checked_at=at, settings_tab=tab)

    check.__name__ = f"check_{name.lower().replace(' ', '_')}"
    return check


def _by_id(health: dict) -> dict[str, dict]:
    return {check["id"]: check for check in health["checks"]}


def test_kept_results_survive_a_restart_and_go_stale_on_their_own_schedule(profile, monkeypatch, reload_for_data_dir):
    monkeypatch.setattr(status_checks, "LOCAL_CHECKS", (
        _fixed("Disk", "ok", "40.0 GB free", T0_S),
        _fixed("Threads DB", "error", "token=fixture-secret-123 C:\\Users\\Fixture\\threads.db is locked", T0_S),
    ))
    monkeypatch.setattr(status_checks, "NETWORK_CHECKS", (
        _fixed("GitHub", "warn", "Token expired", T0_S, "Accounts"),
    ))
    client_diagnosis.run_local_checks()
    client_diagnosis.run_connection_checks()

    fresh = _by_id(client_diagnosis.read_system_health(now=T0_S + 4 * 60))
    assert fresh["disk"] == {
        "id": "disk", "name": "Disk", "status": "ok", "detail": "40.0 GB free",
        "checked_at": T0_S, "settings_tab": "System", "network": False, "stale": False, "fix": None,
    }
    assert fresh["github"]["network"] is True and fresh["github"]["stale"] is False
    # What a check says stays readable; credentials and private paths never do.
    assert fresh["github"]["detail"] == "Token expired"
    kept = (profile / "system_health.json").read_text(encoding="utf-8")
    for text in (json.dumps(fresh), kept):
        assert "fixture-secret-123" not in text and "Fixture\\\\threads.db" not in text
    assert "is locked" in fresh["threads-db"]["detail"]

    # A local result is stale once its 15-minute schedule has missed about
    # two runs; a connection result once the hourly one has.
    later = _by_id(client_diagnosis.read_system_health(now=T0_S + 46 * 60))
    assert later["disk"]["stale"] is True and later["github"]["stale"] is False
    assert _by_id(client_diagnosis.read_system_health(now=T0_S + 2 * 3600 + 60))["github"]["stale"] is True

    # A new process reads the same results back from disk.
    (restarted,) = reload_for_data_dir(profile, "row_bot.application.client_diagnosis")
    assert _by_id(restarted.read_system_health(now=T0_S + 60)) == _by_id(client_diagnosis.read_system_health(now=T0_S + 60))


def test_a_newer_result_replaces_the_kept_one_and_diagnosis_drops_checks_that_are_gone(profile, monkeypatch):
    monkeypatch.setattr(status_checks, "LOCAL_CHECKS", (
        _fixed("Disk", "ok", "40.0 GB free", T0_S), _fixed("Telegram", "warn", "Stopped", T0_S, "Channels"),
    ))
    monkeypatch.setattr(status_checks, "NETWORK_CHECKS", ())
    client_diagnosis.run_local_checks()
    monkeypatch.setattr(status_checks, "LOCAL_CHECKS", (_fixed("Disk", "warn", "3.0 GB free (90% used)", T0_S + 900),))
    client_diagnosis.run_local_checks()
    kept = _by_id(client_diagnosis.read_system_health(now=T0_S + 900))
    assert kept["disk"]["detail"] == "3.0 GB free (90% used)"
    assert "telegram" in kept  # a background run keeps what it didn't check

    health = client_diagnosis.run_system_diagnosis()
    assert [check["id"] for check in health["checks"]] == ["disk"]


def test_local_checks_run_after_start_and_every_15_minutes_without_the_network(profile):
    """The scheduled local set is the real one; the conftest guard fails this
    test if any of those checks connects to anything but loopback."""
    scheduler = FakeScheduler()
    client_diagnosis.schedule_health_checks(scheduler, now=T0)
    job = scheduler.jobs[client_diagnosis.LOCAL_JOB]
    assert job.trigger == "interval" and job.options["minutes"] == 15
    assert T0 < job.next_run_time <= T0 + timedelta(minutes=1)

    job.func()

    health = client_diagnosis.read_system_health()
    ids = {check["id"] for check in health["checks"]}
    assert {"model", "tunnel", "workflows", "knowledge", "dream-cycle", "wiki-vault", "disk",
            "threads-db", "faiss-index", "documents"} <= ids
    assert not ids & {"ollama", "github", "network", "gmail-oauth", "calendar-oauth", "x-oauth"}
    assert all(check["network"] is False and check["stale"] is False for check in health["checks"])


def test_connection_checks_are_scheduled_hourly_only_while_the_switch_is_on(profile, monkeypatch):
    calls = []
    monkeypatch.setattr(status_checks, "NETWORK_CHECKS", (
        lambda: calls.append("network") or CheckResult("Network", "ok", "Connected", checked_at=T0_S - 1800),
    ))
    scheduler = FakeScheduler()
    client_diagnosis.schedule_health_checks(scheduler, now=T0)
    job = scheduler.jobs[client_diagnosis.NETWORK_JOB]
    assert job.trigger == "interval" and job.options["hours"] == 1
    # Nothing checked yet: the first run follows start-up, a few minutes on.
    assert T0 < job.next_run_time <= T0 + timedelta(minutes=10)
    job.func()
    assert calls == ["network"]

    # After a restart the next run is an hour after the newest result.
    client_diagnosis.schedule_health_checks(scheduler, now=T0)
    assert scheduler.jobs[client_diagnosis.NETWORK_JOB].next_run_time == T0 + timedelta(minutes=30)

    health = client_diagnosis.set_hourly_network_checks(False)
    assert health["hourly_network_checks"] is False
    assert client_diagnosis.NETWORK_JOB not in scheduler.jobs
    client_diagnosis.schedule_health_checks(scheduler, now=T0)
    assert client_diagnosis.NETWORK_JOB not in scheduler.jobs
    assert client_diagnosis.LOCAL_JOB in scheduler.jobs
    # A run already queued when the switch went off does nothing.
    job.func()
    assert calls == ["network"]
    assert client_diagnosis.read_system_health()["hourly_network_checks"] is False

    client_diagnosis.set_hourly_network_checks(True)
    assert client_diagnosis.NETWORK_JOB in scheduler.jobs


def test_a_failed_turn_on_the_local_model_checks_it_again_now(profile, monkeypatch):
    monkeypatch.setattr(status_checks, "check_ollama", _fixed("Ollama", "warn", "Local model server unreachable",
                                                              T0_S, "Models"))
    client_diagnosis.recheck_after_failed_turn("model:ollama:fixture")  # before start-up: nothing
    scheduler = FakeScheduler()
    client_diagnosis.schedule_health_checks(scheduler, now=T0)
    client_diagnosis.recheck_after_failed_turn("model:openai:gpt-fixture")
    assert client_diagnosis.MODEL_JOB not in scheduler.jobs

    client_diagnosis.recheck_after_failed_turn("model:ollama:fixture")
    job = scheduler.jobs[client_diagnosis.MODEL_JOB]
    assert job.trigger is None  # once, now
    job.func()
    ollama = _by_id(client_diagnosis.read_system_health(now=T0_S))["ollama"]
    assert ollama["network"] is True and ollama["detail"] == "Local model server unreachable"


def test_account_sign_in_checks_follow_the_switch(profile, monkeypatch):
    # The switch says connection checks stop when it is off; the start-up and
    # 6-hourly sign-in checks (Google, X, GitHub) contact accounts too.
    from row_bot import app as app_module

    calls: list[str] = []
    monkeypatch.setattr(app_module, "_check_oauth_tokens", lambda *_args: calls.append("accounts") or [])
    monkeypatch.setattr(app_module, "_check_github_account_health", lambda *_args: calls.append("github") or [])

    client_diagnosis.set_hourly_network_checks(False)
    app_module._periodic_oauth_check()
    assert calls == []

    client_diagnosis.set_hourly_network_checks(True)
    app_module._periodic_oauth_check()
    assert calls == ["accounts", "github"]


def test_a_channel_started_or_stopped_is_checked_again_now(profile, monkeypatch):
    """Phase 18: after Monitor's Restart (or Settings' Start and Stop) the kept
    channel result and its fix follow at once, not at the next 15-minute run."""
    state = {"status": "warn", "detail": "Stopped"}

    def channels():
        return [CheckResult("Telegram", state["status"], state["detail"], checked_at=T0_S,
                            settings_tab="Channels")]

    monkeypatch.setattr(status_checks, "check_channels", channels)
    monkeypatch.setattr(status_checks, "LOCAL_CHECKS", (channels,))
    client_diagnosis.run_local_checks()
    assert _by_id(client_diagnosis.read_system_health(now=T0_S))["channel:telegram"]["fix"]["kind"] == (
        "restart_channel")

    client_diagnosis.recheck_channels()  # before start-up: nothing to schedule on
    scheduler = FakeScheduler()
    client_diagnosis.schedule_health_checks(scheduler, now=T0)
    state.update(status="ok", detail="Running")
    client_diagnosis.recheck_channels()
    job = scheduler.jobs[client_diagnosis.CHANNELS_JOB]
    assert job.trigger is None  # once, now
    job.func()

    telegram = _by_id(client_diagnosis.read_system_health(now=T0_S))["channel:telegram"]
    assert (telegram["status"], telegram["detail"], telegram["fix"]) == ("ok", "Running", None)


def test_each_kept_warning_or_error_carries_its_one_fix(profile, monkeypatch):
    """Phase 18: Monitor, Overview and the attention list offer one fix per
    problem: restart a stopped channel, renew an account sign-in, choose a
    model, open the exact setting, or check again once it is fixed outside."""
    def channels():
        return [
            CheckResult("Telegram", "warn", "Stopped", checked_at=T0_S, settings_tab="Channels"),
            CheckResult("Slack", "error", "invalid_auth", checked_at=T0_S, settings_tab="Channels"),
            CheckResult("Discord", "ok", "Running", checked_at=T0_S, settings_tab="Channels"),
        ]

    monkeypatch.setattr(status_checks, "check_channels", channels)
    monkeypatch.setattr(status_checks, "LOCAL_CHECKS", (
        channels,
        _fixed("Model", "warn", "No model selected", T0_S, "Models"),
        _fixed("Tunnel", "error", "Not running: agent failed", T0_S, "Access"),
        _fixed("Wiki Vault", "warn", "3 articles need review", T0_S, "Knowledge"),
        _fixed("Disk", "error", "1.2 GB free (97% used)", T0_S),
        _fixed("Tools", "ok", "12 / 14 enabled", T0_S, "Tools"),
    ))
    monkeypatch.setattr(status_checks, "NETWORK_CHECKS", (
        _fixed("Gmail OAuth", "warn", "Token expired", T0_S, "Accounts"),
        _fixed("Ollama", "error", "Local model server unreachable", T0_S, "Models"),
    ))

    fixes = {check["id"]: check["fix"] for check in client_diagnosis.run_system_diagnosis()["checks"]}

    assert fixes == {
        "channel:telegram": {"kind": "restart_channel", "href": "/settings/apps/telegram",
                             "target": "telegram", "name": "Telegram"},
        "channel:slack": {"kind": "open", "href": "/settings/apps/slack", "target": None, "name": "Slack"},
        "channel:discord": None,
        "model": {"kind": "choose_model", "href": "/settings/models#default-model", "target": None,
                  "name": "Default model"},
        "tunnel": {"kind": "open", "href": "/settings/access#tunnel", "target": None, "name": "Public link"},
        "wiki-vault": {"kind": "open", "href": "/settings/knowledge#wiki-vault", "target": None,
                       "name": "Wiki vault"},
        "disk": {"kind": "check_again", "href": None, "target": None, "name": "Disk"},
        "tools": None,
        "gmail-oauth": {"kind": "reconnect_account", "href": "/settings/apps/google", "target": "google",
                        "name": "Google"},
        "ollama": {"kind": "check_again", "href": "/settings/providers", "target": None, "name": "Ollama"},
    }
