"""Strict saved subscription options use canonical files and fake secure storage."""
from dataclasses import asdict
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from row_bot.application import subscription_controls, subscription_options as controls
from row_bot.providers import auth_store, codex, claude_subscription, config, xai_oauth
from row_bot.runtime import admissions
from tests.subsystem.providers.test_provider_settings_controls import store as store

pytestmark = pytest.mark.subsystem


@pytest.fixture(params=["codex", "claude_subscription"])
def reference(request, tmp_path, store, monkeypatch):
    provider = request.param
    folder = tmp_path / "synthetic-cli"
    folder.mkdir()
    primary = folder / "auth.json"
    legacy = folder / "legacy.json"
    primary.write_text(json.dumps({"access_token": "private-synthetic-cli-token"}), encoding="utf-8")
    monkeypatch.setattr(codex, "codex_auth_path", lambda: primary)
    monkeypatch.setattr(claude_subscription, "claude_credentials_path", lambda: primary)
    monkeypatch.setattr(claude_subscription, "claude_legacy_credentials_path", lambda: legacy)
    monkeypatch.setattr(claude_subscription, "claude_cli_info", lambda *_a: pytest.fail("No CLI process discovery"))
    monkeypatch.setattr(claude_subscription, "claude_auth_status", lambda *_a: pytest.fail("No CLI status process"))
    monkeypatch.setattr(codex, "codex_cli_info", lambda *_a: pytest.fail("No CLI lookup"))
    monkeypatch.setattr("row_bot.application.provider_settings_commands.invalidate_provider_runtime", lambda: None)
    return provider, primary, legacy


def command(provider="xai_oauth", operation="client_id_save", value="synthetic-client"):
    payload = {"provider_id": provider, "provider_revision": controls.read_options().revision}
    if value is not None:
        payload["value"] = value
    cmd = {"command_id": str(uuid4()), "type": controls.COMMANDS[operation], "expected_revision": "0", "payload": payload}
    review = controls.review_options(provider, payload["provider_revision"], operation, value, validate=lambda: None)
    return cmd, review


def execute(cmd, review, validate=lambda: None):
    def validate_review(actual):
        if actual != review:
            raise ValueError("review does not match")
    return controls.execute_options(owner_id="synthetic-owner", key=cmd["command_id"], command=cmd,
        validate=validate, validate_review=validate_review)


def test_passive_options_does_not_read_cli_files_or_secrets(reference, store, monkeypatch):
    before = config.CONFIG_PATH.read_bytes()
    monkeypatch.setattr(controls, "_capture_reference", lambda *_a: pytest.fail("No CLI file read"))
    monkeypatch.setattr(auth_store, "get_provider_secret", lambda *_a: pytest.fail("No secret read"))
    result = asdict(controls.read_options())
    assert result["runtime_state"] == "unknown"
    assert config.CONFIG_PATH.read_bytes() == before and store.writes == []
    assert "private-synthetic" not in json.dumps(result)


def test_reference_is_metadata_only_and_keeps_external_bytes_and_recovery(reference, store):
    provider, path, _ = reference
    save = codex.save_codex_oauth_tokens if provider == "codex" else claude_subscription.save_claude_subscription_oauth_tokens
    token = codex.CodexTokenSet if provider == "codex" else claude_subscription.ClaudeSubscriptionTokenSet
    save(token(access_token="old-synthetic-owned-token", account_id="synthetic-account"))
    old = dict(store.values)
    before = path.read_bytes()
    cmd, review = command(provider, "reference", None)
    result = execute(cmd, review)
    assert path.read_bytes() == before and store.values == old
    assert result["options"]["references"][0 if provider == "codex" else 1]["metadata_saved"]
    entry = config.load_provider_config()["providers"][provider]
    assert entry["oauth_bundle_previous"]["metadata"]["source"] != "external_cli"
    runtime = codex.codex_runtime_credentials if provider == "codex" else claude_subscription.claude_subscription_runtime_credentials
    assert not runtime(refresh_if_needed=False).access_token
    owner = subscription_controls.SubscriptionFlows()
    cmd = {"command_id": str(uuid4()), "type": "provider.subscription.restore", "expected_revision": "0",
        "payload": {"provider_id": provider, "provider_revision": subscription_controls.read_accounts().revision}}
    try:
        owner.execute(owner_id="synthetic-owner", key=cmd["command_id"], command=cmd,
            validate=lambda: None, validate_review=lambda _: None)
        assert auth_store.get_provider_secret(provider, "access_token") == "old-synthetic-owned-token"
    finally:
        assert owner.dispose()
    assert "private-synthetic-cli-token" not in config.CONFIG_PATH.read_text(encoding="utf-8")
    assert "synthetic-cli" not in json.dumps(review)


