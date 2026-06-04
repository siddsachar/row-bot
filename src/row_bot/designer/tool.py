"""Designer tool — DesignerTool(BaseTool) with agent sub-tools."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import pathlib
from typing import Optional

from langchain_core.tools import StructuredTool

from row_bot.tools.base import BaseTool
from row_bot.tools import registry
from row_bot.designer.canvas_resize import resolve_canvas_target, resize_project_canvas
from row_bot.designer.components import render_component_html
from row_bot.designer.critique import critique_page_html, apply_page_repairs
from row_bot.designer.html_ops import (
    build_media_fragment,
    duplicate_element_in_html,
    find_asset_identifier_in_html,
    insert_component_in_html,
    move_element_in_html,
    move_asset_in_html,
    preserve_app_mockup_widgets,
    remove_asset_from_html,
    replace_asset_in_html,
    restyle_element_in_html,
    sanitize_agent_html,
    summarize_page_html,
    wrap_asset_fragment,
)
from row_bot.designer.render_assets import (
    find_unresolved_asset_placeholders,
    normalize_asset_reference_sources,
    normalize_inline_image_sources,
    restore_inline_asset_sources,
)
from row_bot.designer.session import (
    get_active_project,
    get_ui_active_project,
    get_undo_stack,
    prepare_project_mutation,
    set_active_project,
)
from row_bot.designer.references import find_project_reference
from row_bot.designer.state import DesignerAsset, DesignerProject, DesignerPage, BrandConfig, ASPECT_RATIOS, CANVAS_PRESETS
from row_bot.designer.storage import save_asset_bytes, save_project

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# SUB-TOOL IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════════

def _require_project() -> DesignerProject:
    """Return active project or raise."""
    project = get_active_project()
    if project is None:
        raise ValueError(
            "No designer project is currently open. "
            "Please open or create a project first."
        )
    return project


def _unresolved_asset_placeholder_error(placeholders: list[str]) -> str:
    shown = ", ".join(placeholders[:3])
    suffix = "" if len(placeholders) <= 3 else f" and {len(placeholders) - 3} more"
    return (
        "Error: unresolved designer asset placeholder(s): "
        f"{shown}{suffix}. Use asset://<asset-id> references from designer_get_project "
        "and keep data-asset-id on the img when possible, or use designer_insert_image / "
        "designer_generate_image instead of placeholder tokens."
    )


def _pre_mutate(project: DesignerProject, label: str = "") -> None:
    """Push undo state and save a persistent snapshot before a mutation.

    All agent tool calls flow through here, so the snapshot is tagged
    with ``author="agent"`` — the editor diff view (Phase 2.2.L) uses
    that flag to surface the most recent agent change.
    """
    prepare_project_mutation(project, label=label, author="agent")


def _escape_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _make_asset_filename(source_name: str, mime_type: str, fallback_stem: str) -> str:
    candidate = pathlib.Path((source_name or fallback_stem).strip()).name or fallback_stem
    suffix = pathlib.Path(candidate).suffix.lower()
    if not suffix:
        suffix = mimetypes.guess_extension(mime_type or "") or ".bin"
        if suffix == ".jpe":
            suffix = ".jpg"
        candidate = f"{candidate}{suffix}"
    return candidate


def _upsert_project_asset(
    project: DesignerProject,
    *,
    data: bytes,
    asset_kind: str,
    label: str,
    mime_type: str,
    filename: str,
    asset_id: str = "",
    width: int | None = None,
    height: int | None = None,
) -> DesignerAsset:
    asset = next((existing for existing in project.assets if existing.id == asset_id), None) if asset_id else None
    if asset is None:
        asset = DesignerAsset(id=asset_id) if asset_id else DesignerAsset()
        project.assets.append(asset)

    asset.kind = asset_kind
    asset.label = label
    asset.mime_type = mime_type
    asset.filename = filename
    asset.size_bytes = len(data)
    asset.sha256 = hashlib.sha256(data).hexdigest()
    asset.width = width
    asset.height = height
    asset.stored_name = save_asset_bytes(project.id, asset.id, filename, data)
    return asset


def _build_asset_image_tag(asset_id: str, *, width: int = 800, alt: str = "") -> str:
    return (
        f'<img src="asset://{asset_id}" '
        f'data-asset-id="{asset_id}" '
        f'alt="{_escape_attr(alt)}" '
        f'style="width:{width}px; max-width:100%; height:auto; display:block; margin:0 auto;" />'
    )


def _build_asset_fragment(
    project: DesignerProject,
    *,
    data: bytes,
    asset_kind: str,
    label: str,
    mime_type: str,
    filename: str,
    width: int = 800,
    height: int | None = None,
    alt: str = "",
    asset_id: str = "",
) -> tuple[str, str]:
    asset = _upsert_project_asset(
        project,
        data=data,
        asset_kind=asset_kind,
        label=label,
        mime_type=mime_type,
        filename=filename,
        asset_id=asset_id,
        width=width,
        height=height,
    )
    img_tag = _build_asset_image_tag(asset.id, width=width, alt=alt or label or filename)
    wrapped_fragment, _ = wrap_asset_fragment(
        img_tag,
        asset_kind,
        label=label,
        asset_id=asset.id,
    )
    return wrapped_fragment, asset.id


def _resolve_page_index(project: DesignerProject, page_index: int) -> int:
    if page_index == -1:
        page_index = project.active_page
    if page_index < 0:
        page_index = len(project.pages) + page_index
    if page_index < 0 or page_index >= len(project.pages):
        raise ValueError(f"page index {page_index} out of range.")
    return page_index


def _set_pages(pages: list[dict]) -> str:
    """Replace ALL pages in the active project."""
    project = _require_project()
    if not pages:
        return "Error: pages list cannot be empty."
    source_htmls = [page.html for page in project.pages]
    is_app_mockup = normalize_designer_mode(project.mode) == "app_mockup"
    _pre_mutate(project, "set_pages")
    new_pages = []
    for i, p in enumerate(pages):
        html = p.get("html", "")
        title = p.get("title", f"Page {i + 1}")
        notes = p.get("notes", "")
        if not html:
            return f"Error: page {i} has no HTML content."
        html = sanitize_agent_html(html)
        html = restore_inline_asset_sources(html, source_htmls)
        # For app_mockup projects, reinject widget CSS if the agent
        # dropped it — stops Settings/Detail screens decaying into
        # plain-text comma lists every time the agent rewrites them.
        if is_app_mockup and i < len(source_htmls):
            html = preserve_app_mockup_widgets(source_htmls[i], html)
        html, _ = normalize_inline_image_sources(html, project)
        html, _ = normalize_asset_reference_sources(html, project)
        unresolved_placeholders = find_unresolved_asset_placeholders(html)
        if unresolved_placeholders:
            return _unresolved_asset_placeholder_error(unresolved_placeholders)
        new_pages.append(DesignerPage(html=html, title=title, notes=notes))
    project.pages = new_pages
    project.active_page = 0
    save_project(project)
    return f"Set {len(new_pages)} pages. Preview updated."


def _update_page(index: int, html: str, title: Optional[str] = None,
                 notes: Optional[str] = None) -> str:
    """Update a single page's HTML (and optionally title/notes)."""
    project = _require_project()
    if index < 0:
        index = len(project.pages) + index
    if index < 0 or index >= len(project.pages):
        return f"Error: page index {index} out of range (0\u2013{len(project.pages) - 1})."
    if not html:
        return "Error: html cannot be empty."
    html = sanitize_agent_html(html)
    source_htmls = [page.html for page in project.pages]
    html = restore_inline_asset_sources(html, source_htmls)
    # Reinject widget CSS the agent may have dropped on rewrite.
    if normalize_designer_mode(project.mode) == "app_mockup":
        html = preserve_app_mockup_widgets(source_htmls[index], html)
    html, _ = normalize_inline_image_sources(html, project)
    html, _ = normalize_asset_reference_sources(html, project)
    unresolved_placeholders = find_unresolved_asset_placeholders(html)
    if unresolved_placeholders:
        return _unresolved_asset_placeholder_error(unresolved_placeholders)
    _pre_mutate(project, f"update_page_{index}")
    project.pages[index].html = html
    if title is not None:
        project.pages[index].title = title
    if notes is not None:
        project.pages[index].notes = notes
    project.pages[index].thumbnail_b64 = None  # invalidate
    save_project(project)
    page_title = project.pages[index].title
    return f"Updated page {index + 1}: \"{page_title}\". Preview refreshed."


