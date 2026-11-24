"""Ready to publish (C10): a made video's publish kit for the platforms the person names.

The kit is made only when the person names a platform or asks for one (G9), never with every video. For each
platform it has the video in that platform's shape (the version's own video when it's that shape already, else
the same version exported in it, each scene reframed on its zoom: ``video_projects.export``, in the background),
a thumbnail in that shape (a picture the person or Row-Bot made, else a still of the video's longest scene, else
its title card, with the video's name laid on it in the brand kit's title style), the post's title and description
(YouTube's limits; a YouTube description lists the video's chapters, C7), and its subtitles as SRT and WebVTT, from
the words its captions are timed by (C7). Posting stays with the person's apps (X, YouTube), each asking first.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Any

from row_bot.video_projects import ProjectError

logger = logging.getLogger(__name__)

# Where a video goes, and the shape each platform shows it in.
PLATFORMS: dict[str, str] = {"youtube": "16:9", "shorts": "9:16", "tiktok": "9:16", "reels": "9:16",
                             "instagram": "4:5", "facebook": "4:5", "x": "16:9", "linkedin": "1:1"}
PLATFORM_WORDS = {"youtube": "YouTube", "shorts": "YouTube Shorts", "tiktok": "TikTok", "reels": "Instagram Reels",
                  "instagram": "Instagram", "facebook": "Facebook", "x": "X", "linkedin": "LinkedIn"}
# A thumbnail or cover in each shape: YouTube's 1280×720, and the phone apps' full-size covers.
THUMBNAIL_SIZES = {"16:9": (1280, 720), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}
# YouTube's limits: a title of 100 characters, a description of 5,000, a thumbnail of 2 MB, and no angle brackets.
MAX_TITLE, MAX_DESCRIPTION, THUMBNAIL_BYTES = 100, 5000, 2_000_000
_REFUSED = re.compile(r"[<>]")
# A subtitle shows at most two lines of this many characters, each cue at least this long.
SUBTITLE_LINE, SHORTEST_CUE = 42, 0.5


def shapes_for(platforms: list[str]) -> list[str]:
    """The shapes these platforms show a video in, each once, in the order they were named."""
    unknown = [name for name in platforms if name not in PLATFORMS]
    if unknown or not platforms:
        raise ProjectError(f"Name where it's going: {', '.join(PLATFORMS)}. When the person hasn't said, ask them.")
    return list(dict.fromkeys(PLATFORMS[name] for name in platforms))


# ── Subtitles ───────────────────────────────────────────────────────────────


def _two_lines(text: str) -> str:
    """A cue's words on one line, or on two of about the same length when they're longer than a line."""
    if len(text) <= SUBTITLE_LINE or " " not in text:
        return text
    middle = len(text) / 2
    split = min((index for index, character in enumerate(text) if character == " "),
                key=lambda index: abs(index - middle))
    return f"{text[:split]}\n{text[split + 1:]}"


def subtitle_cues(words: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """A video's subtitles from its words in time: a phrase at a time (``video_editing.caption_pages``), at most two
    lines, each cue after the one before it."""
    from row_bot.video_editing import caption_pages

    cues: list[tuple[float, float, str]] = []
    for start, until, texts, _active in caption_pages(list(words), SUBTITLE_LINE * 2):
        start = max(start, cues[-1][1]) if cues else start
        cues.append((round(start, 3), round(max(until, start + SHORTEST_CUE), 3), _two_lines(" ".join(texts))))
    return cues


def _stamp(seconds: float, mark: str) -> str:
    millis = round(max(0.0, seconds) * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    whole, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole:02d}{mark}{millis:03d}"


def srt(cues: list[tuple[float, float, str]]) -> str:
    """Subtitles as SubRip (.srt), which YouTube, LinkedIn and Facebook take."""
    return "\n".join(f"{number}\n{_stamp(start, ',')} --> {_stamp(end, ',')}\n{text}\n"
                     for number, (start, end, text) in enumerate(cues, start=1))


def vtt(cues: list[tuple[float, float, str]]) -> str:
    """Subtitles as WebVTT (.vtt), which web players take."""
    return "WEBVTT\n\n" + "\n".join(f"{_stamp(start, '.')} --> {_stamp(end, '.')}\n{text}\n"
                                    for start, end, text in cues)


# ── Title and description ───────────────────────────────────────────────────


def post_words(title: str, description: str) -> tuple[str, str]:
    """The post's title and description as YouTube takes them: no angle brackets, within its limits."""
    title = " ".join(_REFUSED.sub("", str(title or "")).split())[:MAX_TITLE].strip()
    lines = [" ".join(line.split()) for line in _REFUSED.sub("", str(description or "")).splitlines()]
    return title, "\n".join(lines).strip()[:MAX_DESCRIPTION]


