"""Designer — interactive iframe preview engine with aspect-ratio container, zoom, and JS bridge."""

from __future__ import annotations

import base64
import json
import logging

from nicegui import ui

from designer.render_assets import resolve_project_image_sources, resolve_project_media_sources
from designer.storage import load_asset_bytes
from designer.state import DesignerProject, BrandConfig
from designer.interaction import inject_bridge_js

logger = logging.getLogger(__name__)

# Zoom levels
ZOOM_LEVELS = {"Fit": None, "50%": 0.5, "75%": 0.75, "100%": 1.0}


def _build_brand_css(brand: BrandConfig) -> str:
    """Build the <style> block with :root CSS variables and @font-face for a brand."""
    from designer.fonts import get_all_fonts_css, get_fallback_stack
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
        f'<div data-thoth-brand-logo="auto" aria-hidden="true" '
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

    resolved_html = resolve_project_media_sources(page_html, project)
    return inject_brand_variables(resolved_html, project.brand, project=project, page_index=page_index)


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
    from designer.runtime import build_routes_payload, inject_runtime

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
            f'<section data-thoth-route-host="1" '
            f'data-thoth-route="{rid}" '
            f'data-thoth-route-index="{idx}" '
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
    from designer.fonts import get_fallback_stack
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


def build_preview(project: DesignerProject, *,
                   on_element_click=None, on_text_edit=None,
                   on_undo_shortcut=None, on_redo_shortcut=None,
                   on_navigate=None) -> dict:
    """Build the preview panel returning a dict with refresh_fn and zoom control.

    Returns ``{"refresh": callable, "container": ui.element}``.

    Parameters
    ----------
    on_element_click : callable, optional
        Called with element info dict when user clicks an element in the preview.
    on_text_edit : callable, optional
        Called with edit detail dict when user finishes inline text editing.
    on_undo_shortcut : callable, optional
        Called when the preview iframe forwards a designer undo shortcut.
    on_redo_shortcut : callable, optional
        Called when the preview iframe forwards a designer redo shortcut.
    on_navigate : callable, optional
        Called when the page structure or active page changes (e.g. agent added
        or deleted a page).  The page navigator uses this to re-render.
    """
    _last_html: list[str | None] = [None]
    _last_structure: list[tuple[int, int, int, int]] = [
        (len(project.pages), project.active_page,
         project.canvas_width, project.canvas_height)
    ]
    # Fingerprint of every page's title+html so we can detect agent edits
    # that mutate a page in place (no structural change) and still rebuild
    # the navigator thumbnails. Uses hash() per-page to stay cheap; a tuple
    # of ints is trivial to compare on each poll tick.
    def _content_fingerprint() -> tuple[int, ...]:
        return tuple(hash((p.title, p.html)) for p in project.pages)
    _last_content: list[tuple[int, ...]] = [_content_fingerprint()]
    _iframe_id = f"designer-preview-{project.id[:8]}"
    _zoom_value: list[str] = ["Fit"]
    # "authoring" = the designer-side click/edit bridge that captures clicks
    # to drive the hotspot recorder and inline text editor. This is ON by
    # default when the caller registers element_click/text_edit handlers.
    _authoring_enabled: bool = on_element_click is not None or on_text_edit is not None
    # Preview mode toggle — when True we suppress the authoring bridge so
    # clicks flow through to the runtime bridge (navigate/toggle_state/etc.)
    # and the user can test interactive prototypes without leaving the editor.
    # Exposed via the returned dict so the toolbar can flip it.
    _preview_mode: list[bool] = [False]
    # Scripts must be allowed for any interactive-mode project so the runtime
    # bridge can run, independent of whether authoring is active.
    _scripts_allowed: bool = _authoring_enabled or (
        getattr(project, "mode", "deck") in INTERACTIVE_MODES
    )

    with ui.column().classes("w-full h-full").style("position: relative;") as container:
        # Zoom controls bar
        with ui.row().classes("w-full items-center justify-end gap-2").style(
            "padding: 4px 8px; background: rgba(0,0,0,0.3); border-radius: 8px 8px 0 0;"
        ):
            ui.label("Zoom:").classes("text-xs text-grey-5")
            for label in ZOOM_LEVELS:
                def _set_zoom(lbl=label):
                    _zoom_value[0] = lbl
                    _apply_zoom()
                ui.button(label, on_click=_set_zoom).props(
                    "flat dense no-caps size=xs"
                ).style("font-size: 0.7rem;")

        # Aspect-ratio container
        ratio = project.canvas_width / project.canvas_height
        _sandbox = "allow-same-origin allow-scripts" if _scripts_allowed else "allow-same-origin"
        _chrome = get_preview_chrome(project)
        with ui.element("div").classes("w-full flex-grow").style(
            "display: flex; align-items: center; justify-content: center;"
            "overflow: hidden; background: #111;"
        ) as _ratio_wrap:
            # Sized wrapper — JS will set width/height to the scaled dims
            _wrapper_id = f"designer-wrapper-{project.id[:8]}"
            _iframe_markup = (
                f'<iframe id="{_iframe_id}" '
                f'sandbox="{_sandbox}" '
                f'style="border: none; background: white; '
                f'width: {project.canvas_width}px; height: {project.canvas_height}px; '
                f'transform-origin: top left; position: absolute; top: 0; left: 0;" '
                f'></iframe>'
            )
            if _chrome.get("kind") == "phone":
                _inner_html = (
                    f'<div style="{_chrome["bezel_style"]}">'
                    f'<div style="{_chrome["notch_style"]}"></div>'
                    f'<div id="{_wrapper_id}" style="{_chrome["screen_style"]}'
                    "overflow: hidden;\">"
                    f"{_iframe_markup}"
                    "</div>"
                    "</div>"
                )
            else:
                _inner_html = (
                    f'<div id="{_wrapper_id}" style="position: relative; overflow: hidden;">'
                    f"{_iframe_markup}"
                    "</div>"
                )
            ui.html(_inner_html, sanitize=False)

    def _apply_zoom():
        zoom = ZOOM_LEVELS.get(_zoom_value[0])
        if zoom is None:
            # Fit: scale iframe and size wrapper to match
            js = f'''
                (function() {{
                    var iframe = document.getElementById("{_iframe_id}");
                    var wrapper = document.getElementById("{_wrapper_id}");
                    if (!iframe || !wrapper) return;
                    var container = wrapper.closest(".flex-grow");
                    if (!container) return;
                    var pw = container.clientWidth;
                    var ph = container.clientHeight;
                    var scale = Math.min(pw / {project.canvas_width}, ph / {project.canvas_height});
                    iframe.style.transform = "scale(" + scale + ")";
                    wrapper.style.width = Math.ceil({project.canvas_width} * scale) + "px";
                    wrapper.style.height = Math.ceil({project.canvas_height} * scale) + "px";
                }})();
            '''
        else:
            js = f'''
                (function() {{
                    var iframe = document.getElementById("{_iframe_id}");
                    var wrapper = document.getElementById("{_wrapper_id}");
                    if (!iframe) return;
                    iframe.style.transform = "scale({zoom})";
                    if (wrapper) {{
                        wrapper.style.width = Math.ceil({project.canvas_width} * {zoom}) + "px";
                        wrapper.style.height = Math.ceil({project.canvas_height} * {zoom}) + "px";
                    }}
                }})();
            '''
        ui.run_javascript(js)

    def _refresh(force: bool = False):
        """Refresh the preview iframe with current page HTML.

        When ``force`` is true, bypass the HTML cache guard and hard-reload the
        iframe srcdoc. This is used after undo/redo style state restores where
        the preview DOM may have diverged from the stored page HTML.
        """
        if not project.pages:
            return
        # Detect structural changes (page added/deleted/navigated/resized)
        cur_structure = (len(project.pages), project.active_page,
                         project.canvas_width, project.canvas_height)
        structure_changed = cur_structure != _last_structure[0]
        dims_changed = (cur_structure[2] != _last_structure[0][2] or
                        cur_structure[3] != _last_structure[0][3])
        # Detect per-page content changes (agent edits a slide in place)
        cur_content = _content_fingerprint()
        content_changed = cur_content != _last_content[0]
        if structure_changed:
            _last_structure[0] = cur_structure
        if content_changed:
            _last_content[0] = cur_content
        # Nav bar must rebuild whenever structure OR page content changed
        # so thumbnails reflect the latest HTML. Without the content check
        # an agent-driven designer_update_page leaves the thumbnail stale
        # until the user clicks the page tile.
        if (structure_changed or content_changed) and on_navigate:
            on_navigate()
        # If canvas dimensions changed, resize the iframe element
        if dims_changed:
            cw, ch = project.canvas_width, project.canvas_height
            ui.run_javascript(f'''
                (function() {{
                    var iframe = document.getElementById("{_iframe_id}");
                    if (iframe) {{
                        iframe.style.width = "{cw}px";
                        iframe.style.height = "{ch}px";
                    }}
                }})();
            ''')
        idx = max(0, min(project.active_page, len(project.pages) - 1))
        page = project.pages[idx]
        _is_interactive_mode = (
            getattr(project, "mode", "deck") in INTERACTIVE_MODES
        )
        if _is_interactive_mode:
            route_ids = _ensure_page_route_ids(project)
            active_route = route_ids[idx] if idx < len(route_ids) else None
            html = render_multi_route_html(project, active_route_id=active_route)
        else:
            html = render_page_html(project, page.html, page_index=idx)
        # Inject the authoring (click/edit capture) bridge only when the
        # caller wired authoring handlers AND the user is NOT in preview
        # mode. Preview mode lets clicks reach the runtime bridge so
        # interactive prototypes can be exercised from the editor.
        if _authoring_enabled and not _preview_mode[0]:
            html = inject_bridge_js(html)
        if not force and html == _last_html[0] and not structure_changed:
            return
        safe_html = json.dumps(html)
        if force:
            js = f'''
                (function() {{
                    var iframe = document.getElementById("{_iframe_id}");
                    if (!iframe) return;
                    var replacement = iframe.cloneNode(false);
                    iframe.replaceWith(replacement);
                    replacement.srcdoc = {safe_html};
                }})();
            '''
        else:
            js = f'''
                (function() {{
                    var iframe = document.getElementById("{_iframe_id}");
                    if (iframe) iframe.srcdoc = {safe_html};
                }})();
            '''
        ui.run_javascript(js)
        _last_html[0] = html
        # Re-apply zoom after content change
        _apply_zoom()

    # Initial render + poll timer
    _refresh()

    def _safe_refresh():
        try:
            _refresh()
        except RuntimeError:
            try:
                _refresh_timer.deactivate()
            except Exception:
                pass
            pass  # parent slot deleted — page navigated away
    _refresh_timer = ui.timer(0.5, _safe_refresh)
    try:
        ui.context.client.on_disconnect(lambda: _refresh_timer.deactivate())
    except Exception:
        pass

    # Register parent-side message listener for interactive bridge
    if _authoring_enabled:
        _setup_message_listener(
            on_element_click=on_element_click,
            on_text_edit=on_text_edit,
            on_undo_shortcut=on_undo_shortcut,
            on_redo_shortcut=on_redo_shortcut,
        )

    def _set_preview_mode(enabled: bool) -> None:
        """Toggle preview mode (suppresses the authoring click/edit bridge).

        Forces a full iframe reload so the sandboxed document reflects the
        new bridge-injection state.
        """
        new_val = bool(enabled)
        if _preview_mode[0] == new_val:
            return
        _preview_mode[0] = new_val
        try:
            _refresh(force=True)
        except Exception:
            pass

    return {
        "refresh": _refresh,
        "force_refresh": lambda: _refresh(force=True),
        "container": container,
        "set_preview_mode": _set_preview_mode,
        "is_preview_mode": lambda: _preview_mode[0],
        "supports_preview_mode": _authoring_enabled,
    }


