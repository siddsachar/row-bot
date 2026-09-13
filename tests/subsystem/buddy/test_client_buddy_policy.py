"""Explicit Buddy policy uses fake accounts and canonical local policy rules."""

from copy import deepcopy
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from row_bot.application import buddy_policy


@pytest.fixture
def env(tmp_path, monkeypatch):
    from row_bot import api_keys, tasks
    from row_bot.providers import auth_store, config
    from row_bot.tools import profile_policy  # noqa: F401 -- canonical package initialized before fake registry

    monkeypatch.setattr(tasks, "_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(tasks, "_SCHEMA_READY_PATH", None)
    values = {
        "image_gen": "openai/gpt-image-1.5",
        "video_gen": "google/veo-3.1-generate-preview",
    }
    enabled = {"image_gen": True, "video_gen": True}
    keys = {
        "openai": "synthetic-openai",
        "google": "synthetic-google",
        "xai": "synthetic-xai",
    }
    runtime = {
        auth_store.PROVIDER_API_KEY_ENV[key]: value for key, value in keys.items()
    }
    providers = {"providers": {}}
    context = {"approval_mode": "approve", "agent_profile_snapshot": {}}
    calls = []
    registry = SimpleNamespace(
        get_tool=lambda name: object(),
        is_enabled=lambda name: enabled[name],
        get_tool_config=lambda name, field, default: values.get(name, default),
        _config_path=lambda: tmp_path / "tools_config.json",
    )
    monkeypatch.setitem(sys.modules, "row_bot.tools.registry", registry)
    monkeypatch.setitem(
        sys.modules, "row_bot.models", SimpleNamespace(_cloud_model_cache={})
    )
    monkeypatch.setattr(
        auth_store, "get_provider_secret", lambda provider: keys.get(provider, "")
    )
    monkeypatch.setattr(api_keys, "get_key", lambda key: runtime.get(key, ""))
    monkeypatch.setattr(
        config, "load_provider_config", lambda **kwargs: deepcopy(providers)
    )
    policy = buddy_policy.create_buddy_policy(
        read_context=lambda: deepcopy(context),
        validate=lambda: None,
        validate_confirmation=lambda action, current: calls.append((action, current)),
    )
    return SimpleNamespace(
        policy=policy,
        values=values,
        enabled=enabled,
        keys=keys,
        runtime=runtime,
        providers=providers,
        context=context,
        calls=calls,
        registry=registry,
        path=tmp_path / "tools_config.json",
    )


def test_passive_capture_is_stable_content_free_and_never_confirms_or_constructs_provider(
    env, monkeypatch
):
    from row_bot.providers import xai_oauth

    monkeypatch.setattr(
        xai_oauth,
        "refresh_xai_oauth_token",
        lambda *a, **k: pytest.fail("unexpected refresh"),
    )
    first = env.policy.capture_provider_policy("full")
    assert first == env.policy.capture_provider_policy("full")
    assert (
        first["image_model"] == env.values["image_gen"]
        and first["video_model"] == env.values["video_gen"]
    )
    assert not env.calls
    assert all(secret not in json.dumps(first) for secret in env.keys.values())
    assert not env.path.exists()


@pytest.mark.parametrize("action", ["full", "motion", "still", "remove", "update"])
def test_block_rejects_effects_and_cancel_preserves_stop_authority(env, action):
    env.context["approval_mode"] = "block"
    with pytest.raises(ValueError, match="buddy_action_blocked"):
        env.policy.validate_action(action)
    env.policy.validate_action("cancel")
    assert not env.calls


def test_ask_requires_original_confirmation_at_every_provider_boundary(env):
    captured = env.policy.capture_provider_policy("full")
    for _ in range(2):
        env.policy.validate_provider("image", captured["image_model"], captured)
    env.policy.validate_provider("motion", captured["video_model"], captured)
    assert [row[0] for row in env.calls] == ["full", "full", "full"]
    env.policy.validate_action("update")
    assert len(env.calls) == 3


def test_auto_never_creates_another_confirmation(env):
    env.context["approval_mode"] = "allow_all"
    captured = env.policy.capture_provider_policy("motion")
    env.policy.validate_provider("motion", captured["video_model"], captured)
    assert not env.calls


@pytest.mark.parametrize(
    "change",
    ["selection", "enabled", "key", "account", "approval", "profile", "allowlist"],
)
def test_current_policy_change_rejects_original_capture(env, change):
    captured = env.policy.capture_provider_policy("full")
    if change == "selection":
        env.values["image_gen"] = "openai/gpt-image-1"
    elif change == "enabled":
        env.enabled["image_gen"] = False
    elif change == "key":
        env.keys["openai"] = env.runtime["OPENAI_API_KEY"] = "synthetic-new-key"
    elif change == "account":
        env.providers["providers"]["openai"] = {"credential_ref": {"generation": "new"}}
    elif change == "approval":
        env.context["approval_mode"] = "block"
    elif change == "profile":
        env.context["agent_profile_snapshot"] = {
            "id": "read-only",
            "tool_policy_json": {"capability": "read_only"},
        }
    elif change == "allowlist":
        env.context["tool_allowlist"] = []
    with pytest.raises(ValueError):
        env.policy.validate_provider("image", captured["image_model"], captured)
    assert not env.calls


def test_captured_auth_uses_canonical_key_instead_of_stale_legacy_key(env):
    env.keys["openai"] = "synthetic-canonical-new-key"
    captured = env.policy.capture_provider_policy("full")
    auth = env.policy.validate_provider("image", captured["image_model"], captured)
    assert auth.credential == "synthetic-canonical-new-key"
    assert auth.credential != env.runtime["OPENAI_API_KEY"]


def test_stored_disabled_tool_cannot_use_stale_enabled_registry(env):
    env.path.write_text(json.dumps({"tools": {"image_gen": False}}))
    with pytest.raises(ValueError, match="buddy_tool_unavailable"):
        env.policy.capture_provider_policy("full")


def test_profile_dispatch_uses_exact_operation_allowlist_and_readonly_restriction(env):
    env.context["agent_profile_snapshot"] = {
        "id": "writer",
        "tool_policy_json": {
            "capability": "write_capable",
            "allow_tools": ["generate_image", "animate_image"],
        },
    }
    assert env.policy.capture_provider_policy("full")["image_model"]
    env.context["agent_profile_snapshot"]["tool_policy_json"]["deny_tools"] = [
        "animate_image"
    ]
    with pytest.raises(ValueError, match="buddy_tool_policy_denied"):
        env.policy.capture_provider_policy("full")


def test_unavailable_frozen_profile_and_local_only_policy_fail_closed(env):
    env.context["agent_profile_id"] = "selected"
    with pytest.raises(ValueError, match="buddy_policy_unavailable"):
        env.policy.capture_provider_policy("full")
    env.context.pop("agent_profile_id")
    env.context["data_policy"] = "local_only"
    with pytest.raises(ValueError, match="buddy_data_policy_denied"):
        env.policy.capture_provider_policy("full")


def test_context_change_while_account_is_read_rejects_mixed_capture(env, monkeypatch):
    from row_bot.providers import auth_store

    def key(provider):
        env.context["approval_mode"] = "block"
        return env.keys[provider]

    monkeypatch.setattr(auth_store, "get_provider_secret", key)
    with pytest.raises(ValueError, match="buddy_policy_changed"):
        env.policy.capture_provider_policy("full")


def test_confirmation_and_auth_exceptions_keep_original_identity(env):
    sentinel = PermissionError("synthetic authority rejected")

    def deny(*args):
        raise sentinel

    policy = buddy_policy.create_buddy_policy(
        read_context=lambda: env.context,
        validate=lambda: None,
        validate_confirmation=deny,
    )
    with pytest.raises(PermissionError) as caught:
        policy.validate_action("full")
    assert caught.value is sentinel
    revoked = buddy_policy.create_buddy_policy(
        read_context=lambda: env.context,
        validate=deny,
        validate_confirmation=lambda *a: None,
    )
    with pytest.raises(PermissionError) as caught:
        revoked.validate_action("cancel")
    assert caught.value is sentinel


def test_oauth_capture_uses_same_nonrefreshing_bundle_and_detects_account_change(
    env, monkeypatch
):
    from row_bot.providers import auth_store, xai_oauth

    env.values["video_gen"] = "xai_oauth/grok-imagine-video"
    env.providers["providers"]["xai_oauth"] = {
        "auth_method": "oauth_pkce",
        "token_health": "valid",
    }
    credentials = SimpleNamespace(
        access_token="synthetic-access",
        refresh_token="synthetic-refresh",
        expires_at="2099-01-01T00:00:00Z",
        account_id="synthetic-account",
        user_id="synthetic-user",
    )
    bundle = ({}, env.providers["providers"]["xai_oauth"], "saved-revision")
    monkeypatch.setattr(
        auth_store,
        "read_provider_oauth_bundle_snapshot",
        lambda provider: deepcopy(bundle),
    )

    def read(*, refresh_if_needed, _snapshot):
        assert refresh_if_needed is False and _snapshot == bundle
        return credentials

    monkeypatch.setattr(xai_oauth, "xai_oauth_runtime_credentials", read)
    monkeypatch.setattr(xai_oauth, "xai_oauth_base_url", lambda: "https://api.x.ai/v1")
    monkeypatch.setattr(
        xai_oauth,
        "refresh_xai_oauth_token",
        lambda *a, **k: pytest.fail("unexpected refresh"),
    )
    captured = env.policy.capture_provider_policy("motion")
    assert credentials.access_token not in json.dumps(captured)
    env.policy.validate_provider("motion", captured["video_model"], captured)
    credentials.account_id = "replacement-account"
    with pytest.raises(ValueError, match="buddy_policy_changed"):
        env.policy.validate_provider("motion", captured["video_model"], captured)


def test_no_provider_or_registry_import_for_settings_and_cancel(env, monkeypatch):
    monkeypatch.delitem(sys.modules, "row_bot.tools.registry")
    env.policy.validate_action("update")
    env.policy.validate_action("cancel")
    assert env.policy.capture_provider_policy("still")["media"] == {}
    with pytest.raises(ValueError, match="buddy_tool_unavailable"):
        env.policy.capture_provider_policy("full")


def test_fresh_capture_never_initializes_tools_or_provider_runtime(tmp_path):
    code = """
import sys
from row_bot.application.buddy_policy import create_buddy_policy
p = create_buddy_policy(read_context=lambda: {'approval_mode': 'approve'}, validate=lambda: None,
                        validate_confirmation=lambda *args: None)
try:
    p.capture_provider_policy('full')
except ValueError as error:
    assert str(error) == 'buddy_tool_unavailable', str(error)
else:
    raise AssertionError('uninitialized tools were admitted')
assert not {'row_bot.tools', 'row_bot.tools.registry', 'row_bot.tools.image_gen_tool',
            'row_bot.tools.video_gen_tool', 'openai', 'google.genai'} & set(sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "ROW_BOT_DATA_DIR": str(tmp_path / "isolated")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "selection",
    [
        "gpt-image-1",
        "model:openai:gpt-image-1",
        "foreign/model",
        "openai/",
        "openai/model\n",
    ],
)
def test_noncanonical_or_unknown_media_selection_is_never_guessed(env, selection):
    env.values["image_gen"] = selection
    with pytest.raises(ValueError, match="invalid_media_selection"):
        env.policy.capture_provider_policy("full")


def test_changed_model_in_saved_registry_is_not_silently_reconciled(env):
    env.path.write_text(
        json.dumps(
            {
                "tools": {"image_gen": True},
                "tool_configs": {"image_gen": {"model": "openai/gpt-image-1"}},
            }
        )
    )
    before = env.path.read_bytes()
    with pytest.raises(ValueError, match="buddy_policy_changed"):
        env.policy.capture_provider_policy("full")
    assert env.path.read_bytes() == before


def test_oversized_or_corrupt_saved_registry_is_not_fallback_authority(env):
    for data in (b"{broken", b" " * (1024 * 1024 + 1)):
        env.path.write_bytes(data)
        with pytest.raises(ValueError, match="buddy_policy_unavailable"):
            env.policy.capture_provider_policy("full")
        assert env.path.read_bytes() == data