def described(kit: dict[str, Any]) -> str:
    """The description as it's pasted: its words, then its chapters (a YouTube video's, C7)."""
    from row_bot.video_editing import clock

    chapters = "\n".join(f"{clock(item['at'])} {item['title']}" for item in kit.get("chapters") or [])
    return "\n\n".join(part for part in (kit.get("description") or "", chapters) if part)


# ── Thumbnails ──────────────────────────────────────────────────────────────


def thumbnail(picture: Any, words: str, shape: str, look: Any) -> bytes:
    """A thumbnail or cover in *shape* (``THUMBNAIL_SIZES``): *picture* (a PIL image) cropped to fill it, else the
    brand's title card backdrop, with *words* large in the brand kit's typeface and title style (``Look.titles``),
    clear of the shape's safe area, and its logo; a JPEG within YouTube's 2 MB."""
    from PIL import Image, ImageDraw, ImageFilter, ImageOps

    from row_bot.video_editing import _wrap, card_backdrop, font, logo_overlay, margins

    width, height = THUMBNAIL_SIZES[shape]
    if picture is None:
        canvas = Image.open(io.BytesIO(card_backdrop(look.colour, width, height))).convert("RGBA")
    else:
        canvas = ImageOps.fit(picture.convert("RGB"), (width, height), Image.Resampling.LANCZOS).convert("RGBA")
    tall = height > width * 1.2
    words = " ".join(str(words or "").split())
    if words:
        draw = ImageDraw.Draw(canvas)
        left, _top, right, bottom = margins(width, height)
        largest = round(min(width * 0.09, height * 0.12) * (1.15 if look.titles == "bold" else 1.0))
        size = largest
        while True:  # Within 86% of the width, as captions are, on at most three lines: smaller for a longer name.
            typeface = font(size, look.font)
            pad, line_height = round(size * 0.5), round(size * 1.15)
            edge = max(4, round(size * 0.12)) if look.titles == "studio" else 0
            room = min(int(width * 0.86), width - left - right) - 2 * pad - edge
            if len(_wrap(draw, words, typeface, room, 99)) <= 3 or size <= largest * 0.6:
                break
            size = round(size * 0.9)
        lines = _wrap(draw, words, typeface, room, 3) or [words]
        box_width = edge + max(round(draw.textlength(line, font=typeface)) for line in lines) + 2 * pad
        box_height = line_height * len(lines) + 2 * pad
        x = (width - box_width) // 2 if tall else left
        y = round(height * 0.42 - box_height / 2) if tall else height - bottom - box_height
        shade = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        if look.titles == "minimal":  # White words over a soft shade, a short rule of the brand's colour under them.
            fade = Image.new("L", (1, 256))
            for row in range(256):
                fade.putpixel((0, row), round(170 * max(0.0, row / 255 - 0.4) / 0.6))
            shade.putalpha(fade.resize((width, height)))
            for index, line in enumerate(lines):
                ImageDraw.Draw(shade).text((x + pad, y + pad + index * line_height), line, font=typeface,
                                           fill=(0, 0, 0, 190))
            canvas.alpha_composite(shade.filter(ImageFilter.GaussianBlur(max(2, size * 0.12))))
            rule = max(4, round(size * 0.12))
            draw.rectangle((x + pad, y + box_height - rule, x + pad + round(size * 2.4), y + box_height),
                           fill=(*look.colour, 255))
        else:  # On a card of the brand's colour (studio, with a light edge), or on its solid colour (bold).
            radius = round(size * 0.3) if look.titles == "studio" else round(size * 0.08)
            lift = round(size * 0.12)
            ImageDraw.Draw(shade).rounded_rectangle((x, y + lift, x + box_width, y + box_height + lift),
                                                    radius=radius, fill=(0, 0, 0, 120))
            canvas.alpha_composite(shade.filter(ImageFilter.GaussianBlur(max(2, size * 0.25))))
            card = Image.new("RGBA", (box_width, box_height), (*look.colour, 240))
            if edge:
                ImageDraw.Draw(card).rectangle((0, 0, edge - 1, box_height), fill=(255, 255, 255, 235))
            shape_mask = Image.new("L", card.size, 0)
            ImageDraw.Draw(shape_mask).rounded_rectangle((0, 0, box_width - 1, box_height - 1), radius=radius,
                                                         fill=255)
            layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            layer.paste(card, (x, y), shape_mask)
            canvas.alpha_composite(layer)
        for index, line in enumerate(lines):
            draw.text((x + edge + pad, y + pad + index * line_height - size * 0.08), line, font=typeface,
                      fill=(255, 255, 255, 255))
    if look.logo:  # In its corner; at the top when the words are at the bottom.
        corner = look.logo_corner if tall or not look.logo_corner.startswith("bottom") else \
            look.logo_corner.replace("bottom", "top")
        logo = logo_overlay(look.logo, corner, width, height)
        canvas.alpha_composite(Image.open(io.BytesIO(logo.png)).convert("RGBA"), (logo.x, logo.y))
    picture_rgb = canvas.convert("RGB")
    for quality in (90, 80, 70, 60):
        output = io.BytesIO()
        picture_rgb.save(output, format="JPEG", quality=quality, optimize=True)
        if output.tell() <= THUMBNAIL_BYTES:
            break
    return output.getvalue()