def _setup_message_listener(
    *,
    on_element_click=None,
    on_text_edit=None,
    on_undo_shortcut=None,
    on_redo_shortcut=None,
):
    """Register a window.message listener that forwards iframe events to Python."""
    # Use a hidden NiceGUI element to receive events from JS
    bridge = ui.element("div").style("display:none;")

    def _handle_bridge_event(e):
        data = e.args or {}
        msg_type = data.get("msgType", "")
        detail = data.get("detail", {})
        if msg_type == "element-click" and on_element_click:
            on_element_click(detail)
        elif msg_type == "text-edit" and on_text_edit:
            on_text_edit(detail)
        elif msg_type == "designer-undo-shortcut" and on_undo_shortcut:
            on_undo_shortcut()
        elif msg_type == "designer-redo-shortcut" and on_redo_shortcut:
            on_redo_shortcut()

    bridge.on("bridge_msg", _handle_bridge_event)

    # Register JS listener that forwards postMessage events to the bridge element
    js = f"""
    (function() {{
        window.__thothDesignerBridgeId = {bridge.id};
        if (window.__thothDesignerListener) return;
        window.__thothDesignerListener = true;

        window.addEventListener('message', function(e) {{
            var data = e.data;
            if (!data || !data.type) return;
            if (data.type === 'element-click' || data.type === 'text-edit' ||
                data.type === 'edit-start' || data.type === 'edit-cancel' ||
                data.type === 'designer-undo-shortcut' || data.type === 'designer-redo-shortcut') {{
                var bridge = getElement(window.__thothDesignerBridgeId);
                if (!bridge) return;
                var bridgeEvent = new Event('bridge_msg', {{ bubbles: true }});
                bridgeEvent.msgType = data.type;
                bridgeEvent.detail = data.detail || {{}};
                bridge.dispatchEvent(bridgeEvent);
            }}
        }});
    }})();
    """
    ui.run_javascript(js)
