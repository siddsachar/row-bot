"""The guarded Settings capture must not inspect host GitHub CLI auth."""

from __future__ import annotations

from row_bot.github_account import GitHubAccountStatus
from row_bot.ui.settings import _github_status_for_settings


def test_docs_capture_uses_passive_github_status_without_cli_or_network(monkeypatch):
    import row_bot.github_account as github_account

    monkeypatch.setenv("ROW_BOT_DOCS_CAPTURE", "1")
    expected = GitHubAccountStatus(connected=False, message="Synthetic passive status")
    calls: list[str] = []

    def passive() -> GitHubAccountStatus:
        calls.append("passive")
        return expected

    def forbidden(*_args, **_kwargs):
        raise AssertionError("capture must not verify a host account or clear caches")

    monkeypatch.setattr(github_account, "get_passive_github_account_status", passive)
    monkeypatch.setattr(github_account, "get_verified_github_account_status", forbidden)
    monkeypatch.setattr(github_account, "clear_github_caches", forbidden)

    assert _github_status_for_settings(force=True) is expected
    assert calls == ["passive"]


def test_normal_settings_can_explicitly_refresh_github_status(monkeypatch):
    import row_bot.github_account as github_account

    monkeypatch.delenv("ROW_BOT_DOCS_CAPTURE", raising=False)
    expected = GitHubAccountStatus(connected=False, message="Synthetic verified status")
    calls: list[str] = []
    monkeypatch.setattr(github_account, "clear_github_caches", lambda: calls.append("clear"))

    def verified(*, use_cache: bool) -> GitHubAccountStatus:
        calls.append(f"verify:{use_cache}")
        return expected

    monkeypatch.setattr(github_account, "get_verified_github_account_status", verified)

    assert _github_status_for_settings(force=True) is expected
    assert calls == ["clear", "verify:False"]
