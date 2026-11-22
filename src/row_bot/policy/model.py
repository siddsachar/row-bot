"""The effective policy: ``NoPolicy``, ``OrgPolicy`` or ``Lockdown``, and the checks the seams will call.

``NoPolicy`` allows everything and changes nothing: it is today's Row-Bot. ``OrgPolicy`` applies an
organisation's settings. ``Lockdown`` applies the strictest documented values (``schema.LOCKDOWN``) because a
policy is present but can't be used; ``reason`` says why in a stable word for the client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from row_bot.approval_policy import ApprovalMode, stricter_approval_mode
from row_bot.policy.schema import LOCKDOWN, SUBSCRIPTION_PROVIDERS, Settings

SourceKind = Literal["registry", "managed_preferences", "file", "env_file"]
ExtensionKind = Literal["plugins", "mcp_servers", "skills"]
Feature = Literal["learning", "proactive", "remote_access", "data_export", "install_count"]
LockdownReason = Literal[
    "unreadable",  # present, but couldn't be read
    "untrusted_location",  # a person could have written it
    "invalid",  # not a valid v1 policy (unknown key, wrong type, bad value, duplicate key)
    "unsupported_schema_version",
    "keys_invalid",  # the pinned keys file can't be used
    "signature_missing",  # a signature is required and there is none
    "signature_invalid",  # a signature is present and no pinned key made it
    "signature_key_missing",  # a signature is present but no keys are pinned
]


@dataclass(frozen=True)
class PolicySource:
    """Where a policy was found: its kind and its location (registry path or file path)."""

    kind: SourceKind
    location: str


@dataclass(frozen=True)
class IgnoredSource:
    """A policy source that is present but not used, and why."""

    source: PolicySource
    reason: str


class _Rules:
    """The checks POL-2's seams call. Without settings (``NoPolicy``) every check allows."""

    settings: Settings | None

    def limit_approval_level(self, level: ApprovalMode) -> ApprovalMode:
        """*level*, lowered to the highest level the policy allows (Hands-off needs ``max_level`` full)."""
        if self.settings is None:
            return level
        return stricter_approval_mode(level, self.settings.approvals.max_level)

    def allows_provider(self, provider_id: str) -> bool:
        if self.settings is None:
            return True
        models = self.settings.models
        if provider_id in SUBSCRIPTION_PROVIDERS and not models.subscription_sign_ins:
            return False
        return models.allowed_providers is None or provider_id in models.allowed_providers

    def allows_model(self, model_ref: str) -> bool:
        """Whether a provider-qualified ref (``model:<provider>:<model id>``) may be used."""
        if self.settings is None:
            return True
        prefix, _, rest = model_ref.partition(":")
        provider, _, model = rest.partition(":")
        if prefix != "model" or not provider or not model or not self.allows_provider(provider):
            return False
        allowed = self.settings.models.allowed_models
        return allowed is None or model_ref in allowed

    def allows_personal_api_keys(self) -> bool:
        return self.settings is None or self.settings.models.personal_api_keys

    def allows_local_models(self) -> bool:
        return self.settings is None or self.settings.models.local_models

    def allows_tool(self, name: str) -> bool:
        if self.settings is None:
            return True
        disabled = self.settings.tools.disabled
        return "*" not in disabled and name not in disabled

    def allows_files_outside_workspace(self) -> bool:
        return self.settings is None or self.settings.tools.files_outside_workspace

    def allows_channel(self, channel_id: str) -> bool:
        return self.settings is None or channel_id in self.settings.channels.allowed

    def allows_extension(self, kind: ExtensionKind, extension_id: str) -> bool:
        if self.settings is None:
            return True
        extensions = self.settings.extensions
        mode = getattr(extensions, kind)
        return mode == "any" or (mode == "allow-list" and extension_id in getattr(extensions, f"allowed_{kind}"))

    def allows_feature(self, feature: Feature) -> bool:
        return self.settings is None or bool(getattr(self.settings.features, feature))


@dataclass(frozen=True)
class NoPolicy(_Rules):
    """No organisation policy: Row-Bot behaves exactly as it does without one."""

    kind: Literal["none"] = "none"
    settings: None = None
    source: None = None
    signer: None = None
    ignored: tuple[IgnoredSource, ...] = ()


@dataclass(frozen=True)
class OrgPolicy(_Rules):
    """An organisation's policy, read and verified. ``signer`` is the pinned key id that signed it, if any."""

    settings: Settings
    source: PolicySource
    signer: str | None = None
    ignored: tuple[IgnoredSource, ...] = ()
    kind: Literal["org"] = "org"


@dataclass(frozen=True)
class Lockdown(_Rules):
    """A policy is present but can't be used, so the strictest values apply until IT fixes it."""

    reason: LockdownReason
    detail: str
    source: PolicySource
    ignored: tuple[IgnoredSource, ...] = ()
    settings: Settings = field(default=LOCKDOWN, init=False)
    signer: None = None
    kind: Literal["lockdown"] = "lockdown"


Policy = NoPolicy | OrgPolicy | Lockdown
