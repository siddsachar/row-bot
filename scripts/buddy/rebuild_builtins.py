"""Rebuild the built-in living Buddies (a one-off, run by hand; not shipped).

    python scripts/buddy/rebuild_builtins.py art [pack ...]
        Cut each source painting (scripts/buddy/sources/<pack>.png) out into
        static/buddy/builtins/<pack>/art.png. Local only.

    python scripts/buddy/rebuild_builtins.py expressions [pack ...]
        Paint the six expression edits with OpenAI gpt-image-2 and turn them into
        patches.webp plus the rig's patch entries. Needs OPENAI_API_KEY in the
        environment of the terminal it runs in (the owner sets it; nothing here
        prints or stores it). Each edit is kept in .local/buddy-rebuild/<pack>/,
        so a rerun only pays for what is missing; delete a file to repaint it.
        A review sheet of every patch lands beside them.

The hand-tuned bones in each rig.json are kept; only patches (and, when the
blink finds the same number of eyes, their exact ellipses) are replaced.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCES = ROOT / "scripts" / "buddy" / "sources"
BUILTINS = ROOT / "static" / "buddy" / "builtins"
WORK = ROOT / ".local" / "buddy-rebuild"
PACKS = ("ember", "glyph", "lumen", "orbit", "pixel", "sprout")
MODEL = "gpt-image-2"

# Importing row_bot runs data-folder repairs: point it at a throwaway folder, never a real profile.
os.environ.setdefault("ROW_BOT_DATA_DIR", tempfile.mkdtemp(prefix="buddy-rebuild-"))
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image  # noqa: E402

from row_bot.buddy import living_art, living_hatch  # noqa: E402


def art(pack: str) -> None:
    cut = living_art.cut_out((SOURCES / f"{pack}.png").read_bytes(), size=living_hatch.ART_SIZE)
    (BUILTINS / pack / "art.png").write_bytes(cut.png)
    print(f"{pack}: art.png {len(cut.png) // 1024} KB, base {cut.base:.3f}, noise {cut.background_noise:.1f}")


def _paint(client, base_png: bytes, prompt: str) -> bytes:
    response = client.images.edit(model=MODEL, image=[("base.png", base_png, "image/png")], prompt=prompt,
                                  size="1024x1024", quality="high", n=1)
    return base64.b64decode(response.data[0].b64_json)


def _face(rig: dict) -> tuple[float, float, float, float]:
    head = next(bone for bone in rig["bones"] if bone["id"] == "head")
    cx, cy = head.get("center", head["pivot"])
    rx, ry = head["radius"]
    return (cx - rx * 1.25, cy - ry * 1.3, cx + rx * 1.25, cy + ry * 1.35)


def expressions(pack: str, client) -> None:
    folder = BUILTINS / pack
    work = WORK / pack
    work.mkdir(parents=True, exist_ok=True)
    art_png = (folder / "art.png").read_bytes()
    rig = json.loads((folder / "rig.json").read_text(encoding="utf-8"))
    cut = living_art.cut_out(art_png, size=living_hatch.ART_SIZE)
    base = living_hatch.edit_source(art_png)
    (work / "base.png").write_bytes(base)
    patches: dict[str, living_hatch.Patch] = {}
    for kind in living_hatch.EXPRESSIONS:
        edited = work / f"{kind}.png"
        if not edited.exists():
            print(f"{pack}: painting {kind} …", flush=True)
            edited.write_bytes(_paint(client, base, living_hatch.EXPRESSION_PROMPTS[kind]))
        patch = living_hatch.extract_patch(kind, cut, edited.read_bytes(), _face(rig))
        if patch is None:
            print(f"{pack}: {kind} dropped (nothing changed, or the character drifted)")
        else:
            patches[kind] = patch
    if not patches:
        print(f"{pack}: no patches")
        return
    atlas, meta = living_hatch.pack_atlas(list(patches.values()))
    (folder / living_hatch.PATCHES_FILE).write_bytes(atlas)
    rig["patches"] = meta
    blink = patches.get("blink")
    if blink and len(blink.mask_boxes[:3]) == len(rig["eyes"]):
        rig["eyes"] = [living_hatch._ellipse(box) for box in sorted(blink.mask_boxes[:3], key=lambda box: box[0])]
    (folder / "rig.json").write_text(json.dumps(rig, indent=2) + "\n", encoding="utf-8", newline="\n")
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["patches"] = living_hatch.PATCHES_FILE
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    _review(work, art_png, patches)
    print(f"{pack}: patches {sorted(patches)} -> {living_hatch.PATCHES_FILE} {len(atlas) // 1024} KB; review in {work / 'review.png'}")


def _review(work: pathlib.Path, art_png: bytes, patches: dict[str, living_hatch.Patch]) -> None:
    """The art, then the art with each patch on, on white and on near-black."""
    with Image.open(io.BytesIO(art_png)) as source:
        base = source.convert("RGBA")
    size = 256
    tiles = [("art", base)]
    for kind, patch in patches.items():
        tile = base.copy()
        x, y = round(patch.at[0] * base.width), round(patch.at[1] * base.height)
        tile.alpha_composite(patch.image, (x, y))
        tiles.append((kind, tile))
    sheet = Image.new("RGBA", (size * len(tiles), size * 2), (255, 255, 255, 255))
    for column, (_, tile) in enumerate(tiles):
        for row, colour in enumerate(((255, 255, 255, 255), (22, 22, 26, 255))):
            cell = Image.new("RGBA", tile.size, colour)
            cell.alpha_composite(tile)
            sheet.paste(cell.resize((size, size), Image.Resampling.LANCZOS), (column * size, row * size))
    sheet.convert("RGB").save(work / "review.png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=("art", "expressions"))
    parser.add_argument("packs", nargs="*", metavar="pack", help="default: all six")
    arguments = parser.parse_args()
    packs = arguments.packs or list(PACKS)
    if unknown := sorted(set(packs) - set(PACKS)):
        parser.error(f"unknown pack: {', '.join(unknown)}")
    if arguments.step == "art":
        for pack in packs:
            art(pack)
        return
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set in this terminal.")
    from openai import OpenAI

    client = OpenAI()
    for pack in packs:
        expressions(pack, client)


if __name__ == "__main__":
    main()
