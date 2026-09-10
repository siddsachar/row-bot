"""Freeze trusted profile controls before the shared execution admission."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from row_bot.application.client_platform import ClientPlatformError


def freeze_profile(configurable: dict[str, Any], *, frozen: bool = False) -> None:
    """Retain an accepted snapshot and intersect, never widen, explicit tool gates.

    Only server-created runtime configuration reaches this helper. The profile
    registry remains the owner of instructions, skills and policy fields.
    """
    frozen = frozen or configurable.get("agent_profile_frozen") is True
    reference = str(configurable.get("agent_profile_id") or "").strip()
    snapshot = configurable.get("agent_profile_snapshot")
    if not isinstance(snapshot, dict) or not snapshot:
        if reference:
            if frozen:
                raise ClientPlatformError("profile_snapshot_unavailable")
            from row_bot.agent_profiles import get_agent_profile
            snapshot = get_agent_profile(reference, enabled_only=True)
            if not snapshot:
                raise ClientPlatformError("profile_unavailable")
        else:
            snapshot = {}
    snapshot = deepcopy(snapshot)
    configurable["agent_profile_id"] = str(snapshot.get("id") or reference)
    configurable["agent_profile_snapshot"] = snapshot
    configurable["agent_profile_frozen"] = True
    policy = snapshot.get("tool_policy_json") or {}
    allowed = [str(value).strip() for value in (policy.get("allow_tools") or []) if str(value).strip()] if isinstance(policy, dict) else []
    # A profile's empty list means inherit, matching the retained owner. An
    # explicit runtime [] remains deny-all rather than becoming unrestricted.
    if "tool_allowlist" in configurable and configurable["tool_allowlist"] is not None:
        existing = configurable["tool_allowlist"]
        existing = [existing] if isinstance(existing, str) else list(existing)
        configurable["tool_allowlist"] = [str(value).strip() for value in existing
                                            if str(value).strip() and (not allowed or str(value).strip() in allowed)]
    elif allowed:
        configurable["tool_allowlist"] = list(dict.fromkeys(allowed))
