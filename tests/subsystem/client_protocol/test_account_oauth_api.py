"""Google and X OAuth actions are explicit, fenced, and isolated."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from row_bot.application import client_account_oauth as owner
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem


def _command(snapshot, action, **extra):
    return dict(owner_id="local", command_id=str(uuid4()), account=snapshot["account"],
                expected_revision=snapshot["revision"], action=action, **extra)


def test_account_auth_read_has_no_profile_or_provider_effect(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(profile))
    google = owner.read_account_auth(account="google")
    assert google["state"] == "not_configured"
    assert not profile.exists()


def test_google_credential_import_validates_and_keeps_existing_on_failure(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(profile))
    from row_bot.tools import registry
    monkeypatch.setattr(registry, "set_tool_config", lambda *_args: None)
    before = owner.read_account_auth(account="google")
    valid = json.dumps({"installed": {"client_id": "fixture-client", "client_secret": "fixture-secret",
                                        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}})
    with pytest.raises(Exception, match="account_credentials_invalid"):
        owner.execute_account_auth(**_command(before, "import_credentials", credentials_json='{"invalid": true}'))
    assert not (profile / "gmail" / "credentials.json").exists()
    imported = owner.execute_account_auth(**_command(before, "import_credentials", credentials_json=valid))
    assert imported["phase"] == "completed"
    assert imported["snapshot"]["configured"] is True
    assert "fixture-secret" not in json.dumps(imported)
    saved = (profile / "gmail" / "credentials.json").read_text(encoding="utf-8")
    assert "fixture-client" in saved
    with pytest.raises(Exception, match="account_changed"):
        owner.execute_account_auth(**_command(before, "start"))


@pytest.mark.parametrize(("token_uri", "accepted"), [
    ("https://accounts.google.com/o/oauth2/token", True),  # Older client files still name this one.
    ("https://evil.example.test/token", False),
    ("http://oauth2.googleapis.com/token", False),
])
def test_a_google_client_file_must_name_only_googles_own_sign_in_hosts(tmp_path, monkeypatch, token_uri, accepted):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    from row_bot.tools import registry
    monkeypatch.setattr(registry, "set_tool_config", lambda *_args: None)
    before = owner.read_account_auth(account="google")
    file = json.dumps({"installed": {"client_id": "fixture-client", "client_secret": "fixture-secret",
                                     "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": token_uri,
                                     "redirect_uris": ["http://localhost"]}})
    if accepted:
        assert owner.execute_account_auth(**_command(before, "import_credentials", credentials_json=file))["phase"] == "completed"
    else:
        with pytest.raises(Exception, match="account_credentials_invalid"):
            owner.execute_account_auth(**_command(before, "import_credentials", credentials_json=file))


def test_google_auth_cancellation_discards_late_credentials(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(profile))
    (profile / "gmail").mkdir(parents=True)
    (profile / "gmail" / "credentials.json").write_text("{}", encoding="utf-8")
    captured = []

    class FakeThread:
        def __init__(self, *, target, args, **_kwargs):
            captured.append((target, args))

        def start(self):
            return None

    monkeypatch.setattr(owner, "Thread", FakeThread)
    monkeypatch.setattr(owner, "_google_flow", lambda: b'{"token":"fixture"}')
    snapshot = owner.read_account_auth(account="google")
    command = _command(snapshot, "start")
    running = owner.execute_account_auth(**command)
    assert running["phase"] == "running"
    cancelled = owner.cancel_account_auth(owner_id="local", command_id=command["command_id"])
    assert cancelled["phase"] == "cancel_requested"
    captured[0][0](*captured[0][1])
    recovered = owner.read_account_auth_receipt(owner_id="local", command_id=command["command_id"])
    assert recovered["phase"] == "cancelled"
    assert not (profile / "gmail" / "token.json").exists()
    assert not (profile / "calendar" / "token.json").exists()


def test_google_auth_revoked_before_callback_keeps_prior_tokens(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(profile))
    (profile / "gmail").mkdir(parents=True)
    (profile / "gmail" / "credentials.json").write_text("{}", encoding="utf-8")
    (profile / "gmail" / "token.json").write_text("original", encoding="utf-8")
    queued = []

    class FakeThread:
        def __init__(self, *, target, args, **_kwargs):
            queued.append((target, args))

        def start(self):
            return None

    monkeypatch.setattr(owner, "Thread", FakeThread)
    monkeypatch.setattr(owner, "_google_flow", lambda: b"new-token")
    snapshot = owner.read_account_auth(account="google")
    command = _command(snapshot, "start")
    owner.execute_account_auth(**command, validate=lambda: (_ for _ in ()).throw(RuntimeError("revoked")))
    queued[0][0](*queued[0][1])
    receipt = owner.read_account_auth_receipt(owner_id="local", command_id=command["command_id"])
    assert receipt["phase"] == "failed"
    assert (profile / "gmail" / "token.json").read_text(encoding="utf-8") == "original"
    assert not (profile / "calendar" / "token.json").exists()


def test_google_auth_replaces_both_tokens_and_retains_original_command(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(profile))
    (profile / "gmail").mkdir(parents=True)
    (profile / "gmail" / "credentials.json").write_text("{}", encoding="utf-8")
    (profile / "gmail" / "token.json").write_text("old-token", encoding="utf-8")
    monkeypatch.setattr(owner, "_google_flow", lambda: b'new-token')

    class ImmediateThread:
        def __init__(self, *, target, args, **_kwargs):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(owner, "Thread", ImmediateThread)
    snapshot = owner.read_account_auth(account="google")
    command = _command(snapshot, "start")
    receipt = owner.execute_account_auth(**command)
    assert receipt["phase"] == "completed"
    # Tokens the provider has just issued are a passing check (B263).
    assert receipt["snapshot"]["state"] == "connected"
    assert (profile / "gmail" / "token.json").read_bytes() == b'new-token'
    assert (profile / "calendar" / "token.json").read_bytes() == b'new-token'
    assert owner.execute_account_auth(**command) == receipt


def test_disconnect_requires_confirmation_and_removes_only_account_tokens(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(profile))
    (profile / "gmail").mkdir(parents=True)
    (profile / "calendar").mkdir(parents=True)
    (profile / "gmail" / "credentials.json").write_text("{}", encoding="utf-8")
    (profile / "gmail" / "token.json").write_text("gmail-token", encoding="utf-8")
    (profile / "calendar" / "token.json").write_text("calendar-token", encoding="utf-8")
    snapshot = owner.read_account_auth(account="google")
    with pytest.raises(Exception, match="account_confirmation_required"):
        owner.execute_account_auth(**_command(snapshot, "disconnect"))
    assert (profile / "gmail" / "token.json").exists()
    receipt = owner.execute_account_auth(**_command(snapshot, "disconnect", confirmed=True))
    assert receipt["phase"] == "completed"
    assert (profile / "gmail" / "credentials.json").exists()
    assert not (profile / "gmail" / "token.json").exists()
    assert not (profile / "calendar" / "token.json").exists()


def test_account_auth_api_rejects_remote_before_provider_or_file_access(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    calls = []
    monkeypatch.setattr(owner, "read_account_auth", lambda **kwargs: calls.append(kwargs))
    remote, _, _ = client_app(remote=True)
    with remote:
        _, headers = bootstrap(remote)
        assert remote.get("/api/v1/accounts/google/auth", headers=headers).status_code == 403
        command_id = str(uuid4())
        response = remote.post("/api/v1/accounts/google/auth/commands", headers={**headers, "idempotency-key": command_id}, json={
            "command_id": command_id, "account": "google", "action": "check",
            "expected_revision": "a" * 64,
        })
        assert response.status_code == 403
    assert calls == []


def test_account_auth_api_local_start_receipt_and_cancel(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(profile))
    (profile / "gmail").mkdir(parents=True)
    (profile / "gmail" / "credentials.json").write_text("{}", encoding="utf-8")
    queued = []

    class FakeThread:
        def __init__(self, *, target, args, **_kwargs):
            queued.append((target, args))

        def start(self):
            return None

    monkeypatch.setattr(owner, "Thread", FakeThread)
    monkeypatch.setattr(owner, "_google_flow", lambda: b'{"token":"fixture"}')
    local, _, _ = client_app()
    with local:
        _, headers = bootstrap(local)
        loaded = local.get("/api/v1/accounts/google/auth", headers=headers)
        assert loaded.status_code == 200, loaded.text
        assert queued == []
        command_id = str(uuid4())
        body = {"command_id": command_id, "account": "google", "action": "start",
                "expected_revision": loaded.json()["revision"]}
        launched = local.post("/api/v1/accounts/google/auth/commands", headers={**headers, "idempotency-key": command_id}, json=body)
        assert launched.status_code == 200, launched.text
        assert launched.json()["phase"] == "running"
        assert len(queued) == 1
        assert local.get(f"/api/v1/accounts/google/auth/commands/{command_id}", headers=headers).json()["phase"] == "running"
        cancelled = local.post(f"/api/v1/accounts/google/auth/commands/{command_id}/cancel", headers=headers)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["phase"] == "cancel_requested"
        queued[0][0](*queued[0][1])
        assert local.get(f"/api/v1/accounts/google/auth/commands/{command_id}", headers=headers).json()["phase"] == "cancelled"
    assert not (profile / "gmail" / "token.json").exists()


def test_x_oauth_uses_saved_credentials_and_writes_only_after_explicit_start(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(profile))
    from row_bot import api_keys
    monkeypatch.setattr(api_keys, "get_key", lambda key: {"X_CLIENT_ID": "fixture-id", "X_CLIENT_SECRET": "fixture-secret"}.get(key, ""))
    monkeypatch.setattr(owner, "_x_flow", lambda: b'{"access_token":"fixture-token"}')

    class ImmediateThread:
        def __init__(self, *, target, args, **_kwargs):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(owner, "Thread", ImmediateThread)
    before = owner.read_account_auth(account="x")
    assert before["configured"] is True
    assert not (profile / "x" / "token.json").exists()
    command = _command(before, "start")
    completed = owner.execute_account_auth(**command)
    assert completed["phase"] == "completed"
    assert (profile / "x" / "token.json").read_bytes() == b'{"access_token":"fixture-token"}'
    assert "fixture-token" not in json.dumps(completed)


# B263: Settings › Accounts reports what Check found, not a guess from the
# token file. Expiry values are far from today so the real clock never
# matters; no test contacts a provider.
_FUTURE, _PAST = "2099-01-01T00:00:00Z", "2000-01-01T00:00:00Z"


def _google_token(expiry, *, refresh=True, access="fixture-access"):
    token = {"token": access, "client_id": "fixture-client", "client_secret": "fixture-secret",
             "token_uri": "https://oauth2.googleapis.com/token", "expiry": expiry}
    if refresh:
        token["refresh_token"] = "fixture-refresh"
    return json.dumps(token)


def _google_profile(tmp_path, monkeypatch, token):
    profile = tmp_path / "profile"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(profile))
    for folder in ("gmail", "calendar"):
        (profile / folder).mkdir(parents=True)
        (profile / folder / "token.json").write_text(token, encoding="utf-8")
    (profile / "gmail" / "credentials.json").write_text("{}", encoding="utf-8")
    return profile


def _states(client, headers, *accounts):
    response = client.get("/api/v1/settings/snapshot", headers=headers)
    assert response.status_code == 200, response.text
    page = response.json()["accounts"]
    return tuple(page[account]["authentication_state"] for account in accounts)


def _check(client, headers, account):
    loaded = client.get(f"/api/v1/accounts/{account}/auth", headers=headers).json()
    command_id = str(uuid4())
    response = client.post(f"/api/v1/accounts/{account}/auth/commands",
                           headers={**headers, "idempotency-key": command_id},
                           json={"command_id": command_id, "account": account, "action": "check",
                                 "expected_revision": loaded["revision"]})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_healthy_check_is_what_accounts_reports_until_the_token_file_changes(tmp_path, monkeypatch):
    # The access token has not run out, so Check passes without a refresh.
    profile = _google_profile(tmp_path, monkeypatch, _google_token(_FUTURE))
    local, _, _ = client_app()
    with local:
        _, headers = bootstrap(local)
        assert _states(local, headers, "gmail", "calendar") == ("saved_unchecked", "saved_unchecked")
        receipt = _check(local, headers, "google")
        assert receipt["phase"] == "completed"
        assert receipt["snapshot"]["state"] == "connected"
        assert _states(local, headers, "gmail", "calendar") == ("connected", "connected")
        # Anything that rewrites a token file makes it unchecked again.
        (profile / "gmail" / "token.json").write_text(
            _google_token(_FUTURE, access="fixture-access-rotated"), encoding="utf-8")
        assert _states(local, headers, "gmail", "calendar") == ("saved_unchecked", "connected")
        assert local.get("/api/v1/accounts/google/auth", headers=headers).json()["state"] == "saved_unchecked"
        assert "fixture-access" not in json.dumps(receipt)


def test_a_failed_check_says_so_and_accounts_asks_for_a_reconnect(tmp_path, monkeypatch):
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials

    def revoked(self, request):
        raise RefreshError("invalid_grant: Token has been expired or revoked.")

    _google_profile(tmp_path, monkeypatch, _google_token(_PAST))
    monkeypatch.setattr(Credentials, "refresh", revoked)
    local, _, _ = client_app()
    with local:
        _, headers = bootstrap(local)
        receipt = _check(local, headers, "google")
        assert receipt["phase"] == "failed"
        assert receipt["snapshot"]["state"] == "invalid"
        assert _states(local, headers, "gmail", "calendar") == ("invalid", "invalid")


def test_opening_accounts_never_refreshes_and_an_expired_access_token_with_a_refresh_token_is_fine(
    tmp_path, monkeypatch,
):
    from google.oauth2.credentials import Credentials

    profile = _google_profile(tmp_path, monkeypatch, _google_token(_PAST))
    monkeypatch.setattr(Credentials, "refresh", lambda *_args: pytest.fail("opening Accounts refreshed a token"))
    saved = (profile / "calendar" / "token.json").read_bytes()
    local, _, _ = client_app()
    with local:
        _, headers = bootstrap(local)
        # Google's access token lasts an hour; the refresh token keeps it working.
        assert _states(local, headers, "gmail", "calendar") == ("saved_unchecked", "saved_unchecked")
        assert local.get("/api/v1/accounts/google/auth", headers=headers).json()["state"] == "saved_unchecked"
        (profile / "gmail" / "token.json").write_text(_google_token(_PAST, refresh=False), encoding="utf-8")
        assert _states(local, headers, "gmail", "calendar") == ("expired", "saved_unchecked")
    assert (profile / "calendar" / "token.json").read_bytes() == saved


def test_x_reads_its_numeric_expiry(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(profile))
    monkeypatch.setenv("X_CLIENT_ID", "fixture-id")
    monkeypatch.setenv("X_CLIENT_SECRET", "fixture-secret")
    token = profile / "x" / "token.json"
    token.parent.mkdir(parents=True)
    local, _, _ = client_app()
    states = []
    with local:
        _, headers = bootstrap(local)
        for saved in (
            {"access_token": "fixture-token", "expires_at": 946684800.0},
            {"access_token": "fixture-token", "expires_at": 946684800, "refresh_token": "fixture-refresh"},
            {"access_token": "fixture-token", "expires_at": 4070908800.5},
        ):
            token.write_text(json.dumps(saved), encoding="utf-8")
            states.append(_states(local, headers, "x")[0])
    assert states == ["expired", "saved_unchecked", "saved_unchecked"]
