"""The brand kit (5.3.1 B3): one look every video project uses unless a version turns it off.

Its colour fills title cards, titles and speed badges, its caption style sets how lines show under
their scenes, its logo (a picture from Library › Media) sits in a corner of the whole video, and its
intro and outro are title cards on its colour before the first scene and after the last. Its framing
sets footage on its backdrop or edge to edge, its camera follows what was done or stays still, and
its voice and speed say every video's lines (a video may choose its own). Its music mood is what
Row-Bot picks when it makes music for a video, and its loudness what every video's mix is brought to (-14 LUFS,
as social video plays). Its font sets every word's typeface, and its title style how titles, lower thirds and the
end card look (C5). It is kept in one small file in the data folder.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

CaptionStyle = Literal["box", "bold", "title"]
CAPTION_STYLES: tuple[str, ...] = ("box", "bold", "title")
MOODS: tuple[str, ...] = ("calm", "upbeat", "focused", "warm")
CORNERS: tuple[str, ...] = ("top-left", "top-right", "bottom-left", "bottom-right")
FRAMINGS: tuple[str, ...] = ("framed", "full")
CAMERAS: tuple[str, ...] = ("follow", "still")
# Its typeface and how its titles, lower thirds and end card look (C5): ``video_editing.FONTS`` and ``TITLE_STYLES``.
FONTS: tuple[str, ...] = ("sans", "modern", "serif", "display")
TITLE_STYLES: tuple[str, ...] = ("studio", "minimal", "bold")
# A narration's speed: a little slower or faster than normal, never harsh.
VOICE_SPEEDS = (0.9, 1.25)
# A mix's loudness (LUFS): -14 for social video and YouTube, -16 for podcasts, down to -24 for broadcast.
LOUDNESS = (-24.0, -10.0)
MAX_CARD = 120
_COLOUR = re.compile(r"#[0-9a-fA-F]{6}")
_FILE = "video-brand.json"
# Saved with every kit since footage fills the frame by default (M8, 22 Nov). A kit saved before then wrote "framed"
# as the old default, not as a choice, so it loads edge to edge; one saved since keeps its own framing.
_KIT = 2


class BrandError(ValueError):
    """The kit can't be saved as asked; the message says why."""


@dataclass
class Brand:
    colour: str = "#1f2937"
    captions: str = "box"
    # A picture in the chats' files (Library › Media), by its reference; "" for none.
    logo: str = ""
    logo_corner: str = "top-left"
    intro: str = ""
    outro: str = ""
    music_mood: str = "calm"
    # Edge to edge by default (R3, 12 Nov; M8, 22 Nov): footage small on a plain backdrop read as unfinished.
    # "framed" sets it inset on the colour, only when the person chooses it.
    framing: str = "full"
    camera: str = "follow"
    # The voice that says the lines ("" the one chosen in Settings) and its speed.
    voice: str = ""
    voice_speed: float = 1.0
    font: str = "sans"
    titles: str = "studio"
    loudness: float = -14.0


def _path() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir() / _FILE


