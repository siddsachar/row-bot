"""Endpoint keys at rest: sealed with the server key (``ROW_BOT_ORG_GATEWAY_KEY``), AES-256-GCM.

A sealed key is a 12-byte random nonce followed by the ciphertext and its tag. The endpoint's id is
the associated data, so a sealed key copied onto another endpoint's row doesn't open. A copy of the
database without the server key holds no usable key.
"""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12


class KeyUnavailable(Exception):
    """A sealed key that doesn't open with this server key (another server's key, or a moved row)."""


def seal(server_key: bytes, endpoint_id: str, api_key: str) -> bytes:
    nonce = os.urandom(_NONCE_BYTES)
    return nonce + AESGCM(server_key).encrypt(nonce, api_key.encode("utf-8"), endpoint_id.encode("ascii"))


def unseal(server_key: bytes, endpoint_id: str, sealed: bytes) -> str:
    try:
        plain = AESGCM(server_key).decrypt(sealed[:_NONCE_BYTES], sealed[_NONCE_BYTES:], endpoint_id.encode("ascii"))
    except InvalidTag:
        raise KeyUnavailable("the endpoint's key doesn't open with this server's gateway key") from None
    return plain.decode("utf-8")


__all__ = ["KeyUnavailable", "seal", "unseal"]
