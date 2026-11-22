"""The gateway's setting: ``ROW_BOT_ORG_GATEWAY_KEY``, read by ``row_bot_org.settings``.

It is the server key that seals every endpoint key in the database (``gateway.keys``): 32 random
bytes, base64-encoded, for example from
``python -c "import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"``.
It is a secret: removed from the environment once read and never in the settings' ``repr``. Without
it the server has no gateway: no model routes.

Standard library only: the settings import this at start.
"""

from __future__ import annotations

import base64
import binascii

GATEWAY_KEY_SUFFIX = "GATEWAY_KEY"
KEY_BYTES = 32


def parse_gateway_key(raw: str) -> bytes:
    """The 32-byte key in ``raw``; ``ValueError`` worded without the value."""
    value = raw.strip()
    try:
        standard = value.replace("-", "+").replace("_", "/").rstrip("=")
        key = base64.b64decode(standard + "=" * (-len(standard) % 4), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("must be 32 random bytes, base64-encoded") from None
    if len(key) != KEY_BYTES:
        raise ValueError("must be 32 random bytes, base64-encoded")
    return key


__all__ = ["GATEWAY_KEY_SUFFIX", "KEY_BYTES", "parse_gateway_key"]