def load() -> Brand:
    """The kit as saved, or the plain default."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Brand()
    if isinstance(data, dict) and data.get("kit") != _KIT:
        data = {**data, "framing": "full"}
    try:
        return _clean(data, check_logo=False)
    except BrandError:
        logger.warning("The saved brand kit was unreadable; using the default")
        return Brand()


def _clean(data: dict[str, Any], *, check_logo: bool) -> Brand:
    brand = Brand()
    colour = str(data.get("colour") or brand.colour)
    if not _COLOUR.fullmatch(colour):
        raise BrandError("The colour is a hex colour such as #1f2937.")
    captions = str(data.get("captions") or "box")
    if captions not in CAPTION_STYLES:
        raise BrandError(f"Captions are {', '.join(CAPTION_STYLES)}.")
    corner = str(data.get("logo_corner") or "top-left")
    if corner not in CORNERS:
        raise BrandError(f"The logo goes {', '.join(CORNERS)}.")
    mood = str(data.get("music_mood") or "calm")
    if mood not in MOODS:
        raise BrandError(f"The music mood is {', '.join(MOODS)}.")
    framing = str(data.get("framing") or "full")
    if framing not in FRAMINGS:
        raise BrandError(f"Footage is {', '.join(FRAMINGS)}.")
    camera = str(data.get("camera") or "follow")
    if camera not in CAMERAS:
        raise BrandError(f"The camera is {', '.join(CAMERAS)}.")
    voice = narration_voice(data.get("voice"))
    speed = narration_speed(data.get("voice_speed"))
    try:
        loudness = round(float(data.get("loudness", -14.0)), 1)
    except (TypeError, ValueError):
        raise BrandError("The loudness is a number of LUFS, -14 for social video.") from None
    if not LOUDNESS[0] <= loudness <= LOUDNESS[1]:
        raise BrandError(f"The loudness is {LOUDNESS[0]:g} to {LOUDNESS[1]:g} LUFS (-14 for social video).")
    typeface = str(data.get("font") or "sans")
    if typeface not in FONTS:
        raise BrandError(f"The font is {', '.join(FONTS)}.")
    titles = str(data.get("titles") or "studio")
    if titles not in TITLE_STYLES:
        raise BrandError(f"Titles are {', '.join(TITLE_STYLES)}.")
    logo = str(data.get("logo") or "").strip()
    if logo and check_logo:
        _check_logo(logo)
    return Brand(colour=colour.lower(), captions=captions, logo=logo, logo_corner=corner,
                 intro=re.sub(r"\s+", " ", str(data.get("intro") or "")).strip()[:MAX_CARD],
                 outro=re.sub(r"\s+", " ", str(data.get("outro") or "")).strip()[:MAX_CARD], music_mood=mood,
                 framing=framing, camera=camera, voice=voice, voice_speed=speed, loudness=loudness, font=typeface,
                 titles=titles)


def narration_voice(value: Any) -> str:
    """A voice for a video's lines: one of the local voices, or "" for the one chosen in Settings."""
    from row_bot.tts import VOICE_CATALOG

    voice = str(value or "").strip()
    if voice and voice not in VOICE_CATALOG:
        raise BrandError(f"There's no voice called {voice}.")
    return voice


def narration_speed(value: Any) -> float:
    """A speed for a video's lines (1 is normal)."""
    try:
        speed = float(value) if value not in (None, "") else 1.0
    except (TypeError, ValueError):
        raise BrandError("The voice's speed is a number, 1 for normal.") from None
    if not VOICE_SPEEDS[0] <= speed <= VOICE_SPEEDS[1]:
        raise BrandError(f"The voice's speed is {VOICE_SPEEDS[0]:g} to {VOICE_SPEEDS[1]:g} (1 is normal).")
    return round(speed, 2)


def _check_logo(ref: str) -> None:
    from row_bot import media_library

    if not any(item["ref"] == ref and item["kind"] == "image" for item in media_library.items(limit=500)):
        raise BrandError("The logo is a picture from Library › Media.")


def update(changes: dict[str, Any]) -> Brand:
    """Change some of the kit's fields, keep the rest (from a chat: the person said how videos should look)."""
    return save({**asdict(load()), **{key: value for key, value in changes.items() if value is not None}})


def save(data: dict[str, Any]) -> Brand:
    """Replace the kit (every field; a missing one is its default)."""
    brand = _clean(data, check_logo=True)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.part")
    partial.write_text(json.dumps({**asdict(brand), "kit": _KIT}), encoding="utf-8", newline="\n")
    os.replace(partial, path)
    return brand


def logo_png(brand: Brand) -> bytes | None:
    """The logo as a PNG for the renderer, or None when there's none (or it can't be read any more)."""
    if not brand.logo:
        return None
    import io

    from PIL import Image

    from row_bot.application.attachments import AttachmentError, read_attachment

    try:
        picture = Image.open(io.BytesIO(read_attachment(brand.logo)[1])).convert("RGBA")
        output = io.BytesIO()
        picture.save(output, format="PNG")
        return output.getvalue()
    except (AttachmentError, OSError, ValueError):
        logger.info("The brand kit's logo couldn't be read")
        return None


def rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)
