"""Create deterministic paired, overlay and diff images for core parity evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageChops, ImageDraw


def _pairs(root: Path) -> Iterable[tuple[str, Path, Path]]:
    for nicegui in sorted(root.rglob("*--nicegui.png")):
        react = nicegui.with_name(nicegui.name.replace("--nicegui.png", "--react.png"))
        if react.is_file():
            relative = nicegui.relative_to(root).as_posix().replace("--nicegui.png", "")
            yield relative, nicegui, react


def _canvas(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    target = Image.new("RGBA", size, (15, 20, 26, 255))
    target.alpha_composite(image.convert("RGBA"), (0, 0))
    return target


def compose(root: Path, output: Path) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for name, nicegui_path, react_path in _pairs(root):
        with (
            Image.open(nicegui_path) as nicegui_source,
            Image.open(react_path) as react_source,
        ):
            width = max(nicegui_source.width, react_source.width)
            height = max(nicegui_source.height, react_source.height)
            nicegui = _canvas(nicegui_source, (width, height))
            react = _canvas(react_source, (width, height))
        slug = name.replace("/", "--")
        side = Image.new("RGBA", (width * 2, height + 36), (15, 20, 26, 255))
        side.alpha_composite(nicegui, (0, 36))
        side.alpha_composite(react, (width, 36))
        draw = ImageDraw.Draw(side)
        draw.text((12, 10), "NiceGUI reference", fill=(244, 247, 250, 255))
        draw.text((width + 12, 10), "React", fill=(244, 247, 250, 255))
        side_path = output / f"{slug}--side-by-side.png"
        overlay_path = output / f"{slug}--overlay.png"
        diff_path = output / f"{slug}--diff.png"
        side.save(side_path, optimize=True)
        Image.blend(nicegui, react, 0.5).save(overlay_path, optimize=True)
        difference = ImageChops.difference(nicegui, react)
        difference.save(diff_path, optimize=True)
        histogram = difference.convert("RGB").histogram()
        changed = sum(
            count for index, count in enumerate(histogram) if index % 256 != 0
        )
        total = width * height * 3
        records.append(
            {
                "pair": name,
                "viewport": {"width": width, "height": height},
                "changed_channel_ratio": changed / total if total else 0,
                "nicegui_sha256": hashlib.sha256(nicegui_path.read_bytes()).hexdigest(),
                "react_sha256": hashlib.sha256(react_path.read_bytes()).hexdigest(),
                "side_by_side": side_path.name,
                "overlay": overlay_path.name,
                "diff": diff_path.name,
            }
        )
    manifest = {"schema_version": 1, "pairs": records}
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Playwright artifact directory")
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    manifest = compose(options.root.resolve(), options.output.resolve())
    print(f"Composed {len(manifest['pairs'])} core-surface pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
