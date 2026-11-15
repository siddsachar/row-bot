"""Claude Code kept up to date: once a day, while a Claude plan is set up, never under a running Claude.

A fake of Anthropic's release location on loopback (``tests.helpers.fake_claude_release``) serves the newest
version, its manifest and a stand-in binary whose ``install`` puts a fake Claude Code where Anthropic's installer
puts the real one. HOME and PATH point at scratch folders, so no real Claude Code is found or run and nothing
reaches Anthropic.
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timedelta, timezone

import pytest

from row_bot.providers import claude_install, claude_subscription, claude_update, config
from row_bot.providers.transports.claude_plan_cli import ClaudeCodeRun
from tests.helpers.fake_claude import install_fake_claude
from tests.helpers.fake_claude_release import FakeClaudeRelease
from tests.subsystem.providers.test_provider_settings_controls import store as store  # noqa: F401

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

BINARY = "claude.exe" if sys.platform == "win32" else "claude"


@pytest.fixture(autouse=True)
def isolated(store, monkeypatch):  # noqa: F811
    """Its own installer, providers.json and a recorded (never started) model-list refresh."""
    from row_bot.providers import model_catalog_cache

    refreshes: list[dict] = []
    monkeypatch.setattr(claude_install, "_installer", None)
    monkeypatch.setattr(model_catalog_cache, "start_model_catalog_refresh_background",
                        lambda **kwargs: refreshes.append(kwargs) or True)
    claude_subscription.forget_claude_plan_status()
    yield refreshes
    claude_subscription.forget_claude_plan_status()


def _installed(tmp_path, monkeypatch, version: str = "2.1.300") -> None:
    """A Claude plan set up with Anthropic's installer: Claude Code ``version``, signed in."""
    with FakeClaudeRelease(tmp_path, monkeypatch, version=version, installed_version=f"{version} (Claude Code)",
                           signed_in=True) as release:
        claude_install.ClaudeCodeInstaller().install()
    assert claude_subscription.claude_plan_status(fresh=True).state == "ready"
    assert release.installs()
    release.install_log.unlink()  # the checks below see only the update's own installs
    claude_subscription.forget_claude_plan_status()


def _version() -> str:
    return claude_subscription.claude_plan_status(fresh=True).version


