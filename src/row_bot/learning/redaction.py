"""What Learning never keeps: secrets.

Capture redacts content *before* it becomes a blob or a record, with the
codebase's own rules:

- **Secret-shaped text**: ``evolution.redact_secrets`` (provider keys, GitHub
  tokens, bearer credentials, ``key=value`` secrets), plus token shapes those
  patterns don't name (Slack, AWS, Google, JWTs, private key blocks) and secrets
  written as a quoted value under a credential-like name (``"api_key": "…"``).
- **The secrets this Row-Bot holds** (saved API keys, provider keys and channel
  tokens), wherever they appear, as given or URL-encoded:
  ``mcp_client.logging.redact``.
- **Credential arguments**: a tool argument named like a credential
  (``migration.redaction.is_sensitive_key``: keys, tokens, passwords, auth
  headers) and every environment or header value (as
  ``mcp_client.logging.mask_mapping`` treats them) is dropped.

Every function says whether it removed anything, so the record can be marked
``redacted``. Nothing here logs content.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

REDACTED = "[redacted]"

# Known secrets shorter than this are left to the patterns: short values (a PIN, a
# user id) would replace ordinary text.
MIN_KNOWN_SECRET = 16
_KNOWN_REFRESH_SECONDS = 300.0

_TOKEN_SHAPES = (
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bxapp-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)
_CREDENTIAL_NAME = (
    r"(?:api[_-]?key|apikey|access[_-]?key|secret|password|passwd|authorization|bearer|token"
    r"|private[_-]?key|session[_-]?key|credential|cookie)"
)
# A quoted value under a credential-like name: JSON, or a Python dict as text.
_QUOTED_SECRETS = (
    re.compile(r'(?i)("[^"\\\n]{0,64}?' + _CREDENTIAL_NAME + r'[^"\\\n]{0,64}?"\s*:\s*)"(?:[^"\\\n]|\\.)+"'),
    re.compile(r"(?i)('[^'\\\n]{0,64}?" + _CREDENTIAL_NAME + r"[^'\\\n]{0,64}?'\s*:\s*)'(?:[^'\\\n]|\\.)+'"),
)
# Argument names whose every value is a secret (environment variables, headers).
_SECRET_CONTAINERS = frozenset({"env", "headers", "cookies", "cookie"})

_known_lock = threading.Lock()
_known: tuple[int, float, tuple[str, ...]] | None = None


def _known_values() -> tuple[str, ...]:
    """The secrets this Row-Bot holds, longest first. Cached until a secret changes."""

    global _known
    try:
        from row_bot import secret_store

        changes = secret_store.change_count()
    except Exception:
        changes = -1
    now = time.monotonic()
    with _known_lock:
        cached = _known
        if cached is not None and cached[0] == changes and now - cached[1] < _KNOWN_REFRESH_SECONDS:
            return cached[2]
    values: set[str] = set()
    try:
        from row_bot import api_keys

        for name in sorted(api_keys.saved_key_names()):
            value = str(api_keys.get_key(name) or "").strip()
            if len(value) >= MIN_KNOWN_SECRET:
                values.add(value)
    except Exception:
        logger.debug("Learning could not list the saved keys to redact")
    try:
        from row_bot.channels.auth_store import get_channel_secret
        from row_bot.channels.passive_catalog import passive_core_channels

        for channel in passive_core_channels():
            for field in channel.config_fields:
                key = str(getattr(field, "env_key", "") or "")
                if not key:
                    continue
                try:
                    value = str(get_channel_secret(channel.name, key) or "").strip()
                except Exception:
                    continue
                if len(value) >= MIN_KNOWN_SECRET:
                    values.add(value)
    except Exception:
        logger.debug("Learning could not list the channel secrets to redact")
    found = tuple(sorted(values, key=len, reverse=True))
    with _known_lock:
        _known = (changes, now, found)
    return found


def forget_known_secrets() -> None:
    """Drop the cached list (tests; a secret saved outside the secret store)."""

    global _known
    with _known_lock:
        _known = None


def redact_text(text: str) -> tuple[str, bool]:
    """``text`` without secrets, and whether anything was removed."""

    if not text:
        return text, False
    from row_bot.evolution import redact_secrets
    from row_bot.mcp_client.logging import redact as redact_known

    value = redact_secrets(text)
    for pattern in _TOKEN_SHAPES:
        value = pattern.sub(REDACTED, value)
    for pattern in _QUOTED_SECRETS:
        value = pattern.sub(lambda match: f'{match.group(1)}"{REDACTED}"', value)
    known = _known_values()
    if known:
        value = redact_known(value, list(known))
    return value, value != text


def is_credential_argument(name: str) -> bool:
    """A tool argument (or header, or environment variable) that holds a credential."""

    from row_bot.migration.redaction import is_sensitive_key

    return is_sensitive_key(name) or str(name).strip().lower() in _SECRET_CONTAINERS


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == {} or value == []


def redact_arguments(value: Any) -> tuple[Any, bool]:
    """Tool arguments without secrets: credential arguments are dropped (their value
    becomes ``[redacted]``), and every other text is redacted."""

    return _redact(value, arguments=True)


def redact_content(value: Any) -> tuple[Any, bool]:
    """A message or other content (dicts, lists, text) without secrets.

    Tool-call arguments inside it (``args``, ``arguments``) are treated as
    :func:`redact_arguments`; keys elsewhere are kept as they are (``input_tokens``
    is a count, not a token).
    """

    return _redact(value, arguments=False)


def _redact(value: Any, *, arguments: bool) -> tuple[Any, bool]:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        changed = False
        out: dict[Any, Any] = {}
        for key, item in value.items():
            name = str(key)
            if arguments and is_credential_argument(name) and not _empty(item):
                out[key] = REDACTED
                changed = True
                continue
            inner_arguments = arguments or name in {"args", "arguments"}
            new, item_changed = _redact(item, arguments=inner_arguments)
            out[key] = new
            changed = changed or item_changed
        return out, changed
    if isinstance(value, (list, tuple)):
        changed = False
        items = []
        for item in value:
            new, item_changed = _redact(item, arguments=arguments)
            items.append(new)
            changed = changed or item_changed
        return (tuple(items) if isinstance(value, tuple) else items), changed
    return value, False


__all__ = [
    "MIN_KNOWN_SECRET",
    "REDACTED",
    "forget_known_secrets",
    "is_credential_argument",
    "redact_arguments",
    "redact_content",
    "redact_text",
]
