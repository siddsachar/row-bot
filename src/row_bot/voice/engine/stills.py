"""Stills from the camera or screen the person shares during voice.

The browser sends one downscaled still when Row-Bot needs it, never a video
stream. A still lives only in the voice session's memory until it is attached
to a spoken turn (then it is saved like any image the person attaches) or
dropped: when sharing stops, the session pauses or voice ends.
"""
from __future__ import annotations

from dataclasses import dataclass
import io
from typing import Literal

#: Largest still accepted from the browser.
MAX_STILL_BYTES = 1024 * 1024
#: The browser scales a still to at most this many pixels on its long side.
MAX_STILL_SIDE = 768

ShareSource = Literal["camera", "screen"]
SHARE_SOURCES: tuple[ShareSource, ...] = ("camera", "screen")


@dataclass(frozen=True)
class Still:
    data: bytes
    mime_type: str
    source: ShareSource

    @property
    def name(self) -> str:
        """The attachment's name: what the chat and the model see for it."""
        stem = "Live camera" if self.source == "camera" else "Shared screen"
        return stem + (".png" if self.mime_type == "image/png" else ".jpg")


def checked_still(data: bytes, source: ShareSource) -> Still:
    """A still within the limits (JPEG or PNG, by its bytes); ``ValueError`` otherwise."""
    if not data or len(data) > MAX_STILL_BYTES:
        raise ValueError("voice_still_invalid")
    if data.startswith(b"\xff\xd8\xff"):
        mime, expected = "image/jpeg", "JPEG"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime, expected = "image/png", "PNG"
    else:
        raise ValueError("voice_still_invalid")
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as picture:  # Reads the header only.
            kind, size = picture.format, picture.size
    except Exception:
        raise ValueError("voice_still_invalid") from None
    if kind != expected or not 0 < max(size) <= MAX_STILL_SIDE or min(size) < 1:
        raise ValueError("voice_still_invalid")
    return Still(bytes(data), mime, source)