def _checked_hours_ago(hours: float) -> None:
    def _update(cfg: dict) -> None:
        cfg["providers"]["claude_subscription"]["claude_code_checked_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    config.update_provider_config(_update)


def _idle_program_run(tmp_path) -> ClaudeCodeRun:
    """A live Claude Code run, as a chat turn holds one: it waits for its input."""
    return ClaudeCodeRun([sys.executable, "-c", "import sys; sys.stdin.read()"], env=dict(os.environ),
                         cwd=tmp_path)


def test_no_claude_plan_no_check(tmp_path, monkeypatch, isolated) -> None:
    with FakeClaudeRelease(tmp_path, monkeypatch) as release:
        assert claude_update.update_claude_code() == "no_plan"  # Claude Code isn't installed
        claude_install.ClaudeCodeInstaller().install()  # installed, but not signed in to a Claude plan
        release.requests.clear()
        claude_subscription.forget_claude_plan_status()
        assert claude_subscription.claude_plan_status().state == "signed_out"
        assert claude_update.update_claude_code() == "no_plan"
    assert release.requests == [] and isolated == []


def test_a_newer_claude_code_installs_and_its_models_are_listed_again(tmp_path, monkeypatch, isolated) -> None:
    _installed(tmp_path, monkeypatch, "2.1.300")
    with FakeClaudeRelease(tmp_path, monkeypatch, version="2.1.301", installed_version="2.1.301 (Claude Code)",
                           signed_in=True) as release:
        assert claude_update.update_claude_code() == "updated"
        assert release.requests == ["/latest", "/2.1.301/manifest.json", f"/2.1.301/{release.platform}/{BINARY}"]
        # The binary's own install, with Claude Code's own updater and telemetry off.
        [run] = release.installs()
        assert run["argv"] == ["install", "latest"]
        assert run["env"]["DISABLE_AUTOUPDATER"] == "1"
        assert release.downloads() == []
        assert _version() == "2.1.301"
        # Claude's model list is read again, so the new version's models appear.
        assert isolated == [{"reason": "claude_code_updated", "provider_id": "claude_subscription", "force": True,
                             "follow_up": True}]
        # Once a day: the next look, an hour later or after a restart, asks nobody.
        release.requests.clear()
        assert claude_update.update_claude_code() == "not_due"
        assert release.requests == []


def test_the_same_version_does_nothing_and_the_next_check_is_a_day_later(tmp_path, monkeypatch, isolated) -> None:
    _installed(tmp_path, monkeypatch, "2.1.300")
    with FakeClaudeRelease(tmp_path, monkeypatch, version="2.1.300", signed_in=True) as release:
        assert claude_update.update_claude_code() == "current"
        assert release.requests == ["/latest"]  # nothing downloaded
        assert release.installs() == [] and isolated == []
        _checked_hours_ago(23)
        assert claude_update.update_claude_code() == "not_due"
        _checked_hours_ago(25)
        assert claude_update.update_claude_code() == "current"
        assert release.requests == ["/latest", "/latest"]


def test_a_running_claude_turn_defers_the_update(tmp_path, monkeypatch, isolated) -> None:
    _installed(tmp_path, monkeypatch, "2.1.300")
    with FakeClaudeRelease(tmp_path, monkeypatch, version="2.1.301", installed_version="2.1.301 (Claude Code)",
                           signed_in=True) as release:
        run = _idle_program_run(tmp_path)
        try:
            assert claude_update.update_claude_code() == "busy"
            assert release.requests == [] and _version() == "2.1.300"
        finally:
            run.stop()
        # Not put off to tomorrow: the next hourly look, with Claude free, updates.
        assert claude_update.update_claude_code() == "updated"
    assert _version() == "2.1.301"


@pytest.mark.slow
def test_a_turn_that_starts_during_the_download_keeps_the_old_version_until_it_ends(tmp_path, monkeypatch,
                                                                                  isolated) -> None:
    _installed(tmp_path, monkeypatch, "2.1.300")
    monkeypatch.setattr(claude_update, "IDLE_WAIT_SECONDS", 0.0)
    runs: list[ClaudeCodeRun] = []
    download = claude_install.ClaudeCodeInstaller._download

    def turn_starts_meanwhile(self, *args):
        download(self, *args)
        runs.append(_idle_program_run(tmp_path))

    monkeypatch.setattr(claude_install.ClaudeCodeInstaller, "_download", turn_starts_meanwhile)
    with FakeClaudeRelease(tmp_path, monkeypatch, version="2.1.301", installed_version="2.1.301 (Claude Code)",
                           signed_in=True) as release:
        try:
            assert claude_update.update_claude_code() == "deferred"
        finally:
            runs[0].stop()
        assert release.installs() == [] and release.downloads() == []
        assert _version() == "2.1.300"


def test_no_run_starts_while_claude_code_is_replaced(tmp_path) -> None:
    runs = claude_subscription.claude_code_runs()
    started: list[ClaudeCodeRun] = []
    with runs.replacing(0.0) as free:
        assert free
        waiting = threading.Thread(target=lambda: started.append(_idle_program_run(tmp_path)))
        waiting.start()
        waiting.join(0.2)
        assert waiting.is_alive() and started == []
    waiting.join(10)
    try:
        assert len(started) == 1 and runs.busy()
        with runs.replacing(0.0) as free:  # and nothing is replaced under a live run
            assert not free
    finally:
        started[0].stop()
    assert not runs.busy()


def test_a_checksum_mismatch_keeps_the_installed_version(tmp_path, monkeypatch, isolated, caplog) -> None:
    _installed(tmp_path, monkeypatch, "2.1.300")
    with FakeClaudeRelease(tmp_path, monkeypatch, version="2.1.301", installed_version="2.1.301 (Claude Code)",
                           signed_in=True, checksum="0" * 64) as release:
        with caplog.at_level("INFO", logger="row_bot.providers.claude_update"):
            assert claude_update.update_claude_code() == "failed"
        assert "checksum_mismatch" in caplog.text
        assert release.installs() == [] and release.downloads() == []  # never run, not left behind
        assert _version() == "2.1.300" and isolated == []
        # No retries: the next attempt is the next day.
        release.requests.clear()
        assert claude_update.update_claude_code() == "not_due"
        assert release.requests == []


def test_a_claude_code_installed_another_way_is_left_to_it(tmp_path, monkeypatch, isolated) -> None:
    install_fake_claude(tmp_path, monkeypatch, version="2.1.300 (Claude Code)")  # on PATH, as npm puts it
    assert claude_subscription.claude_plan_status(fresh=True).state == "ready"
    assert claude_update.update_claude_code() == "elsewhere"
    assert claude_update.update_claude_code() == "not_due"


def test_start_up_only_schedules_the_check() -> None:
    class Scheduler:
        jobs: list[dict] = []

        def add_job(self, func, **options) -> None:
            self.jobs.append(options)

    scheduler = Scheduler()
    claude_update.schedule(scheduler)
    [job] = scheduler.jobs
    assert job["id"] == "claude_code_daily_update" and job["trigger"] == "interval"
    assert job["next_run_time"] > datetime.now() + timedelta(minutes=5)
