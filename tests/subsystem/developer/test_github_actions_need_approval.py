from __future__ import annotations

import subprocess


def _fresh_modules(tmp_path, reload_for_data_dir):

    return reload_for_data_dir(tmp_path / "data", "row_bot.developer.github")[0]


def test_developer_gh_status_handles_missing_cli(tmp_path, monkeypatch, reload_for_data_dir):
    github = _fresh_modules(tmp_path, reload_for_data_dir)
    monkeypatch.setattr(github, "resolve_github_cli", lambda: "")

    status = github.get_gh_status()

    assert status.installed is False
    assert status.authenticated is False
    assert "not installed" in status.message


def test_developer_push_requires_approval_until_user_confirms(tmp_path, monkeypatch, reload_for_data_dir):
    github = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    repo.mkdir()
    calls: list[list[str]] = []

    def fake_run(args, *, cwd=None, timeout=20):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="pushed", stderr="")

    monkeypatch.setattr(github, "_run", fake_run)

    blocked = github.push_current_branch(str(repo), "approve")
    allowed = github.push_current_branch(str(repo), "approve", confirmed=True)

    assert blocked.ran is False
    assert blocked.decision.decision == "ask"
    assert allowed.ran is True
    assert allowed.ok is True
    assert calls == [["git", "push", "-u", "origin", "HEAD"]]


def test_developer_pr_uses_gh_cli_after_confirmed_approval(tmp_path, monkeypatch, reload_for_data_dir):
    github = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(github, "resolve_github_cli", lambda: "gh")

    def fake_run(args, *, cwd=None, timeout=20):
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="https://github.com/example/repo/pull/12\n",
            stderr="",
        )

    monkeypatch.setattr(github, "_run", fake_run)

    result = github.create_pull_request(str(repo), "approve", title="Feature", body="Ready", confirmed=True)

    assert result.ran is True
    assert result.ok is True
    assert result.url == "https://github.com/example/repo/pull/12"


def test_developer_pr_is_blocked_in_block_even_when_clicked(tmp_path, monkeypatch, reload_for_data_dir):
    github = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(github, "resolve_github_cli", lambda: "gh")

    result = github.create_pull_request(str(repo), "block", confirmed=True)

    assert result.ran is False
    assert result.decision.decision == "block"
