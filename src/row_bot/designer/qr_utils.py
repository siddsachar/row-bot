"""Phase 2.2.M — QR code generation helpers for the publish flow.

A thin wrapper around the ``qrcode`` library so the designer editor
(and any future caller) can turn a published URL into a base64
``data:`` URI ready to drop into ``ui.image``.

All helpers are pure and do not depend on NiceGUI.
"""

from __future__ import annotations

import base64
import io


def generate_qr_png_b64(
    url: str,
    *,
    box_size: int = 8,
    border: int = 2,
) -> str:
    """Return a ``data:image/png;base64,...`` string encoding a QR for ``url``.

    Returns an empty string for an empty URL or if the ``qrcode`` library
    is not installed. The caller should fall back to displaying the raw
    URL in that case.
    """
    target = (url or "").strip()
    if not target:
        return ""

    try:
        import qrcode  # type: ignore
    except Exception:  # pragma: no cover - dependency missing
        return ""

    try:
        img = qrcode.make(target, box_size=max(1, int(box_size)),
                          border=max(0, int(border)))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return ""

    return f"data:image/png;base64,{b64}"


def decode_qr_data_uri(data_uri: str) -> bytes:
    """Inverse of :func:`generate_qr_png_b64`. Used by tests."""
    if not data_uri.startswith("data:image/png;base64,"):
        return b""
    payload = data_uri.split(",", 1)[1]
    try:
        return base64.b64decode(payload)
    except Exception:
        return b""
