"""Hatch v2's expression patches change Buddy's face, never its outline."""

import io

import numpy as np
from PIL import Image

from row_bot.buddy import living_art, living_hatch
from tests.helpers import buddy_art

FACE = (0.15, 0.05, 0.85, 0.7)


def _rgba(png: bytes) -> Image.Image:
    with Image.open(io.BytesIO(png)) as image:
        return image.convert("RGBA")


def test_a_blink_patch_closes_the_eyes_and_leaves_the_outline_alone_when_the_model_repaints_it():
    base = living_art.cut_out(buddy_art.painting(256), size=192)
    # Image models repaint the whole picture: this blink also redraws the outline, tinted towards the key colour.
    edited = buddy_art.painting(256, eyes="closed", outline=(235, 70, 225))

    patch = living_hatch.extract_patch("blink", base, edited, FACE)

    assert patch is not None and len(patch.mask_boxes) >= 2  # both eyes, closed
    art = _rgba(base.png)
    before = np.asarray(art).astype(int)
    art.alpha_composite(patch.image, (round(patch.at[0] * base.size), round(patch.at[1] * base.size)))
    after = np.asarray(art).astype(int)
    edge = before[..., 3] < 255  # the outline's soft edge and everything past it
    assert np.abs(after - before)[edge].max() <= 2
    eyes = [(round(x0 * base.size), round(y0 * base.size), round(x1 * base.size), round(y1 * base.size))
            for x0, y0, x1, y1 in patch.mask_boxes[:2]]
    assert all(np.abs(after - before)[y0:y1, x0:x1].max() > 60 for x0, y0, x1, y1 in eyes)
