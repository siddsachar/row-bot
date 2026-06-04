"""Shared static thumbnail helpers for Designer previews."""

from __future__ import annotations

import html

from nicegui import ui

from row_bot.designer.preview import inject_brand_variables, render_page_html
from row_bot.designer.state import BrandConfig, DesignerProject


def compute_thumbnail_dimensions(
    canvas_width: int,
    canvas_height: int,
    preview_height: int,
) -> tuple[int, float]:
    """Return the visible thumbnail width and scale for a fixed preview height."""

    if not canvas_height:
        return max(preview_height, 1), 0.08
    scale = preview_height / canvas_height
    return max(int(canvas_width * scale), 1), scale


def render_static_page_thumbnail(
    *,
    frame_id: str,
    page_html: str,
    brand: BrandConfig | None,
    project: DesignerProject | None = None,
    page_index: int = 0,
    canvas_width: int,
    canvas_height: int,
    preview_height: int,
    empty_label: str = "Untitled",
) -> None:
    """Render a static iframe thumbnail into the current UI context."""

    _, scale = compute_thumbnail_dimensions(canvas_width, canvas_height, preview_height)
    page_markup = (page_html or "").strip()
    if page_markup:
        rendered = (
            render_page_html(project, page_markup, page_index=page_index)
            if project is not None else
            inject_brand_variables(page_markup, brand, project=project, page_index=page_index)
        )
        ui.html(
            f'<iframe id="{frame_id}" '
            f'srcdoc="{html.escape(rendered, quote=True)}" '
            f'style="width:{canvas_width}px;height:{canvas_height}px;border:none;'
            f'transform:scale({scale:.6f});transform-origin:top left;'
            f'pointer-events:none;position:absolute;top:0;left:0;" '
            f'sandbox="allow-same-origin" tabindex="-1"></iframe>',
            sanitize=False,
        )
        return

    title_text = empty_label[:18] + ("…" if len(empty_label) > 18 else "")
    ui.label(title_text).classes("text-xs text-grey-5").style(
        "position:absolute;top:50%;left:50%;"
        "transform:translate(-50%,-50%);text-align:center;"
    )
    return