"""Hatch v2's expression patches change Buddy's face: never its outline, never with the key colour."""

import io

import numpy as np
from PIL import Image

from row_bot.buddy import living_art, living_hatch
from tests.helpers import buddy_art

FACE = (0.15, 0.05, 0.85, 0.7)


def _blink(edited: bytes):
    """The base art and the art with the blink patch on, as arrays, and the closed eyes' boxes in pixels."""
    base = living_art.cut_out(buddy_art.painting(256), size=192)
    patch = living_hatch.extract_patch("blink", base, edited, FACE)
    assert patch is not None and len(patch.mask_boxes) >= 2  # both eyes, closed
    with Image.open(io.BytesIO(base.png)) as image:
        art = image.convert("RGBA")
    before = np.asarray(art).astype(int)
    art.alpha_composite(patch.image, (round(patch.at[0] * base.size), round(patch.at[1] * base.size)))
    eyes = [tuple(round(value * base.size) for value in box) for box in patch.mask_boxes[:2]]
    return before, np.asarray(art).astype(int), eyes


def _eyes_closed(before, after, eyes):
    return all(np.abs(after - before)[y0:y1, x0:x1].max() > 60 for x0, y0, x1, y1 in eyes)


def test_a_blink_patch_leaves_the_outline_alone_when_the_model_repaints_it():
    # Image models repaint the whole picture: this blink also redraws the outline, tinted towards the key colour.
    before, after, eyes = _blink(buddy_art.painting(256, eyes="closed", outline=(235, 70, 225)))

    edge = before[..., 3] < 255  # the outline's soft edge and everything past it
    assert np.abs(after - before)[edge].max() <= 2
    assert _eyes_closed(before, after, eyes)


def test_a_blink_patch_leaves_out_the_key_colour_the_model_reflected_onto_the_head():
    # A glossy head repainted reflecting the magenta backdrop: a key-tinted shine on top of it.
    before, after, eyes = _blink(buddy_art.painting(256, eyes="closed", shine=(230, 110, 225)))

    r, g, b = after[..., 0], after[..., 1], after[..., 2]
    assert not ((after[..., 3] > 24) & (g + 50 < np.minimum(r, b)) & (abs(r - b) < 40)).any()
    assert _eyes_closed(before, after, eyes)


def test_the_expression_atlas_is_webp_that_keeps_each_patch_feathered_edge_exactly():
    base = living_art.cut_out(buddy_art.painting(256), size=192)
    patches = [living_hatch.extract_patch("blink", base, buddy_art.painting(256, eyes="closed"), FACE),
               living_hatch.extract_patch("talk", base, buddy_art.painting(256, mouth=True), FACE)]
    assert all(patches)

    data, rects = living_hatch.pack_atlas(patches)

    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    with Image.open(io.BytesIO(data)) as image:
        atlas = np.asarray(image.convert("RGBA")).astype(int)
    for patch in patches:
        x, y, w, h = rects[patch.kind]["atlas"]
        want, got = np.asarray(patch.image).astype(int), atlas[y:y + h, x:x + w]
        # Alpha is lossless: where a patch fades into the art it does exactly what it did as a PNG.
        assert (got[..., 3] == want[..., 3]).all()
        # Colour is lossy but stays the patch's own: what is drawn (premultiplied) is close wherever it shows.
        weight = want[..., 3:] / 255
        assert np.abs((got[..., :3] - want[..., :3]) * weight)[want[..., 3] > 0].mean() < 8
