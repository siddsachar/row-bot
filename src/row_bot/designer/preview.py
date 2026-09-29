"""Designer page rendering: brand variables, logo, isolation and multi-route HTML."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from row_bot.designer.render_assets import resolve_project_media_sources
from row_bot.designer.storage import load_asset_bytes
from row_bot.designer.state import DesignerProject, BrandConfig

logger = logging.getLogger(__name__)

def _build_brand_css(brand: BrandConfig) -> str:
    """Build the <style> block with :root CSS variables and @font-face for a brand."""
    from row_bot.designer.fonts import get_all_fonts_css, get_fallback_stack
    families = [f for f in dict.fromkeys([brand.heading_font, brand.body_font]) if f]
    font_css = get_all_fonts_css(families)
    h_fallback = get_fallback_stack(brand.heading_font or "Inter")
    b_fallback = get_fallback_stack(brand.body_font or "Inter")
    return (
        f"<style>\n{font_css}\n"
        ":root {"
        f" --primary: {brand.primary_color};"
        f" --secondary: {brand.secondary_color};"
        f" --accent: {brand.accent_color};"
        f" --bg: {brand.bg_color};"
        f" --text: {brand.text_color};"
        f" --heading-font: '{brand.heading_font}', {h_fallback};"
        f" --body-font: '{brand.body_font}', {b_fallback};"
        " }\n</style>"
    )


def branded_blank_html(project: DesignerProject, title: str) -> str:
    """A minimal page in the design's brand, for a new blank page or screen."""
    brand = project.brand
    w, h = project.canvas_width, project.canvas_height
    brand_css = _build_brand_css(brand) if brand else ""
    return (
        f"<!DOCTYPE html><html><head>{brand_css}"
        f"<style>html,body{{margin:0;width:{w}px;height:{h}px;overflow:hidden;"
        f"background:var(--bg,#0F172A);color:var(--text,#F8FAFC);"
        f"font-family:var(--body-font,sans-serif);}}"
        f"h1,h2,h3,h4{{font-family:var(--heading-font,sans-serif);}}</style>"
        f"</head><body>"
        f"<div style=\"display:flex;align-items:center;justify-content:center;"
        f"height:100%;\">"
        f"<h1 style=\"font-size:2.5rem;opacity:0.3;\">{_escape_attr(title)}</h1>"
        f"</div></body></html>"
    )


def _escape_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _brand_has_logo(brand: BrandConfig) -> bool:
    return bool((brand.logo_asset_id or "").strip() or brand.logo_b64)


def _logo_data_uri(project: DesignerProject | None, brand: BrandConfig) -> str:
    if project is not None and brand.logo_asset_id:
        asset = next((item for item in project.assets if item.id == brand.logo_asset_id), None)
        if asset is not None and asset.stored_name:
            data = load_asset_bytes(project.id, asset.stored_name)
            if data:
                mime = (
                    asset.mime_type
                    or brand.logo_mime_type
                    or "image/png"
                ).strip() or "image/png"
                encoded = base64.b64encode(data).decode("ascii")
                return f"data:{mime};base64,{encoded}"
    if brand.logo_b64:
        mime = (brand.logo_mime_type or "image/png").strip() or "image/png"
        return f"data:{mime};base64,{brand.logo_b64}"
    return ""


def _logo_should_render_on_page(brand: BrandConfig, page_index: int | None) -> bool:
    if not _brand_has_logo(brand):
        return False
    scope = (brand.logo_scope or "all").lower()
    idx = 0 if page_index is None else page_index
    if scope == "first":
        return idx == 0
    return True


def _logo_corner_style(brand: BrandConfig) -> str:
    padding = max(int(getattr(brand, "logo_padding", 24) or 24), 0)
    position = (brand.logo_position or "top_right").lower()
    corners = {
        "top_left": f"top:{padding}px;left:{padding}px;",
        "top_right": f"top:{padding}px;right:{padding}px;",
        "bottom_left": f"bottom:{padding}px;left:{padding}px;",
        "bottom_right": f"bottom:{padding}px;right:{padding}px;",
    }
    return corners.get(position, corners["top_right"])


def _build_logo_img(project: DesignerProject | None, brand: BrandConfig, *, max_width: str = "100%") -> str:
    max_height = max(int(getattr(brand, "logo_max_height", 72) or 72), 24)
    alt = _escape_attr(brand.logo_filename or "Brand logo")
    logo_uri = _logo_data_uri(project, brand)
    if not logo_uri:
        return ""
    return (
        f'<img src="{logo_uri}" '
        f'alt="{alt}" '
        f'style="display:block;width:auto;height:auto;max-height:{max_height}px;'
        f'max-width:{max_width};object-fit:contain;" />'
    )