def _add_page(html: str, title: str, index: int = -1,
              notes: str = "") -> str:
    """Insert a new page at the given position (-1 = append)."""
    project = _require_project()
    if not html:
        return "Error: html cannot be empty."
    html = sanitize_agent_html(html)
    source_htmls = [page.html for page in project.pages]
    html = restore_inline_asset_sources(html, source_htmls)
    # New app_mockup screens frequently inherit widget vocabulary from
    # sibling screens but the agent forgets to include the style block.
    # Seed the new page's <head> with widget CSS from the first sibling
    # that has it, so toggle rows / tab bars / pill buttons keep their
    # appearance.
    if normalize_designer_mode(project.mode) == "app_mockup":
        for sibling in source_htmls:
            merged = preserve_app_mockup_widgets(sibling, html)
            if merged != html:
                html = merged
                break
    html, _ = normalize_inline_image_sources(html, project)
    html, _ = normalize_asset_reference_sources(html, project)
    unresolved_placeholders = find_unresolved_asset_placeholders(html)
    if unresolved_placeholders:
        return _unresolved_asset_placeholder_error(unresolved_placeholders)
    _pre_mutate(project, "add_page")
    page = DesignerPage(html=html, title=title, notes=notes)
    if index == -1 or index >= len(project.pages):
        project.pages.append(page)
        pos = len(project.pages)
    else:
        if index < 0:
            index = max(0, len(project.pages) + index + 1)
        project.pages.insert(index, page)
        pos = index + 1
    # Navigate to the newly added page so the preview refreshes
    project.active_page = pos - 1
    save_project(project)
    return f"Added page \"{title}\" at position {pos}. Total: {len(project.pages)} pages."


def _delete_page(index: int) -> str:
    """Remove a page by index."""
    project = _require_project()
    if len(project.pages) <= 1:
        return "Error: cannot delete the last remaining page."
    _pre_mutate(project, "delete_page")
    if index < 0:
        index = len(project.pages) + index
    if index < 0 or index >= len(project.pages):
        return f"Error: page index {index} out of range (0–{len(project.pages) - 1})."
    removed = project.pages.pop(index)
    # Adjust active_page to keep tracking the correct page
    if index < project.active_page:
        project.active_page -= 1
    elif project.active_page >= len(project.pages):
        project.active_page = len(project.pages) - 1
    save_project(project)
    return f"Deleted page {index + 1}: \"{removed.title}\". Now {len(project.pages)} pages remain."


def _move_page(from_index: int, to_index: int) -> str:
    """Move a page from one position to another."""
    project = _require_project()
    _pre_mutate(project, "move_page")
    n = len(project.pages)
    if from_index < 0:
        from_index = n + from_index
    if to_index < 0:
        to_index = n + to_index
    if from_index < 0 or from_index >= n:
        return f"Error: from_index {from_index} out of range (0–{n - 1})."
    if to_index < 0 or to_index >= n:
        return f"Error: to_index {to_index} out of range (0–{n - 1})."
    if from_index == to_index:
        return "No change — source and target positions are the same."
    page = project.pages.pop(from_index)
    project.pages.insert(to_index, page)
    # Track active_page to follow the correct page after reorder
    if project.active_page == from_index:
        project.active_page = to_index
    elif from_index < project.active_page <= to_index:
        project.active_page -= 1
    elif to_index <= project.active_page < from_index:
        project.active_page += 1
    save_project(project)
    return f"Moved \"{page.title}\" from position {from_index + 1} to {to_index + 1}."


def _get_project() -> str:
    """Return a JSON summary of the current project state."""
    project = _require_project()
    pages_summary = []
    for i, p in enumerate(project.pages):
        pages_summary.append({
            "index": i,
            "title": p.title,
            "has_notes": bool(p.notes.strip()),
            "notes_word_count": len(p.notes.split()) if p.notes.strip() else 0,
            "html_length": len(p.html),
            "notes_preview": p.notes[:200] if p.notes else "",
            "summary": summarize_page_html(p.html),
        })
    brand_dict = project.brand.to_dict() if project.brand else None
    brief_dict = project.brief.to_dict() if project.brief else None
    summary = {
        "name": project.name,
        "aspect_ratio": project.aspect_ratio,
        "canvas_width": project.canvas_width,
        "canvas_height": project.canvas_height,
        "page_count": len(project.pages),
        "active_page": project.active_page,
        "brand": brand_dict,
        "brief": brief_dict,
        "publish_url": project.publish_url,
        "published_at": project.published_at,
        "assets": [asset.to_summary_dict() for asset in project.assets],
        "references": [reference.to_summary_dict() for reference in project.references],
        "pages": pages_summary,
    }
    return json.dumps(summary, indent=2)


def _resize_project(preset: Optional[str] = None, aspect_ratio: Optional[str] = None) -> str:
    """Resize the project canvas using a preset or explicit aspect ratio."""
    project = _require_project()
    try:
        resolved_ratio, resolved_label = resolve_canvas_target(
            preset=preset,
            aspect_ratio=aspect_ratio,
        )
    except ValueError as exc:
        return f"Error: {exc}"

    try:
        info = resize_project_canvas(
            project,
            aspect_ratio=resolved_ratio,
            label=resolved_label,
            source="designer tool",
        )
    except Exception as exc:
        logger.exception("Designer resize failed")
        return f"Error resizing project: {exc}"
    return str(info["message"])
    return (
        f"Resized project to {resolved_label} ({resolved_ratio}) at "
        f"{project.canvas_width}×{project.canvas_height}."
    )


def _get_reference(reference_ref: str) -> str:
    """Return the stored metadata and extracted excerpt for a project reference."""

    project = _require_project()
    reference = find_project_reference(project, reference_ref)
    if reference is None:
        return f"Error: could not find project reference '{reference_ref}'."
    payload = reference.to_dict()
    return json.dumps(payload, indent=2)


def _get_page_html(page_index: int = -1) -> str:
    """Return the full stored HTML for a single page."""

    project = _require_project()
    try:
        page_index = _resolve_page_index(project, page_index)
    except ValueError as e:
        return f"Error: {e}"

    page = project.pages[page_index]
    payload = {
        "index": page_index,
        "title": page.title,
        "notes": page.notes,
        "html": page.html,
    }
    return json.dumps(payload, indent=2)


def _generate_notes(page_index: int = -1) -> str:
    """Generate speaker notes for a page and save them into the project."""

    project = _require_project()
    try:
        page_index = _resolve_page_index(project, page_index)
    except ValueError as e:
        return f"Error: {e}"

    page = project.pages[page_index]
    try:
        from row_bot.designer.ai_content import generate_speaker_notes

        summary = summarize_page_html(page.html)
        notes = (generate_speaker_notes(page.title, summary, page.notes) or "").strip()
    except Exception as e:
        logger.exception("Failed to generate speaker notes")
        return f"Error generating speaker notes: {e}"

    if not notes:
        return f"No speaker notes were generated for page {page_index + 1}."
    if notes == (page.notes or "").strip():
        return f"Speaker notes for page {page_index + 1} are already up to date."

    _pre_mutate(project, f"generate_notes_{page_index}")
    page.notes = notes
    save_project(project)
    return f"Generated speaker notes for page {page_index + 1}: \"{page.title}\"."