def test_reference_retains_cleared_tombstone_and_original_recovery(reference, store):
    provider, _, _ = reference
    save = codex.save_codex_oauth_tokens if provider == "codex" else claude_subscription.save_claude_subscription_oauth_tokens
    token = codex.CodexTokenSet if provider == "codex" else claude_subscription.ClaudeSubscriptionTokenSet
    save(token(access_token="old-synthetic-owned-token", account_id="synthetic-account"))
    disconnect = codex.disconnect_codex_metadata if provider == "codex" else claude_subscription.disconnect_claude_subscription_metadata
    disconnect()
    old = config.load_provider_config()["providers"][provider]["oauth_bundle_previous"]
    execute(*command(provider, "reference", None))
    entry = config.load_provider_config()["providers"][provider]
    assert entry["oauth_bundle_ref"] == {"cleared": True} and entry["oauth_bundle_previous"] == old
    snapshot = next(item for item in subscription_controls.read_accounts().accounts if item.provider_id == provider)
    assert snapshot.saved_state == "metadata_only"
    assert not auth_store.get_provider_secret(provider, "access_token")
    runtime = codex.codex_runtime_credentials if provider == "codex" else claude_subscription.claude_subscription_runtime_credentials
    assert not runtime(refresh_if_needed=False).access_token


@pytest.mark.parametrize("content", [None, b"not-json", b"[]", b"x" * (1024 * 1024 + 1)], ids=["missing", "invalid", "not-object", "oversized"])
def test_invalid_reference_preserves_existing_config(reference, content):
    provider, path, _ = reference
    if content is None:
        path.unlink()
    else:
        path.write_bytes(content)
    before = config.CONFIG_PATH.read_bytes()
    with pytest.raises(config.ProviderConfigError, match="subscription_reference_unavailable"):
        command(provider, "reference", None)
    assert config.CONFIG_PATH.read_bytes() == before


def test_changed_same_size_reference_after_review_is_rejected(reference):
    provider, path, _ = reference
    cmd, review = command(provider, "reference", None)
    before = config.CONFIG_PATH.read_bytes()
    path.write_bytes(path.read_bytes().replace(b"token", b"other"))
    with pytest.raises(config.ProviderConfigError, match="subscription_options_review_invalid"):
        execute(cmd, review)
    assert config.CONFIG_PATH.read_bytes() == before


def test_reference_rechecked_at_canonical_publication(reference, monkeypatch):
    provider, path, _ = reference
    cmd, review = command(provider, "reference", None)
    before = config.CONFIG_PATH.read_bytes()
    module = codex if provider == "codex" else claude_subscription
    original = module.save_external_reference
    def mutate(**kwargs):
        path.write_text('{"different":"synthetic"}', encoding="utf-8")
        return original(**kwargs)
    monkeypatch.setattr(module, "save_external_reference", mutate)
    with pytest.raises(config.ProviderConfigError, match="subscription_options_unconfirmed"):
        execute(cmd, review)
    assert config.CONFIG_PATH.read_bytes() == before


def test_linked_reference_is_not_followed(reference):
    provider, path, _ = reference
    if os.name == "nt":
        linked = path.parent.with_name("linked-cli")
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(path.parent)], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        with pytest.raises(config.ProviderConfigError, match="subscription_reference_unavailable"):
            controls._capture_file(linked / path.name)
        return
    retained = path.with_name("retained.json")
    path.rename(retained)
    path.symlink_to(retained)
    with pytest.raises(config.ProviderConfigError, match="subscription_reference_unavailable"):
        command(provider, "reference", None)


