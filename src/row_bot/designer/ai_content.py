"""Designer — AI-powered content helpers.

Provides functions for:
- AI image generation → raw bytes or legacy base64 <img> helpers
- AI copy refinement → rewrite text elements with LLM
- Data-viz chart embedding → Plotly figure → static PNG helpers
- Brand-enriched prompt construction (all generated assets must reflect
  the active project's BrandConfig + ProjectBrief — see
  ``tool_guides/designer_guide/SKILL.md``)
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import re
import uuid
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from row_bot.designer.state import DesignerProject  # noqa: F401

logger = logging.getLogger(__name__)


def _designer_text_llm():
    """Return the Agent-ready LLM selected for the active Designer run."""
    from row_bot.models import _active_model_override, get_current_model, get_llm_for
    from row_bot.providers.readiness import ensure_agent_ready

    model_label = str(_active_model_override.get() or get_current_model() or "").strip()
    ensure_agent_ready(model_label)
    return get_llm_for(model_label)


# ═══════════════════════════════════════════════════════════════════════
# BRAND & THEME ENFORCEMENT (Phase 2.1.C)
# ═══════════════════════════════════════════════════════════════════════
#
# Every generated asset (image, video, chart, component, recorded demo)
# MUST route through ``_brand_enriched_prompt`` so that the provider
# receives the brand palette, typography, logo treatment, voice/tone,
# project brief, and canvas aspect as context. The returned prompt has a
# stable structure so brand/theme changes can be detected via
# ``brand_theme_cache_key`` and used to invalidate cached assets.

_TONE_MOTION_MAP: dict[str, str] = {
    "minimal":     "slow dolly, shallow depth of field, 24fps cinematic calm",
    "enterprise":  "slow dolly, shallow depth of field, 24fps cinematic calm",
    "professional":"slow dolly, shallow depth of field, 24fps cinematic calm",
    "playful":     "bouncy handheld, saturated colors, 30fps energetic pacing",
    "energetic":   "bouncy handheld, saturated colors, 30fps energetic pacing",
    "technical":   "orthographic or isometric angles, mechanical motion",
    "luxury":      "macro detail, slow reveal, soft key light, premium finish",
    "editorial":   "cinematic framing, soft contrast, gentle parallax",
    "whimsical":   "playful 2D parallax, bright colors, soft bounce",
}


def _tone_motion_language(tone: str) -> str:
    key = (tone or "").strip().lower()
    return _TONE_MOTION_MAP.get(key, "")


def _brand_summary_lines(project: "DesignerProject | None") -> list[str]:
    if project is None or project.brand is None:
        return []
    brand = project.brand
    lines = [
        "[Brand]",
        (
            f"Palette: primary {brand.primary_color}, secondary {brand.secondary_color}, "
            f"accent {brand.accent_color}, background {brand.bg_color}, text {brand.text_color}."
        ),
        f"Typography: {brand.heading_font} for headings, {brand.body_font} for body.",
    ]
    has_logo = bool((brand.logo_asset_id or "").strip() or brand.logo_b64)
    if has_logo:
        scope = (brand.logo_scope or "all").lower()
        position = (brand.logo_position or "top_right").replace("_", "-")
        lines.append(
            f"Logo: present (scope={scope}, position={position}, "
            f"safe padding {brand.logo_padding}px). Do not distort, recolor, "
            f"or crop the logo mark."
        )
    else:
        lines.append("Logo: none set — do not invent a logo.")
    return lines


def _brief_summary_lines(project: "DesignerProject | None") -> list[str]:
    if project is None or project.brief is None:
        return []
    brief = project.brief
    parts: list[str] = ["[Brief]"]
    if brief.output_type:
        parts.append(f"Output type: {brief.output_type}.")
    if brief.audience:
        parts.append(f"Audience: {brief.audience}.")
    if brief.tone:
        parts.append(f"Tone: {brief.tone}.")
    if brief.length:
        parts.append(f"Length / scale: {brief.length}.")
    if brief.build_description:
        # Truncate to avoid exploding provider prompts.
        desc = brief.build_description.strip()
        if len(desc) > 400:
            desc = desc[:400].rsplit(" ", 1)[0] + "…"
        parts.append(f"Key message: {desc}")
    if brief.brand_url:
        parts.append(f"Brand reference URL: {brief.brand_url}.")
    return parts if len(parts) > 1 else []


def _composition_lines(
    *,
    project: "DesignerProject | None",
    asset_kind: str,
    aspect: str | None,
    tone_override: str | None,
) -> list[str]:
    lines = ["[Composition]"]
    resolved_aspect = (aspect or "").strip()
    if not resolved_aspect and project is not None:
        resolved_aspect = project.aspect_ratio or ""
    if resolved_aspect:
        lines.append(f"Aspect: {resolved_aspect}.")
    tone = (tone_override or "").strip()
    if not tone and project is not None and project.brief is not None:
        tone = project.brief.tone or ""
    motion = _tone_motion_language(tone) if asset_kind in ("video",) else ""
    if motion:
        lines.append(f"Motion: {motion}.")
    # Common guardrails.
    lines.extend([
        "Respect brand palette; avoid off-brand color harmonies.",
        "No lorem ipsum, no stock-photo clichés, no competing gradients.",
        "Honor logo safe-zone when present.",
    ])
    if asset_kind == "video":
        lines.append(
            "End frame / poster must include the brand logo lockup if a "
            "logo is set, unless the user explicitly asked for no logo."
        )
    return lines


def _brand_enriched_prompt(
    user_prompt: str,
    *,
    project: "DesignerProject | None",
    asset_kind: str,
    aspect: str | None = None,
    tone_override: str | None = None,
) -> str:
    """Return a provider-ready prompt enriched with brand + brief context.

    ``asset_kind`` is one of ``"image"``, ``"video"``, ``"chart"``,
    ``"component"``, ``"demo"``. For ``"video"`` the prompt additionally
    includes tone→motion language derived from the project brief.
    """
    kind = (asset_kind or "image").strip().lower() or "image"
    clean_prompt = (user_prompt or "").strip()
    blocks: list[str] = [
        "[User intent]",
        clean_prompt or "(empty)",
        "",
    ]
    brand_lines = _brand_summary_lines(project)
    if brand_lines:
        blocks.extend(brand_lines)
        blocks.append("")
    brief_lines = _brief_summary_lines(project)
    if brief_lines:
        blocks.extend(brief_lines)
        blocks.append("")
    comp_lines = _composition_lines(
        project=project,
        asset_kind=kind,
        aspect=aspect,
        tone_override=tone_override,
    )
    blocks.extend(comp_lines)
    return "\n".join(blocks).rstrip() + "\n"


def brand_theme_cache_key(project: "DesignerProject | None") -> str:
    """Stable hash of the brand/brief/aspect triple for cache invalidation.

    Generation caches keyed on ``(user_prompt, brand_theme_cache_key)``
    automatically miss when the brand palette, typography, logo, brief
    tone, or canvas aspect changes.
    """
    if project is None:
        return "no-project"
    parts: list[str] = []
    if project.brand is not None:
        brand = project.brand
        parts.extend([
            brand.primary_color, brand.secondary_color, brand.accent_color,
            brand.bg_color, brand.text_color,
            brand.heading_font, brand.body_font,
            brand.logo_asset_id or "", brand.logo_scope or "",
            brand.logo_position or "", str(brand.logo_max_height),
        ])
    else:
        parts.append("no-brand")
    if project.brief is not None:
        b = project.brief
        parts.extend([
            b.output_type, b.audience, b.tone, b.length,
            b.build_description, b.brand_preset,
        ])
    else:
        parts.append("no-brief")
    parts.append(project.aspect_ratio or "")
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


# ── AI Image Generation ──────────────────────────────────────────────────────


def generate_image_bytes(
    prompt: str,
    *,
    size: str = "auto",
) -> tuple[bytes, str]:
    """Generate an image and return raw bytes plus the detected MIME type."""

    try:
        from row_bot.tools.image_gen_tool import _detect_mime, _generate_image, _resolve_image_source
    except ImportError as exc:
        raise ValueError("image generation tool is not available") from exc

    result = _generate_image(prompt, size=size)
    try:
        image_bytes = _resolve_image_source("last")
    except Exception as exc:
        raise ValueError(f"image generation returned no data. Result: {result}") from exc
    return image_bytes, _detect_mime(image_bytes)

def generate_image_html(
    prompt: str,
    width: int = 800,
    height: int = 500,
    position: str = "bottom",
    size: str = "auto",
) -> str:
    """Generate an image and return an <img> tag with base64 data.

    Parameters
    ----------
    prompt : str
        Text prompt for the image generator.
    width, height : int
        Display dimensions in the design (CSS pixels).
    position : str
        Ignored here (caller decides where to insert).
    size : str
        Passed to image generator ('auto', '1024x1024', etc.).

    Returns
    -------
    str
        An ``<img src="data:image/png;base64,...">`` HTML string,
        or an error message string starting with "Error:".
    """
    try:
        image_bytes, mime = generate_image_bytes(prompt, size=size)
    except ValueError as exc:
        return f"Error: {exc}"

    img_id = f"ai-img-{uuid.uuid4().hex[:8]}"
    b64 = base64.b64encode(image_bytes).decode()
    tag = (
        f'<img id="{img_id}" src="data:{mime};base64,{b64}" '
        f'alt="{_escape_attr(prompt)}" '
        f'style="width:{width}px; max-width:100%; height:auto; display:block; margin:16px auto;" />'
    )
    return tag


_IMAGE_PLACEHOLDER_CLASS_RE = re.compile(
    r'class\s*=\s*"([^"]*?\b(?:'
    r'hero-image|hero-photo|hero-visual|'
    r'bg-image|background-image|'
    r'product-photo|product-image|product-shot|'
    r'recipe-photo|recipe-image|food-photo|'
    r'phone-photo|phone-image|screen-photo|device-photo|'
    r'card-photo|card-image|card-visual|'
    r'visual|photo|image-slot|image-placeholder|media-slot'
    r')\b[^"]*)"',
    re.IGNORECASE,
)

_EMPTY_DIV_RE = re.compile(
    r'<div\b([^>]*)>(\s*)</div>',
    re.IGNORECASE,
)

_EMPTY_IMG_RE = re.compile(
    r'<img\b([^>]*\bsrc\s*=\s*"\s*")([^>]*)/?>',
    re.IGNORECASE,
)


def _find_image_placeholder(page_html: str):
    """Heuristically locate an obvious empty visual container.

    Returns ``("slot", match)`` where ``match`` has groups
    ``(opening, inner, closing)`` just like the data-attribute slot
    regex, or ``("empty_img", match)`` for an empty ``<img src="">``
    tag, or ``None`` when nothing obvious is found.
    """
    # Prefer empty <img src=""> tags — usually an explicit author
    # intent to fill with a real source later.
    img_m = _EMPTY_IMG_RE.search(page_html)
    if img_m is not None:
        return ("empty_img", img_m)

    # Scan every <div ...>...</div> whose class matches a known image
    # placeholder pattern AND whose inner HTML has no <img>/<svg>
    # already.  We walk matches manually so we can filter.
    div_iter = re.finditer(
        r'(<div\b([^>]*)>)(.*?)(</div>)',
        page_html,
        re.IGNORECASE | re.DOTALL,
    )
    for m in div_iter:
        attrs = m.group(2) or ""
        inner = m.group(3) or ""
        if not _IMAGE_PLACEHOLDER_CLASS_RE.search(attrs):
            continue
        if re.search(r'<(?:img|svg|video)\b', inner, re.IGNORECASE):
            continue
        # Synthesize a (opening, inner, closing) match-like object
        # compatible with _fill_slot.
        class _SlotMatch:
            def __init__(self, full, opening, inner_html, closing, start, end):
                self._groups = (opening, inner_html, closing)
                self._start = start
                self._end = end
            def group(self, n):  # noqa: D401 - mimic re.Match
                return self._groups[n - 1]
            def start(self):
                return self._start
            def end(self):
                return self._end
        return (
            "slot",
            _SlotMatch(
                m.group(0), m.group(1), inner, m.group(4),
                m.start(), m.end(),
            ),
        )
    return None


def _find_element_by_selector(page_html: str, selector: str):
    """Return an object with .group(1/2/3)/.start()/.end() for the first
    element matching a simple CSS selector (``.class``, ``#id``, tag, or
    ``[attr=value]``). Returns ``None`` when not found. Supports only
    a single simple-selector component — enough for the common case
    ``designer_insert_image(position="replace:.hero-image")``.
    """
    sel = (selector or "").strip()
    if not sel:
        return None

    if sel.startswith("."):
        cls = re.escape(sel[1:])
        attr_re = rf'class\s*=\s*"[^"]*\b{cls}\b[^"]*"'
    elif sel.startswith("#"):
        ident = re.escape(sel[1:])
        attr_re = rf'id\s*=\s*"{ident}"'
    elif sel.startswith("["):
        # [attr=value] — treat as raw attribute match.
        body = sel[1:-1] if sel.endswith("]") else sel[1:]
        if "=" in body:
            k, v = body.split("=", 1)
            v = v.strip().strip('"').strip("'")
            attr_re = rf'{re.escape(k.strip())}\s*=\s*"{re.escape(v)}"'
        else:
            attr_re = rf'\b{re.escape(body.strip())}\b'
    else:
        # tag — match any element of that name.
        tag = re.escape(sel)
        pattern = re.compile(
            rf'(<{tag}\b[^>]*>)(.*?)(</{tag}>)',
            re.IGNORECASE | re.DOTALL,
        )
        m = pattern.search(page_html)
        return m

    pattern = re.compile(
        r'(<div\b[^>]*\b' + attr_re + r'[^>]*>)(.*?)(</div>)',
        re.IGNORECASE | re.DOTALL,
    )
    m = pattern.search(page_html)
    return m


def _fill_slot(page_html: str, slot_match, img_tag: str) -> str:
    """Replace the inner HTML of a matched container with ``img_tag``,
    sized to cover the slot and with any dashed/placeholder styling
    stripped from the wrapper."""
    opening = slot_match.group(1)
    closing = slot_match.group(3)

    # Drop dashed-border + placeholder background from wrapper style.
    opening = re.sub(
        r'border\s*:\s*[^;"]*dashed[^;"]*;?',
        '',
        opening,
        flags=re.IGNORECASE,
    )
    opening = re.sub(
        r'background\s*:\s*rgba\([^)]*\)\s*;?',
        '',
        opening,
        flags=re.IGNORECASE,
    )

    # Ensure overflow:hidden + border-radius on the wrapper.
    style_attr_re = re.compile(r'style\s*=\s*"([^"]*)"', re.IGNORECASE)
    sm = style_attr_re.search(opening)
    if sm:
        current_style = sm.group(1)
        extras = ""
        if "overflow" not in current_style.lower():
            extras += "overflow:hidden;"
        if "border-radius" not in current_style.lower():
            extras += "border-radius:14px;"
        if extras:
            new_style = current_style.rstrip("; ") + ";" + extras
            opening = opening[:sm.start(1)] + new_style + opening[sm.end(1):]

    # Apply cover styles to the ACTUAL <img> or <video> element inside
    # the wrapped fragment, not the outer wrapper div (object-fit only
    # works on replaced elements, not divs). Match the innermost tag
    # so a div-wrapped fragment still sizes correctly.
    fill_img = _apply_cover_to_media_element(img_tag)

    replacement = opening + fill_img + closing
    return page_html[:slot_match.start()] + replacement + page_html[slot_match.end():]


def _apply_cover_to_media_element(fragment: str) -> str:
    """Force ``width:100%; height:100%; object-fit:cover; display:block``
    on the first <img> or <video> element inside a fragment. Preserves
    all other attributes on the element."""
    media_re = re.compile(r'(<(?:img|video)\b)([^>]*)(/?>)', re.IGNORECASE)
    m = media_re.search(fragment)
    if not m:
        return fragment
    opening, attrs, closing = m.group(1), m.group(2), m.group(3)
    cover_decls = "width:100%;height:100%;object-fit:cover;display:block;"

    style_m = re.search(r'style\s*=\s*"([^"]*)"', attrs, re.IGNORECASE)
    if style_m:
        current = style_m.group(1)
        # Strip conflicting width/height/margin from author style;
        # keep any other declarations (e.g. transform, border-radius).
        cleaned_parts = []
        for part in current.split(";"):
            key = part.split(":", 1)[0].strip().lower()
            if key in {"width", "height", "max-width", "max-height",
                       "min-width", "min-height", "object-fit",
                       "display", "margin"}:
                continue
            if part.strip():
                cleaned_parts.append(part.strip())
        cleaned = "; ".join(cleaned_parts)
        new_style = cover_decls + (cleaned + ";" if cleaned else "")
        new_attrs = (
            attrs[:style_m.start()]
            + f'style="{new_style}"'
            + attrs[style_m.end():]
        )
    else:
        new_attrs = attrs.rstrip() + f' style="{cover_decls}"'

    new_elem = opening + new_attrs + closing
    return fragment[:m.start()] + new_elem + fragment[m.end():]


def _replace_empty_img(page_html: str, match, img_tag: str) -> str:
    """Swap an empty ``<img src="">`` tag for the new image fragment,
    preserving the width/height/style of the placeholder when present so
    the surrounding layout is undisturbed."""
    existing_attrs = (match.group(1) or "") + " " + (match.group(2) or "")
    # Lift width/height/style/class from the placeholder onto the new img.
    pass_through_attrs = []
    for key in ("class", "style", "width", "height", "id", "alt"):
        am = re.search(
            rf'\b{key}\s*=\s*"([^"]*)"', existing_attrs, re.IGNORECASE,
        )
        if am:
            pass_through_attrs.append(f'{key}="{am.group(1)}"')

    # Merge passthrough attrs onto the img_tag. If img_tag already has
    # a style attribute, we leave it alone to avoid double-style.
    new_img = img_tag
    for attr in pass_through_attrs:
        key = attr.split("=", 1)[0]
        if re.search(rf'\b{key}\s*=', new_img, re.IGNORECASE):
            continue
        new_img = new_img.replace("<img", f"<img {attr}", 1)

    return page_html[:match.start()] + new_img + page_html[match.end():]


def insert_image_into_page(
    page_html: str,
    img_tag: str,
    position: str = "bottom",
) -> str:
    """Insert an <img>/media fragment into page HTML at the given position.

    Parameters
    ----------
    position : str
        'top' → after <body>, 'bottom' → before </body>,
        'replace:SELECTOR' → replace first matching element (future).

    Behaviour notes
    ---------------
    Deck/document/storyboard pages use a fixed canvas with
    ``body { overflow:hidden }`` and content that typically fills the
    entire viewport (e.g. the blank-slide placeholder or a pitch title
    slide). Blindly appending before ``</body>`` pushes the new element
    below the visible canvas, so the user perceives the generation as a
    no-op. We mitigate this in two ways:

    1. **Blank-placeholder replacement** — when the page still shows the
       "Blank slide — describe what to build" placeholder (or similar),
       we replace the placeholder wrapper with a centred container that
       hosts the new fragment.
    2. **Overlay fallback for fixed canvases** — when the body has
       ``overflow:hidden`` and the fragment would otherwise land outside
       the canvas, we wrap it in an absolutely positioned overlay so it
       is at least visible; the user can then reposition via
       ``designer_move_image``.
    """
    # 0. Storyboard / typed-slot placeholder replacement. When a page
    #    carries a dedicated ``data-row-bot-shot-visual`` (or a generic
    #    ``data-row-bot-image-slot``) container, swap its *contents* for
    #    the generated image so the new asset fills the designer slot
    #    — preserving aspect ratio, max-width, and surrounding layout
    #    instead of dropping a centered overlay on top of it.
    slot_re = re.compile(
        r'(<div\b[^>]*\bdata-row-bot-(?:shot-visual|image-slot)\s*=\s*"[^"]*"[^>]*>)'
        r'(.*?)'
        r'(</div>)',
        re.IGNORECASE | re.DOTALL,
    )
    slot_match = slot_re.search(page_html)
    if slot_match:
        return _fill_slot(page_html, slot_match, img_tag)

    # 0b. Explicit replace:SELECTOR — caller named a CSS selector
    #     (class, id, tag, or attribute) whose first match should be
    #     replaced by the media fragment sized to fill it.
    pos_lower = (position or "").strip().lower()
    if pos_lower.startswith("replace:"):
        selector = position.split(":", 1)[1].strip()
        sel_match = _find_element_by_selector(page_html, selector)
        if sel_match is not None:
            return _fill_slot(page_html, sel_match, img_tag)

    # 0c. Heuristic: look for an obvious empty visual container
    #     (class name matching hero/bg/visual/photo patterns, or an
    #     empty <img src="">) and fill it in place. This lets the
    #     agent author a page with a clearly-named placeholder and
    #     have subsequent insert_image calls drop into the right spot
    #     without needing explicit data-* attributes.
    heuristic_match = _find_image_placeholder(page_html)
    if heuristic_match is not None:
        kind, match = heuristic_match
        if kind == "empty_img":
            return _replace_empty_img(page_html, match, img_tag)
        return _fill_slot(page_html, match, img_tag)

    # 1. Replace blank-slide placeholder if present. We match loosely so
    #    small copy changes in the starter template don't break the fix.
    placeholder_re = re.compile(
        r'<div[^>]*>\s*<p[^>]*>\s*Blank\s+(?:slide|document)\s+[—\-]\s+describe[^<]*</p>\s*</div>',
        re.IGNORECASE,
    )
    if placeholder_re.search(page_html):
        replacement = (
            '<div style="display:flex;justify-content:center;align-items:center;'
            'height:100%;padding:40px;">'
            + img_tag
            + '</div>'
        )
        return placeholder_re.sub(replacement, page_html, count=1)

    # 2. Detect fixed-canvas bodies (overflow:hidden). In that case,
    #    wrap the fragment in a position:absolute overlay at the top or
    #    bottom so it's visible inside the canvas rather than clipped.
    is_fixed_canvas = bool(re.search(
        r'(?:html\s*,\s*body|body)\s*\{[^}]*overflow\s*:\s*hidden',
        page_html, re.IGNORECASE,
    ))
    if is_fixed_canvas and position in ("top", "bottom"):
        # Inject a relative-positioning hint on <body> so our overlay
        # anchors to the canvas.
        overlay_anchor_re = re.compile(
            r'(<body)(\b[^>]*)(>)', re.IGNORECASE,
        )

        def _ensure_relative(match):
            opening, attrs, close = match.group(1), match.group(2), match.group(3)
            # If body already has a style attr, append position:relative
            style_m = re.search(r'style\s*=\s*"([^"]*)"', attrs, re.IGNORECASE)
            if style_m:
                current = style_m.group(1)
                if 'position' not in current.lower():
                    new_style = current.rstrip('; ') + ';position:relative;'
                    attrs = (
                        attrs[:style_m.start(1)] + new_style + attrs[style_m.end(1):]
                    )
            else:
                attrs = attrs + ' style="position:relative;"'
            return opening + attrs + close

        page_html = overlay_anchor_re.sub(_ensure_relative, page_html, count=1)

        anchor_style = (
            "top:40px;" if position == "top" else "bottom:40px;"
        )
        overlay = (
            f'<div style="position:absolute;left:50%;transform:translateX(-50%);'
            f'{anchor_style}z-index:50;max-width:90%;">'
            + img_tag
            + '</div>'
        )
        m = re.search(r"(</body>)", page_html, re.IGNORECASE)
        if m:
            idx = m.start()
            return page_html[:idx] + "\n" + overlay + "\n" + page_html[idx:]
        return page_html + "\n" + overlay

    # 3. Default flow (scrollable / landing pages): inline insertion.
    if position == "top":
        m = re.search(r"(<body[^>]*>)", page_html, re.IGNORECASE)
        if m:
            idx = m.end()
            return page_html[:idx] + "\n" + img_tag + "\n" + page_html[idx:]
    m = re.search(r"(</body>)", page_html, re.IGNORECASE)
    if m:
        idx = m.start()
        return page_html[:idx] + "\n" + img_tag + "\n" + page_html[idx:]
    return page_html + "\n" + img_tag


# ── AI Video Generation (Phase 2.1.D) ────────────────────────────────────────
#
# These wrappers run every generation through ``_brand_enriched_prompt`` and
# persist the resulting MP4 as a ``DesignerAsset`` (kind="video"). Poster
# extraction is intentionally left optional — when ffmpeg is not available
# we skip the poster and let HTML5 ``<video>`` preview the first frame
# natively via ``preload="metadata"``.

def _extract_video_poster_bytes(video_bytes: bytes) -> tuple[bytes, str] | None:
    """Best-effort first-frame extraction via ffmpeg. Returns (bytes, mime).

    Returns ``None`` when ffmpeg is not on PATH, the extraction fails, or
    the video is empty. Designer callers must tolerate missing posters.
    """
    if not video_bytes:
        return None
    import shutil
    import subprocess
    import tempfile
    import os

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.debug("ffmpeg not on PATH — skipping poster extraction")
        return None

    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as mp4_tmp:
            mp4_tmp.write(video_bytes)
            mp4_path = mp4_tmp.name
        png_path = mp4_path + ".poster.png"
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-ss", "0.1", "-i", mp4_path,
            "-frames:v", "1", "-vf", "scale=640:-2",
            png_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0:
            logger.debug("ffmpeg poster extraction failed: %s", result.stderr[:200])
            return None
        if not os.path.exists(png_path):
            return None
        data = open(png_path, "rb").read()
        return data, "image/png"
    except Exception as exc:
        logger.debug("poster extraction exception: %s", exc)
        return None
    finally:
        try:
            os.unlink(mp4_path)
        except Exception:
            pass
        try:
            os.unlink(png_path)
        except Exception:
            pass


def _call_video_tool_and_collect_bytes(
    *,
    enriched_prompt: str,
    aspect_ratio: str,
    duration_seconds: int,
    resolution: str,
    image_bytes: bytes | None,
) -> tuple[bytes, dict]:
    """Invoke ``tools.video_gen_tool`` and return ``(video_bytes, meta)``.

    Raises ``ValueError`` with a human-readable message on any failure.
    """
    try:
        from row_bot.tools.video_gen_tool import (
            _generate_video_google, _generate_video_xai,
            _get_configured_selection, _parse_model_config,
            get_and_clear_last_video,
        )
    except ImportError as exc:
        raise ValueError("video generation tool is not available") from exc

    # Reset any stale side-channel from a prior call.
    _ = get_and_clear_last_video()

    try:
        provider, _model = _parse_model_config(_get_configured_selection())
    except Exception as exc:
        raise ValueError(f"no video generation model configured: {exc}") from exc

    if provider == "google":
        status = _generate_video_google(
            enriched_prompt, duration_seconds, aspect_ratio, resolution, image_bytes,
        )
    elif provider == "xai":
        status = _generate_video_xai(
            enriched_prompt, duration_seconds, aspect_ratio, resolution, image_bytes,
        )
    else:
        raise ValueError(f"unknown video provider '{provider}'")

    if not isinstance(status, str) or status.lower().startswith(
        ("video generation failed", "video generation timed out",
         "video generation returned no", "video generation polling failed",
         "video generated but download failed", "unknown video")
    ):
        raise ValueError(status or "video generation failed")

    last = get_and_clear_last_video()
    if not last or not last.get("path"):
        raise ValueError("video generation finished but no file was saved")
    path = last["path"]
    try:
        from pathlib import Path as _Path
        data = _Path(path).read_bytes()
    except Exception as exc:
        raise ValueError(f"could not read generated video from {path}: {exc}") from exc
    if not data:
        raise ValueError("generated video file was empty")
    return data, last


def generate_video_bytes(
    prompt: str,
    *,
    project,
    duration_seconds: int = 6,
    aspect_ratio: str | None = None,
    resolution: str = "720p",
    image_source: str | None = None,
    tone_override: str | None = None,
):
    """Generate a video via ``tools.video_gen_tool`` with brand-enriched prompt.

    Saves the MP4 as a ``DesignerAsset`` (``kind="video"``) and returns it.
    Raises ``ValueError`` on generation failure.

    ``image_source``: if provided, the value is forwarded to
    ``tools.image_gen_tool._resolve_image_source`` to produce a still for
    image-to-video. Accepts ``"last"``, a file path, or a data URI.
    """
    from row_bot.designer.state import DesignerAsset
    from row_bot.designer.storage import save_asset_bytes

    aspect = aspect_ratio or (project.aspect_ratio if project is not None else "16:9")
    enriched = _brand_enriched_prompt(
        prompt,
        project=project,
        asset_kind="video",
        aspect=aspect,
        tone_override=tone_override,
    )

    image_bytes: bytes | None = None
    if image_source:
        try:
            from row_bot.tools.image_gen_tool import _resolve_image_source
            image_bytes = _resolve_image_source(image_source)
        except Exception as exc:
            raise ValueError(f"could not resolve image_source '{image_source}': {exc}") from exc

    video_bytes, meta = _call_video_tool_and_collect_bytes(
        enriched_prompt=enriched,
        aspect_ratio=aspect,
        duration_seconds=int(duration_seconds or 6),
        resolution=resolution or "720p",
        image_bytes=image_bytes,
    )

    # Persist MP4 as asset
    video_asset = DesignerAsset(
        kind="video",
        label=(prompt or "video clip")[:80],
        mime_type="video/mp4",
        filename=(meta.get("filename") or f"video-{uuid.uuid4().hex[:8]}.mp4"),
        duration_ms=int(duration_seconds or 6) * 1000,
        muted=True, controls=True, autoplay=False, loop=False,
    )
    video_asset.stored_name = save_asset_bytes(
        project.id, video_asset.id, video_asset.filename, video_bytes,
    )
    video_asset.size_bytes = len(video_bytes)
    video_asset.sha256 = hashlib.sha256(video_bytes).hexdigest()

    # Optional poster frame
    poster = _extract_video_poster_bytes(video_bytes)
    if poster is not None:
        poster_bytes, poster_mime = poster
        poster_asset = DesignerAsset(
            kind="image",
            label=f"{video_asset.label[:60]} poster",
            mime_type=poster_mime,
            filename=(video_asset.filename.rsplit(".", 1)[0] + "-poster.png"),
        )
        poster_asset.stored_name = save_asset_bytes(
            project.id, poster_asset.id, poster_asset.filename, poster_bytes,
        )
        poster_asset.size_bytes = len(poster_bytes)
        poster_asset.sha256 = hashlib.sha256(poster_bytes).hexdigest()
        project.assets.append(poster_asset)
        video_asset.poster_asset_id = poster_asset.id

    project.assets.append(video_asset)
    return video_asset


def animate_image_bytes(
    prompt: str,
    *,
    project,
    image_source: str = "last",
    duration_seconds: int = 6,
    aspect_ratio: str | None = None,
    resolution: str = "720p",
    tone_override: str | None = None,
):
    """Convenience wrapper for image-to-video — delegates to ``generate_video_bytes``."""
    return generate_video_bytes(
        prompt,
        project=project,
        duration_seconds=duration_seconds,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        image_source=image_source,
        tone_override=tone_override,
    )


# ── AI Copy Refinement ───────────────────────────────────────────────────────

_REFINE_ACTIONS = {
    "shorten": "Make this text significantly shorter and more concise while keeping the key message.",
    "expand": "Expand this text with more detail, examples, or supporting points.",
    "professional": "Rewrite this text in a formal, professional tone.",
    "casual": "Rewrite this text in a friendly, casual, conversational tone.",
    "persuasive": "Rewrite this text to be more persuasive and compelling.",
    "simplify": "Simplify this text so a general audience can easily understand it.",
    "bullets": "Convert this text into a clean bulleted list.",
    "paragraph": "Convert this text into flowing paragraph form.",
}


def refine_text(
    text: str,
    action: str,
    custom_instruction: str = "",
) -> str:
    """Refine text using the configured LLM.

    Parameters
    ----------
    text : str
        The original text to refine.
    action : str
        One of the predefined actions or 'custom'.
    custom_instruction : str
        Used when action == 'custom'.

    Returns
    -------
    str
        The refined text, or the original on failure.
    """
    if action == "custom" and custom_instruction:
        instruction = custom_instruction
    else:
        instruction = _REFINE_ACTIONS.get(action, _REFINE_ACTIONS["professional"])

    system_prompt = (
        "You are a copywriting assistant for a visual design tool. "
        "Refine the given text according to the instruction. "
        "Return ONLY the refined text — no explanations, no quotes, no markdown formatting. "
        "Preserve the approximate structure (headings stay headings, lists stay lists) "
        "unless the instruction explicitly asks to change it."
    )
    user_prompt = f"INSTRUCTION: {instruction}\n\nTEXT TO REFINE:\n{text}"

    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        llm = _designer_text_llm()
        resp = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
        refined = resp.content.strip() if resp and resp.content else text
        return refined if refined else text
    except Exception:
        logger.exception("AI copy refinement failed")
        return text


def refine_text_in_html(
    page_html: str,
    tag: str,
    old_text: str,
    action: str,
    custom_instruction: str = "",
) -> tuple[str, str]:
    """Refine a text element in-place within page HTML.

    Returns (new_html, refined_text).
    """
    refined = refine_text(old_text, action, custom_instruction)
    if refined == old_text:
        return page_html, old_text

    from row_bot.designer.interaction import patch_html_text
    new_html = patch_html_text(page_html, "", tag, old_text, refined)
    return new_html, refined


def generate_speaker_notes(
    page_title: str,
    page_summary: dict,
    existing_notes: str = "",
) -> str:
    """Generate concise presenter notes for a single slide."""

    system_prompt = (
        "You write speaker notes for a presentation slide. "
        "Return plain text only. No markdown bullets, no XML, no commentary. "
        "Write concise notes a presenter can read while speaking: the core message, "
        "the important supporting detail, and a short transition if it is obvious."
    )
    user_prompt = (
        f"SLIDE TITLE: {page_title or 'Untitled'}\n\n"
        f"SLIDE SUMMARY JSON:\n{json.dumps(page_summary, indent=2)}\n\n"
        f"EXISTING NOTES:\n{existing_notes or '(none)'}\n\n"
        "Write 3-6 short speaker-note lines for this slide."
    )

    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = _designer_text_llm()
        resp = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])
        notes = resp.content.strip() if resp and resp.content else ""
        return notes or existing_notes
    except Exception:
        logger.exception("Speaker note generation failed")
        return existing_notes or ""


# ── Data-Viz Chart Embedding ─────────────────────────────────────────────────

def build_chart_png(
    chart_type: str,
    data_csv: str,
    title: str = "",
    colors: Optional[list[str]] = None,
    width: int = 800,
    height: int = 500,
) -> bytes:
    """Build a Plotly chart from CSV data and return PNG bytes.

    Parameters
    ----------
    chart_type : str
        Any supported chart type (bar, line, pie, scatter, etc.).
    data_csv : str
        Inline CSV data (header row + data rows).
    title : str
        Chart title.
    colors : list[str] | None
        Custom color sequence (brand colors).
    width, height : int
        Output image dimensions in pixels.

    Returns
    -------
    bytes
        PNG image bytes.
    """
    import pandas as pd

    df = pd.read_csv(io.StringIO(data_csv))
    if df.empty:
        raise ValueError("CSV data is empty.")

    from row_bot.tools.chart_tool import _build_figure
    fig = _build_figure(df, chart_type, x=None, y=None, color=None, title=title)

    if colors:
        fig.update_layout(colorway=colors)

    # Use kaleido for static export
    png_bytes = fig.to_image(format="png", width=width, height=height, scale=2)
    return png_bytes


def build_chart_interactive_html(
    chart_type: str,
    data_csv: str,
    title: str = "",
    colors: Optional[list[str]] = None,
    width: int = 800,
    height: int = 500,
) -> str:
    """Build a Plotly chart and return an HTML <div> with inline Plotly.js.

    This produces an interactive chart for the live preview.
    """
    import pandas as pd
    import plotly.io as pio

    df = pd.read_csv(io.StringIO(data_csv))
    if df.empty:
        raise ValueError("CSV data is empty.")

    from row_bot.tools.chart_tool import _build_figure
    fig = _build_figure(df, chart_type, x=None, y=None, color=None, title=title)

    if colors:
        fig.update_layout(colorway=colors)

    fig.update_layout(width=width, height=height)

    chart_id = f"chart-{uuid.uuid4().hex[:8]}"
    html = pio.to_html(fig, full_html=False, include_plotlyjs="cdn", div_id=chart_id)
    return html


def chart_to_img_tag(
    chart_type: str,
    data_csv: str,
    title: str = "",
    colors: Optional[list[str]] = None,
    width: int = 800,
    height: int = 500,
) -> str:
    """Build a chart and return a static <img> tag with base64 PNG."""
    png_bytes = build_chart_png(chart_type, data_csv, title, colors, width, height)
    b64 = base64.b64encode(png_bytes).decode()
    safe_title = _escape_attr(title or chart_type)
    return (
        f'<img src="data:image/png;base64,{b64}" '
        f'alt="{safe_title}" '
        f'style="width:{width}px; max-width:100%; height:auto; display:block; margin:16px auto;" />'
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _escape_attr(s: str) -> str:
    """Escape a string for use in an HTML attribute."""
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
