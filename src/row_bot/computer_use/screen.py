"""One window's picture as the model sees it, and the way back to the screen.

Cua Driver captures a window and may scale its picture down; its pointer
input takes coordinates in that picture and maps them to the screen itself.
Row-Bot scales the picture once more for the model (at most 1280×800).

On Windows, Cua copies the window into a bitmap the size of its frame on
screen (physical pixels, less the invisible border and a one-pixel inset) and
sends a picture point to ``frame origin + 1 + point × Cua's own ratio``. That
holds for a window that draws at its display's scale. A window Windows scales
for the display (a DPI-unaware app at 150 %: many older apps, and Tk) draws
itself at its own smaller size into that bitmap's top-left corner, the rest
black, so Cua's mapping misses by the display's scale. With the window's
``Scaling`` (Windows' own DPI facts), Row-Bot shows the model the drawn part
only, maps the model's point to the true screen point itself, and hands Cua
the picture point Cua sends to that screen point. Elsewhere (macOS), a point
maps back by one ratio: ``cua = model × cua_size / model_size``.

Controls come from the window's accessibility tree in screen coordinates;
they map into the model's picture by the inverse of the same mapping.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass, field

from row_bot.computer_use.client import CuaElement
from row_bot.computer_use.policy import Control, role_of, secret_field

MODEL_MAX_WIDTH = 1280
MODEL_MAX_HEIGHT = 800
THUMB_MAX_WIDTH = 640

# Plain words for the roles the controls list names.
_ROLE_WORDS = {
    "button": "button", "splitbutton": "button", "popupbutton": "button", "menubutton": "button",
    "togglebutton": "toggle", "switch": "toggle", "hyperlink": "link", "link": "link", "menuitem": "menu item",
    "checkbox": "checkbox", "radiobutton": "radio", "radio": "radio", "tabitem": "tab", "tab": "tab",
    "listitem": "item", "treeitem": "item", "option": "option", "combobox": "dropdown", "edit": "field",
    "textfield": "field", "textbox": "field", "textarea": "field", "searchfield": "search field",
    "securetextfield": "password field", "slider": "slider", "spinner": "number field", "spinbutton": "number field",
    "menubaritem": "menu", "dataitem": "cell", "cell": "cell",
}


@dataclass(frozen=True)
class Window:
    """A top-level window on screen. ``bounds`` are the window's frame in screen coordinates."""

    pid: int
    window_id: int
    app: str
    title: str
    bounds: tuple[float, float, float, float]
    minimized: bool = False
    front: int = 0


@dataclass(frozen=True)
class Scaling:
    """How a window draws on its display (Windows): what Cua's picture of it means on the screen."""

    # The display's DPI over the window's own: 1 for an app that draws at its display's scale,
    # 1.5 for a DPI-unaware app on a 150 % display (Windows stretches it).
    scale: float = 1.0
    # The window's frame on screen less its whole rectangle's origin: the invisible border (physical pixels).
    border: tuple[float, float] = (0.0, 0.0)


# Cua crops its bitmap one pixel inside the window's frame (its DWM_CROP_INSET_PX).
_CUA_INSET = 1


