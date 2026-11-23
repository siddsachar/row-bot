"""Endpoint keys at rest: sealed with the server key (``ROW_BOT_ORG_GATEWAY_KEY``), AES-256-GCM.

A sealed key is a 12-byte random nonce followed by the ciphertext and its tag. The associated data is
the endpoint's id and its origin (scheme, host and port of its base URL), so a sealed key copied onto
another endpoint's row, or left on a row whose address now points at another host, doesn't open. A copy
of the database without the server key holds no usable key.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12
_DEFAULT_PORTS = {"http": 80, "https": 443}


class KeyUnavailable(Exception):
    """A sealed key that doesn't open here: another server key, another endpoint, another origin."""


def endpoint_origin(base_url: str) -> str:
    """``scheme://host:port`` of a base URL, lower case, with the scheme's default port written out."""
    parts = urlsplit(base_url)
    host = (parts.hostname or "").lower()
    host = f"[{host}]" if ":" in host else host
    return f"{parts.scheme.lower()}://{host}:{parts.port or _DEFAULT_PORTS.get(parts.scheme.lower(), 0)}"


def _associated(endpoint_id: str, base_url: str) -> bytes:
    return f"row-bot-org/endpoint-key/v1\0{endpoint_id}\0{endpoint_origin(base_url)}".encode()


def seal(server_key: bytes, endpoint_id: str, base_url: str, api_key: str) -> bytes:
    nonce = os.urandom(_NONCE_BYTES)
    return nonce + AESGCM(server_key).encrypt(nonce, api_key.encode("utf-8"), _associated(endpoint_id, base_url))


def unseal(server_key: bytes, endpoint_id: str, base_url: str, sealed: bytes) -> str:
    try:
        plain = AESGCM(server_key).decrypt(sealed[:_NONCE_BYTES], sealed[_NONCE_BYTES:],
                                           _associated(endpoint_id, base_url))
    except InvalidTag:
        raise KeyUnavailable("the endpoint's key doesn't open here") from None
    return plain.decode("utf-8")


__all__ = ["KeyUnavailable", "endpoint_origin", "seal", "unseal"]