def _set_brand(primary_color: Optional[str] = None,
               secondary_color: Optional[str] = None,
               accent_color: Optional[str] = None,
               bg_color: Optional[str] = None,
               text_color: Optional[str] = None,
               heading_font: Optional[str] = None,
               body_font: Optional[str] = None,
               logo_mode: Optional[str] = None,
               logo_scope: Optional[str] = None,
               logo_position: Optional[str] = None,
               logo_max_height: Optional[int] = None,
               logo_padding: Optional[int] = None) -> str:
    """Update the project's brand configuration."""
    project = _require_project()
    if project.brand is None:
        project.brand = BrandConfig()
    _pre_mutate(project, "set_brand")
    b = project.brand
    if primary_color is not None:
        b.primary_color = primary_color
    if secondary_color is not None:
        b.secondary_color = secondary_color
    if accent_color is not None:
        b.accent_color = accent_color
    if bg_color is not None:
        b.bg_color = bg_color
    if text_color is not None:
        b.text_color = text_color
    if heading_font is not None:
        b.heading_font = heading_font
    if body_font is not None:
        b.body_font = body_font
    if logo_mode is not None:
        if logo_mode not in {"auto", "manual"}:
            return "Error: logo_mode must be 'auto' or 'manual'."
        b.logo_mode = logo_mode
    if logo_scope is not None:
        if logo_scope not in {"all", "first"}:
            return "Error: logo_scope must be 'all' or 'first'."
        b.logo_scope = logo_scope
    if logo_position is not None:
        if logo_position not in {"top_left", "top_right", "bottom_left", "bottom_right"}:
            return "Error: logo_position must be top_left, top_right, bottom_left, or bottom_right."
        b.logo_position = logo_position
    if logo_max_height is not None:
        b.logo_max_height = max(int(logo_max_height), 24)
    if logo_padding is not None:
        b.logo_padding = max(int(logo_padding), 0)
    # Auto-propagate: update the :root CSS block in every page's stored HTML
    from row_bot.designer.preview import update_brand_in_html
    updated = 0
    for page in project.pages:
        if page.html.strip():
            page.html = update_brand_in_html(page.html, b)
            page.thumbnail_b64 = None  # invalidate
            updated += 1
    save_project(project)
    logo_summary = "No logo set."
    if b.logo_b64 or b.logo_asset_id:
        if b.logo_mode == "manual":
            logo_summary = "Logo placement mode: manual placeholder."
        else:
            logo_summary = (
                f"Logo placement mode: auto ({b.logo_scope}, {b.logo_position}, "
                f"max-height {b.logo_max_height}px, inset {b.logo_padding}px)."
            )
    return (
        f"Brand updated and applied to {updated} page(s). "
        f"The CSS variables are now: "
        f"--primary: {b.primary_color}, --secondary: {b.secondary_color}, "
        f"--accent: {b.accent_color}, --bg: {b.bg_color}, --text: {b.text_color}, "
        f"--heading-font: {b.heading_font}, --body-font: {b.body_font}. "
        f"{logo_summary}"
    )


def _insert_component(component_name: str, page_index: int = -1,
                      position: str = "bottom", target_selector: str = "",
                      target_ref: str = "", target_xpath: str = "",
                      replacements_json: str = "") -> str:
    """Insert a curated reusable component into a page."""

    project = _require_project()
    try:
        page_index = _resolve_page_index(project, page_index)
    except ValueError as e:
        return f"Error: {e}"

    page = project.pages[page_index]
    try:
        component_html = render_component_html(component_name, replacements_json)
        new_html, element_id, selector_hint = insert_component_in_html(
            page.html,
            component_html,
            component_name,
            position=position,
            target_selector=target_selector,
            target_ref=target_ref,
            target_xpath=target_xpath,
        )
    except Exception as e:
        return f"Error inserting component: {e}"

    _pre_mutate(project, f"insert_component_{component_name}")
    page.html = new_html
    page.thumbnail_b64 = None
    save_project(project)
    return (
        f"Inserted component '{component_name}' on page {page_index + 1}. "
        f"Root element id: {element_id}. Selector hint: {selector_hint}."
    )


def _critique_page(page_index: int = -1) -> str:
    """Review a page for hierarchy, overflow, contrast, readability, and spacing."""

    project = _require_project()
    try:
        page_index = _resolve_page_index(project, page_index)
    except ValueError as e:
        return f"Error: {e}"

    page = project.pages[page_index]
    report = critique_page_html(page.html, project.canvas_width, project.canvas_height)
    payload = {
        "page_index": page_index,
        "page_title": page.title,
        **report,
    }
    return json.dumps(payload, indent=2)


def _brand_lint(page_index: int = -1) -> str:
    """Run a read-only brand-lint scan.

    ``page_index=-1`` (default) scans every page; otherwise scans the
    specified 0-based page. Returns a JSON report with findings grouped
    by category (contrast, off_palette, font, missing_alt, logo_safe_zone).
    """
    from row_bot.designer.brand_lint import lint_project, lint_page

    project = _require_project()
    if page_index == -1 and not project.pages:
        return json.dumps({"findings": [], "summary": "No pages."}, indent=2)

    if page_index == -1:
        report = lint_project(project)
    else:
        try:
            idx = _resolve_page_index(project, page_index)
        except ValueError as e:
            return f"Error: {e}"
        findings = lint_page(project.pages[idx].html, brand=project.brand, page_index=idx)
        cat_counts: dict[str, int] = {}
        sev_counts = {"low": 0, "medium": 0, "high": 0}
        for f in findings:
            cat_counts[f.category] = cat_counts.get(f.category, 0) + 1
            if f.severity in sev_counts:
                sev_counts[f.severity] += 1
        report = {
            "findings": [f.to_dict() for f in findings],
            "summary": ("No brand issues detected." if not findings
                        else f"{len(findings)} brand issue(s) on page {idx + 1}."),
            "category_counts": cat_counts,
            "severity_counts": sev_counts,
        }
    return json.dumps(report, indent=2)


def _apply_repairs(page_index: int = -1, categories: Optional[list[str]] = None) -> str:
    """Apply safe deterministic repairs for critique categories on a page."""
    project = _require_project()
    try:
        page_index = _resolve_page_index(project, page_index)
    except ValueError as e:
        return f"Error: {e}"

    page = project.pages[page_index]
    try:
        new_html, changes = apply_page_repairs(
            page.html,
            project.canvas_width,
            project.canvas_height,
            categories,
        )
    except Exception as e:
        return f"Error applying repairs: {e}"

    if not changes or new_html == page.html:
        return f"No safe repairs were applied to page {page_index + 1}."

    _pre_mutate(project, f"apply_repairs_{page_index}")
    page.html = new_html
    page.thumbnail_b64 = None
    save_project(project)
    categories_applied = ", ".join(sorted({change['category'] for change in changes}))
    return (
        f"Applied {len(changes)} safe repair(s) on page {page_index + 1}. "
        f"Categories: {categories_applied}."
    )


def _export(format: str, pages: Optional[str] = None, mode: Optional[str] = None) -> str:
    """Export the project. Supports pdf, html, png, pptx."""
    project = _require_project()
    fmt = format.lower().strip()
    supported = ("pdf", "html", "png", "pptx")
    if fmt not in supported:
        return f"Error: unsupported format '{format}'. Supported: {', '.join(supported)}."
    try:
        from row_bot.designer.export import (
            describe_export_destination,
            export_pdf, export_html, export_png,
            export_pptx_screenshot, export_pptx_structured,
        )
        out_path = describe_export_destination(project, fmt, pages, mode if fmt == "pptx" else None)
        if fmt == "pdf":
            data = export_pdf(project, pages)
        elif fmt == "html":
            data = export_html(project, pages)
        elif fmt == "png":
            data = export_png(project, pages)
        elif fmt == "pptx":
            if mode and mode.lower() == "structured":
                data = export_pptx_structured(project, pages)
            else:
                data = export_pptx_screenshot(project, pages)
        actual_path = pathlib.Path(getattr(data, "saved_path", out_path))
        size_kb = len(data) / 1024
        if actual_path != out_path:
            return (
                f"Exported as {fmt.upper()} ({size_kb:.0f} KB). "
                f"The original target was busy, so the file was saved to {actual_path}."
            )
        return f"Exported as {fmt.upper()} ({size_kb:.0f} KB). File saved to {actual_path}."
    except Exception as e:
        logger.exception("Designer export failed")
        return f"Export failed: {e}"


