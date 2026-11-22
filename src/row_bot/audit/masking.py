"""Secret masking for audit records: secret-shaped text is always replaced by ``[redacted]``.

The codebase's own redaction helpers (``learning.redaction``, ``evolution.redact_secrets``,
``migration.redaction``) import the rest of ``row_bot`` (the secret store, channels, MCP logging), and
``row_bot.audit`` must import only the standard library so the Org server can use it without the app. So this is
a small stdlib copy of their *shape* rules, joined into one list; it can't know the secrets a device holds, which
is why free text is only recorded at the ``summaries`` level and identifiers that look like a secret are refused
outright rather than masked.
"""

from __future__ import annotations

import re

MASK = "[redacted]"

_SHAPES = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),  # OpenAI- and Anthropic-style keys
    re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{16,}"),  # Stripe
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{16,}"),  # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"\bxapp-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),  # Google API key
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.DOTALL),
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}"),  # Authorization header values
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@"),  # credentials in a URL
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?key|token|secret|password|passwd|credential)s?\b\s*[:=]\s*"
               r"['\"]?[^'\"\s,;]{8,}"),  # key=value and key: value
)
_QUOTED = (  # A quoted value under a credential-like name: JSON, or a Python dict as text.
    re.compile(r'(?i)("[^"\\\n]{0,64}?(?:api[_-]?key|secret|password|passwd|authorization|token|private[_-]?key'
               r'|credential|cookie)[^"\\\n]{0,64}?"\s*:\s*)"(?:[^"\\\n]|\\.)+"'),
    re.compile(r"(?i)('[^'\\\n]{0,64}?(?:api[_-]?key|secret|password|passwd|authorization|token|private[_-]?key"
               r"|credential|cookie)[^'\\\n]{0,64}?'\s*:\s*)'(?:[^'\\\n]|\\.)+'"),
)


def looks_like_secret(text: str) -> bool:
    """Whether ``text`` holds anything secret-shaped (anything ``mask_text`` would change)."""
    return mask_text(text) != text


def mask_text(text: str) -> str:
    """``text`` with every secret-shaped part replaced by ``[redacted]``."""
    for pattern in _SHAPES:
        text = pattern.sub(MASK, text)
    for pattern in _QUOTED:
        text = pattern.sub(lambda match: f'{match.group(1)}"{MASK}"', text)
    return text
