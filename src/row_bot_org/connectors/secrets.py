"""A connector's secret at rest: sealed with the server key (``ROW_BOT_ORG_GATEWAY_KEY``), AES-256-GCM, as S4 seals
endpoint keys (``row_bot_org.gateway.keys``).

The secret is a Microsoft app's client secret or a Google service account's private key (PEM). A sealed secret is a
12-byte random nonce followed by the ciphertext and its tag. The associated data is the connector's id, its kind,
its tenant and its client id (the service account's email for Google), so a sealed secret copied onto another
connector's row, or left on a row whose tenant or client was changed behind the API, doesn't open. A copy of the
database without the server key holds no usable secret.
"""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12


class SecretUnavailable(Exception):
    """A sealed secret that doesn't open here: another server key, connector, tenant or client."""


def _associated(connector_id: str, kind: str, tenant_id: str | None, client_id: str) -> bytes:
    return f"row-bot-org/connector-secret/v1\0{connector_id}\0{kind}\0{tenant_id or ''}\0{client_id}".encode()


def seal(server_key: bytes, connector_id: str, kind: str, tenant_id: str | None, client_id: str,
         secret: str) -> bytes:
    nonce = os.urandom(_NONCE_BYTES)
    return nonce + AESGCM(server_key).encrypt(nonce, secret.encode("utf-8"),
                                              _associated(connector_id, kind, tenant_id, client_id))


def unseal(server_key: bytes, connector_id: str, kind: str, tenant_id: str | None, client_id: str,
           sealed: bytes) -> str:
    try:
        plain = AESGCM(server_key).decrypt(sealed[:_NONCE_BYTES], sealed[_NONCE_BYTES:],
                                           _associated(connector_id, kind, tenant_id, client_id))
    except InvalidTag:
        raise SecretUnavailable("the connector's secret doesn't open here") from None
    return plain.decode("utf-8")


__all__ = ["SecretUnavailable", "seal", "unseal"]