def _publish_link(pages: Optional[str] = None) -> str:
    """Publish the project as a self-contained HTML link."""
    project = _require_project()
    try:
        from row_bot.designer.publish import publish_project

        info = publish_project(project, pages, ensure_public=True)
    except Exception as e:
        logger.exception("Designer publish failed")
        return f"Publish failed: {e}"

    visibility = "public" if info.get("public") else "local"
    return (
        f"Published {visibility} link: {info['url']}. "
        f"Static file saved to {info['path']}."
    )


# ═══════════════════════════════════════════════════════════════════════
# PHASE 5A: AI-POWERED CONTENT SUB-TOOLS
# ═══════════════════════════════════════════════════════════════════════

def _generate_image(prompt: str, page_index: int = -1,
                    position: str = "bottom", width: int = 800,
                    height: int = 500, size: str = "auto") -> str:
    """Generate an AI image from a text prompt and embed it in a page."""
    project = _require_project()
    try:
        page_index = _resolve_page_index(project, page_index)
    except ValueError as e:
        return f"Error: {e}"

    from row_bot.designer.ai_content import generate_image_bytes, insert_image_into_page

    try:
        image_bytes, mime_type = generate_image_bytes(prompt, size=size)
    except ValueError as exc:
        return f"Error: {exc}"

    wrapped_fragment, asset_id = _build_asset_fragment(
        project,
        data=image_bytes,
        asset_kind="generated-image",
        label=prompt,
        mime_type=mime_type,
        filename=_make_asset_filename(prompt, mime_type, "generated-image"),
        width=width,
        height=height,
        alt=prompt,
    )

    _pre_mutate(project, "generate_image")
    page = project.pages[page_index]
    page.html = insert_image_into_page(page.html, wrapped_fragment, position)
    page.thumbnail_b64 = None
    save_project(project)
    return (
        f"Generated AI image for \"{prompt}\" and inserted it at {position} of "
        f"page {page_index + 1}. Asset id: {asset_id}."
    )


def _refine_text(page_index: int, tag: str, old_text: str,
                 action: str, custom_instruction: str = "") -> str:
    """Refine a text element on a page using AI.

    Actions: shorten, expand, professional, casual, persuasive, simplify, bullets, paragraph, custom.
    """
    project = _require_project()
    if page_index < 0 or page_index >= len(project.pages):
        return f"Error: page index {page_index} out of range."

    valid_actions = {"shorten", "expand", "professional", "casual",
                     "persuasive", "simplify", "bullets", "paragraph", "custom"}
    if action not in valid_actions:
        return f"Error: unknown action '{action}'. Valid: {', '.join(sorted(valid_actions))}"

    from row_bot.designer.ai_content import refine_text_in_html
    _pre_mutate(project, "refine_text")
    page = project.pages[page_index]
    new_html, refined = refine_text_in_html(
        page.html, tag, old_text, action, custom_instruction
    )
    if new_html == page.html:
        return "No changes were made — the text could not be found or refinement returned the same text."
    page.html = new_html
    page.thumbnail_b64 = None
    save_project(project)
    return f"Refined text on page {page_index + 1}: \"{refined[:80]}{'...' if len(refined) > 80 else ''}\""


def _add_chart(chart_type: str, data_csv: str, title: str = "",
               page_index: int = -1, position: str = "bottom",
               width: int = 800, height: int = 500) -> str:
    """Add a data visualization chart to a page.

    Chart types: bar, line, pie, scatter, donut, histogram, box, area, heatmap.
    Data: inline CSV with header row.
    """
    project = _require_project()
    try:
        page_index = _resolve_page_index(project, page_index)
    except ValueError as e:
        return f"Error: {e}"

    # Use brand colors if available
    colors = None
    if project.brand:
        b = project.brand
        brand_colors = [c for c in [b.primary_color, b.secondary_color, b.accent_color] if c]
        if brand_colors:
            colors = brand_colors

    try:
        from row_bot.designer.ai_content import build_chart_png, insert_image_into_page
        png_bytes = build_chart_png(chart_type, data_csv, title, colors, width, height)
        wrapped_fragment, asset_id = _build_asset_fragment(
            project,
            data=png_bytes,
            asset_kind="chart",
            label=title or chart_type,
            mime_type="image/png",
            filename=_make_asset_filename(title or chart_type, "image/png", chart_type),
            width=width,
            height=height,
            alt=title or chart_type,
        )
    except Exception as e:
        return f"Error creating chart: {e}"

    _pre_mutate(project, "add_chart")
    page = project.pages[page_index]
    page.html = insert_image_into_page(page.html, wrapped_fragment, position)
    page.thumbnail_b64 = None
    save_project(project)
    return (
        f"Added {chart_type} chart \"{title or 'Untitled'}\" to page {page_index + 1}. "
        f"Asset id: {asset_id}."
    )


def _insert_image(image_source: str, page_index: int = -1,
                  position: str = "bottom", width: int = 800,
                  alt: str = "") -> str:
    """Insert an attached, pasted, generated, or local image into a page."""

    project = _require_project()
    try:
        page_index = _resolve_page_index(project, page_index)
    except ValueError as e:
        return f"Error: {e}"

    try:
        from row_bot.tools.image_gen_tool import _detect_mime, _resolve_image_source

        img_bytes = _resolve_image_source(image_source)
        mime_type = _detect_mime(img_bytes)
    except Exception as e:
        return f"Error inserting image: {e}"

    from row_bot.designer.ai_content import insert_image_into_page

    label = alt or image_source
    wrapped_fragment, asset_id = _build_asset_fragment(
        project,
        data=img_bytes,
        asset_kind="uploaded-image",
        label=label,
        mime_type=mime_type,
        filename=_make_asset_filename(image_source, mime_type, "uploaded-image"),
        width=width,
        alt=alt or label,
    )

    _pre_mutate(project, "insert_image")
    page = project.pages[page_index]
    page.html = insert_image_into_page(page.html, wrapped_fragment, position)
    page.thumbnail_b64 = None
    save_project(project)
    return (
        f"Inserted image \"{label}\" on page {page_index + 1} at {position}. "
        f"Asset id: {asset_id}."
    )


def _build_video_fragment(
    project: DesignerProject,
    asset,
    *,
    width: int = 800,
    label: str = "",
) -> tuple[str, str]:
    """Wrap a persisted video ``DesignerAsset`` as an insertable HTML block."""
    poster_src = ""
    if asset.poster_asset_id:
        poster_src = f"asset://{asset.poster_asset_id}"
    inner = build_media_fragment(
        asset_kind="video",
        asset_id=asset.id,
        src=f"asset://{asset.id}",
        mime_type=asset.mime_type or "video/mp4",
        label=label or asset.label or "video",
        alt=label or asset.label or "video",
        poster=poster_src,
        width=width,
        autoplay=bool(asset.autoplay),
        loop=bool(asset.loop),
        muted=bool(asset.muted),
        controls=bool(asset.controls),
    )
    wrapped, _ = wrap_asset_fragment(
        inner, "video", label=label or asset.label, asset_id=asset.id,
    )
    return wrapped, asset.id


def _generate_video(prompt: str, page_index: int = -1,
                    position: str = "bottom", width: int = 800,
                    aspect_ratio: str = "", duration: int = 6,
                    resolution: str = "720p",
                    image_source: str = "") -> str:
    """Generate an AI video clip (text-to-video or image-to-video) and embed it in a page.

    ``aspect_ratio`` defaults to the project aspect. ``image_source`` enables
    image-to-video when set (``"last"``, a filename, or path).
    """
    project = _require_project()
    try:
        page_index = _resolve_page_index(project, page_index)
    except ValueError as e:
        return f"Error: {e}"

    from row_bot.designer.ai_content import generate_video_bytes, insert_image_into_page

    try:
        asset = generate_video_bytes(
            prompt,
            project=project,
            duration_seconds=int(duration or 6),
            aspect_ratio=(aspect_ratio or None),
            resolution=resolution or "720p",
            image_source=(image_source or None),
        )
    except ValueError as exc:
        return f"Error: {exc}"

    wrapped_fragment, asset_id = _build_video_fragment(
        project, asset, width=width, label=prompt,
    )

    _pre_mutate(project, "generate_video")
    page = project.pages[page_index]
    page.html = insert_image_into_page(page.html, wrapped_fragment, position)
    page.thumbnail_b64 = None
    save_project(project)
    return (
        f"Generated AI video for \"{prompt}\" and inserted it at {position} of "
        f"page {page_index + 1}. Asset id: {asset_id}."
    )