def _picture(name: str, project: Any) -> Any:
    """A picture the person or Row-Bot made for the thumbnail (a PIL image), by its name in the chat."""
    from PIL import Image, ImageOps

    from row_bot.media_sources import SourceError, resolve

    try:
        found = resolve(name, project.conversation_id, allowed=frozenset())
    except SourceError as error:
        raise ProjectError(str(error)) from None
    if len(found) != 1 or found[0].kind != "image":
        raise ProjectError(f"{name} isn't a picture in this chat: give one for the thumbnail, or leave it empty for "
                           "a still of the video.")
    with Image.open(io.BytesIO(found[0].read_bytes())) as image:
        return ImageOps.exif_transpose(image).convert("RGB")


def still(project: Any, version: Any) -> tuple[Any, int]:
    """A still of the version's longest scene with footage, halfway in (a PIL image), and that scene's number; read
    from its footage, so no caption, title or logo is on it. (None, 0) for a video of title cards, or footage that
    can't be read here."""
    from pathlib import Path

    from row_bot.media_sources import SourceError, resolve
    from row_bot.video_studio import _card_picture

    def length(scene: Any) -> float:
        return float("inf") if scene.end is None else (scene.end - scene.start) / max(scene.speed, 0.01)

    shown = [(number, scene) for number, scene in enumerate(version.scenes, start=1) if scene.source]
    if not shown:
        return None, 0
    number, scene = max(shown, key=lambda item: length(item[1]))
    try:
        found = resolve(scene.source, project.conversation_id,
                        allowed=frozenset(Path(item["path"]) for item in project.outside))
        if len(found) != 1:
            return None, 0
        media = found[0]
        at = 0.0 if media.kind == "image" else scene.start + (2.0 if scene.end is None else
                                                             (scene.end - scene.start) / 2)
        source = media.path if media.path and not media.ref else media.read_bytes(1 << 31)
        return _card_picture(source, picture=media.kind == "image", at=at), number
    except ImportError:  # No PyAV (the media extra): the thumbnail is the title card.
        return None, 0
    except (SourceError, OSError, ValueError):
        logger.debug("A thumbnail's still couldn't be read", exc_info=True)
        return None, 0


# ── The kit ─────────────────────────────────────────────────────────────────


def _kept(conversation_id: str, name: str, data: bytes, role: str, output_id: str, version: int) -> dict[str, str]:
    from row_bot.application.attachments import AttachmentError, register_attachment

    try:
        saved = register_attachment(conversation_id, name, data, role=role, output_id=output_id, version=version)
    except AttachmentError:
        raise ProjectError(f"{name} couldn't be saved in the chat.") from None
    return {"name": str(saved["name"]), "ref": str(saved["attachment_ref"])}


def make(project: Any, platforms: list[str], number: int | None = None, *, title: str = "", description: str = "",
         picture: str = "", download: bool = False) -> Any:
    """A made version's publish kit for *platforms*: its video in each one's shape (the version's own, else an export
    in the background), a thumbnail in each, its title and description, a YouTube video's chapters, and subtitles.
    Saved with the version (``Version.publish``), its files in the chat's files."""
    from row_bot import video_edits, video_projects
    from row_bot.video_editing import Look

    project = video_projects.get(project.project_id, project.conversation_id) or project
    version = project.version(number)
    if version is None:
        raise ProjectError("That version isn't kept any more.")
    shapes = shapes_for(platforms)
    made = video_edits.get(version.edit_id) if version.edit_id else None
    if made is None or made.status != "saved":
        raise ProjectError("Make this version first: its publish kit comes from it.")
    image = _picture(picture, project) if picture else None
    own = own_shape(version)
    for shape in shapes:  # Each other shape once: one already made, or being made, stays.
        done = video_edits.get(version.exports.get(shape, ""))
        if shape != own and not (done is not None and (done.active or done.status == "saved")):
            video_projects.export(project, shape, version.number, download=download)
    still_from = 0
    if image is None:
        image, still_from = still(project, version)
    look = video_projects._brand_look() if version.brand else Look()
    conversation, output = project.conversation_id, project.project_id
    thumbnails = [{"shape": shape, **_kept(conversation, video_projects._file_name(
        project.title, ".jpg", f"thumbnail {shape.replace(':', 'x')}"), thumbnail(image, project.title, shape, look),
        "draft", output, version.number)} for shape in shapes]
    cues = subtitle_cues(made.words)
    subtitles = [{"format": kind, **_kept(conversation, video_projects._file_name(project.title, f".{kind}"),
                                          text.encode("utf-8"), "working", output, version.number)}
                 for kind, text in (("srt", srt(cues)), ("vtt", vtt(cues)))] if cues else []
    words, about = post_words(title or project.title, description or version.logline)
    chapters = video_projects.chapters(version, project.title, list(made.marks), made.expected_seconds) \
        if "youtube" in platforms and len(made.marks) == len(version.scenes) + 1 else []
    kit = {"platforms": list(dict.fromkeys(platforms)), "title": words, "description": about,
           "chapters": [{"at": at, "title": name} for at, name in chapters], "thumbnails": thumbnails,
           "subtitles": subtitles,
           "thumbnail_from": "picture" if picture else f"scene {still_from}" if still_from else "card"}
    video_projects._update(project, version.number, publish=kit)
    return (video_projects.get(project.project_id, project.conversation_id) or project).version(version.number)