def _build_logo_overlay(project: DesignerProject | None, brand: BrandConfig) -> str:
    max_width = f"calc(100% - {max(int(getattr(brand, 'logo_padding', 24) or 24), 0) * 2}px)"
    image_html = _build_logo_img(project, brand, max_width=max_width)
    if not image_html:
        return ""
    return (
        f'<div data-row-bot-brand-logo="auto" aria-hidden="true" '
        f'style="position:absolute;{_logo_corner_style(brand)}'
        f'z-index:2147483000;pointer-events:none;">'
        f'{image_html}'
        f'</div>'
    )


def inject_brand_variables(
    html: str,
    brand: BrandConfig | None,
    *,
    project: DesignerProject | None = None,
    page_index: int | None = None,
) -> str:
    """Inject brand CSS at render time.

    Always appends at the END of <head> (before </head>) so that CSS cascade
    makes brand variables win over any earlier :root in template styles.
    Also replaces ``<!-- BRAND_LOGO -->`` markers with the actual logo ``<img>``.
    This is render-time only — safe to call repeatedly, never stored.
    """
    if not brand:
        return html
    css = _build_brand_css(brand)
    if "</head>" in html:
        html = html.replace("</head>", f"{css}</head>", 1)
    elif "<head>" in html:
        html = html.replace("<head>", f"<head>{css}", 1)
    else:
        html = css + html

    has_logo_placeholder = "<!-- BRAND_LOGO" in html
    if _brand_has_logo(brand) and has_logo_placeholder:
        logo_html = _build_logo_img(project, brand)
        if logo_html:
            html = html.replace("<!-- BRAND_LOGO -->", logo_html)

    if (
        _brand_has_logo(brand)
        and (brand.logo_mode or "auto").lower() != "manual"
        and not has_logo_placeholder
        and _logo_should_render_on_page(brand, page_index)
    ):
        overlay = _build_logo_overlay(project, brand)
        if overlay and "</body>" in html:
            html = html.replace("</body>", f"{overlay}</body>", 1)
        elif overlay:
            html += overlay
    return html


def render_page_html(
    project: DesignerProject,
    page_html: str,
    *,
    page_index: int | None = None,
) -> str:
    """Render one page with resolved image references and brand variables applied."""

    from row_bot.designer.html_ops import sanitize_agent_html

    resolved_html = resolve_project_media_sources(sanitize_agent_html(page_html), project)
    return sanitize_agent_html(inject_brand_variables(
        resolved_html, project.brand, project=project, page_index=page_index,
    ))


def isolate_preview_html(html: str, *, scripts: bool = False,
                         brand: BrandConfig | None = None, strict_fonts: bool = False) -> str:
    """Apply a network-free document policy inside an opaque sandboxed frame.

    Call after trusted bridge injection. This is an extra restriction, never a
    replacement for the host's sandbox attribute and validated message source.
    """
    from bs4 import BeautifulSoup
    import re

    def offline_url(match: re.Match[str]) -> str:
        value = match.group(1).strip().strip("'\"")
        return match.group(0) if value.startswith("data:") else "none"

    soup = BeautifulSoup(html, "html.parser")
    # Frames never fetch application routes or remote assets. Resolve local
    # brand fonts to embedded bytes and omit unavailable CSS URLs up front,
    # avoiding surprise requests and repeated CSP console errors.
    for link in list(soup.find_all("link")):
        link.decompose()
    for style in soup.find_all("style"):
        css = style.get_text()
        css = re.sub(r"@import\s+[^;]+;", "", css, flags=re.IGNORECASE)
        css = re.sub(r"@font-face\s*\{[^}]*\}", "", css, flags=re.IGNORECASE)
        css = re.sub(r"url\((.*?)\)", offline_url, css, flags=re.IGNORECASE | re.DOTALL)
        style.string = css
    if brand:
        from row_bot.designer.fonts import get_font_css_embedded, is_font_available_offline

        fonts = []
        for family in dict.fromkeys([brand.heading_font, brand.body_font]):
            if strict_fonts:
                fonts.append(get_font_css_embedded(family, strict=True))
            elif family and re.fullmatch(r"[A-Za-z0-9 _-]{1,100}", family) and is_font_available_offline(family):
                fonts.append(get_font_css_embedded(family))
        if fonts:
            style = soup.new_tag("style")
            style.string = "\n".join(fonts)
            (soup.head or soup).append(style)
    for tag in soup.find_all(True):
        for attr in ("src", "poster", "srcset"):
            value = tag.get(attr)
            if value and (attr == "srcset" or not str(value).startswith("data:")):
                del tag.attrs[attr]
        if isinstance(tag.get("style"), str):
            tag["style"] = re.sub(r"url\((.*?)\)", offline_url, tag["style"],
                                  flags=re.IGNORECASE | re.DOTALL)
    for meta in list(soup.find_all("meta")):
        if meta.get("http-equiv"):
            meta.decompose()
    policy = ("default-src 'none'; img-src data:; media-src data:; font-src data:; "
              "style-src 'unsafe-inline'; connect-src 'none'; frame-src 'none'; "
              "object-src 'none'; base-uri 'none'; form-action 'none'; ")
    policy += "script-src 'unsafe-inline'" if scripts else "script-src 'none'"
    meta = soup.new_tag("meta", attrs={"http-equiv": "Content-Security-Policy", "content": policy})
    if soup.head:
        soup.head.insert(0, meta)
    else:
        soup.insert(0, meta)
    # Suppress navigation within the generated frame as well as top navigation.
    for anchor in soup.find_all("a"):
        href = anchor.get("href", "")
        if not isinstance(href, str) or not href.startswith("#"):
            anchor.attrs.pop("href", None)
        anchor.attrs.pop("target", None)
    return str(soup)


