from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _install_codex_secret_fixture(monkeypatch, *, access: str = "", refresh: str = "", account: str = "acct", expires_at: str = ""):
    import row_bot.providers.auth_store as auth_store
    import row_bot.providers.config as provider_config
    from row_bot.providers.models import AuthMethod

    secrets = {
        "access_token": access,
        "refresh_token": refresh,
        "id_token": "",
        "account": account,
    }

    monkeypatch.setattr(auth_store, "get_provider_secret", lambda provider_id, name="api_key": secrets.get(name, ""))
    monkeypatch.setattr(auth_store, "read_provider_oauth_bundle_snapshot", lambda provider_id: (
        {**secrets, "user_id": ""}, {"source": AuthMethod.OAUTH_DEVICE.value, "auth_method": AuthMethod.OAUTH_DEVICE.value, "expires_at": expires_at}, "captured-revision"))
    monkeypatch.setattr(provider_config, "load_provider_config", lambda: {
        "providers": {
            "codex": {
                "source": AuthMethod.OAUTH_DEVICE.value,
                "auth_method": AuthMethod.OAUTH_DEVICE.value,
                "expires_at": expires_at,
            }
        }
    })


def test_codex_token_health_valid_does_not_refresh(monkeypatch):
    from row_bot.providers import codex

    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    _install_codex_secret_fixture(monkeypatch, access="access", refresh="refresh", expires_at=future)
    monkeypatch.setattr(codex, "refresh_codex_token", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("refresh should not run")))

    health = codex.check_codex_token_health()

    assert health.status == "valid"
    assert health.runnable


def test_codex_token_health_refreshes_expired_token(monkeypatch):
    from row_bot.providers import codex

    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    _install_codex_secret_fixture(monkeypatch, access="old", refresh="refresh", expires_at=past)
    saved: list[codex.CodexTokenSet] = []

    def _refresh(refresh_token, *, http_client=None):
        assert refresh_token == "refresh"
        return codex.CodexTokenSet(
            access_token="new",
            refresh_token="refresh",
            expires_at=future,
            account_id="acct",
        )

    monkeypatch.setattr(codex, "refresh_codex_token", _refresh)
    monkeypatch.setattr(codex, "save_codex_oauth_tokens", lambda token_set, *, expected_revision: saved.append(token_set) or {"expires_at": future})

    health = codex.check_codex_token_health()

    assert health.status == "refreshed"
    assert health.runnable
    assert health.credentials.access_token == "new"
    assert saved and saved[0].access_token == "new"


def test_codex_credentials_refresh_when_access_token_missing(monkeypatch):
    from row_bot.providers import codex

    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    _install_codex_secret_fixture(monkeypatch, access="", refresh="refresh", expires_at="")

    monkeypatch.setattr(
        codex,
        "refresh_codex_token",
        lambda refresh_token, *, http_client=None: codex.CodexTokenSet(
            access_token="new",
            refresh_token=refresh_token,
            expires_at=future,
            account_id="acct",
        ),
    )
    monkeypatch.setattr(codex, "save_codex_oauth_tokens", lambda token_set, *, expected_revision: {"expires_at": future})

    credentials = codex.codex_runtime_credentials(refresh_if_needed=True)

    assert credentials.access_token == "new"
    assert credentials.account_id == "acct"


def test_codex_token_health_missing_refresh_requires_reconnect(monkeypatch):
    from row_bot.providers import codex

    _install_codex_secret_fixture(monkeypatch, access="", refresh="", expires_at="")

    health = codex.check_codex_token_health()

    assert health.status == "missing"
    assert not health.runnable
    assert "Settings › Models" in health.detail


def test_codex_token_health_revoked_refresh_requires_reconnect(monkeypatch):
    from row_bot.providers import codex

    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _install_codex_secret_fixture(monkeypatch, access="old", refresh="refresh", expires_at=past)
    monkeypatch.setattr(codex, "refresh_codex_token", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("invalid_grant")))

    health = codex.check_codex_token_health()

    assert health.status == "expired"
    assert not health.runnable
    assert "Reconnect ChatGPT" in health.detail


def test_an_expired_access_token_with_a_refresh_token_stays_usable_without_refreshing_now(monkeypatch):
    """A status read never refreshes; the token renews when it's next used, so ChatGPT isn't shown as needing
    a reconnect just because its access token ran out (B351)."""
    from row_bot.providers import codex

    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _install_codex_secret_fixture(monkeypatch, access="old", refresh="refresh", expires_at=past)
    monkeypatch.setattr(codex, "refresh_codex_token", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no refresh on a read")))

    health = codex.check_codex_token_health(refresh_if_needed=False)

    assert health.status == "refresh_due"
    assert health.runnable


def test_a_codex_model_renews_an_expired_token_and_a_revoked_one_asks_to_sign_in_again(monkeypatch):
    from row_bot.providers import codex, runtime

    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    _install_codex_secret_fixture(monkeypatch, access="old", refresh="refresh", expires_at=past)
    renewed = codex.CodexTokenSet(access_token="new", refresh_token="refresh", expires_at=future, account_id="acct")
    monkeypatch.setattr(codex, "refresh_codex_token", lambda *_args, **_kwargs: renewed)
    monkeypatch.setattr(codex, "save_codex_oauth_tokens", lambda token_set, *, expected_revision: {"expires_at": future})

    assert runtime.create_chat_model("gpt-5.5", provider_id="codex").model_name == "gpt-5.5"

    monkeypatch.setattr(codex, "refresh_codex_token", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("invalid_grant")))
    with pytest.raises(runtime.ProviderSignInRequired) as refused:
        runtime.create_chat_model("gpt-5.5", provider_id="codex")
    assert (refused.value.provider_id, refused.value.label) == ("codex", "ChatGPT")