def kit_from(data: Any) -> dict[str, Any]:
    """A saved version's kit, each part it still knows ({} none)."""
    if not isinstance(data, dict) or not data.get("platforms"):
        return {}
    try:
        files = [{"shape": str(item["shape"]), "name": str(item["name"]), "ref": str(item["ref"])}
                 for item in data.get("thumbnails") or [] if item.get("shape") in THUMBNAIL_SIZES]
        subtitles = [{"format": str(item["format"]), "name": str(item["name"]), "ref": str(item["ref"])}
                     for item in data.get("subtitles") or [] if item.get("format") in ("srt", "vtt")]
        return {"platforms": [name for name in data["platforms"] if name in PLATFORMS],
                "title": str(data.get("title") or "")[:MAX_TITLE],
                "description": str(data.get("description") or "")[:MAX_DESCRIPTION],
                "chapters": [{"at": float(item["at"]), "title": str(item["title"])[:120]}
                             for item in data.get("chapters") or []][:200],
                "thumbnails": files, "subtitles": subtitles,
                "thumbnail_from": str(data.get("thumbnail_from") or "card")[:40]}
    except (KeyError, TypeError, ValueError):
        return {}


def own_shape(version: Any) -> str:
    """The shape a version's own video was made in ("" when it isn't one of the shapes, or isn't known)."""
    from row_bot import video_edits

    made = video_edits.get(version.edit_id) if version.edit_id else None
    return (made.shape if made else "") or version.aspect


def summary_lines(kit: dict[str, Any], version: Any) -> list[str]:
    """The tool result's lines for a version's kit: what's in it and where each platform's video is."""
    from row_bot import video_edits

    if not kit:
        return []
    made = video_edits.get(version.edit_id) if version.edit_id else None
    own = own_shape(version)
    lines = [f"Publish kit for {', '.join(PLATFORM_WORDS[name] for name in kit['platforms'])}:"]
    for shape in dict.fromkeys(PLATFORMS[name] for name in kit["platforms"]):
        where = ", ".join(PLATFORM_WORDS[name] for name in kit["platforms"] if PLATFORMS[name] == shape)
        export = video_edits.get(version.exports.get(shape, "")) if shape != own else made
        state = ("not made: " + version.export_problems[shape] if shape in version.export_problems
                 else "being made" if export is None
                 else {"saved": f"made: {export.output_name}", "failed": f"not made: {export.detail or 'it failed'}",
                       "stopped": "stopped"}.get(export.status, "being made"))
        lines.append(f"- {shape} video for {where}: {state}")
    source = {"picture": "the picture given", "card": "the title card"}.get(kit["thumbnail_from"],
                                                                             f"a still of {kit['thumbnail_from']}")
    lines.append(f"- Thumbnails ({source}, titled): {', '.join(item['name'] for item in kit['thumbnails'])}")
    lines.append(f"- Title: {kit['title']}")
    if kit["description"] or kit["chapters"]:
        lines.append("- Description" + (" with its chapters" if kit["chapters"] else "") + ": "
                     + " / ".join(line for line in described(kit).splitlines() if line))
    lines.append("- Subtitles: " + (", ".join(item["name"] for item in kit["subtitles"]) if kit["subtitles"]
                                     else "none: no words are said in it (a narration or a clip's own speech "
                                     "needs captions on)"))
    lines.append("Posting stays with the person: they upload these, or ask to post with an app (each asks first).")
    return lines
