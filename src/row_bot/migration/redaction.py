"""Redaction helpers for migration plans and reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "[redacted]"

_SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "access_key",
    "secret",
    "token",
    "password",
    "passwd",
    "bearer",
    "authorization",
    "auth_token",
    "client_secret",
    "private_key",
    "refresh_token",
    "session_key",
)

_SENSITIVE_VALUE_PREFIXES = (
    "sk-",
    "sk_",
    "xoxb-",
    "xapp-",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "github_pat_",
)


def is_sensitive_key(key: str) -> bool:
    """Return True when a mapping key is likely to carry a secret."""
    normalized = str(key).strip().lower().replace("-", "_")
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _looks_like_secret_value(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith(("bearer ", "basic ")):
        return True
    if any(text.startswith(prefix) for prefix in _SENSITIVE_VALUE_PREFIXES):
        return True
    if len(text) >= 32 and any(ch.isdigit() for ch in text) and any(ch.isalpha() for ch in text):
        return True
    return False


def redact_value(value: Any, *, key: str | None = None) -> Any:
    """Return a JSON-safe copy with secret-looking values replaced.

    Redaction is deliberately conservative for migration reports: key names win,
    then obvious token-shaped string values are masked. Non-secret structure is
    preserved so users can review paths, labels, and config shape.
    """
    if key is not None and is_sensitive_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, str):
        return REDACTED if _looks_like_secret_value(value) else value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_value(item) for item in value]
    return value


def redact_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Return a redacted plain dict copy of *mapping*."""
    redacted: dict[str, Any] = {}
    for key, value in mapping.items():
        redacted[str(key)] = redact_value(value, key=str(key))
    return redacted
