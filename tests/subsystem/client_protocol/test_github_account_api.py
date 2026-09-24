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
    monkeypatch.setattr(github_account, "get_passive_github_account_status", _passive)
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
    monkeypatch.setattr(github_account, "get_passive_github_account_status", _passive)
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
    monkeypatch.setattr(github_account, "get_passive_github_account_status", _passive)
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