def _insert_video(video_source: str, page_index: int = -1,
                  position: str = "bottom", width: int = 800,
                  alt: str = "") -> str:
    """Insert an attached, pasted, or local video file (mp4/webm) into a page.

    ``video_source``: attachment filename/partial, 'last', or absolute path.
    """
    project = _require_project()
    try:
        page_index = _resolve_page_index(project, page_index)
    except ValueError as e:
        return f"Error: {e}"

    from pathlib import Path as _Path
    from row_bot.designer.ai_content import insert_image_into_page
    from row_bot.designer.state import DesignerAsset
    from row_bot.designer.storage import save_asset_bytes
    import hashlib as _hl

    src = (video_source or "").strip()
    if not src:
        return "Error: video_source cannot be empty."

    # Resolve bytes from a local path. (Attachment lookup reuses the same
    # channel as images: callers can pass a stored filename that exists on disk.)
    try:
        p = _Path(src).expanduser()
        if not p.is_file():
            # Try resolving via image_gen_tool helper which understands attachments/last
            from row_bot.tools.image_gen_tool import _resolve_image_source as _ris  # reuses attachment lookup
            try:
                data = _ris(src)
            except Exception as exc:
                return f"Error: could not resolve video_source '{src}': {exc}"
        else:
            data = p.read_bytes()
    except Exception as exc:
        return f"Error: could not read video_source '{src}': {exc}"

    if not data:
        return "Error: video file was empty."

    # Sniff mime from extension
    ext = (_Path(src).suffix or "").lower()
    mime = {
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
    }.get(ext, "video/mp4")

    label = alt or _Path(src).name
    asset = DesignerAsset(
        kind="video",
        label=label[:80],
        mime_type=mime,
        filename=_Path(src).name or f"video-{asset_id_hint()}.mp4",
        muted=True, controls=True, autoplay=False, loop=False,
    )
    asset.stored_name = save_asset_bytes(project.id, asset.id, asset.filename, data)
    asset.size_bytes = len(data)
    asset.sha256 = _hl.sha256(data).hexdigest()
    project.assets.append(asset)

    wrapped_fragment, asset_id = _build_video_fragment(
        project, asset, width=width, label=label,
    )

    _pre_mutate(project, "insert_video")
    page = project.pages[page_index]
    page.html = insert_image_into_page(page.html, wrapped_fragment, position)
    page.thumbnail_b64 = None
    save_project(project)
    return (
        f"Inserted video \"{label}\" on page {page_index + 1} at {position}. "
        f"Asset id: {asset_id}."
    )


def asset_id_hint() -> str:
    import uuid as _uuid
    return _uuid.uuid4().hex[:8]


def _move_image(page_index: int, image_ref: str,
                position: str = "bottom", target_ref: str = "") -> str:
    """Move an existing inserted image or chart block within a page."""

    project = _require_project()
    try:
        page_index = _resolve_page_index(project, page_index)
    except ValueError as e:
        return f"Error: {e}"

    page = project.pages[page_index]
    try:
        new_html, asset_id = move_asset_in_html(
            page.html,
            image_ref,
            position,
            target_ref=target_ref,
        )
    except Exception as e:
        return f"Error moving image: {e}"

    _pre_mutate(project, "move_image")
    page.html = new_html
    page.thumbnail_b64 = None
    save_project(project)
    if target_ref and position in {"before", "after"}:
        return (
            f"Moved asset {asset_id or image_ref} {position} {target_ref} on page {page_index + 1}."
        )
    return f"Moved asset {asset_id or image_ref} to the {position} of page {page_index + 1}."


def _remove_image(image_ref: str, page_index: int = -1) -> str:
    """Remove an inserted image, chart, or video block from a page.

    Leaves surrounding layout (shot-visual placeholders, slide grids) intact
    so the slot reverts to its dashed-border preview. Does not delete the
    stored asset file — only removes the reference from the page HTML.
    """
    project = _require_project()
    try:
        page_index = _resolve_page_index(project, page_index)
    except ValueError as e:
        return f"Error: {e}"

    page = project.pages[page_index]
    try:
        new_html, asset_id = remove_asset_from_html(page.html, image_ref)
    except Exception as e:
        return f"Error removing image: {e}"

    _pre_mutate(project, "remove_image")
    page.html = new_html
    page.thumbnail_b64 = None
    save_project(project)
    return f"Removed asset {asset_id or image_ref} from page {page_index + 1}."


def _replace_image(image_ref: str, image_source: str,
                   page_index: int = -1, width: int = 800,
                   alt: str = "") -> str:
    """Replace an existing inserted image or chart block with a new image."""

    project = _require_project()
    try:
        page_index = _resolve_page_index(project, page_index)
    except ValueError as e:
        return f"Error: {e}"

    try:
        from row_bot.tools.image_gen_tool import _detect_mime, _resolve_image_source

        img_bytes = _resolve_image_source(image_source)
        mime_type = _detect_mime(img_bytes)
        resolved_asset_id = find_asset_identifier_in_html(project.pages[page_index].html, image_ref)
        _upsert_project_asset(
            project,
            data=img_bytes,
            asset_kind="uploaded-image",
            label=alt or image_source,
            mime_type=mime_type,
            filename=_make_asset_filename(image_source, mime_type, "uploaded-image"),
            asset_id=resolved_asset_id,
            width=width,
        )
        img_tag = _build_asset_image_tag(resolved_asset_id, width=width, alt=alt or image_source)
        new_html, asset_id = replace_asset_in_html(
            project.pages[page_index].html,
            image_ref,
            img_tag,
            "uploaded-image",
            label=alt or image_source,
        )
    except Exception as e:
        return f"Error replacing image: {e}"

    _pre_mutate(project, "replace_image")
    page = project.pages[page_index]
    page.html = new_html
    page.thumbnail_b64 = None
    save_project(project)
    return (
        f"Replaced asset {asset_id or image_ref} on page {page_index + 1} with image "
        f"\"{alt or image_source}\"."
    )


def _move_element(page_index: int, selector: str = "",
                  position: str = "bottom", target_selector: str = "",
                  element_ref: str = "", target_ref: str = "",
                  xpath: str = "", target_xpath: str = "") -> str:
    """Move a general DOM element within a page using selector or element id."""

    project = _require_project()
    try:
        page_index = _resolve_page_index(project, page_index)
    except ValueError as e:
        return f"Error: {e}"

    page = project.pages[page_index]
    try:
        new_html, element_id, selector_hint = move_element_in_html(
            page.html,
            selector=selector,
            element_ref=element_ref,
            xpath=xpath,
            position=position,
            target_selector=target_selector,
            target_ref=target_ref,
            target_xpath=target_xpath,
        )
    except Exception as e:
        return f"Error moving element: {e}"

    _pre_mutate(project, "move_element")
    page.html = new_html
    page.thumbnail_b64 = None
    save_project(project)
    return (
        f"Moved element {element_id} on page {page_index + 1}. "
        f"Selector hint: {selector_hint}."
    )


