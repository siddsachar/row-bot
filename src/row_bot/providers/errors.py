from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ProviderErrorKind(StrEnum):
    AUTHENTICATION = "authentication"
    SUBSCRIPTION_MISSING = "subscription_missing"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    MODEL_UNAVAILABLE = "model_unavailable"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    CONTEXT_EXCEEDED = "context_exceeded"
    POLICY_BLOCKED = "policy_blocked"
    TIMEOUT = "timeout"
    PROVIDER_OUTAGE = "provider_outage"
    CREDENTIAL_MISSING = "credential_missing"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class NormalizedProviderError:
    kind: ProviderErrorKind
    message: str
    next_action: str


def normalize_provider_error(exc: BaseException) -> NormalizedProviderError:
    text = str(exc or "")
    lower = text.lower()
    if any(part in lower for part in ("subscription", "plan required", "billing required", "upgrade your plan")):
        return NormalizedProviderError(ProviderErrorKind.SUBSCRIPTION_MISSING, text, "Connect an account with access to this model or choose another provider.")
    if "api key" in lower or "unauthorized" in lower or "401" in lower or "auth" in lower:
        return NormalizedProviderError(ProviderErrorKind.AUTHENTICATION, text, "Reconnect the provider or update its credential.")
    if any(part in lower for part in ("not a chat model", "not chat", "unsupported capability", "unsupported modality", "does not support tools", "tools are not supported", "tool calls not supported", "invalid tool schema")):
        return NormalizedProviderError(ProviderErrorKind.UNSUPPORTED_CAPABILITY, text, "Choose a model whose capability badges match this surface.")
    if any(part in lower for part in ("model not found", "model unavailable", "does not exist", "unknown model")):
        return NormalizedProviderError(ProviderErrorKind.MODEL_UNAVAILABLE, text, "Refresh the provider catalog or choose another model.")
    if "quota" in lower or "credit" in lower:
        return NormalizedProviderError(ProviderErrorKind.QUOTA_EXHAUSTED, text, "Choose another model or add provider credits.")
    if "rate" in lower and "limit" in lower:
        return NormalizedProviderError(ProviderErrorKind.RATE_LIMITED, text, "Retry later or switch to another configured provider.")
    if "context" in lower or "token" in lower and "maximum" in lower:
        return NormalizedProviderError(ProviderErrorKind.CONTEXT_EXCEEDED, text, "Reduce context or choose a larger-context model.")
    if "timeout" in lower:
        return NormalizedProviderError(ProviderErrorKind.TIMEOUT, text, "Retry or choose a faster route.")
    if "5" in lower and ("server" in lower or "unavailable" in lower):
        return NormalizedProviderError(ProviderErrorKind.PROVIDER_OUTAGE, text, "Retry later or switch providers.")
    return NormalizedProviderError(ProviderErrorKind.UNKNOWN, text, "Check provider status and credentials.")