@dataclass
class Shot:
    """A window's picture: the driver's, the model's (scaled once) and its controls."""

    window: Window
    cua_size: tuple[int, int]
    image: bytes = field(repr=False)
    size: tuple[int, int]
    elements: tuple[CuaElement, ...] = ()
    taken: float = field(default_factory=time.monotonic)
    # Windows: how the window draws on its display. None (macOS): Cua maps its picture to the screen itself.
    scaling: Scaling | None = None
    # The part of Cua's picture (width, height from its top-left) the model's picture shows: all of it, or
    # the part a window Windows scales for its display drew.
    shown: tuple[int, int] = (0, 0)
    _thumb: bytes | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.shown == (0, 0):
            self.shown = self.cua_size

    # ── Coordinates ──────────────────────────────────────────────────────────

    def contains(self, x: float, y: float) -> bool:
        return 0 <= x < self.size[0] and 0 <= y < self.size[1]

    def _ratio(self) -> tuple[float, float]:
        """Cua's own ratio: its bitmap (the frame less the inset) over the picture it returned."""

        _left, _top, width, height = self.window.bounds
        return (width - 2 * _CUA_INSET) / self.cua_size[0], (height - 2 * _CUA_INSET) / self.cua_size[1]

    def _axes(self) -> tuple[tuple[float, float], tuple[float, float]]:
        """``screen = start + model × step`` on each axis."""

        left, top, width, height = self.window.bounds
        if self.scaling is None:
            return (left, width / self.size[0]), (top, height / self.size[1])
        scale, (border_x, border_y) = self.scaling.scale, self.scaling.border
        ratio_x, ratio_y = self._ratio()
        # Point p of Cua's picture is bitmap pixel p × ratio, past Cua's crop (border + inset), which the
        # window drew at its own scale from its rectangle's origin (the frame less the border).
        return ((left - border_x + (border_x + _CUA_INSET) * scale, self.shown[0] * ratio_x * scale / self.size[0]),
                (top - border_y + (border_y + _CUA_INSET) * scale, self.shown[1] * ratio_y * scale / self.size[1]))

    def to_screen(self, x: float, y: float) -> tuple[float, float]:
        """A point in the model's picture → the screen (physical pixels on Windows)."""

        (start_x, step_x), (start_y, step_y) = self._axes()
        return start_x + x * step_x, start_y + y * step_y

    def from_screen(self, sx: float, sy: float) -> tuple[int, int]:
        """A screen point → the model's picture."""

        (start_x, step_x), (start_y, step_y) = self._axes()
        if step_x <= 0 or step_y <= 0:
            return -1, -1
        return round((sx - start_x) / step_x), round((sy - start_y) / step_y)

    def to_cua(self, x: float, y: float) -> tuple[float, float]:
        """A point in the model's picture → the point of Cua's picture that Cua sends to the same screen point."""

        if self.scaling is None:
            return (round(x * self.cua_size[0] / self.size[0], 2), round(y * self.cua_size[1] / self.size[1], 2))
        sx, sy = self.to_screen(x, y)
        left, top, _width, _height = self.window.bounds
        ratio_x, ratio_y = self._ratio()
        return round((sx - left - _CUA_INSET) / ratio_x, 2), round((sy - top - _CUA_INSET) / ratio_y, 2)

    # ── Controls ─────────────────────────────────────────────────────────────

    def _box(self, element: CuaElement) -> tuple[int, int, int, int]:
        x, y, width, height = element.bounds
        left, top = self.from_screen(x, y)
        right, bottom = self.from_screen(x + width, y + height)
        return left, top, right, bottom

    def _hits(self, x: float, y: float) -> tuple[list[CuaElement], CuaElement | None]:
        """The controls under a point, the smallest first, and the one a click there acts on (the smallest that a
        click acts on, else the smallest)."""

        hits: list[tuple[int, CuaElement]] = []
        for element in self.elements:
            left, top, right, bottom = self._box(element)
            if left <= x < right and top <= y < bottom and right > left and bottom > top:
                hits.append(((right - left) * (bottom - top), element))
        hits.sort(key=lambda hit: hit[0])
        found = [element for _area, element in hits]
        return found, next((element for element in found if role_of(element.role) in _ROLE_WORDS),
                           found[0] if found else None)

    def control_box(self, x: float, y: float) -> tuple[float, float, float, float] | None:
        """Where on the screen the control under a point is (x, y, width, height, as Cua gives them), or None."""

        chosen = self._hits(x, y)[1]
        return None if chosen is None else (float(chosen.bounds[0]), float(chosen.bounds[1]),
                                            float(chosen.bounds[2]), float(chosen.bounds[3]))

    def control_at(self, x: float, y: float) -> Control | None:
        """The control under a point: the smallest one there that a click acts on, named by what it says."""

        found, chosen = self._hits(x, y)
        if chosen is None:
            return None
        label = chosen.label or next((element.label for element in found if element.label), "")
        return Control(role=chosen.role, label=label)

    def controls(self, budget: int) -> str:
        """``button "Leak" 640,412; field "Postcode" 400,220`` within *budget* characters."""

        seen: set[tuple[str, str]] = set()
        rows: list[tuple[int, int, int, str]] = []
        for element in self.elements:
            words = _ROLE_WORDS.get(role_of(element.role))
            label = " ".join(str(element.label or "").split())[:60]
            if not words or not label or element.enabled is False:
                continue
            left, top, right, bottom = self._box(element)
            cx, cy = (left + right) // 2, (top + bottom) // 2
            if right <= left or bottom <= top or not self.contains(cx, cy):
                continue
            if (words, label.casefold()) in seen:
                continue
            seen.add((words, label.casefold()))
            label = label.replace('"', "'")
            rows.append((0 if element.in_web_content else 1, cy, cx, f'{words} "{label}" {cx},{cy}'))
        rows.sort(key=lambda row: (row[0], row[1] // 8, row[2]))
        out: list[str] = []
        used = 0
        for _web, _y, _x, line in rows:
            if used + len(line) + 2 > budget:
                break
            out.append(line)
            used += len(line) + 2
        if len(out) < len(rows):
            out.append(f"{len(rows) - len(out)} more")
        return "; ".join(out)

    def labels(self) -> list[str]:
        return [element.label for element in self.elements if element.label]

    # ── Pictures ─────────────────────────────────────────────────────────────

    def thumbnail(self, mark: tuple[float, float] | None = None) -> bytes:
        """A small JPEG for the chat (with a ring where Row-Bot is about to act, when asked)."""

        if mark is None and self._thumb is not None:
            return self._thumb
        from PIL import Image, ImageDraw

        with Image.open(io.BytesIO(self.image)) as source:
            picture = source.convert("RGB")
        scale = min(1.0, THUMB_MAX_WIDTH / picture.width)
        if scale < 1.0:
            picture = picture.resize((max(1, round(picture.width * scale)), max(1, round(picture.height * scale))))
        if mark is not None:
            x, y = mark[0] * picture.width / self.size[0], mark[1] * picture.height / self.size[1]
            radius = max(8, picture.width // 40)
            draw = ImageDraw.Draw(picture)
            for width, colour in ((5, (255, 255, 255)), (3, (220, 38, 38))):
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=colour, width=width)
        out = io.BytesIO()
        picture.save(out, "JPEG", quality=72)
        data = out.getvalue()
        if mark is None:
            self._thumb = data
        return data


def drawn_part(window: Window, cua_size: tuple[int, int], scaling: Scaling) -> tuple[int, int]:
    """How much of Cua's picture (from its top-left) the window drew: all of it, unless Windows scales the
    window for its display, which draws it at its own smaller size and leaves the rest black."""

    _left, _top, width, height = window.bounds
    scale, (border_x, border_y) = scaling.scale, scaling.border
    if scale <= 1.0 or min(width, height, *cua_size) <= 0:
        return cua_size
    ratio_x, ratio_y = (width - 2 * _CUA_INSET) / cua_size[0], (height - 2 * _CUA_INSET) / cua_size[1]
    # The frame's far edges in the window's own pixels, less Cua's crop.
    right = (border_x + width) / scale - border_x - _CUA_INSET
    bottom = (border_y + height) / scale - border_y - _CUA_INSET
    return max(1, min(cua_size[0], int(right / ratio_x))), max(1, min(cua_size[1], int(bottom / ratio_y)))


def take(window: Window, png: bytes, cua_size: tuple[int, int], elements: tuple[CuaElement, ...],
         scaling: Scaling | None = None) -> Shot:
    """The model's picture of a window: the part of the driver's the window drew, scaled to fit 1280×800
    (never up), as a JPEG.

    Password, code and card fields are covered, as the browser covers them.
    """

    from PIL import Image, ImageDraw

    with Image.open(io.BytesIO(png)) as source:
        picture = source.convert("RGB")
    shown = drawn_part(window, cua_size, scaling) if scaling is not None else cua_size
    if shown != cua_size:
        picture = picture.crop((0, 0, round(shown[0] * picture.width / cua_size[0]),
                                round(shown[1] * picture.height / cua_size[1])))
    scale = min(1.0, MODEL_MAX_WIDTH / picture.width, MODEL_MAX_HEIGHT / picture.height)
    if scale < 1.0:
        picture = picture.resize(
            (max(1, round(picture.width * scale)), max(1, round(picture.height * scale))), Image.LANCZOS,
        )
    shot = Shot(window, cua_size, b"", picture.size, elements, scaling=scaling, shown=shown)
    draw = ImageDraw.Draw(picture)
    for element in elements:
        if secret_field(element.role, element.label):
            left, top, right, bottom = shot._box(element)
            if right > left and bottom > top:
                draw.rectangle((left, top, right, bottom), fill=(96, 104, 116))
    out = io.BytesIO()
    picture.save(out, "JPEG", quality=80)
    shot.image = out.getvalue()
    return shot
