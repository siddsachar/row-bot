"""One immutable OAuth generation with truthful failure and refresh fencing."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest

from row_bot import secret_store
from row_bot.providers import auth_store, config, codex, claude_subscription, xai_oauth
from tests.subsystem.providers.test_provider_settings_controls import store as store

pytestmark = pytest.mark.subsystem

CASES = [
    ("codex", codex.CodexTokenSet, codex.save_codex_oauth_tokens, codex.disconnect_codex_metadata, codex.codex_runtime_credentials, codex, "refresh_codex_token"),
    ("claude_subscription", claude_subscription.ClaudeSubscriptionTokenSet, claude_subscription.save_claude_subscription_oauth_tokens, claude_subscription.disconnect_claude_subscription_metadata, claude_subscription.claude_subscription_runtime_credentials, claude_subscription, "refresh_claude_subscription_token"),
    ("xai_oauth", xai_oauth.XAIOAuthTokenSet, xai_oauth.save_xai_oauth_tokens, xai_oauth.disconnect_xai_oauth_metadata, xai_oauth.xai_oauth_runtime_credentials, xai_oauth, "refresh_xai_oauth_token"),
]


@pytest.fixture(params=CASES, ids=[value[0] for value in CASES])
def provider(request, store):
    return request.param


def token(provider, prefix="old"):
    return provider[1](access_token=f"{prefix}-access", refresh_token=f"{prefix}-refresh", id_token=f"{prefix}-id", account_id=f"{prefix}-account", expires_at="2020-01-01T00:00:00+00:00")


@pytest.mark.parametrize("failure", ["write", "readback", "publication"])
def test_partial_staging_failure_preserves_old_complete_bundle(provider, store, monkeypatch, failure):
    provider_id, _, save, *_ = provider
    save(token(provider))
    old = config.CONFIG_PATH.read_bytes()
    original_secrets = dict(store.values)
    if failure == "write":
        original = store.set_password
        count = 0
        def fail_after_first(service, account, value):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("synthetic hidden backend data")
            original(service, account, value)
        monkeypatch.setattr(store, "set_password", fail_after_first)
    elif failure == "readback":
        store.corrupt = True
    else:
        monkeypatch.setattr(auth_store, "save_provider_config", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("synthetic failure")))
    with pytest.raises((secret_store.SecretStoreError, OSError)):
        save(token(provider, "new"))
    store.corrupt = False
    assert config.CONFIG_PATH.read_bytes() == old
    assert all(store.values[key] == value for key, value in original_secrets.items())
    values, _, _ = auth_store.read_provider_oauth_bundle_snapshot(provider_id)
    assert values["access_token"] == "old-access" and values["refresh_token"] == "old-refresh"
    assert not auth_store._session_provider_secrets


def test_disconnect_tombstone_preserves_recovery_bytes_without_legacy_fallback(provider, store):
    provider_id, _, save, disconnect, runtime, *_ = provider
    auth_store.set_provider_secret(provider_id, "access_token", "retained-legacy-token")
    save(token(provider))
    before = dict(store.values)
    disconnect()
    entry = config.load_provider_config()["providers"][provider_id]
    assert entry["oauth_bundle_ref"] == {"cleared": True} and entry["configured"] is False
    assert entry["oauth_bundle_previous"]["reference"]["values"]
    assert before == store.values
    assert all(auth_store.get_provider_secret(provider_id, name) == "" for name in auth_store.OAUTH_SECRET_NAMES)
    assert not runtime(refresh_if_needed=False).access_token


def test_metadata_only_disconnect_retains_accessibility_and_disables_runtime(provider):
    provider_id, _, save, disconnect, runtime, *_ = provider
    save(token(provider))
    disconnect(remove_row_bot_tokens=False)
    assert auth_store.get_provider_secret(provider_id, "access_token") == "old-access"
    assert not runtime(refresh_if_needed=False).access_token


@pytest.mark.parametrize("intervening", ["disconnect", "replace"])
def test_refresh_cannot_resurrect_disconnected_or_replaced_account(provider, monkeypatch, intervening):
    provider_id, _, save, disconnect, runtime, module, refresh_name = provider
    save(token(provider))
    arrived, release = Event(), Event()
    def refresh(*_a, **_kw):
        arrived.set()
        assert release.wait(5)
        return token(provider, "late")
    monkeypatch.setattr(module, refresh_name, refresh)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime)
        try:
            assert arrived.wait(5)
            if intervening == "disconnect":
                disconnect()
            else:
                save(token(provider, "replacement"))
            final_bytes = config.CONFIG_PATH.read_bytes()
        finally:
            release.set()
        with pytest.raises(config.ProviderConfigError, match="revision_conflict"):
            future.result(5)
    assert config.CONFIG_PATH.read_bytes() == final_bytes
    assert auth_store.get_provider_secret(provider_id, "access_token") == ("" if intervening == "disconnect" else "replacement-access")


def test_snapshot_reads_captured_generation_during_concurrent_publication(provider, monkeypatch):
    provider_id, _, save, *_ = provider
    save(token(provider))
    original = auth_store._read_immutable_secret_ref
    changed = False
    def replace_during_read(owner, reference, slot):
        nonlocal changed
        if not changed:
            changed = True
            save(token(provider, "new"))
        return original(owner, reference, slot)
    monkeypatch.setattr(auth_store, "_read_immutable_secret_ref", replace_during_read)
    values, metadata, revision = auth_store.read_provider_oauth_bundle_snapshot(provider_id)
    assert values["access_token"] == "old-access" and values["refresh_token"] == "old-refresh"
    assert metadata["oauth_bundle_ref"] != config.load_provider_config()["providers"][provider_id]["oauth_bundle_ref"]
    assert revision != config.provider_config_revision(config.load_provider_config())


def test_missing_refresh_from_new_login_does_not_reuse_previous_account(provider):
    provider_id, _, save, *_ = provider
    save(token(provider))
    save(replace(token(provider, "new"), refresh_token="", id_token=""))
    values, _, _ = auth_store.read_provider_oauth_bundle_snapshot(provider_id)
    assert values["access_token"] == "new-access" and not values["refresh_token"] and not values["id_token"]


def test_revocation_before_config_publish_retains_original_account(provider):
    _, _, save, *_ = provider
    save(token(provider))
    before = config.CONFIG_PATH.read_bytes()
    def deny():
        raise ValueError("action_denied")
    with pytest.raises(ValueError, match="action_denied"):
        save(token(provider, "new"), validate=deny)
    assert config.CONFIG_PATH.read_bytes() == before


def test_oversized_utf8_token_rejected_before_secure_writes(provider, store):
    _, _, save, *_ = provider
    writes = len(store.writes)
    with pytest.raises(ValueError, match="invalid_subscription_credentials"):
        save(replace(token(provider), access_token="\u00e9" * 8193))
    assert len(store.writes) == writes


@pytest.mark.parametrize("intervening", ["disconnect", "replace"])
def test_health_refresh_cannot_publish_after_account_change(provider, monkeypatch, intervening):
    provider_id, _, save, disconnect, _, module, refresh_name = provider
    health = getattr(module, f"check_{provider_id}_token_health")
    save(token(provider))
    arrived, release = Event(), Event()
    def refresh(*_a, **_kw):
        arrived.set()
        assert release.wait(5)
        return token(provider, "late")
    monkeypatch.setattr(module, refresh_name, refresh)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(health)
        try:
            assert arrived.wait(5)
            if intervening == "disconnect":
                disconnect()
            else:
                save(token(provider, "replacement"))
            final_bytes = config.CONFIG_PATH.read_bytes()
        finally:
            release.set()
        outcome = future.result(5)
        assert outcome.status == "error" and not outcome.runnable
    assert config.CONFIG_PATH.read_bytes() == final_bytes
    assert auth_store.get_provider_secret(provider_id, "access_token") == ("" if intervening == "disconnect" else "replacement-access")


def test_late_authority_denial_after_staging_keeps_original_publication(provider, store):
    provider_id, _, save, *_ = provider
    save(token(provider))
    before = config.CONFIG_PATH.read_bytes()
    revoked = False
    def update_metadata(_cfg):
        nonlocal revoked
        revoked = True
    def validate():
        if revoked:
            raise ValueError("action_denied")
    with pytest.raises(ValueError, match="action_denied"):
        auth_store.replace_provider_oauth_bundle(provider_id, {"access_token": "new-staged-access"}, update_metadata=update_metadata, validate=validate)
    assert config.CONFIG_PATH.read_bytes() == before
    assert auth_store.get_provider_secret(provider_id, "access_token") == "old-access"


def test_single_secret_legacy_call_respects_active_bundle_and_retains_other_values(provider):
    provider_id, _, save, *_ = provider
    save(token(provider))
    auth_store.set_provider_secret(provider_id, "access_token", "single-updated-access")
    assert auth_store.get_provider_secret(provider_id, "access_token") == "single-updated-access"
    assert auth_store.get_provider_secret(provider_id, "refresh_token") == "old-refresh"
    auth_store.delete_provider_secret(provider_id, "access_token")
    assert not auth_store.get_provider_secret(provider_id, "access_token")
    assert auth_store.get_provider_secret(provider_id, "refresh_token") == "old-refresh"


def test_repeated_disconnect_preserves_original_recovery_reference(provider):
    provider_id, _, save, disconnect, *_ = provider
    save(token(provider))
    disconnect()
    previous = config.load_provider_config()["providers"][provider_id]["oauth_bundle_previous"]
    disconnect()
    assert config.load_provider_config()["providers"][provider_id]["oauth_bundle_previous"] == previous