def preview_fingerprint(project: DesignerProject, *, page_index: int | None = None,
                        preview_mode: bool = False) -> tuple[Any, ...]:
    """Detect all render inputs before constructing HTML, including unsaved edits."""
    from row_bot.designer import storage
    from row_bot.thread_cleanup import resolve_managed_path

    media_versions = []
    for root, entries in ((storage.ASSETS_DIR, project.assets),
                          (storage.REFERENCES_DIR, project.references)):
        for item in entries:
            if not item.stored_name:
                continue
            path = resolve_managed_path(resolve_managed_path(root, project.id), item.stored_name)
            try:
                stat = path.stat()
                media_versions.append((item.id, stat.st_mtime_ns, stat.st_size))
            except FileNotFoundError:
                media_versions.append((item.id, None, None))
    return (project.id, project.updated_at, project.active_page if page_index is None else page_index,
            project.canvas_width, project.canvas_height, project.aspect_ratio, project.mode,
            preview_mode, project.runtime_version,
            tuple((p.route_id, p.title, p.html, p.kind, tuple(p.states)) for p in project.pages),
            json.dumps(project.brand.to_dict() if project.brand else None, sort_keys=True),
            json.dumps([a.to_dict() for a in project.assets], sort_keys=True),
            json.dumps([r.to_dict() for r in project.references], sort_keys=True),
            json.dumps([i.to_dict() for i in project.interactions], sort_keys=True),
            tuple(media_versions))


# ── Interactive (landing / app_mockup / storyboard) multi-route render ──
INTERACTIVE_MODES = {"landing", "app_mockup", "storyboard"}

_BODY_OPEN_RE = _re_body_open = __import__("re").compile(r"<body[^>]*>", __import__("re").IGNORECASE)
_BODY_CLOSE_RE = __import__("re").compile(r"</body>", __import__("re").IGNORECASE)
# Per-page <style>...</style> and <link rel="stylesheet" ...> blocks. The
# multi-route preview merges these from every page so sections rendered
# from pages 1..N keep their CSS (otherwise they'd inherit only page 0's
# head and look unstyled — see app_mockup preview regression).
_HEAD_STYLE_RE = __import__("re").compile(
    r"<style\b[^>]*>.*?</style>", __import__("re").IGNORECASE | __import__("re").DOTALL,
)
_HEAD_LINK_CSS_RE = __import__("re").compile(
    r"<link\b[^>]*\brel\s*=\s*['\"]?stylesheet['\"]?[^>]*>",
    __import__("re").IGNORECASE,
)


# ── Phase 2.2.I — phone-frame chrome for app_mockup preview ───────────

# Visual thickness of the phone bezel, in CSS px. Applied evenly around
# the iframe so the content canvas retains its original aspect ratio.
PHONE_BEZEL_PADDING_PX = 14
PHONE_BEZEL_RADIUS_PX = 44
PHONE_NOTCH_WIDTH_PX = 120
PHONE_NOTCH_HEIGHT_PX = 22


