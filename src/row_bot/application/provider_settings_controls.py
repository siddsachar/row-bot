"""Explicit credential review using canonical provider configuration and secrets.

These operations verify local secure storage only; they never test accounts,
refresh catalogues, or change provider-qualified model selection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from row_bot.providers import credential_controls as credentials
from row_bot.providers.catalog import PROVIDER_DEFINITIONS
from row_bot.providers.config import ProviderConfigError


@dataclass(frozen=True)
class ProviderSettingsSnapshot:
    schema_version: int
    provider_id: str
    display_name: str
    revision: str
    configured: bool
    source: str
    externally_managed: bool
    storage_unavailable: bool
    recovery_available: bool
    runtime_state: Literal["unknown"] = "unknown"


def read_provider_settings(provider_id: str) -> ProviderSettingsSnapshot:
    """Return an allowlisted saved state; never return credential fragments."""
    try:
        state = credentials.credential_snapshot(provider_id)
    except ProviderConfigError:
        raise credentials.CredentialControlError("provider_settings_unavailable") from None
    definition = PROVIDER_DEFINITIONS.get(provider_id)
    return ProviderSettingsSnapshot(
        state["schema_version"], provider_id, definition.display_name if definition else provider_id,
        state["revision"], state["configured"], state["source"],
        state["externally_managed"], state["storage_unavailable"], state["recovery_available"],
    )


def review_provider_credential(provider_id: str, expected_revision: str, operation: str,
                               value: str | None = None) -> ProviderSettingsSnapshot:
    """Review only; no durable admission, write, probe, or network activity."""
    if operation not in {"save", "clear", "restore"}:
        raise credentials.CredentialControlError("invalid_command")
    if operation == "save":
        credentials.normalize_api_key(provider_id, value)
    elif value is not None:
        raise credentials.CredentialControlError("invalid_command")
    snapshot = read_provider_settings(provider_id)
    if snapshot.revision != expected_revision:
        raise credentials.CredentialControlError("revision_conflict", snapshot.revision)
    if snapshot.externally_managed or (snapshot.storage_unavailable and operation != "restore"):
        raise credentials.CredentialControlError("action_denied")
    if operation == "restore" and not snapshot.recovery_available:
        raise credentials.CredentialControlError("provider_recovery_unavailable")
    return snapshot
