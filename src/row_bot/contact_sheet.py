"""Contact sheets (F5, decision D6): many pictures or video frames tiled into one labelled picture.

One picture costs a vision model far fewer tokens than many: a sheet of twelve frames from a
recording reads as one image. Each tile is numbered and captioned ("3 · 0:42" for a frame,
"2 · login.png" for a picture), so the model can say which one it means. Pictures are
decoded with Pillow, upright, and never more than 64 megapixels each.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

MAX_TILES = 48
MAX_SOURCE_PIXELS = 64_000_000
SHEET_WIDTH = 2048
GAP = 8
BACKGROUND = (17, 24, 39)
LABEL_COLOUR = (243, 244, 246)


class SheetError(ValueError):
    """The pictures can't be tiled; the message says why in plain words."""


@dataclass(frozen=True)
class Tile:
    number: int
    label: str
    box: tuple[int, int, int, int]  # left, top, right, bottom of the picture area


def open_picture(data: bytes, *, max_side: int = 0):
    """Decode a picture upright (RGB), refusing what isn't one or is too large to decode safely."""
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > MAX_SOURCE_PIXELS:
                raise SheetError("A picture is too large to look at (more than 64 megapixels).")
            if max_side:
                source.draft("RGB", (max_side * 2, max_side * 2))
            picture = ImageOps.exif_transpose(source)
            picture = picture.convert("RGB")
            picture.load()
    except SheetError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError):
        raise SheetError("A file isn't a picture Row-Bot can read.") from None
    if max_side and max(picture.size) > max_side:
        picture.thumbnail((max_side, max_side))
    return picture


def grid(count: int) -> tuple[int, int]:
    """Columns and rows for this many tiles: as square as it fits (12 → 4 × 3)."""
    columns = max(1, math.ceil(math.sqrt(count)))
    rows = max(1, math.ceil(count / columns))
    return columns, rows


def make_sheet(pictures: list[tuple[bytes, str]], *, width: int = SHEET_WIDTH) -> tuple[bytes, list[Tile]]:
    """Tile pictures (bytes, caption) into one JPEG: the sheet and where each tile is."""
    from PIL import Image, ImageDraw

    from row_bot.video_editing import font

    if not pictures:
        raise SheetError("There are no pictures to put on a contact sheet.")
    if len(pictures) > MAX_TILES:
        raise SheetError(f"A contact sheet holds at most {MAX_TILES} pictures.")
    images = [open_picture(data, max_side=1024) for data, _label in pictures]
    columns, rows = grid(len(images))
    tile_width = max(64, (width - GAP * (columns + 1)) // columns)
    first = images[0]
    aspect = first.width / max(1, first.height)
    tile_height = max(36, round(tile_width / max(0.25, min(aspect, 4.0))))
    label_height = max(18, min(40, round(tile_width * 0.075)))
    sheet_height = rows * (tile_height + label_height) + GAP * (rows + 1)
    sheet_width = columns * tile_width + GAP * (columns + 1)
    sheet = Image.new("RGB", (sheet_width, sheet_height), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    typeface = font(max(10, round(label_height * 0.62)))
    tiles: list[Tile] = []
    for index, (image, (_data, label)) in enumerate(zip(images, pictures)):
        row, column = divmod(index, columns)
        left = GAP + column * (tile_width + GAP)
        top = GAP + row * (tile_height + label_height + GAP)
        fitted = image.copy()
        fitted.thumbnail((tile_width, tile_height))
        sheet.paste(fitted, (left + (tile_width - fitted.width) // 2, top + (tile_height - fitted.height) // 2))
        caption = f"{index + 1} · {label}" if label else str(index + 1)
        while len(caption) > 4 and draw.textlength(caption, font=typeface) > tile_width - 8:
            caption = caption[:-2].rstrip() + "…"
        draw.text((left + 4, top + tile_height + (label_height - typeface.size) // 2 - 1), caption,
                  font=typeface, fill=LABEL_COLOUR)
        tiles.append(Tile(index + 1, caption, (left, top, left + tile_width, top + tile_height)))
    output = io.BytesIO()
    sheet.save(output, format="JPEG", quality=88, optimize=True)
    return output.getvalue(), tiles
