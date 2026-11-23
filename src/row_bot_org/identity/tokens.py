"""The random secrets sign-in hands out, and the verifiers the database keeps instead of them.

Every secret is 256 bits from ``secrets``. A verifier is ``HMAC-SHA256(pepper, purpose || secret)`` (F-13):
with secrets this long a slow hash adds nothing, and without the pepper (the server's secret setting) a
copy of the database can't be turned back into a working cookie. Each purpose is its own label, so a
verifier of one kind can never stand for another.

A device's credentials (``identity.device``) are secrets of the same kind with their own prefixes and purposes:
the one-time authorization code (``rboc_``), the access token (``rboa_``) and the refresh token (``rbor_``).
``device-request`` labels the MAC that signs a device sign-in request carried through the browser, and
``device-form`` the verifier of a confirm form's one-time nonce.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

SESSION_PREFIX = "rbos_"
CODE_PREFIX = "rboc_"
ACCESS_PREFIX = "rboa_"
REFRESH_PREFIX = "rbor_"
_SECRET_BYTES = 32
_SECRET = re.compile(r"^[A-Za-z0-9_-]{43}$")
# RFC 7636 §4.1: 43 to 128 unreserved characters.
_CODE_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
PURPOSES = ("session", "sign-in-state", "sign-in-browser", "sign-in-nonce", "device-code", "device-access",
            "device-refresh", "device-request", "device-form")


def new_secret() -> str:
    """43 URL-safe characters: 256 random bits."""
    return secrets.token_urlsafe(_SECRET_BYTES)


def verifier(pepper: str, purpose: str, secret: str) -> bytes:
    """The 32-byte verifier of ``secret`` for ``purpose``."""
    if purpose not in PURPOSES:
        raise ValueError(f"unknown verifier purpose {purpose!r}")
    label = f"row-bot-org/v1/{purpose}\x00".encode("ascii")
    return hmac.new(pepper.encode("utf-8"), label + secret.encode("utf-8"), hashlib.sha256).digest()


def new_session_token() -> str:
    return SESSION_PREFIX + new_secret()


def prefixed_secret(prefix: str, token: str | None) -> str | None:
    """The secret in ``token`` when it is ``prefix`` and a secret this server issues, else None."""
    if not token or not token.startswith(prefix):
        return None
    secret = token[len(prefix):]
    return secret if _SECRET.fullmatch(secret) else None


def session_secret(token: str | None) -> str | None:
    """The secret in a session cookie's value, or None when it isn't one this server issues."""
    return prefixed_secret(SESSION_PREFIX, token)


def is_secret(value: str | None) -> bool:
    return bool(value) and _SECRET.fullmatch(value or "") is not None


def is_code_verifier(value: str | None) -> bool:
    """Whether ``value`` is a PKCE code verifier as RFC 7636 allows one."""
    return isinstance(value, str) and _CODE_VERIFIER.fullmatch(value) is not None


def pkce_challenge(code_verifier: str) -> str:
    """RFC 7636 S256: BASE64URL(SHA256(ASCII(code_verifier))), unpadded."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


__all__ = ["ACCESS_PREFIX", "CODE_PREFIX", "PURPOSES", "REFRESH_PREFIX", "SESSION_PREFIX", "is_code_verifier",
           "is_secret", "new_secret", "new_session_token", "pkce_challenge", "prefixed_secret", "session_secret",
           "verifier"]
