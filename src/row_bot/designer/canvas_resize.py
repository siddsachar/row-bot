"""Designer canvas resize helpers."""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup, Tag

from row_bot.designer.session import prepare_project_mutation
from row_bot.designer.state import ASPECT_RATIOS, CANVAS_PRESETS, DesignerProject
from row_bot.designer.storage import save_project

logger = logging.getLogger(__name__)

_FIT_SOURCE_WIDTH_ATTR = "data-row-bot-fit-source-width"
_FIT_SOURCE_HEIGHT_ATTR = "data-row-bot-fit-source-height"
_FIT_SCALE_ATTR = "data-row-bot-fit-scale"


def resolve_canvas_target(*, preset: str | None = None, aspect_ratio: str | None = None) -> tuple[str, str]:
    """Resolve a canvas target from a preset label or explicit aspect ratio."""

    if aspect_ratio:
        normalized = aspect_ratio.strip()
        if normalized not in ASPECT_RATIOS:
            raise ValueError(f"unknown aspect_ratio '{aspect_ratio}'.")
        return normalized, normalized

    if preset:
        lookup = preset.strip().lower()
        for preset_name, meta in CANVAS_PRESETS.items():
            if lookup in {
                preset_name.lower(),
                meta["label"].lower(),
                meta["aspect_ratio"].lower(),
            }:
                return meta["aspect_ratio"], meta["label"]
        preset_list = ", ".join(meta["label"] for meta in CANVAS_PRESETS.values())
        raise ValueError(f"unknown resize preset '{preset}'. Available presets: {preset_list}.")

    raise ValueError("provide either preset or aspect_ratio.")


def resize_project_canvas(
    project: DesignerProject,
    *,
    aspect_ratio: str,
    label: str | None = None,
    source: str = "resize",
) -> dict[str, object]:
    """Resize a project and auto-fit every page to the new canvas."""

    if aspect_ratio not in ASPECT_RATIOS:
        raise ValueError(f"unknown aspect_ratio '{aspect_ratio}'.")

    resolved_label = label or aspect_ratio
    previous_ratio = project.aspect_ratio
    previous_width = int(project.canvas_width or ASPECT_RATIOS.get(previous_ratio, (1920, 1080))[0])
    previous_height = int(project.canvas_height or ASPECT_RATIOS.get(previous_ratio, (1920, 1080))[1])
    target_width, target_height = ASPECT_RATIOS[aspect_ratio]

    if aspect_ratio == previous_ratio and (previous_width, previous_height) == (target_width, target_height):
        return {
            "aspect_ratio": aspect_ratio,
            "label": resolved_label,
            "width": target_width,
            "height": target_height,
            "fitted_pages": 0,
            "message": (
                f"Project already uses {resolved_label} ({target_width}×{target_height})."
            ),
        }

    prepare_project_mutation(project, f"resize_{previous_ratio}_to_{aspect_ratio}")
    project.aspect_ratio = aspect_ratio
    project.canvas_width = target_width
    project.canvas_height = target_height

    fitted_pages = 0
    for page in project.pages:
        fitted_html = fit_page_html_to_canvas(
            page.html,
            previous_width=previous_width,
            previous_height=previous_height,
            target_width=target_width,
            target_height=target_height,
        )
        if fitted_html != page.html:
            page.html = fitted_html
            page.thumbnail_b64 = None
            fitted_pages += 1

    project.manual_edits.append(
        f"Designer resized the canvas from {previous_ratio} to {resolved_label} "
        f"({target_width}×{target_height}) via {source}. Auto-fitted {fitted_pages} page(s)."
    )
    save_project(project)

    return {
        "aspect_ratio": aspect_ratio,
        "label": resolved_label,
        "width": target_width,
        "height": target_height,
        "fitted_pages": fitted_pages,
        "message": (
            f"Resized project to {resolved_label} ({aspect_ratio}) at {target_width}×{target_height}. "
            f"Auto-fitted {fitted_pages} page(s) to prevent cropping."
        ),
    }


def fit_page_html_to_canvas(
    page_html: str,
    *,
    previous_width: int,
    previous_height: int,
    target_width: int,
    target_height: int,
) -> str:
    """Apply a conservative whole-page fit transform for a resized canvas."""

    html = (page_html or "").strip()
    if not html:
        return page_html

    soup, html_tag, body = _ensure_document(html)
    source_width = _positive_int(body.get(_FIT_SOURCE_WIDTH_ATTR)) or max(previous_width, 1)
    source_height = _positive_int(body.get(_FIT_SOURCE_HEIGHT_ATTR)) or max(previous_height, 1)
    target_width = max(int(target_width or 0), 1)
    target_height = max(int(target_height or 0), 1)

    html_styles = _parse_inline_style(html_tag.get("style", ""))
    body_styles = _parse_inline_style(body.get("style", ""))

    html_styles.update({
        "width": f"{target_width}px",
        "height": f"{target_height}px",
        "margin": "0",
        "overflow": "hidden",
        "position": "relative",
        "background": body_styles.get("background") or body_styles.get("background-color") or "var(--bg, #0F172A)",
    })

    same_size = source_width == target_width and source_height == target_height
    if same_size:
        body.attrs.pop(_FIT_SOURCE_WIDTH_ATTR, None)
        body.attrs.pop(_FIT_SOURCE_HEIGHT_ATTR, None)
        body.attrs.pop(_FIT_SCALE_ATTR, None)
        body_styles.update({
            "width": f"{target_width}px",
            "height": f"{target_height}px",
            "margin": "0",
            "overflow": "hidden",
        })
        for prop in ("position", "left", "top", "transform", "transform-origin"):
            body_styles.pop(prop, None)
    else:
        scale = min(target_width / source_width, target_height / source_height)
        body[_FIT_SOURCE_WIDTH_ATTR] = str(source_width)
        body[_FIT_SOURCE_HEIGHT_ATTR] = str(source_height)
        body[_FIT_SCALE_ATTR] = f"{scale:.6f}"
        body_styles.update({
            "width": f"{source_width}px",
            "height": f"{source_height}px",
            "margin": "0",
            "overflow": "hidden",
            "position": "absolute",
            "left": "50%",
            "top": "50%",
            "transform": f"translate(-50%, -50%) scale({scale:.6f})",
            "transform-origin": "center center",
        })

    html_tag["style"] = _serialize_inline_style(html_styles)
    body["style"] = _serialize_inline_style(body_styles)
    return str(soup)


def _ensure_document(page_html: str) -> tuple[BeautifulSoup, Tag, Tag]:
    soup = BeautifulSoup(page_html, "html.parser")

    html_tag = soup.html
    if html_tag is None:
        html_tag = soup.new_tag("html")
        existing = [child for child in list(soup.contents)]
        for child in existing:
            if getattr(child, "name", None) == "html":
                continue
            html_tag.append(child.extract())
        soup.append(html_tag)

    body = soup.body
    if body is None:
        body = soup.new_tag("body")
        for child in [item for item in list(html_tag.contents) if not (isinstance(item, Tag) and item.name == "head")]:
            body.append(child.extract())
        html_tag.append(body)

    return soup, html_tag, body


def _parse_inline_style(style_attr: str) -> dict[str, str]:
    styles: dict[str, str] = {}
    for declaration in (style_attr or "").split(";"):
        declaration = declaration.strip()
        if not declaration or ":" not in declaration:
            continue
        key, value = declaration.split(":", 1)
        styles[key.strip().lower()] = value.strip()
    return styles


def _serialize_inline_style(style_map: dict[str, str]) -> str:
    return "; ".join(f"{key}: {value}" for key, value in style_map.items() if value)


def _positive_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None