"""Hatching a living Buddy: families, pose guides, prompts, patches and rigs.

The provider work is one painting (an edit of the family's pose guide) and six
small edits of that painting (eyes closed, mouth open, four expressions).
Everything else here is local and deterministic: the cut-out, aligning each
edit to the painting, turning what changed into feathered patches, packing
them into one atlas, and fitting the family's rig to the silhouette.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .living_art import CutOut, CutOutError, cut_out, free_rig

Family = Literal["floater", "biped", "winged", "free"]
FAMILIES: tuple[Family, ...] = ("floater", "biped", "winged")
EXPRESSIONS = ("blink", "talk", "happy", "worried", "focused", "surprised")
#: Painted images in a full hatch (the character, then each expression).
FULL_STEPS = 1 + len(EXPRESSIONS)
KEY_COLOUR = (255, 0, 255)
#: The living art's square size in pixels (Buddy is drawn at up to ~240 px, at 2× and 3× pixel ratios).
ART_SIZE = 768
_GUIDE_SIZE = 1024

_WINGED = re.compile(r"\b(wing(s|ed)?|bird|owl|bat|fairy|faerie|moth|butterfly|dragon|angel|griffin|phoenix|bee|"
                     r"raven|crow|parrot|penguin|duck|chick|pegasus)\b", re.IGNORECASE)
_FLOATER = re.compile(r"\b(ghost|wisp|orb|jelly(fish)?|cloud|flame|fire|spirit|spectre|specter|floating|float(s|y)?|"
                      r"blob|slime|bubble|star|comet|lantern|balloon|genie|will-o)\b", re.IGNORECASE)


def choose_family(prompt: str, override: str | None = None) -> Family:
    """The body family for a description: wings → winged, ethereal → floater, else little biped."""

    if override in FAMILIES:
        return override  # type: ignore[return-value]
    if _WINGED.search(prompt or ""):
        return "winged"
    if _FLOATER.search(prompt or ""):
        return "floater"
    return "biped"


# Where a family's parts sit, as fractions of the character's bounding box
# (x right, y down). The pose guides are drawn from the same numbers, so a
# painting that follows its guide fits its rig.
_TEMPLATES: dict[str, dict] = {
    "floater": {
        "head": {"center": (0.5, 0.36), "radius": (0.42, 0.3), "pivot": (0.5, 0.6)},
        "body": (0.5, 0.68),
        "limbs": {
            "arm_l": ((0.27, 0.6), (0.1, 0.68)),
            "arm_r": ((0.73, 0.6), (0.9, 0.68)),
            "tail": ((0.5, 0.82), (0.52, 1.0)),
        },
    },
    "biped": {
        "head": {"center": (0.5, 0.33), "radius": (0.42, 0.3), "pivot": (0.5, 0.58)},
        "body": (0.5, 0.7),
        "limbs": {
            "arm_l": ((0.28, 0.62), (0.08, 0.72)),
            "arm_r": ((0.72, 0.62), (0.92, 0.72)),
            "leg_l": ((0.4, 0.86), (0.4, 1.0)),
            "leg_r": ((0.6, 0.86), (0.6, 1.0)),
        },
    },
    "winged": {
        "head": {"center": (0.5, 0.35), "radius": (0.3, 0.3), "pivot": (0.5, 0.6)},
        "body": (0.5, 0.7),
        "limbs": {
            "wing_l": ((0.34, 0.6), (0.0, 0.6)),
            "wing_r": ((0.66, 0.6), (1.0, 0.6)),
            "leg_l": ((0.44, 0.9), (0.44, 1.0)),
            "leg_r": ((0.56, 0.9), (0.56, 1.0)),
        },
    },
}
# Where the character's bounding box sits on a guide (fractions of the image).
_GUIDE_BOX = (0.2, 0.14, 0.8, 0.9)


def pose_guide(family: Family) -> bytes:
    """A grey mannequin in the family's layout on the flat key colour, for the painting edit."""

    size = _GUIDE_SIZE
    image = Image.new("RGB", (size, size), KEY_COLOUR)
    draw = ImageDraw.Draw(image)
    grey, joint = (196, 196, 200), (150, 150, 156)
    template = _TEMPLATES["biped" if family == "free" else family]
    x0, y0, x1, y1 = (value * size for value in _GUIDE_BOX)
    width, height = x1 - x0, y1 - y0

    def at(u: float, v: float) -> tuple[float, float]:
        return x0 + u * width, y0 + v * height

    for limb, (pivot, tip) in template["limbs"].items():
        (px, py), (tx, ty) = at(*pivot), at(*tip)
        if limb.startswith("wing"):
            # A wing: a broad leaf from the shoulder, widest two-thirds out.
            dx, dy = tx - px, ty - py
            nx, ny = (-dy, dx) if dx < 0 else (dy, -dx)  # the normal that points up the screen
            spread = 0.42
            draw.polygon([(px, py - height * 0.04), (px + dx * 0.66 + nx * spread, py + dy * 0.66 + ny * spread - height * 0.05),
                          (tx, ty), (px + dx * 0.6 - nx * spread * 0.6, py + dy * 0.6 - ny * spread * 0.6),
                          (px, py + height * 0.05)], fill=grey)
        else:
            thickness = width * 0.1
            draw.line([(px, py), (tx, ty)], fill=grey, width=round(thickness), joint="curve")
            draw.ellipse([tx - thickness / 2, ty - thickness / 2, tx + thickness / 2, ty + thickness / 2], fill=grey)
        draw.ellipse([px - 9, py - 9, px + 9, py + 9], fill=joint)
    bx, by = at(*template["body"])
    body_w, body_h = width * 0.3, height * 0.26
    draw.ellipse([bx - body_w, by - body_h, bx + body_w, by + body_h], fill=grey)
    head = template["head"]
    hx, hy = at(*head["center"])
    rx, ry = head["radius"][0] * width, head["radius"][1] * height
    draw.ellipse([hx - rx, hy - ry, hx + rx, hy + ry], fill=grey)
    for side in (-1, 1):  # eye marks, so the face lands where the rig expects it
        ex, ey = hx + side * rx * 0.36, hy + ry * 0.05
        draw.ellipse([ex - rx * 0.12, ey - ry * 0.14, ex + rx * 0.12, ey + ry * 0.14], fill=joint)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