def _duplicate_element(page_index: int, selector: str = "",
                       position: str = "after", target_selector: str = "",
                       element_ref: str = "", target_ref: str = "",
                       xpath: str = "", target_xpath: str = "") -> str:
    """Duplicate a general DOM element and insert the copy."""

    project = _require_project()
    try:
        page_index = _resolve_page_index(project, page_index)
    except ValueError as e:
        return f"Error: {e}"

    page = project.pages[page_index]
    try:
        new_html, element_id, selector_hint = duplicate_element_in_html(
            page.html,
            selector=selector,
            element_ref=element_ref,
            xpath=xpath,
            position=position,
            target_selector=target_selector,
            target_ref=target_ref,
            target_xpath=target_xpath,
        )
    except Exception as e:
        return f"Error duplicating element: {e}"

    _pre_mutate(project, "duplicate_element")
    page.html = new_html
    page.thumbnail_b64 = None
    save_project(project)
    return (
        f"Duplicated an element on page {page_index + 1}. "
        f"New element id: {element_id}. Selector hint: {selector_hint}."
    )


def _restyle_element(page_index: int, selector: str = "",
                     style_updates: str = "", add_classes: str = "",
                     remove_classes: str = "", element_ref: str = "",
                     xpath: str = "") -> str:
    """Restyle a DOM element using inline CSS updates and class changes."""

    project = _require_project()
    try:
        page_index = _resolve_page_index(project, page_index)
    except ValueError as e:
        return f"Error: {e}"

    page = project.pages[page_index]
    try:
        new_html, element_id, selector_hint = restyle_element_in_html(
            page.html,
            selector=selector,
            element_ref=element_ref,
            xpath=xpath,
            style_updates=style_updates,
            add_classes=add_classes,
            remove_classes=remove_classes,
        )
    except Exception as e:
        return f"Error restyling element: {e}"

    _pre_mutate(project, "restyle_element")
    page.html = new_html
    page.thumbnail_b64 = None
    save_project(project)
    return (
        f"Restyled element {element_id} on page {page_index + 1}. "
        f"Selector hint: {selector_hint}."
    )


# ═══════════════════════════════════════════════════════════════════════
# Phase 2.2 — Interactive-mode sub-tools
# (landing / app_mockup / storyboard only)
# ═══════════════════════════════════════════════════════════════════════

from row_bot.designer.state import (  # noqa: E402
    DESIGNER_MODES,
    DesignerInteraction,
    normalize_designer_mode,
    default_page_kind_for_mode,
)

INTERACTIVE_PROJECT_MODES = {"landing", "app_mockup", "storyboard"}


def _require_interactive(project: DesignerProject, tool_name: str) -> str:
    if project.mode not in INTERACTIVE_PROJECT_MODES:
        return (
            f"Error: {tool_name} is only available in landing / app_mockup / "
            f"storyboard modes. Current mode: {project.mode}."
        )
    return ""


def _slugify_route_tool(value: str, fallback: str) -> str:
    import re as _re
    slug = _re.sub(r"[^a-zA-Z0-9]+", "-", (value or "")).strip("-").lower()
    return slug or fallback


def _ensure_unique_route_id(project: DesignerProject, candidate: str) -> str:
    existing = {
        (getattr(p, "route_id", "") or "").strip().lower()
        for p in project.pages
    }
    base = candidate
    rid = candidate
    dedup = 2
    while rid.lower() in existing:
        rid = f"{base}-{dedup}"
        dedup += 1
    return rid


def _find_page_by_route(project: DesignerProject, route_id: str) -> int:
    target = (route_id or "").strip().lower()
    for idx, page in enumerate(project.pages):
        if (getattr(page, "route_id", "") or "").strip().lower() == target:
            return idx
    return -1


def _set_mode(mode: str) -> str:
    """Change the project mode. No migration side effects — caller must be OK
    with a clean-slate mode change."""
    project = _require_project()
    m = normalize_designer_mode(mode)
    if m not in DESIGNER_MODES:
        return f"Error: unknown mode '{mode}'. Valid: {sorted(DESIGNER_MODES.keys())}."
    if project.mode == m:
        return f"Project is already in '{m}' mode."
    _pre_mutate(project, f"set_mode_{m}")
    project.mode = m
    # Update page.kind so preview/nav pick the right variant.
    new_kind = default_page_kind_for_mode(m)
    for page in project.pages:
        page.kind = new_kind
    save_project(project)
    return f"Project mode set to '{m}'. Pages converted to kind='{new_kind}'."


def _add_screen(title: str, route_id: str = "", html: str = "",
                copy_from: int = -1) -> str:
    """Add a new screen/route in an interactive project."""
    project = _require_project()
    err = _require_interactive(project, "designer_add_screen")
    if err:
        return err
    title = (title or "").strip() or f"Screen {len(project.pages) + 1}"
    if copy_from is not None and copy_from >= 0 and copy_from < len(project.pages):
        base_html = html or project.pages[copy_from].html
    else:
        base_html = html or ""
    if not base_html:
        # Minimal branded skeleton reused from page_navigator.
        try:
            from row_bot.designer.page_navigator import _branded_blank_html
            base_html = _branded_blank_html(project, title)
        except Exception:
            base_html = (
                f"<!DOCTYPE html><html><head><title>{_escape_attr(title)}</title>"
                f"</head><body><h1>{_escape_attr(title)}</h1></body></html>"
            )
    base_html = sanitize_agent_html(base_html)
    # Resolve route_id
    candidate = (route_id or "").strip()
    if not candidate:
        candidate = _slugify_route_tool(title, f"page-{len(project.pages) + 1}")
    else:
        candidate = _slugify_route_tool(candidate, candidate)
    final_route = _ensure_unique_route_id(project, candidate)

    _pre_mutate(project, "add_screen")
    page = DesignerPage(
        html=base_html,
        title=title,
        route_id=final_route,
        kind=default_page_kind_for_mode(project.mode),
    )
    project.pages.append(page)
    project.active_page = len(project.pages) - 1
    save_project(project)
    return (
        f"Added screen \"{title}\" with route_id='{final_route}'. "
        f"Total screens: {len(project.pages)}."
    )


def _patch_action_attribute(html: str, selector: str, action_value: str) -> tuple[str, bool]:
    """Set data-row-bot-action on the first element matching the selector."""
    from bs4 import BeautifulSoup
    from row_bot.designer.html_ops import ensure_element_identifier
    soup = BeautifulSoup(html, "html.parser")
    target = None
    try:
        # Allow either a real CSS selector or a data-row-bot-element-id token.
        sel = (selector or "").strip()
        if not sel:
            return html, False
        if sel.startswith("#") and " " not in sel and "[" not in sel:
            target = soup.select_one(sel)
        else:
            try:
                target = soup.select_one(sel)
            except Exception:
                target = None
        if target is None:
            # Try element-id attr.
            target = soup.find(attrs={"data-row-bot-element-id": sel})
    except Exception:
        target = None
    if target is None:
        return html, False
    ensure_element_identifier(target)
    target["data-row-bot-action"] = action_value
    return str(soup), True


def _link_screens(source_route: str, selector: str, target_route: str,
                  event: str = "click", transition: str = "fade") -> str:
    """Wire a click on ``selector`` within ``source_route`` to navigate to
    ``target_route``. Adds a DesignerInteraction and patches HTML."""
    project = _require_project()
    err = _require_interactive(project, "designer_link_screens")
    if err:
        return err
    src_idx = _find_page_by_route(project, source_route)
    if src_idx < 0:
        return f"Error: source route '{source_route}' not found."
    if _find_page_by_route(project, target_route) < 0:
        return f"Error: target route '{target_route}' not found."
    _pre_mutate(project, "link_screens")
    action_value = f"navigate:{target_route}"
    new_html, ok = _patch_action_attribute(
        project.pages[src_idx].html, selector, action_value
    )
    if not ok:
        return f"Error: selector '{selector}' matched nothing on '{source_route}'."
    project.pages[src_idx].html = new_html
    project.pages[src_idx].thumbnail_b64 = None
    interaction = DesignerInteraction(
        source_route=source_route,
        selector=selector,
        event=event or "click",
        action="navigate",
        target=target_route,
        transition=transition or "fade",
    )
    project.interactions.append(interaction)
    save_project(project)
    return (
        f"Linked '{selector}' on '{source_route}' → navigate to '{target_route}' "
        f"(transition={transition})."
    )