def get_preview_chrome(project: DesignerProject) -> dict:
    """Return chrome metadata used by the preview renderer.

    Interactive ``app_mockup`` projects get a phone bezel + notch. All
    other modes return ``{"kind": "none"}`` so existing preview rendering
    is untouched.
    """

    mode = getattr(project, "mode", "deck") or "deck"
    if mode != "app_mockup":
        return {"kind": "none"}
    return {
        "kind": "phone",
        "bezel_padding_px": PHONE_BEZEL_PADDING_PX,
        "bezel_radius_px": PHONE_BEZEL_RADIUS_PX,
        "notch_width_px": PHONE_NOTCH_WIDTH_PX,
        "notch_height_px": PHONE_NOTCH_HEIGHT_PX,
        "bezel_style": (
            f"padding: {PHONE_BEZEL_PADDING_PX}px;"
            f"border-radius: {PHONE_BEZEL_RADIUS_PX}px;"
            "background: #111;"
            "box-shadow: 0 0 0 2px #333, 0 12px 36px rgba(0,0,0,0.5);"
            "position: relative; display: inline-block;"
        ),
        "screen_style": (
            "overflow: hidden;"
            f"border-radius: {max(0, PHONE_BEZEL_RADIUS_PX - PHONE_BEZEL_PADDING_PX)}px;"
            "position: relative; background: #000;"
        ),
        "notch_style": (
            "position: absolute;"
            f"top: {max(2, PHONE_BEZEL_PADDING_PX // 2)}px;"
            "left: 50%; transform: translateX(-50%);"
            f"width: {PHONE_NOTCH_WIDTH_PX}px;"
            f"height: {PHONE_NOTCH_HEIGHT_PX}px;"
            "background: #000; border-radius: 14px;"
            "z-index: 3; pointer-events: none;"
        ),
    }


def _slugify_route(value: str, fallback: str) -> str:
    import re as _re
    slug = _re.sub(r"[^a-zA-Z0-9]+", "-", (value or "")).strip("-").lower()
    return slug or fallback


def _ensure_page_route_ids(project: DesignerProject) -> list[str]:
    """Return the list of route_ids for pages, synthesizing where missing."""
    seen: set[str] = set()
    out: list[str] = []
    for idx, page in enumerate(project.pages):
        rid = (getattr(page, "route_id", "") or "").strip()
        if not rid:
            rid = _slugify_route(page.title, f"page-{idx + 1}")
        base = rid
        dedup = 2
        while rid in seen:
            rid = f"{base}-{dedup}"
            dedup += 1
        seen.add(rid)
        out.append(rid)
    return out


def _extract_body_inner(html: str) -> tuple[str, str]:
    """Split rendered page HTML into ``(head_block, body_inner)``.

    ``head_block`` is the whole document up through the opening ``<body…>`` tag
    (used to seed the multi-route shell).  ``body_inner`` is the inner HTML of
    ``<body>`` with outer ``<body>``/``</body>`` stripped.
    """
    m_open = _BODY_OPEN_RE.search(html)
    m_close = _BODY_CLOSE_RE.search(html)
    if not m_open or not m_close:
        return "", html
    head_block = html[: m_open.end()]
    body_inner = html[m_open.end(): m_close.start()]
    return head_block, body_inner