def test_xai_client_override_and_reset_preserve_unknown_fields_and_environment(store, monkeypatch):
    monkeypatch.setenv(xai_oauth.ROW_BOT_XAI_OAUTH_CLIENT_ID_ENV, "environment-synthetic-client")
    config.update_provider_config(lambda cfg: cfg.setdefault("providers", {}).setdefault("xai_oauth", {}).update({"unknown": {"keep": True}}))
    execute(*command())
    snapshot = controls.read_options()
    assert snapshot.xai_client_id_source == "environment" and snapshot.xai_saved_client_id == "synthetic-client"
    execute(*command(operation="client_id_reset", value=None))
    assert controls.read_options().xai_saved_client_id is None
    assert config.load_provider_config()["providers"]["xai_oauth"]["unknown"] == {"keep": True}
    assert store.writes == []


@pytest.mark.parametrize("value", ["", "client-id", "a b", "x" * 513, "a\n", "é", "secret\ud800"])
def test_invalid_client_id_is_rejected_without_saving(store, value):
    before = config.CONFIG_PATH.read_bytes()
    with pytest.raises(config.ProviderConfigError, match="invalid_subscription_options"):
        command(value=value)
    assert config.CONFIG_PATH.read_bytes() == before


def test_stale_review_preserves_other_writer(store):
    cmd, review = command()
    config.update_provider_config(lambda cfg: cfg.update({"another_writer": "retained"}))
    before = config.CONFIG_PATH.read_bytes()
    with pytest.raises(config.ProviderConfigError, match="subscription_options_review_invalid"):
        execute(cmd, review)
    assert config.CONFIG_PATH.read_bytes() == before


def test_completed_replay_reports_current_options_without_rewriting(store):
    cmd, review = command()
    execute(cmd, review)
    xai_oauth.save_xai_oauth_client_id("later-synthetic-client")
    before = config.CONFIG_PATH.read_bytes()
    assert execute(cmd, review)["options"]["xai_saved_client_id"] == "later-synthetic-client"
    assert config.CONFIG_PATH.read_bytes() == before


def test_failure_and_replay_do_not_blindly_write(store, monkeypatch):
    cmd, review = command()
    before = config.CONFIG_PATH.read_bytes()
    original = config.write_provider_metadata
    monkeypatch.setattr(config, "write_provider_metadata", lambda *_a: (_ for _ in ()).throw(OSError("synthetic storage failure")))
    with pytest.raises(config.ProviderConfigError, match="subscription_options_unconfirmed"):
        execute(cmd, review)
    assert config.CONFIG_PATH.read_bytes() == before
    monkeypatch.setattr(config, "write_provider_metadata", original)
    with pytest.raises(config.ProviderConfigError, match="operation_uncertain"):
        execute(cmd, review)
    assert config.CONFIG_PATH.read_bytes() == before


def test_published_receipt_recovery_is_passive_then_completes_original_only(store, monkeypatch):
    cmd, review = command()
    original = admissions.complete_command
    monkeypatch.setattr(admissions, "complete_command", lambda *_a: (_ for _ in ()).throw(OSError("synthetic receipt failure")))
    with pytest.raises(config.ProviderConfigError, match="subscription_options_unconfirmed"):
        execute(cmd, review)
    before = config.CONFIG_PATH.read_bytes()
    row = controls.read_options_receipt(owner_id="synthetic-owner", command_id=cmd["command_id"], validate=lambda: None)
    assert row["published"] and row["status"] == "completed"
    assert controls.read_options_receipt(owner_id="other-owner", command_id=cmd["command_id"], validate=lambda: None) is None
    assert config.CONFIG_PATH.read_bytes() == before
    monkeypatch.setattr(admissions, "complete_command", original)
    assert execute(cmd, review)["status"] == "completed"
    assert config.CONFIG_PATH.read_bytes() == before


def test_canonical_client_id_late_authority_denial_rolls_back(store):
    before = config.CONFIG_PATH.read_bytes()
    calls = 0
    def validate():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("revoked")
    with pytest.raises(ValueError, match="revoked"):
        xai_oauth.save_xai_oauth_client_id("synthetic-client", expected_revision=controls.read_options().revision, validate=validate)
    assert config.CONFIG_PATH.read_bytes() == before


def test_cold_options_read_creates_nothing(tmp_path):
    cold = tmp_path / "cold-options"
    code = """
from pathlib import Path
import os,sys
from row_bot.application.subscription_options import read_options
assert len(read_options().references) == 2
assert 'row_bot.api_keys' not in sys.modules
assert not Path(os.environ['ROW_BOT_DATA_DIR']).exists()
"""
    result = subprocess.run([sys.executable, "-c", code], env={**os.environ, "ROW_BOT_DATA_DIR": str(cold)}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