def _set_interaction(source_route: str, selector: str, action: str,
                     target: str = "", event: str = "click",
                     transition: str = "fade") -> str:
    """Generic — attach navigate/toggle_state/play_media to any selector."""
    project = _require_project()
    err = _require_interactive(project, "designer_set_interaction")
    if err:
        return err
    action = (action or "").strip().lower()
    if action not in {"navigate", "toggle_state", "play_media"}:
        return (
            "Error: action must be 'navigate', 'toggle_state', or 'play_media'."
        )
    src_idx = _find_page_by_route(project, source_route)
    if src_idx < 0:
        return f"Error: source route '{source_route}' not found."
    if action == "navigate" and _find_page_by_route(project, target) < 0:
        return f"Error: navigate target route '{target}' not found."
    _pre_mutate(project, f"set_interaction_{action}")
    action_value = f"{action}:{target}" if target else action
    new_html, ok = _patch_action_attribute(
        project.pages[src_idx].html, selector, action_value
    )
    if not ok:
        return f"Error: selector '{selector}' matched nothing on '{source_route}'."
    project.pages[src_idx].html = new_html
    project.pages[src_idx].thumbnail_b64 = None
    project.interactions.append(DesignerInteraction(
        source_route=source_route,
        selector=selector,
        event=event or "click",
        action=action,
        target=target,
        transition=transition or "fade",
    ))
    save_project(project)
    return f"Set {action}:{target} on '{selector}' within '{source_route}'."


def _preview_screen(route_id: str) -> str:
    """Switch the editor's active screen to a specific route_id."""
    project = _require_project()
    err = _require_interactive(project, "designer_preview_screen")
    if err:
        return err
    idx = _find_page_by_route(project, route_id)
    if idx < 0:
        return f"Error: route '{route_id}' not found."
    project.active_page = idx
    save_project(project)
    return f"Switched preview to route '{route_id}' (page {idx + 1})."


def _reorder_routes(route_ids: list[str]) -> str:
    """Reorder project.pages to match the provided route_id list."""
    project = _require_project()
    err = _require_interactive(project, "designer_reorder_routes")
    if err:
        return err
    wanted = [(r or "").strip() for r in (route_ids or []) if r and (r or "").strip()]
    if len(wanted) != len(project.pages):
        return (
            f"Error: expected {len(project.pages)} route_ids, got {len(wanted)}."
        )
    lookup: dict[str, int] = {}
    for idx, p in enumerate(project.pages):
        rid = (getattr(p, "route_id", "") or "").strip()
        if rid:
            lookup[rid] = idx
    new_pages = []
    for rid in wanted:
        if rid not in lookup:
            return f"Error: route '{rid}' not found."
        new_pages.append(project.pages[lookup[rid]])
    if len(new_pages) != len(project.pages):
        return "Error: route_ids must reference every existing page exactly once."
    _pre_mutate(project, "reorder_routes")
    project.pages = new_pages
    project.active_page = 0
    save_project(project)
    return f"Reordered routes: {', '.join(wanted)}."


# ═══════════════════════════════════════════════════════════════════════
# TOOL REGISTRATION
# ═══════════════════════════════════════════════════════════════════════