def render_multi_route_html(
    project: DesignerProject,
    *,
    active_route_id: str | None = None,
) -> str:
    """Render every page into one HTML doc with route sections + runtime bridge.

    Used for preview and (after minor tweaks) publish in interactive modes.
    """
    from row_bot.designer.runtime import build_routes_payload, inject_runtime

    if not project.pages:
        return "<html><body></body></html>"

    route_ids = _ensure_page_route_ids(project)
    labels = {rid: project.pages[i].title for i, rid in enumerate(route_ids)}
    initial = active_route_id or ""
    if initial not in route_ids:
        idx0 = max(0, min(project.active_page, len(project.pages) - 1))
        initial = route_ids[idx0]

    # Use the first page's rendered head as the shell; route sections live in body.
    first_rendered = render_page_html(project, project.pages[0].html, page_index=0)
    head_block, first_body_inner = _extract_body_inner(first_rendered)
    if not head_block:
        head_block = "<!DOCTYPE html><html><head></head><body>"
        first_body_inner = first_rendered

    # Collect <style>/<link rel=stylesheet> blocks from every page beyond
    # the first, deduped, so per-page CSS survives the multi-route merge.
    # Without this, pages 1..N render with only page 0's head — which is
    # how the v3.17 app_mockup preview lost its branded styling on routes
    # other than the first one.
    extra_head_parts: list[str] = []
    seen_blocks: set[str] = set()
    for _i in range(1, len(project.pages)):
        try:
            _r = render_page_html(project, project.pages[_i].html, page_index=_i)
        except Exception:
            continue
        _h, _ = _extract_body_inner(_r)
        if not _h:
            continue
        for _m in _HEAD_STYLE_RE.findall(_h) + _HEAD_LINK_CSS_RE.findall(_h):
            _key = _m.strip()
            if _key and _key not in seen_blocks:
                seen_blocks.add(_key)
                extra_head_parts.append(_m)

    if extra_head_parts:
        _injected = "\n".join(extra_head_parts)
        # Insert just before </head> in the shell; fall back to prepending
        # to the body open tag if no </head> is present.
        if "</head>" in head_block.lower():
            # case-insensitive replace of the first </head>
            _idx = head_block.lower().find("</head>")
            head_block = head_block[:_idx] + _injected + head_block[_idx:]
        else:
            # Find the <body…> tag and inject before it
            _bm = _BODY_OPEN_RE.search(head_block)
            if _bm:
                head_block = head_block[: _bm.start()] + _injected + head_block[_bm.start():]
            else:
                head_block = head_block + _injected

    sections: list[str] = []
    for idx, page in enumerate(project.pages):
        rid = route_ids[idx]
        if idx == 0:
            inner = first_body_inner
        else:
            rendered = render_page_html(project, page.html, page_index=idx)
            _, inner = _extract_body_inner(rendered)
            if not inner:
                inner = rendered
        sections.append(
            f'<section data-row-bot-route-host="1" '
            f'data-row-bot-route="{_escape_attr(rid)}" '
            f'data-row-bot-route-index="{idx}" '
            f'aria-label="{_escape_attr(page.title)}">'
            f'{inner}'
            f'</section>'
        )

    assembled = f"{head_block}\n" + "\n".join(sections) + "\n</body></html>"
    payload = build_routes_payload(initial=initial, order=route_ids, labels=labels)
    return inject_runtime(assembled, routes_payload=payload)


import re as _re

# Matches a standalone brand <style> block (produced by _build_brand_css)
_BRAND_STYLE_RE = _re.compile(
    r'<style>\s*(?:/\*[^*]*\*/\s*)?(?:@font-face[^}]*}\s*)*:root\s*\{[^}]*--primary:[^}]*\}\s*</style>',
    _re.DOTALL,
)

# Matches :root { ... --primary: ... } within ANY context (e.g. inside
# a larger <style> block from templates that also has body/card rules)
_ROOT_VARS_RE = _re.compile(
    r':root\s*\{[^}]*--primary:[^}]*\}',
    _re.DOTALL,
)


def _build_root_block(brand: BrandConfig) -> str:
    """Build just the :root { ... } CSS declaration (no <style> wrapper)."""
    from row_bot.designer.fonts import get_fallback_stack
    h_fallback = get_fallback_stack(brand.heading_font)
    b_fallback = get_fallback_stack(brand.body_font)
    return (
        ":root {"
        f" --primary: {brand.primary_color};"
        f" --secondary: {brand.secondary_color};"
        f" --accent: {brand.accent_color};"
        f" --bg: {brand.bg_color};"
        f" --text: {brand.text_color};"
        f" --heading-font: '{brand.heading_font}', {h_fallback};"
        f" --body-font: '{brand.body_font}', {b_fallback};"
        " }"
    )


def update_brand_in_html(html: str, brand: BrandConfig) -> str:
    """Replace the existing brand CSS in stored page HTML.

    Tries three strategies in order:
    1. Replace a standalone brand <style> block (from _build_brand_css).
    2. Replace the :root { ... } declaration inside a larger <style> block
       (e.g. from templates that also contain body/card rules).
    3. Inject a full brand <style> block at the end of <head>.
    """
    css = _build_brand_css(brand)

    # Strategy 1: standalone brand <style> block
    new_html, n = _BRAND_STYLE_RE.subn(css, html, count=1)
    if n:
        return new_html

    # Strategy 2: :root block inside a larger <style> (template pages)
    root_block = _build_root_block(brand)
    new_html, n = _ROOT_VARS_RE.subn(root_block, html, count=1)
    if n:
        return new_html

    # Strategy 3: no existing block — inject at end of <head>
    if "</head>" in html:
        return html.replace("</head>", f"{css}</head>", 1)
    if "<head>" in html:
        return html.replace("<head>", f"<head>{css}", 1)
    return css + html