_LIMB_WORDS = {
    "floater": "its little arms and its wispy tail",
    "biped": "its arms and its two short legs",
    "winged": "its two wings and its feet",
}
_KEEP = ("Keep everything else exactly the same: the same character, pose, position, size, colours and lighting, "
         "and the same flat solid magenta (#FF00FF) background. No text.")


def paint_prompt(description: str, family: Family) -> str:
    limbs = _LIMB_WORDS.get(family, "its arms and legs")
    return (
        "Paint one original companion character for a desktop app, as described below, in exactly the pose, "
        f"size and position of the grey mannequin in this image: its head where the mannequin's head is and {limbs} "
        "where the mannequin's are. The mannequin is only a layout guide: replace it completely, no grey shapes may "
        "remain. Full body, facing the viewer, eyes open and looking at the viewer, mouth closed, a calm friendly "
        "neutral expression. Keep the flat solid magenta (#FF00FF) background exactly as it is, with nothing else on "
        "it: no shadow, no floor, no glow reaching the edges, no text, no other characters. A clean rim light or "
        "outline on every edge so it separates from the background. Readable at 96 pixels, polished painterly "
        f"style. Character: {description.strip()}"
    )


EXPRESSION_PROMPTS = {
    "blink": "Change only the eyes: close both eyes gently, as in a calm blink, with curved closed eyelids. " + _KEEP,
    "talk": ("Change only the mouth: open it as if saying 'ah', a medium open mouth. If the character shows no mouth, "
             "add a small one in the same style. " + _KEEP),
    "happy": "Change only the face: a joyful smile with happy, slightly squinted (still open) eyes. " + _KEEP,
    "worried": ("Change only the face: a worried, apologetic look, brows raised in the middle, a small frown. "
                + _KEEP),
    "focused": ("Change only the face: concentrating, eyes slightly narrowed, brows a little lowered, mouth neutral. "
                + _KEEP),
    "surprised": "Change only the face: surprised, eyes wide open, brows raised, a small round open mouth. " + _KEEP,
}

REPAINT_PROMPT = (
    "Repaint this exact character, unchanged in design, pose, framing and proportions, on a flat solid magenta "
    "(#FF00FF) background with nothing else on it: no shadow, no floor, no checkerboard, no text. Keep its eyes "
    "open and its mouth closed."
)


