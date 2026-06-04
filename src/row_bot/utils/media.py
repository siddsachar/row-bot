"""Media-payload classification helpers.

Throughout Thoth, ``msg["images"]`` entries (and ``gen.captured_images``)
can be either:
  * a **base64-encoded** image string (large, opaque), OR
  * a **filename** that has been spilled/persisted to
    ``~/.thoth/media/<thread_id>/<fname>``.

These helpers provide a single canonical classifier + MIME helper so
callers don't each re-invent a slightly different heuristic.
"""

from __future__ import annotations

from typing import Any


# Known raw-image base64 magic prefixes.  A real base64 image string
# always begins with one of these (the first few bytes of the binary
# header encoded).
_B64_IMAGE_PREFIXES = ("iVBOR", "UklGR", "R0lGO", "/9j/", "AAAA")

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def is_image_filename(value: Any) -> bool:
    """Return True iff *value* looks like a persisted-media filename
    (``cap_001.png``, ``gen_002.jpg`` …) rather than a base64 blob.

    Intentionally conservative: short strings that end in a known image
    extension and don't contain path separators are filenames.  Anything
    else — including long base64 blobs, ``data:`` URIs, absolute paths,
    or non-strings — returns False.
    """
    if not isinstance(value, str) or not value:
        return False
    if len(value) >= 200:
        return False
    if value.startswith(("/", "\\")) or value.startswith("data:"):
        return False
    return value.lower().endswith(_IMAGE_EXTENSIONS)


def is_base64_image(value: Any) -> bool:
    """Return True iff *value* looks like a raw base64 image string.

    Checks the well-known magic prefixes of PNG/WebP/GIF/JPEG/BMP.  A
    ``data:image/...;base64,...`` URI also qualifies — callers that need
    the raw payload should strip the prefix first.
    """
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("data:"):
        return True
    return value.startswith(_B64_IMAGE_PREFIXES)


def image_ext_from_b64(b64: str) -> str:
    """Determine the file extension for a raw base64 image by magic
    prefix.  Falls back to ``"jpg"`` when the prefix is unknown."""
    if not isinstance(b64, str):
        return "jpg"
    if b64.startswith("iVBOR"):
        return "png"
    if b64.startswith("UklGR"):
        return "webp"
    if b64.startswith("R0lGO"):
        return "gif"
    return "jpg"


def image_mime_from_bytes(data: bytes) -> str:
    """Inspect magic bytes of *data* to return a precise image MIME type.

    Falls back to ``"image/jpeg"`` for unknown/ambiguous bytes.
    """
    if not data:
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return "image/jpeg"
