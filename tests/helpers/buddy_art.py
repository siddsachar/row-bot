"""Synthetic Buddy paintings for Hatch tests: a round character on the flat key colour.

A fake image provider answers the painting prompt with ``painting()`` and an
expression edit with the same character changed only in its face, so the real
cut-out, alignment and patch extraction run on them.
"""

from __future__ import annotations

import base64
import io

from PIL import Image, ImageDraw

KEY = (255, 0, 255)


def painting(size: int = 160, *, eyes: str = "open", mouth: bool = False,
             outline: tuple[int, int, int] = (245, 245, 245), shine: tuple[int, int, int] | None = None) -> bytes:
    image = Image.new("RGB", (size, size), KEY)
    draw = ImageDraw.Draw(image)
    k = size / 256
    draw.ellipse([80 * k, 140 * k, 176 * k, 228 * k], fill=(60, 120, 200), outline=outline, width=max(1, round(2 * k)))
    draw.ellipse([70 * k, 36 * k, 186 * k, 152 * k], fill=(80, 150, 230), outline=outline, width=max(1, round(2 * k)))
    for cx in (108, 148):
        if eyes == "open":
            draw.ellipse([(cx - 12) * k, 80 * k, (cx + 12) * k, 108 * k], fill=(255, 255, 255))
            draw.ellipse([(cx - 5) * k, 88 * k, (cx + 5) * k, 102 * k], fill=(20, 20, 30))
        else:
            draw.line([(cx - 12) * k, 96 * k, (cx + 12) * k, 96 * k], fill=(20, 20, 30), width=max(1, round(3 * k)))
    if mouth:
        draw.ellipse([118 * k, 118 * k, 138 * k, 136 * k], fill=(120, 20, 40))
    if shine:  # a highlight on top of the head
        draw.ellipse([110 * k, 50 * k, 146 * k, 66 * k], fill=shine)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def answer(prompt: str, size: int = 160) -> bytes:
    """What a well-behaved image model returns for a Hatch prompt."""

    if "close both eyes" in prompt:
        return painting(size, eyes="closed")
    if "open it as if saying" in prompt:
        return painting(size, mouth=True)
    return painting(size)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