def edit_source(data: bytes) -> bytes:
    """A still as an opaque 1024 square on the key colour: image models treat transparency as "edit here"."""

    from PIL import ImageOps

    with Image.open(io.BytesIO(data)) as source:
        image = source.convert("RGBA")
    if image.getchannel("A").getextrema()[0] < 250:
        side = max(image.size)
        canvas = Image.new("RGBA", (side, side), (*KEY_COLOUR, 255))
        canvas.alpha_composite(image, ((side - image.width) // 2, (side - image.height) // 2))
        image = canvas
    image = ImageOps.fit(image.convert("RGB"), (1024, 1024), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def personality_for(description: str) -> dict[str, float]:
    """Motion personality from the description, clamped to tested limits."""

    text = (description or "").lower()
    bounce = 0.5 + 0.25 * bool(re.search(r"\b(bouncy|playful|energetic|hyper|excited|cheerful|lively|silly)\b", text))
    bounce -= 0.25 * bool(re.search(r"\b(calm|serene|stoic|sleepy|gentle|quiet|wise|old|ancient|lazy)\b", text))
    tempo = 0.5 + 0.2 * bool(re.search(r"\b(quick|fast|nimble|zippy|speedy|energetic)\b", text))
    tempo -= 0.2 * bool(re.search(r"\b(slow|sleepy|lazy|serene|calm|wise)\b", text))
    curiosity = 0.5 + 0.25 * bool(re.search(r"\b(curious|inquisitive|scholar|explorer|nosy|alert)\b", text))
    curiosity -= 0.2 * bool(re.search(r"\b(shy|timid|stoic|focused|quiet)\b", text))
    clamp = lambda value: round(min(0.9, max(0.1, value)), 3)  # noqa: E731
    return {"bounce": clamp(bounce), "tempo": clamp(tempo), "curiosity": clamp(curiosity)}


# ── Patches ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Patch:
    kind: str
    image: Image.Image  # RGBA, already masked and feathered
    at: tuple[float, float, float, float]  # normalised x, y, w, h on the art
    mask_boxes: list[tuple[float, float, float, float]] = field(default_factory=list)


def _rgba(png: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(png)) as image:
        return np.asarray(image.convert("RGBA")).astype(np.float64)


def _shift(array: np.ndarray, dx: int, dy: int) -> np.ndarray:
    return np.roll(np.roll(array, dy, axis=0), dx, axis=1)


def _align(a: np.ndarray, b: np.ndarray, where: np.ndarray) -> tuple[int, int]:
    """The (dx, dy) that best lays b over a where nothing should differ: coarse (¼) then fine (½) luminance."""

    weights = np.array([0.2126, 0.7152, 0.0722])

    def reduced(rgba: np.ndarray, factor: int) -> np.ndarray:
        size = rgba.shape[0] // factor
        luma = Image.fromarray((rgba[..., :3] @ weights).clip(0, 255).astype(np.uint8))
        return np.asarray(luma.resize((size, size), Image.Resampling.BOX)).astype(np.float64)

    def mask(factor: int) -> np.ndarray:
        size = where.shape[0] // factor
        return np.asarray(Image.fromarray(where.astype(np.uint8) * 255).resize((size, size), Image.Resampling.BOX)) > 200

    def search(factor: int, centre: tuple[int, int], reach: int) -> tuple[int, int]:
        la, lb, keep = reduced(a, factor), reduced(b, factor), mask(factor)
        if not keep.any():
            return centre
        best, score = centre, np.inf
        for dy in range(centre[1] - reach, centre[1] + reach + 1):
            for dx in range(centre[0] - reach, centre[0] + reach + 1):
                value = float(np.mean(np.abs(_shift(lb, dx, dy) - la)[keep]))
                if value < score:
                    best, score = (dx, dy), value
        return best

    coarse = search(4, (0, 0), 2)
    fine = search(2, (coarse[0] * 2, coarse[1] * 2), 1)
    return fine[0] * 2, fine[1] * 2


def extract_patch(kind: str, base: CutOut, edited: bytes, face: tuple[float, float, float, float]) -> Patch | None:
    """What an edit changed inside the face, as a feathered patch over the base art.

    Both are cut out the same way, so their bounding boxes (and so their
    placement on the canvas) line up; a small search then absorbs any
    remaining drift. Returns None when nothing changed or too much did.
    """

    try:
        other = cut_out(edited, size=base.size)
    except CutOutError:
        return None
    a = _rgba(base.png)
    b = _rgba(other.png)
    size = base.size
    fx0, fy0, fx1, fy1 = (round(value * size) for value in face)
    region = np.zeros((size, size), bool)
    region[max(0, fy0):min(size, fy1), max(0, fx0):min(size, fx1)] = True
    # Align on everything outside the face, where nothing should have changed.
    outside = (a[..., 3] > 128) & ~region
    best = _align(a, b, outside)
    b = _shift(b, *best)
    drift = float(np.mean(np.abs(b[..., :3] - a[..., :3])[outside])) if outside.any() else 0.0
    if drift > 40:  # the whole character changed: identity drift
        return None
    difference = np.abs(b[..., :3] - a[..., :3]).max(axis=-1) * (np.maximum(a[..., 3], b[..., 3]) / 255.0)
    changed = (difference > 30) & region
    opened = Image.fromarray((changed * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3))
    mask = opened.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.MaxFilter(5))
    mask_array = np.asarray(mask) > 127
    face_area = max(1, int(region.sum()))
    share = mask_array.sum() / face_area
    if share < 0.004 or share > 0.55:
        return None
    feather = np.asarray(Image.fromarray((mask_array * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3))) / 255.0
    ys, xs = np.nonzero(feather > 0.02)
    left, top, right, bottom = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    patch = b[top:bottom, left:right].copy()
    patch[..., 3] = patch[..., 3] * feather[top:bottom, left:right]
    image = Image.fromarray(patch.clip(0, 255).astype(np.uint8), "RGBA")
    # Features (eyes, a mouth) are measured on what changed, before the patch's margin joins them.
    features = np.asarray(opened.filter(ImageFilter.MaxFilter(3))) > 127
    boxes = [tuple(value / size for value in box) for box in _components(features)]
    return Patch(kind, image, (left / size, top / size, (right - left) / size, (bottom - top) / size), boxes)


def _components(mask: np.ndarray, limit: int = 4) -> list[tuple[int, int, int, int]]:
    """Bounding boxes of the largest connected regions of a mask (at half size, numpy only)."""

    small = Image.fromarray((mask * 255).astype(np.uint8)).resize((mask.shape[1] // 2, mask.shape[0] // 2),
                                                                    Image.Resampling.BOX)
    grid = np.asarray(small) > 40
    labels = np.zeros(grid.shape, np.int32)
    boxes: list[tuple[int, tuple[int, int, int, int]]] = []
    current = 0
    for y, x in zip(*np.nonzero(grid)):
        if labels[y, x]:
            continue
        current += 1
        stack = [(y, x)]
        labels[y, x] = current
        points = []
        while stack:
            cy, cx = stack.pop()
            points.append((cy, cx))
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < grid.shape[0] and 0 <= nx < grid.shape[1] and grid[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = current
                    stack.append((ny, nx))
        ys = [p[0] for p in points]
        xs = [p[1] for p in points]
        boxes.append((len(points), (min(xs) * 2, min(ys) * 2, (max(xs) + 1) * 2, (max(ys) + 1) * 2)))
    boxes.sort(key=lambda item: -item[0])
    return [box for _, box in boxes[:limit]]


def pack_atlas(patches: list[Patch]) -> tuple[bytes, dict[str, dict]]:
    """Shelf-pack patches into one PNG; returns it and each patch's atlas and placement rects."""

    gap = 2
    width = 512
    while any(patch.image.width + gap * 2 > width for patch in patches) and width < 2048:
        width *= 2
    x = y = gap
    shelf = 0
    places: list[tuple[int, int]] = []
    for patch in sorted(patches, key=lambda item: -item.image.height):
        if x + patch.image.width + gap > width:
            x, y = gap, y + shelf + gap
            shelf = 0
        places.append((x, y))
        x += patch.image.width + gap
        shelf = max(shelf, patch.image.height)
    height = max(1, y + shelf + gap)
    atlas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    meta: dict[str, dict] = {}
    for patch, (px, py) in zip(sorted(patches, key=lambda item: -item.image.height), places):
        atlas.alpha_composite(patch.image, (px, py))
        meta[patch.kind] = {"atlas": [px, py, patch.image.width, patch.image.height],
                            "at": [round(value, 5) for value in patch.at]}
    buffer = io.BytesIO()
    atlas.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), meta


# ── Rig ──────────────────────────────────────────────────────────────────

def _ellipse(box: tuple[float, float, float, float]) -> dict:
    x0, y0, x1, y1 = box
    return {"center": [round((x0 + x1) / 2, 4), round((y0 + y1) / 2, 4)],
            "radius": [round(max(0.01, (x1 - x0) / 2), 4), round(max(0.01, (y1 - y0) / 2), 4)]}


def fit_rig(cut: CutOut, family: Family, patches: dict[str, Patch], description: str) -> dict:
    """The family's rig mapped onto the silhouette, refined by what the patches found."""

    if family == "free" or family not in _TEMPLATES:
        rig = free_rig(cut.bounds, cut.base)
    else:
        x0, y0, x1, _ = cut.bounds
        base = cut.base
        width, height = x1 - x0, base - y0

        def at(u: float, v: float) -> list[float]:
            return [round(x0 + u * width, 4), round(y0 + v * height, 4)]

        template = _TEMPLATES[family]
        head = template["head"]
        alpha = _rgba(cut.png)[..., 3] > 96
        size = cut.size
        bones = [
            {"id": "body", "pivot": at(*template["body"])},
            {"id": "head", "parent": "body", "pivot": at(*head["pivot"]), "center": at(*head["center"]),
             "radius": [round(head["radius"][0] * width, 4), round(head["radius"][1] * height, 4)]},
        ]
        for limb, (pivot, tip) in template["limbs"].items():
            # Keep a limb only where the painting has something there.
            tx, ty = at(*tip)
            px, py = at(*pivot)
            samples = [(px + (tx - px) * t, py + (ty - py) * t) for t in (0.55, 0.7, 0.85)]
            filled = sum(alpha[min(size - 1, round(sy * size)), min(size - 1, round(sx * size))] for sx, sy in samples)
            if filled >= 2:
                bones.append({"id": limb, "parent": "body", "pivot": [px, py], "tip": [tx, ty]})
        rig = {"schema": 1, "family": family, "base": round(base, 4), "bones": bones, "eyes": [], "mouth": None,
               "patches": {}, "personality": {}}
    blink = patches.get("blink")
    if blink and blink.mask_boxes:
        boxes = sorted(blink.mask_boxes[:3], key=lambda box: box[0])
        rig["eyes"] = [_ellipse(box) for box in boxes]
        # The head is centred over the eyes, a little above them.
        head = next(bone for bone in rig["bones"] if bone["id"] == "head")
        centre_x = sum(eye["center"][0] for eye in rig["eyes"]) / len(rig["eyes"])
        head["center"][0] = round(centre_x, 4)
    talk = patches.get("talk")
    if talk and talk.mask_boxes:
        rig["mouth"] = _ellipse(talk.mask_boxes[0])
    rig["personality"] = personality_for(description)
    return rig


def face_region(cut: CutOut, family: Family) -> tuple[float, float, float, float]:
    """Where expression edits may change the art: the head, generously."""

    x0, y0, x1, _ = cut.bounds
    template = _TEMPLATES.get(family, _TEMPLATES["biped"])
    width, height = x1 - x0, cut.base - y0
    cx, cy = template["head"]["center"]
    rx, ry = template["head"]["radius"]
    if family == "free":
        rx, ry, cy = 0.5, 0.4, 0.4
    return (x0 + (cx - rx * 1.25) * width, y0 + (cy - ry * 1.3) * height,
            x0 + (cx + rx * 1.25) * width, y0 + (cy + ry * 1.35) * height)


@dataclass(frozen=True)
class LivingPack:
    art: bytes
    rig: bytes
    patches: bytes | None
    report: dict


def build_pack(base_png: bytes, edits: dict[str, bytes | None], family: Family, description: str) -> LivingPack:
    """Assemble a living pack from the painting and whichever expression edits came back."""

    cut = cut_out(base_png, size=ART_SIZE)
    face = face_region(cut, family)
    patches: dict[str, Patch] = {}
    dropped: list[str] = []
    for kind in EXPRESSIONS:
        data = edits.get(kind)
        patch = extract_patch(kind, cut, data, face) if data and data != base_png else None
        if patch is None:
            dropped.append(kind)
        else:
            patches[kind] = patch
    rig = fit_rig(cut, family, patches, description)
    atlas = None
    if patches:
        atlas, meta = pack_atlas(list(patches.values()))
        rig["patches"] = meta
    report = {"coverage": round(cut.coverage, 4), "background_noise": round(cut.background_noise, 3),
              "had_alpha": cut.had_alpha, "family": family, "patches": sorted(patches), "dropped": dropped,
              "limbs": [bone["id"] for bone in rig["bones"] if bone["id"] not in {"body", "head"}]}
    return LivingPack(cut.png, json.dumps(rig, separators=(",", ":")).encode(), atlas, report)
