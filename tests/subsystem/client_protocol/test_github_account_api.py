"""Account health stays passive until an explicit local-owner action."""

from __future__ import annotations

from uuid import uuid4

import pytest

from row_bot import github_account
from row_bot.application import client_accounts
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem


def _passive() -> github_account.GitHubAccountStatus:
    return github_account.GitHubAccountStatus(
        connected=False, source="keyring", fingerprint="fixture-fingerprint",
        state="configured_unchecked",
    )


def test_github_access_is_passive_and_check_is_explicit_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    checks = []
    monkeypatch.setattr(github_account, "shared_github_status", _passive)
    monkeypatch.setattr(client_accounts, "resolve_github_cli", lambda: "")

    def check():
        checks.append("check")
        return github_account.GitHubAccountStatus(
            connected=True, source="keyring", fingerprint="fixture-fingerprint",
            state="connected", authenticated=True, token_valid=True,
        )

    monkeypatch.setattr(github_account, "check_github_access", check)
    local, _, _ = client_app()
    with local:
        _, headers = bootstrap(local)
        loaded = local.get("/api/v1/accounts/github/access", headers=headers)
        assert loaded.status_code == 200, loaded.text
        assert loaded.json()["state"] == "configured_unchecked"
        assert checks == []
        command_id = str(uuid4())
        body = {"command_id": command_id, "expected_revision": loaded.json()["revision"], "action": "check"}
        result = local.post("/api/v1/accounts/github/access/commands", headers={**headers, "idempotency-key": command_id}, json=body)
        assert result.status_code == 200, result.text
        assert result.json()["snapshot"]["connected"] is True
        assert result.json()["snapshot"]["credential_source"] == "keyring"
        assert checks == ["check"]
        repeated = local.post("/api/v1/accounts/github/access/commands", headers={**headers, "idempotency-key": command_id}, json=body)
        assert repeated.json() == result.json()
        assert checks == ["check"]
        recovered = local.get(f"/api/v1/accounts/github/access/commands/{command_id}", headers=headers)
        assert recovered.json() == result.json()
        next_id = str(uuid4())
        stale = local.post("/api/v1/accounts/github/access/commands", headers={**headers, "idempotency-key": next_id}, json={**body, "command_id": next_id})
        assert stale.status_code == 409


def test_github_cli_action_starts_only_on_click_and_does_not_claim_login(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    monkeypatch.setattr(github_account, "shared_github_status", _passive)
    monkeypatch.setattr(client_accounts, "resolve_github_cli", lambda: "fixture-gh")
    starts = []
    monkeypatch.setattr(client_accounts, "_start_cli", lambda mode: starts.append(mode))
    snapshot = client_accounts.read_github_access(owner_id="owner-fixture")
    assert starts == []
    receipt = client_accounts.execute_github_access(
        owner_id="owner-fixture", command_id=str(uuid4()),
        expected_revision=snapshot["revision"], action="cli_login",
    )
    assert receipt["phase"] == "started"
    assert receipt["snapshot"]["connected"] is False
    assert starts == ["login"]


def test_github_access_api_denies_remote_before_account_probe(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    monkeypatch.setattr(github_account, "shared_github_status", _passive)
    monkeypatch.setattr(client_accounts, "resolve_github_cli", lambda: "")
    calls = []
    monkeypatch.setattr(github_account, "check_github_access", lambda: calls.append("check"))
    remote, _, _ = client_app(remote=True)
    with remote:
        _, headers = bootstrap(remote)
        assert remote.get("/api/v1/accounts/github/access", headers=headers).status_code == 403
        command_id = str(uuid4())
        response = remote.post("/api/v1/accounts/github/access/commands", headers={**headers, "idempotency-key": command_id}, json={
            "command_id": command_id, "expected_revision": "a" * 64, "action": "check",
        })
        assert response.status_code == 403
    assert calls == []


def test_accounts_and_monitor_share_one_github_status(tmp_path, monkeypatch):
    """B118: Accounts said "Not connected" while Monitor said "Connected as …"
    for a GitHub CLI sign-in. Both read one status now, and Accounts never
    probes the network to show it."""
    from row_bot import status_checks
    from row_bot.application.settings_snapshot import _accounts

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    github_account._clear_token_cache_for_tests()
    github_account.clear_github_status_cache()
    monkeypatch.setattr(github_account.api_keys, "get_key", lambda _name: "")
    monkeypatch.setattr(github_account, "_github_cli_token", lambda *args, **kwargs: "fixture-cli-token")
    monkeypatch.setattr(github_account, "_github_cli_status", lambda: github_account.GitHubAccountStatus(
        connected=False, gh_installed=True, gh_authenticated=True, user="octo"))
    monkeypatch.setattr(client_accounts, "resolve_github_cli", lambda: "fixture-gh")
    monkeypatch.setattr(github_account, "_github_cli_installed", lambda: True)
    probes = []

    def api(token, source="", timeout=10):
        probes.append(token.source)
        return github_account.GitHubAccountStatus(
            connected=True, source=token.source, fingerprint=token.fingerprint, user="octo",
            state=github_account.GITHUB_STATE_CONNECTED, authenticated=True, token_valid=True)

    monkeypatch.setattr(github_account, "check_github_token_access", api)
    # Before anything checked it: the CLI is there, not yet checked (a read never starts it).
    passive = client_accounts.read_github_access(owner_id="owner-fixture")
    assert passive["state"] == "configured_unchecked" and passive["credential_source"] == "github_cli"
    assert _accounts(tmp_path, {}, {}, {})["github"]["authentication_state"] == "configured_unchecked"
    assert probes == []
    # Monitor's check verifies it; Accounts then says the same.
    assert status_checks.check_github_oauth().status == "ok"
    assert probes == ["github_cli"]
    assert client_accounts.read_github_access(owner_id="owner-fixture")["state"] == "connected"
    assert _accounts(tmp_path, {}, {}, {})["github"]["authentication_state"] == "connected"
    assert probes == ["github_cli"]
    github_account.clear_github_status_cache()
    github_account._clear_token_cache_for_tests()
