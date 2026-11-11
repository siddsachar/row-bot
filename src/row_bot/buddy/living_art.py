"""Image work for living Buddies: cutting a character out of a flat background.

Everything here is local and deterministic (Pillow + numpy). A generated or
legacy still arrives opaque on a flat (or gently shaded) background; the
cut-out gives it real alpha, recovers soft glows by colour unmixing, and
places it on a square canvas with its base near the bottom.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# Colour distance (0–441 in RGB) below which a pixel is background, and above
# which it is fully the character; soft glows fall in between.
_KEY_NEAR = 14.0
_KEY_FAR = 72.0
_FLOOD = 60.0
_ANALYSIS = 512
_MAX_SIDE = 4096


class CutOutError(ValueError):
    """The still could not be cut out cleanly (code in ``args[0]``)."""


@dataclass(frozen=True)
class CutOut:
    png: bytes
    size: int
    #: Fraction of the canvas the character covers (alpha > 0.5).
    coverage: float
    #: Where the character's base (its lowest solid row) sits, 0–1 from the top.
    base: float
    #: The cut-out's bounds on the canvas: x0, y0, x1, y1 (0–1).
    bounds: tuple[float, float, float, float]
    #: How flat the background was (mean distance of the border ring to its model).
    background_noise: float
    #: True when the source already had real transparency.
    had_alpha: bool


def _load(data: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as source:
            if max(source.size) > _MAX_SIDE or min(source.size) < 16:
                raise CutOutError("cutout_size")
            return source.convert("RGBA")
    except CutOutError:
        raise
    except Exception:
        raise CutOutError("cutout_unreadable") from None


def _background_model(rgb: np.ndarray) -> tuple[np.ndarray, float]:
    """Fit a smooth (quadratic) background to the border ring; return it per pixel."""

    height, width, _ = rgb.shape
    ring = max(2, min(height, width) // 40)
    mask = np.zeros((height, width), bool)
    mask[:ring] = mask[-ring:] = True
    mask[:, :ring] = mask[:, -ring:] = True
    ys, xs = np.nonzero(mask)
    step = max(1, len(xs) // 6000)
    xs, ys = xs[::step], ys[::step]
    u = xs / max(1, width - 1) - 0.5
    v = ys / max(1, height - 1) - 0.5
    design = np.stack([np.ones_like(u), u, v, u * u, v * v, u * v], axis=1)
    samples = rgb[ys, xs].astype(np.float64)
    keep = np.ones(len(xs), bool)
    for _ in range(2):  # refit once without outliers (a limb touching the border)
        coefficients, *_ = np.linalg.lstsq(design[keep], samples[keep], rcond=None)
        residual = np.linalg.norm(design @ coefficients - samples, axis=1)
        keep = residual <= max(6.0, float(np.percentile(residual, 80)) * 1.5)
    gy, gx = np.mgrid[0:height, 0:width]
    gu = gx / max(1, width - 1) - 0.5
    gv = gy / max(1, height - 1) - 0.5
    full = np.stack([np.ones_like(gu), gu, gv, gu * gu, gv * gv, gu * gv], axis=-1)
    model = full @ coefficients
    noise = float(np.mean(np.linalg.norm(design[keep] @ coefficients - samples[keep], axis=1)))
    return model, noise


def _flood_from_border(near: np.ndarray) -> np.ndarray:
    """Background reachable from the border through background-like pixels."""

    height, width = near.shape
    # A copy: an image sharing a numpy buffer is read-only and ignores the fill.
    image = Image.fromarray(np.where(near, 255, 0).astype(np.uint8)).copy()
    border = [(x, 0) for x in range(width)] + [(x, height - 1) for x in range(width)]
    border += [(0, y) for y in range(height)] + [(width - 1, y) for y in range(height)]
    pixels = image.load()
    for x, y in border:
        if pixels[x, y] == 255:
            ImageDraw.floodfill(image, (x, y), 128, thresh=0)
    return np.asarray(image) == 128


def _smoothstep(edge0: float, edge1: float, value: np.ndarray) -> np.ndarray:
    t = np.clip((value - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def cut_out(data: bytes, *, size: int = 768, margin: float = 0.07) -> CutOut:
    """Cut a character out of its background and place it on a square canvas."""

    image = _load(data)
    rgba = np.asarray(image).astype(np.float64)
    alpha_in = rgba[..., 3] / 255.0
    rgb = rgba[..., :3]
    had_alpha = bool((alpha_in < 0.5).mean() > 0.02)
    noise = 0.0
    if had_alpha:
        alpha = alpha_in
        colour = rgb
    else:
        model, noise = _background_model(rgb)
        distance = np.linalg.norm(rgb - model, axis=-1)
        # Flood at analysis size (fast), then bring the region back to full size.
        small = Image.fromarray((distance < _FLOOD).astype(np.uint8) * 255)
        scale = _ANALYSIS / max(image.size)
        if scale < 1:
            small = small.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                                 Image.Resampling.NEAREST)
        outer_small = _flood_from_border(np.asarray(small) > 127)
        outer = np.asarray(
            Image.fromarray(outer_small.astype(np.uint8) * 255)
            .resize(image.size, Image.Resampling.NEAREST)
            .filter(ImageFilter.MaxFilter(5))
        ) > 127
        soft = _smoothstep(_KEY_NEAR, _KEY_FAR, distance)
        alpha = np.where(outer, soft, 1.0)
        # Unmix the background from partly transparent pixels (glows, edges).
        safe = np.maximum(alpha, 0.04)[..., None]
        colour = np.clip(model + (rgb - model) / safe, 0, 255)
    solid = alpha > 0.5
    if solid.mean() < 0.02:
        raise CutOutError("cutout_empty")
    if solid.mean() > 0.85:
        raise CutOutError("cutout_background_not_found")

    # Keep the main body and whatever soft light hangs close to it.
    label_scale = 192 / max(image.size)
    tiny = Image.fromarray(solid.astype(np.uint8) * 255).resize(
        (max(1, round(image.width * label_scale)), max(1, round(image.height * label_scale))),
        Image.Resampling.BOX,
    )
    tiny_mask = np.asarray(tiny) > 60
    ys, xs = np.nonzero(tiny_mask)
    seed = np.zeros_like(tiny_mask)
    centre = np.array([tiny_mask.shape[0] / 2, tiny_mask.shape[1] / 2])
    nearest = np.argmin((ys - centre[0]) ** 2 + (xs - centre[1]) ** 2)
    seed[ys[nearest], xs[nearest]] = True
    body = seed
    for _ in range(400):
        grown = body.copy()
        grown[1:] |= body[:-1]
        grown[:-1] |= body[1:]
        grown[:, 1:] |= body[:, :-1]
        grown[:, :-1] |= body[:, 1:]
        grown &= tiny_mask
        if (grown == body).all():
            break
        body = grown
    halo = Image.fromarray(body.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(9)).resize(
        image.size, Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(3))
    alpha = alpha * (np.asarray(halo) / 255.0)

    out = np.dstack([colour, alpha * 255.0]).clip(0, 255).astype(np.uint8)
    cut = Image.fromarray(out, "RGBA")
    box = cut.getchannel("A").point(lambda value: 255 if value > 8 else 0).getbbox()
    if not box:
        raise CutOutError("cutout_empty")
    left, top, right, bottom = box
    solid_rows = np.nonzero((np.asarray(cut.getchannel("A")) > 160).any(axis=1))[0]
    solid_bottom = int(solid_rows.max()) + 1 if len(solid_rows) else bottom
    cut = cut.crop(box)
    inner = size * (1 - 2 * margin)
    scale = min(inner / cut.width, inner / cut.height)
    placed = cut.resize((max(1, round(cut.width * scale)), max(1, round(cut.height * scale))),
                        Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x = (size - placed.width) // 2
    y = size - round(size * margin) - placed.height
    canvas.alpha_composite(placed, (x, y))
    base = (y + (solid_bottom - top) * scale) / size
    final_alpha = np.asarray(canvas.getchannel("A")) / 255.0
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    return CutOut(
        png=buffer.getvalue(),
        size=size,
        coverage=float((final_alpha > 0.5).mean()),
        base=float(min(0.99, base)),
        bounds=(x / size, y / size, (x + placed.width) / size, (y + placed.height) / size),
        background_noise=noise,
        had_alpha=had_alpha,
    )
