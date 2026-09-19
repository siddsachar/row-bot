"""Canonical media/profile/approval capture for explicit Buddy commands.

This adapter never constructs SDK clients, discovers models or refreshes OAuth.
The trusted root supplies current conversation context and its original reviewed
confirmation. Only keyed identities leave the account inspection helper.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import hmac
import json
import os
import sys
from row_bot.providers.media_auth import CapturedMediaAuth, validate_auth

from row_bot.application.client_platform import ClientPlatformError
from row_bot.approval_policy import decision_for_action, normalize_approval_mode
from row_bot.runtime import admissions


@dataclass(frozen=True)
class BuddyPolicyCallbacks:
    capture_provider_policy: Callable[[str], dict]
    validate_action: Callable[[str], None]
    validate_provider: Callable[[str, str, dict], CapturedMediaAuth]


def _bounded(value, limit=65536):
    try:
        if len(json.dumps(value, allow_nan=False).encode()) > limit:
            raise ValueError
        return deepcopy(value)
    except (TypeError, ValueError, RecursionError):
        raise ClientPlatformError("buddy_policy_unavailable") from None


def _media_state(surface: str, context: dict) -> tuple[str, dict]:
    """Inspect the existing registry, never import tools to initialize defaults."""
    registry = sys.modules.get("row_bot.tools.registry")
    if registry is None:
        raise ClientPlatformError("buddy_tool_unavailable")
    from row_bot.providers.media import curated_media_cache_entries
    from row_bot.providers.capabilities import snapshot_supports_surface
    from row_bot.tools.profile_policy import dispatch_refusal

    parent, operation = (
        ("image_gen", "generate_image")
        if surface == "image"
        else ("video_gen", "animate_image")
    )
    if (
        registry is None
        or registry.get_tool(parent) is None
        or registry.is_enabled(parent) is not True
    ):
        raise ClientPlatformError("buddy_tool_unavailable")
    selection = registry.get_tool_config(parent, "model", None)
    if not selection:
        # The actual existing media owner is the sole default-selection owner.
        owner = sys.modules.get("row_bot.tools." + parent + "_tool")
        selection = getattr(owner, "DEFAULT_MODEL", None) if owner is not None else None
    if (
        not isinstance(selection, str)
        or not 1 <= len(selection) <= 256
        or "/" not in selection
    ):
        raise ClientPlatformError("invalid_media_selection")
    # Media owners use provider/model, unlike normal-chat model:provider:model.
    provider, model = selection.split("/", 1)
    if (
        not model
        or model != model.strip()
        or any(not char.isprintable() for char in model)
    ):
        raise ClientPlatformError("invalid_media_selection")
    if provider not in (
        {"openai", "google", "xai", "xai_oauth"}
        if surface == "image"
        else {"google", "xai", "xai_oauth"}
    ):
        raise ClientPlatformError("invalid_media_selection")
    # Explicit stored changes must not silently use a stale process registry.
    path = registry._config_path()
    if path.exists():
        with path.open("rb") as handle:
            raw = handle.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ClientPlatformError("buddy_policy_unavailable")
        stored = json.loads(raw)
        if not isinstance(stored, dict):
            raise ClientPlatformError("buddy_policy_unavailable")
        enabled = stored.get("tools", stored)
        configs = stored.get("tool_configs", {})
        if (
            not isinstance(enabled, dict)
            or not isinstance(configs, dict)
            or not isinstance(configs.get(parent, {}), dict)
        ):
            raise ClientPlatformError("buddy_policy_unavailable")
        if parent in enabled and enabled[parent] is not True:
            raise ClientPlatformError("buddy_tool_unavailable")
        if configs.get(parent, {}).get("model", selection) != selection:
            raise ClientPlatformError("buddy_policy_changed")
    allowlist = context.get("tool_allowlist")
    if allowlist is not None and (
        not isinstance(allowlist, (list, tuple))
        or any(type(item) is not str for item in allowlist)
    ):
        raise ClientPlatformError("buddy_policy_unavailable")
    profile = context.get("agent_profile_snapshot") or {}
    if not isinstance(profile, dict):
        raise ClientPlatformError("buddy_policy_unavailable")
    if dispatch_refusal(
        profile,
        operation,
        {},
        source="core",
        parent=parent,
        allowlist=tuple(allowlist) if allowlist is not None else None,
    ):
        raise ClientPlatformError("buddy_tool_policy_denied")
    entries = curated_media_cache_entries(surface)
    entry = entries.get(selection)
    models = sys.modules.get("row_bot.models")
    cached = getattr(models, "_cloud_model_cache", {}) if models is not None else {}
    if isinstance(cached, dict):
        candidate = cached.get(selection, cached.get(model))
        if isinstance(candidate, dict) and candidate.get("provider") == provider:
            snapshot = candidate.get("capabilities_snapshot")
            if isinstance(snapshot, dict) and snapshot_supports_surface(
                snapshot, surface
            ):
                entry = {"provider": provider, "capabilities_snapshot": snapshot}
    if entry is None:
        raise ClientPlatformError("buddy_media_capability_unavailable")
    policy = context.get("data_policy")
    if policy == "local_only" or policy == "allow_api_key" and provider == "xai_oauth":
        raise ClientPlatformError("buddy_data_policy_denied")
    return selection, {
        "tool": parent,
        "operation": operation,
        "capability": admissions.keyed_digest(_bounded(entry)),
    }


def _account_auth(selection: str) -> CapturedMediaAuth:
    from row_bot.providers import auth_store
    from row_bot.providers.config import load_provider_config

    provider = selection.split("/", 1)[0]
    organization = os.environ.get("OPENAI_ORG_ID", "") if provider == "openai" else ""
    project = os.environ.get("OPENAI_PROJECT_ID", "") if provider == "openai" else ""
    if any(
        len(value) > 256 or any(not char.isprintable() for char in value)
        for value in (organization, project)
    ):
        raise ClientPlatformError("buddy_account_unavailable")

    before = load_provider_config(strict=True).get("providers", {}).get(provider, {})
    if (
        not isinstance(before, dict)
        or before.get("enabled") is False
        or before.get("configured") is False
    ):
        raise ClientPlatformError("buddy_account_unavailable")
    if provider == "xai_oauth":
        from row_bot.providers import xai_oauth

        captured = auth_store.read_provider_oauth_bundle_snapshot(provider)
        if captured[1] != before:
            raise ClientPlatformError("buddy_policy_changed")
        credentials = xai_oauth.xai_oauth_runtime_credentials(
            refresh_if_needed=False, _snapshot=captured
        )
        if (
            not credentials.access_token
            or xai_oauth._expires_soon(credentials.expires_at, skew_seconds=300)
            or before.get("token_health")
            in {"expired", "entitlement_denied", "error", "missing"}
            or before.get("requires_reconnect") is True
            or before.get("entitlement_denied") is True
        ):
            raise ClientPlatformError("buddy_account_unavailable")
        base_url = xai_oauth._validated_xai_base_url(
            before.get("base_url") or xai_oauth.XAI_OAUTH_BASE_URL
        )
        selected = credentials.access_token
        identity = {
            "account": credentials.account_id,
            "user": credentials.user_id,
            "access": credentials.access_token,
            "refresh": credentials.refresh_token,
            "bundle": captured[1],
            "base_url": base_url,
        }
    else:
        selected = auth_store.get_provider_secret(provider)
        if not selected or len(selected) > 16384:
            raise ClientPlatformError("buddy_account_unavailable")
        identity = {"key": selected}
        base_url = {
            "openai": "https://api.openai.com/v1",
            "google": "https://generativelanguage.googleapis.com",
            "xai": "https://api.x.ai/v1",
        }[provider]
    after = load_provider_config(strict=True).get("providers", {}).get(provider, {})
    if before != after:
        raise ClientPlatformError("buddy_policy_changed")
    digest = admissions.keyed_digest(
        _bounded(
            {
                "provider": provider,
                "entry": before,
                "identity": identity,
                "organization": organization,
                "project": project,
            }
        )
    )
    auth = CapturedMediaAuth(
        selection, provider, selected, digest, base_url, organization, project
    )
    validate_auth(auth, selection)
    return auth


def create_buddy_policy(
    *,
    read_context: Callable[[], dict],
    validate: Callable[[], None],
    validate_confirmation: Callable[[str, dict], None],
) -> BuddyPolicyCallbacks:
    """Return callbacks bound to the root's current authenticated conversation.

    read_context supplies canonical approval_mode and a frozen profile snapshot /
    tool_allowlist. Confirmation must revalidate the same admitted command and
    review; it must not create another approval or blindly accept a browser flag.
    """

    def context() -> dict:
        validate()
        value = _bounded(read_context())
        if not isinstance(value, dict):
            raise ClientPlatformError("buddy_policy_unavailable")
        if value.get("agent_profile_id") and (
            not isinstance(value.get("agent_profile_snapshot"), dict)
            or not value["agent_profile_snapshot"]
        ):
            raise ClientPlatformError("buddy_policy_unavailable")
        value["approval_mode"] = normalize_approval_mode(value.get("approval_mode"))
        validate()
        return value

    def action_allowed(action: str, current: dict, *, confirming: bool) -> None:
        if action not in {"full", "motion", "still", "remove", "update", "cancel"}:
            raise ClientPlatformError("invalid_hatch_request")
        if action == "cancel":
            return  # Exact original owner validation remains in buddy_commands.
        decision = decision_for_action(current["approval_mode"])
        if decision == "block":
            raise ClientPlatformError("buddy_action_blocked")
        if action == "update":
            from row_bot.developer.edits import ordinary_edit_decision

            if ordinary_edit_decision(current["approval_mode"]).decision != "allow":
                raise ClientPlatformError("buddy_action_blocked")
        elif decision == "ask" and confirming:
            validate_confirmation(action, deepcopy(current))

    def capture(action: str) -> dict:
        current = context()
        action_allowed(action, current, confirming=False)
        result = {
            "image_model": None,
            "video_model": None,
            "action": action,
            "context_identity": admissions.keyed_digest(current),
            "media": {},
        }
        try:
            for surface in (
                ("image", "video")
                if action == "full"
                else ("video",)
                if action == "motion"
                else ()
            ):
                selection, identity = _media_state(surface, current)
                identity["account"] = _account_auth(selection).identity
                result[surface + "_model"] = selection
                result["media"][surface] = identity
        except ClientPlatformError:
            raise
        except Exception:
            raise ClientPlatformError("buddy_policy_unavailable") from None
        if admissions.keyed_digest(context()) != result["context_identity"]:
            raise ClientPlatformError("buddy_policy_changed")
        return result

    def action(action: str) -> None:
        current = context()
        action_allowed(action, current, confirming=True)
        validate()

    def provider(media: str, selection: str, captured: dict) -> CapturedMediaAuth:
        surface = {"image": "image", "motion": "video"}.get(media)
        if (
            surface is None
            or not isinstance(captured, dict)
            or captured.get(surface + "_model") != selection
        ):
            raise ClientPlatformError("buddy_policy_changed")
        current = capture(captured.get("action"))
        if not hmac.compare_digest(
            admissions.keyed_digest(current),
            admissions.keyed_digest(_bounded(captured)),
        ):
            raise ClientPlatformError("buddy_policy_changed")
        action(captured["action"])
        auth = _account_auth(selection)
        if auth.identity != captured["media"][surface]["account"]:
            raise ClientPlatformError("buddy_policy_changed")
        validate()
        return auth

    return BuddyPolicyCallbacks(capture, action, provider)
