"""Claude Code on request (H14): Anthropic's installer steps, done by Row-Bot only when the person asks.

A fake of Anthropic's release location on loopback (``tests.helpers.fake_claude_release``) serves the newest
version, a manifest with checksums and a stand-in binary whose ``install`` puts a fake Claude Code where the
native installer puts the real one. HOME and PATH point at scratch folders, so no real Claude Code is found or
run and nothing reaches Anthropic.
"""

from __future__ import annotations

import httpx
import pytest

from row_bot.providers import claude_install, claude_subscription
from tests.helpers.fake_claude_release import FakeClaudeRelease

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


def _install(release: FakeClaudeRelease) -> claude_install.InstallState:
    installer = claude_install.ClaudeCodeInstaller()
    installer.start()
    return installer.wait(60)


def test_installs_the_newest_claude_code_checked_against_anthropics_checksum(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-row-bot-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-the-plan")
    with FakeClaudeRelease(tmp_path, monkeypatch) as release:
        assert claude_subscription.find_claude_code() == ""  # a scratch home: nothing installed yet
        state = _install(release)

    assert state.state == "done" and state.reason == ""
    assert state.version == "2.1.300" and state.received == state.total == len(release.binary)
    # Only Anthropic's release location (here, its fake): the version, the manifest, the binary.
    assert release.requests == ["/latest", "/2.1.300/manifest.json",
                                f"/2.1.300/{release.platform}/{'claude.exe' if release.windows else 'claude'}"]
    # The verified download ran its own install, as Anthropic's installer does, without Row-Bot's secrets and
    # with Claude Code's telemetry and automatic updates off.
    [run] = release.installs()
    assert run["argv"] == ["install", "latest"]
    assert "OPENAI_API_KEY" not in run["env"] and "ANTHROPIC_API_KEY" not in run["env"]
    assert run["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    # Claude Code now lives where it always does, and Row-Bot finds and asks it; the download is gone.
    assert claude_subscription.find_claude_code() == str(release.installed_program())
    assert claude_subscription.claude_plan_status().state == "signed_out"
    assert release.downloads() == []


def test_update_reinstalls_the_newest(tmp_path, monkeypatch) -> None:
    with FakeClaudeRelease(tmp_path, monkeypatch) as release:
        assert _install(release).version == "2.1.300"
    with FakeClaudeRelease(tmp_path, monkeypatch, version="2.1.301",
                           installed_version="2.1.301 (Claude Code)") as release:
        state = _install(release)
    assert state.state == "done" and state.version == "2.1.301"
    assert claude_subscription.claude_plan_status(fresh=True).version == "2.1.301"


def test_a_newest_release_older_than_row_bots_minimum_is_not_installed(tmp_path, monkeypatch) -> None:
    with FakeClaudeRelease(tmp_path, monkeypatch, version="2.1.100") as release:
        state = _install(release)
    assert (state.state, state.reason) == ("failed", "download_failed")
    assert release.requests == ["/latest"] and release.installs() == []


def test_a_download_that_does_not_match_the_checksum_is_deleted_and_never_run(tmp_path, monkeypatch) -> None:
    with FakeClaudeRelease(tmp_path, monkeypatch, checksum="0" * 64) as release:
        state = _install(release)

    assert (state.state, state.reason) == ("failed", "checksum_mismatch")
    assert release.installs() == []  # never executed
    assert release.downloads() == []  # and not left behind
    assert claude_subscription.find_claude_code() == ""


@pytest.mark.parametrize(("change", "reason"), [
    ({"platforms": ["plan9-x64"]}, "unsupported_platform"),
    ({"latest_status": 503}, "download_failed"),
    ({"binary_status": 404}, "download_failed"),
    ({"install_exit": 3}, "install_failed"),
])
def test_install_failures_have_a_plain_reason(tmp_path, monkeypatch, change, reason) -> None:
    with FakeClaudeRelease(tmp_path, monkeypatch, **change) as release:
        state = _install(release)
    assert (state.state, state.reason) == ("failed", reason)
    assert release.downloads() == []
    assert claude_subscription.find_claude_code() == ""


def test_no_internet_says_so(tmp_path, monkeypatch) -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    with FakeClaudeRelease(tmp_path, monkeypatch) as release:
        installer = claude_install.ClaudeCodeInstaller(
            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(unreachable)))
        installer.start()
        state = installer.wait(60)
    assert (state.state, state.reason) == ("failed", "no_internet")
    assert release.downloads() == [] and release.installs() == []


def test_a_full_disk_says_so_before_downloading(tmp_path, monkeypatch) -> None:
    with FakeClaudeRelease(tmp_path, monkeypatch) as release:
        monkeypatch.setattr(claude_install.shutil, "disk_usage",
                            lambda _path: type("Usage", (), {"free": 1024})())
        state = _install(release)
    assert (state.state, state.reason) == ("failed", "disk_full")
    assert release.requests[-1].endswith("manifest.json")  # it never started the download
    assert release.downloads() == []


def test_one_install_at_a_time(tmp_path, monkeypatch) -> None:
    import threading

    gate = threading.Event()
    with FakeClaudeRelease(tmp_path, monkeypatch) as release:
        installer = claude_install.ClaudeCodeInstaller()
        original = installer._latest

        def slow_latest(*args):
            gate.wait(10)
            return original(*args)

        monkeypatch.setattr(installer, "_latest", slow_latest)
        first = installer.start()
        second = installer.start()
        assert first.state == second.state == "running" and installer.running()
        gate.set()
        assert installer.wait(60).state == "done"
    assert release.requests.count("/latest") == 1