class DesignerTool(BaseTool):

    @property
    def name(self) -> str:
        return "designer"

    @property
    def display_name(self) -> str:
        return "🎨 Designer"

    @property
    def description(self) -> str:
        return (
            "Create and edit multi-page visual designs — presentations, "
            "one-pagers, marketing material, wireframes, and reports. "
            "All designs are HTML/CSS rendered in a live preview."
        )

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def destructive_tool_names(self) -> set[str]:
        return {"designer_delete_page"}

    @property
    def inference_keywords(self) -> list[str]:
        return [
            "design", "slide", "presentation", "deck", "page",
            "layout", "brand", "one-pager", "wireframe", "mockup",
            "marketing material", "poster", "brochure",
        ]

    def as_langchain_tools(self) -> list:
        if get_ui_active_project() is None:
            return []
        base_tools = [
            StructuredTool.from_function(
                func=_set_pages,
                name="designer_set_pages",
                description=(
                    "Replace ALL pages in the active designer project. Use for "
                    "creating the first draft, new designs, or full reworks. "
                    "REQUIRED parameter: pages (list of {html, title, notes} dicts). "
                    "Each page's html must be a complete HTML document with inline "
                    "<style>. Example call: designer_set_pages(pages=[{\"html\": "
                    "\"<!doctype html>...\", \"title\": \"Cover\", \"notes\": \"\"}, ...]). "
                    "Never call this tool with no arguments."
                ),
            ),
            StructuredTool.from_function(
                func=_update_page,
                name="designer_update_page",
                description=(
                    "Update a single page's HTML in the designer project. "
                    "Input: index (0-based), html (full HTML), optional title and notes."
                ),
            ),
            StructuredTool.from_function(
                func=_add_page,
                name="designer_add_page",
                description=(
                    "Add a new page to the designer project. "
                    "Input: html, title, index (-1 to append)."
                ),
            ),
            StructuredTool.from_function(
                func=_delete_page,
                name="designer_delete_page",
                description=(
                    "Delete a page from the designer project by index. "
                    "DESTRUCTIVE — requires user approval."
                ),
            ),
            StructuredTool.from_function(
                func=_move_page,
                name="designer_move_page",
                description="Reorder a page. Input: from_index, to_index (0-based).",
            ),
            StructuredTool.from_function(
                func=_get_project,
                name="designer_get_project",
                description=(
                    "Read the current designer project state, including structured page summaries "
                    "and asset IDs plus selector hints for targetable elements. Use before targeted edits."
                ),
            ),
            StructuredTool.from_function(
                func=_get_page_html,
                name="designer_get_page_html",
                description=(
                    "Read the full stored HTML for a single page. Use before full-page rewrites so "
                    "you preserve existing assets and layout. Input: page_index (-1=active)."
                ),
            ),
            StructuredTool.from_function(
                func=_get_reference,
                name="designer_get_reference",
                description=(
                    "Read one saved project reference by id, exact filename, partial filename, or 'latest'. "
                    "Returns its stored summary, warnings, and extracted excerpt so you can reuse the reference "
                    "without asking the user to reattach it."
                ),
            ),
            StructuredTool.from_function(
                func=_generate_notes,
                name="designer_generate_notes",
                description=(
                    "Generate speaker notes for a single page and save them into the project. "
                    "Input: optional page_index (-1=active)."
                ),
            ),
            StructuredTool.from_function(
                func=_insert_component,
                name="designer_insert_component",
                description=(
                    "Insert a curated reusable block into a page. Input: component_name (hero_callout, "
                    "stats_band, feature_cards, testimonial_quote, pricing_cards, timeline_steps), "
                    "optional page_index (-1=active), position ('top'/'bottom'/'before'/'after'), "
                    "optional target_selector or target_ref for before/after insertion, and optional "
                    "replacements_json for overriding default copy."
                ),
            ),
            StructuredTool.from_function(
                func=_critique_page,
                name="designer_critique_page",
                description=(
                    "Review a page for hierarchy, overflow, contrast, readability, and spacing issues. "
                    "Input: optional page_index (-1=active). Returns a structured JSON critique report."
                ),
            ),
            StructuredTool.from_function(
                func=_apply_repairs,
                name="designer_apply_repairs",
                description=(
                    "Apply safe deterministic repairs to a page after critique. Input: optional page_index "
                    "(-1=active) and optional categories list chosen from hierarchy, overflow, contrast, "
                    "readability, spacing."
                ),
            ),
            StructuredTool.from_function(
                func=_brand_lint,
                name="designer_brand_lint",
                description=(
                    "Read-only brand-lint scan across contrast, off-palette colors, non-brand fonts, "
                    "missing image alt text, and logo safe-zone overlaps. "
                    "Input: optional page_index (-1=all pages). Returns structured JSON findings."
                ),
            ),
            StructuredTool.from_function(
                func=_set_brand,
                name="designer_set_brand",
                description=(
                    "Update the project's brand colors, fonts, and logo placement settings. "
                    "Input: any combination of primary_color, secondary_color, "
                    "accent_color, bg_color, text_color, heading_font, body_font, "
                    "logo_mode, logo_scope, logo_position, logo_max_height, and logo_padding."
                ),
            ),
            StructuredTool.from_function(
                func=_resize_project,
                name="designer_resize_project",
                description=(
                    "Resize the project canvas using an explicit aspect_ratio or a built-in preset. "
                    "Preset options include Presentation Widescreen, Presentation Standard, Square Social, "
                    "Story Vertical, A4 Document, and Letter Document."
                ),
            ),
            StructuredTool.from_function(
                func=_export,
                name="designer_export",
                description=(
                    "Export the designer project. Input: format ('pdf', 'pptx', 'html', or 'png'), "
                    "optional pages range, optional mode ('screenshot' or 'structured' for pptx)."
                ),
            ),
            StructuredTool.from_function(
                func=_publish_link,
                name="designer_publish_link",
                description=(
                    "Publish the current design as a self-contained HTML link served by Thoth. "
                    "Input: optional pages range when you want to publish only part of the deck."
                ),
            ),
            # ── Phase 5A: AI Content ─────────────────────────────
            StructuredTool.from_function(
                func=_generate_image,
                name="designer_generate_image",
                description=(
                    "Generate an AI image from a text prompt and embed it in a page. "
                    "Input: prompt (text description), optional page_index (-1=active), "
                    "position ('top'/'bottom'), width, height, size."
                ),
            ),
            StructuredTool.from_function(
                func=_insert_image,
                name="designer_insert_image",
                description=(
                    "Insert an attached, pasted, generated, or local image into a page. "
                    "Input: image_source (attachment filename, partial filename, 'last', or path), "
                    "optional page_index (-1=active), position ('top'/'bottom'), width, alt."
                ),
            ),
            StructuredTool.from_function(
                func=_generate_video,
                name="designer_generate_video",
                description=(
                    "Generate an AI video clip (MP4) from a text prompt (or image-to-video) and embed it "
                    "in a page. Input: prompt, optional page_index (-1=active), position ('top'/'bottom'), "
                    "width, aspect_ratio (e.g. '16:9', '9:16', '1:1' — defaults to project aspect), "
                    "duration (seconds, default 6), resolution ('720p'/'1080p'), and optional image_source "
                    "('last', attachment filename, or path) to animate an existing image."
                ),
            ),
            StructuredTool.from_function(
                func=_insert_video,
                name="designer_insert_video",
                description=(
                    "Insert an attached, pasted, or local video file (mp4/webm/mov) into a page. "
                    "Input: video_source (attachment filename, partial filename, 'last', or path), "
                    "optional page_index (-1=active), position ('top'/'bottom'), width, alt."
                ),
            ),
            StructuredTool.from_function(
                func=_move_image,
                name="designer_move_image",
                description=(
                    "Move an existing inserted image or chart using its asset ID or label. "
                    "Input: page_index, image_ref, position ('top'/'bottom'/'before'/'after'), optional target_ref."
                ),
            ),
            StructuredTool.from_function(
                func=_replace_image,
                name="designer_replace_image",
                description=(
                    "Replace an existing inserted image or chart using its asset ID or label. "
                    "Input: image_ref, image_source, optional page_index (-1=active), width, alt."
                ),
            ),
            StructuredTool.from_function(
                func=_remove_image,
                name="designer_remove_image",
                description=(
                    "Remove an inserted image, chart, or video from a page, leaving the surrounding "
                    "layout (shot-visual placeholders, slide grids) intact. Use this when the user asks "
                    "to delete/remove a picture from a page WITHOUT deleting the page itself. "
                    "Input: image_ref (asset ID or label), optional page_index (-1=active)."
                ),
            ),
            StructuredTool.from_function(
                func=_move_element,
                name="designer_move_element",
                description=(
                    "Move a DOM element without rewriting the whole page. Input: page_index, selector or "
                    "element_ref, position ('top'/'bottom'/'before'/'after'), and optional target_selector or target_ref. "
                    "Use selector_hint values from designer_get_project or a CSS selector from designer_get_page_html."
                ),
            ),
            StructuredTool.from_function(
                func=_duplicate_element,
                name="designer_duplicate_element",
                description=(
                    "Duplicate a DOM element or section and insert the copy. Input: page_index, selector or element_ref, "
                    "position ('top'/'bottom'/'before'/'after'), and optional target_selector or target_ref. "
                    "Returns a new element id and selector hint for follow-up edits."
                ),
            ),
            StructuredTool.from_function(
                func=_restyle_element,
                name="designer_restyle_element",
                description=(
                    "Restyle an existing DOM element without regenerating the page. Input: page_index, selector or "
                    "element_ref, optional style_updates (JSON object or CSS declarations), add_classes, remove_classes."
                ),
            ),
            StructuredTool.from_function(
                func=_refine_text,
                name="designer_refine_text",
                description=(
                    "Refine a text element on a page using AI. "
                    "Input: page_index, tag (e.g. 'h1', 'p'), old_text (exact text to find), "
                    "action ('shorten'/'expand'/'professional'/'casual'/'persuasive'/"
                    "'simplify'/'bullets'/'paragraph'/'custom'), optional custom_instruction."
                ),
            ),
            StructuredTool.from_function(
                func=_add_chart,
                name="designer_add_chart",
                description=(
                    "Add a data visualization chart to a page. "
                    "Input: chart_type (bar/line/pie/scatter/donut/histogram/box/area/heatmap), "
                    "data_csv (inline CSV with header), optional title, page_index, position, width, height."
                ),
            ),
        ]

        # ── Phase 2.2 — mode-gated interactive sub-tools ───────────────
        project = get_ui_active_project()
        current_mode = getattr(project, "mode", "deck") if project else "deck"
        is_interactive = current_mode in INTERACTIVE_PROJECT_MODES

        if is_interactive:
            # Remove designer_move_page — pages are routes in interactive
            # modes and nav order is controlled by designer_reorder_routes.
            base_tools = [
                t for t in base_tools
                if getattr(t, "name", "") != "designer_move_page"
            ]
            base_tools.extend([
                StructuredTool.from_function(
                    func=_add_screen,
                    name="designer_add_screen",
                    description=(
                        "Add a new screen/route in an interactive project (landing or app_mockup). "
                        "Input: title (display name), optional route_id (auto-slugified from title if empty), "
                        "optional html (branded blank if empty), optional copy_from (page index to duplicate)."
                    ),
                ),
                StructuredTool.from_function(
                    func=_link_screens,
                    name="designer_link_screens",
                    description=(
                        "Wire a click on one element to navigate to another screen. "
                        "Input: source_route (existing route_id), selector (CSS selector or "
                        "data-row-bot-element-id), target_route (existing route_id), "
                        "optional event ('click'), optional transition ('fade'/'slide_left'/'slide_up'/'none')."
                    ),
                ),
                StructuredTool.from_function(
                    func=_set_interaction,
                    name="designer_set_interaction",
                    description=(
                        "Attach a generic interaction to an element. "
                        "Input: source_route, selector, action ('navigate'|'toggle_state'|'play_media'), "
                        "target (route_id / state key / asset_id), optional event, optional transition."
                    ),
                ),
                StructuredTool.from_function(
                    func=_preview_screen,
                    name="designer_preview_screen",
                    description=(
                        "Switch the editor's active screen to a specific route_id. "
                        "Input: route_id."
                    ),
                ),
                StructuredTool.from_function(
                    func=_reorder_routes,
                    name="designer_reorder_routes",
                    description=(
                        "Reorder screens to match the given list of route_ids. "
                        "Input: route_ids (list of every existing route_id in the desired order)."
                    ),
                ),
            ])

        base_tools.append(
            StructuredTool.from_function(
                func=_set_mode,
                name="designer_set_mode",
                description=(
                    "Change project mode. Input: mode "
                    "('deck'|'document'|'landing'|'app_mockup'|'storyboard'). "
                    "Switches the available tool surface and preview layout."
                ),
            )
        )

        return base_tools


# ── Auto-register ────────────────────────────────────────────────────────
registry.register(DesignerTool())


